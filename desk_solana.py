"""Solana wire format and JSON-RPC, with no SDK and no new dependency.

This module exists because the only part of Solana a swap desk needs is small:
base58, an Ed25519 signature, and three RPC calls. ``pynacl`` is already
installed, so the whole thing is ~150 lines instead of a dependency on
``solders`` and its build chain.

The transaction layout was established empirically against mainnet
``simulateTransaction`` (not assumed):

    compact-u16 signature count
    64-byte signatures
    version prefix   (0x80 for v0; absent on a legacy transaction)
    message          (header, keys, blockhash, instructions, lookups)

**The bytes signed are the version prefix plus the message.** Signing from the
message alone fails signature verification, which is the kind of detail worth
writing down: it costs two RPC round trips to rediscover.

:func:`sign_transaction` refuses to sign anything it cannot account for — more
than one required signature, a fee payer that is not ours, or a transaction too
short to be real — so a swapped-format surprise is an error rather than a
corrupt send.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from nacl.signing import SigningKey

USER_AGENT = "AetherDesk/1.0"
DEFAULT_TIMEOUT = 25

#: Wrapped SOL. Every memecoin swap on this desk is priced against it.
SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {char: i for i, char in enumerate(_ALPHABET)}


class SolanaError(RuntimeError):
    """Raised when an RPC call or a transaction cannot be handled."""


# ---------------------------------------------------------------------------
# base58
# ---------------------------------------------------------------------------
def b58encode(raw: bytes) -> str:
    count = len(raw) - len(raw.lstrip(b"\0"))
    number = int.from_bytes(raw, "big")
    out = ""
    while number:
        number, rest = divmod(number, 58)
        out = _ALPHABET[rest] + out
    return "1" * count + out


def b58decode(text: str) -> bytes:
    number = 0
    for char in text:
        try:
            number = number * 58 + _INDEX[char]
        except KeyError as exc:
            raise SolanaError(f"not base58: {char!r}") from exc
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(text) - len(text.lstrip("1"))) + body


# ---------------------------------------------------------------------------
# Keypairs
# ---------------------------------------------------------------------------
@dataclass
class Keypair:
    """A signing key and its public address, both in the forms RPC wants."""

    key: SigningKey
    address: str

    @property
    def public_bytes(self) -> bytes:
        return self.key.verify_key.encode()


#: PKCS#8 wrapper around an Ed25519 seed: SEQUENCE, id-ecPublicKey,
#: OCTET STRING of 32 bytes. ``openssl`` and many wallet exports emit exactly
#: this, so it is checked byte for byte rather than parsed as ASN.1.
_PKCS8 = bytes.fromhex("302e020100300506032b657004220420")


def _from_pem(text: str) -> bytes:
    body = "".join(line for line in text.splitlines()
                   if line and not line.startswith("-----"))
    try:
        der = base64.b64decode(body, validate=True)
    except (ValueError, TypeError) as exc:
        raise SolanaError(f"unreadable PEM: {exc}") from exc
    if not der.startswith(_PKCS8) or len(der) != len(_PKCS8) + 32:
        raise SolanaError("PEM is not a PKCS#8 Ed25519 key")
    return der[-32:]


def load_keypair(secret: str) -> Keypair:
    """Accept every shape a wallet exports.

    ``solana-keygen`` writes a JSON array of 64 integers; Phantom exports a
    base58 string of the same 64 bytes; ``openssl`` writes PKCS#8 PEM. All
    carry the same 32-byte seed, and any trailing public key is discarded
    rather than trusted.
    """
    text = (secret or "").strip().strip('"')
    if not text:
        raise SolanaError("empty private key")
    if "-----BEGIN" in text:
        raw = _from_pem(text)
    elif text.startswith("["):
        try:
            raw = bytes(json.loads(text))
        except (ValueError, TypeError) as exc:
            raise SolanaError(f"unreadable keypair array: {exc}") from exc
    else:
        raw = b58decode(text)
    if len(raw) not in (32, 64):
        raise SolanaError(f"key must be 32 or 64 bytes, got {len(raw)}")
    key = SigningKey(raw[:32])
    return Keypair(key=key, address=b58encode(key.verify_key.encode()))


# ---------------------------------------------------------------------------
# Transaction envelope
# ---------------------------------------------------------------------------
def _compact_u16(raw: bytes, offset: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if offset >= len(raw):
            raise SolanaError("truncated transaction")
        byte = raw[offset]
        value |= (byte & 0x7F) << shift
        offset += 1
        shift += 7
        if not byte & 0x80:
            return value, offset


@dataclass(frozen=True)
class Envelope:
    """Where everything sits in a wire transaction. Offsets, not copies."""

    signatures: int
    signature_start: int
    version: Optional[int]
    signed_from: int
    message: int

    @property
    def signature_end(self) -> int:
        return self.signature_start + 64 * self.signatures


def parse_envelope(raw: bytes) -> Envelope:
    signatures, cursor = _compact_u16(raw, 0)
    if signatures < 1:
        raise SolanaError("transaction carries no signature slot")
    start = cursor
    cursor += 64 * signatures
    if cursor >= len(raw):
        raise SolanaError("signature block runs past the transaction")
    version = None
    if raw[cursor] & 0x80:
        version = raw[cursor] & 0x7F
        cursor += 1
    # The signed payload starts at the version prefix (when there is one).
    return Envelope(signatures=signatures, signature_start=start, version=version,
                    signed_from=start + 64 * signatures, message=cursor)


def sign_transaction(transaction: str, keypair: Keypair,
                     last_valid_block_height: Optional[int] = None) -> str:
    """Sign a serialized transaction with ``keypair`` and return it re-encoded.

    ``last_valid_block_height`` is accepted for callers that fetch it; the
    signature does not depend on it.
    """
    del last_valid_block_height  # blockhash is baked into the message
    try:
        raw = bytearray(base64.b64decode(transaction, validate=True))
    except (ValueError, TypeError) as exc:
        raise SolanaError(f"swap transaction is not base64: {exc}") from exc
    if len(raw) < 80:
        raise SolanaError(f"transaction too short ({len(raw)} bytes)")

    envelope = parse_envelope(raw)
    if envelope.version not in (None, 0):
        raise SolanaError(f"unsupported transaction version {envelope.version}")

    message = bytes(raw[envelope.message:])
    header = message[0] if message else 0
    if header != envelope.signatures:
        raise SolanaError(
            f"header wants {header} signature(s), envelope carries "
            f"{envelope.signatures}")
    keys, offset = _compact_u16(message, 3)
    fee_payer = message[offset:offset + 32]
    if fee_payer != keypair.public_bytes:
        # Signing index 0 when the payer is someone else produces a
        # SignatureFailure the chain only reports after a round trip.
        raise SolanaError("transaction is not paid by this key")

    payload = bytes(raw[envelope.signed_from:])
    signature = keypair.key.sign(payload).signature
    raw[envelope.signature_start:envelope.signature_start + 64] = signature
    return base64.b64encode(bytes(raw)).decode()


# ---------------------------------------------------------------------------
# JSON-RPC
# ---------------------------------------------------------------------------
class SolanaRpc:
    """The three RPC methods the desk uses. Plain JSON over ``urllib``."""

    def __init__(self, url: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.url = url
        self.timeout = timeout

    def _call(self, method: str, params: list) -> object:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           "params": params}).encode()
        request = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            raise SolanaError(f"RPC HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SolanaError(f"RPC unreachable: {exc}") from exc
        if payload.get("error"):
            raise SolanaError(str(payload["error"].get("message") or payload["error"]))
        return payload.get("result")

    def lamports(self, owner: str) -> int:
        result = self._call("getBalance", [owner, {"commitment": "confirmed"}])
        return int((result or {}).get("value") or 0)

    def token_balances(self, owner: str) -> dict[str, tuple[float, int]]:
        """``mint -> (ui amount, decimals)`` for every non-empty token account."""
        result = self._call("getTokenAccountsByOwner", [
            owner, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ]) or {}
        out: dict[str, tuple[float, int]] = {}
        for entry in result.get("value") or []:
            info = (((entry or {}).get("account") or {}).get("data") or {}).get("parsed")
            info = (info or {}).get("info") or {}
            token = info.get("tokenAmount") or {}
            mint = info.get("mint") or ""
            amount = float(token.get("uiAmount") or 0.0)
            if mint and amount > 0:
                out[mint] = (amount, int(token.get("decimals") or 0))
        return out

    def token_decimals(self, mint: str) -> int:
        """Decimals for a mint, whether or not this wallet holds any yet.

        A first buy has no token account to read them from, and reading after
        the send races the chain anyway.
        """
        result = self._call("getTokenSupply", [mint, {"commitment": "confirmed"}])
        return int(((result or {}).get("value") or {}).get("decimals") or 0)

    def send(self, transaction: str) -> str:
        return str(self._call("sendTransaction", [
            transaction, {"encoding": "base64", "skipPreflight": False,
                          "preflightCommitment": "confirmed"},
        ]) or "")

    def simulate(self, transaction: str) -> dict:
        return self._call("simulateTransaction", [
            transaction, {"encoding": "base64", "sigVerify": True,
                          "commitment": "confirmed"},
        ]) or {}
