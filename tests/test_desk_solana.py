"""The wire format: base58, keypairs, and what exactly gets signed.

A transaction built here is deliberately hand-rolled. The point of the module
is that the layout is known, so the test asserts the signed payload rather than
trusting a byte offset some future refactor could slide by one.
"""

from __future__ import annotations

import base64

import pytest
from nacl.signing import SigningKey

import desk_solana as S


def _key() -> S.Keypair:
    key = SigningKey.generate()
    return S.Keypair(key=key, address=S.b58encode(key.verify_key.encode()))


def _transaction(payer: bytes, *, version: int | None = 0,
                 required: int = 1, signatures: int = 1) -> bytes:
    """One signature slot, one instruction-free message, two static keys."""
    other = bytes(range(32))
    raw = bytearray([signatures]) + bytearray(64 * signatures)
    if version is not None:
        raw.append(0x80 | version)
    raw += bytes([required, 0, 1, 2])       # header + compact-u16 key count
    raw += payer + other + bytes(32)        # keys + blockhash
    raw.append(0)                           # instruction count
    return bytes(raw)


def _decode(encoded: str) -> bytes:
    return base64.b64decode(encoded)


# ---------------------------------------------------------------------------
# base58
# ---------------------------------------------------------------------------
def test_base58_round_trips_leading_zeros():
    raw = b"\0\0\x01\x02" + bytes(range(28))
    assert S.b58decode(S.b58encode(raw)) == raw


def test_base58_rejects_foreign_characters():
    with pytest.raises(S.SolanaError):
        S.b58decode("0OIl")


# ---------------------------------------------------------------------------
# keypairs
# ---------------------------------------------------------------------------
def test_a_64_byte_key_and_a_32_byte_seed_load_to_the_same_address():
    key = SigningKey.generate()
    seed = bytes(key)
    full = seed + key.verify_key.encode()
    assert S.load_keypair(S.b58encode(full)).address == \
           S.load_keypair(S.b58encode(seed)).address


def test_a_solana_keygen_json_array_loads():
    key = SigningKey.generate()
    array = list(bytes(key) + key.verify_key.encode())
    assert S.load_keypair(str(array).replace(" ", "")).address == \
           S.b58encode(key.verify_key.encode())


def test_a_pkcs8_pem_key_loads_to_its_seed_address():
    import base64

    key = SigningKey.generate()
    der = bytes.fromhex("302e020100300506032b657004220420") + bytes(key)
    pem = ("-----BEGIN PRIVATE KEY-----\n"
           + base64.b64encode(der).decode() + "\n"
           "-----END PRIVATE KEY-----\n")
    assert S.load_keypair(pem).address == S.b58encode(key.verify_key.encode())


def test_a_pem_that_is_not_ed25519_is_refused():
    import base64

    pem = ("-----BEGIN PRIVATE KEY-----\n"
           + base64.b64encode(b"\x30\x2e" + bytes(46)).decode() + "\n"
           "-----END PRIVATE KEY-----")
    with pytest.raises(S.SolanaError, match="not a PKCS#8"):
        S.load_keypair(pem)


def test_a_short_key_is_refused():
    with pytest.raises(S.SolanaError):
        S.load_keypair(S.b58encode(b"tooshort"))


# ---------------------------------------------------------------------------
# signing
# ---------------------------------------------------------------------------
def test_the_signature_covers_the_version_prefix_and_the_message():
    signer = _key()
    raw = _transaction(signer.public_bytes)
    signed = _decode(S.sign_transaction(base64.b64encode(raw).decode(), signer))

    envelope = S.parse_envelope(signed)
    signature = signed[envelope.signature_start:envelope.signature_end]
    payload = signed[envelope.signed_from:]
    assert payload[0] == 0x80                     # the version prefix is signed
    signer.key.verify_key.verify(signature + payload)


def test_a_legacy_transaction_signs_from_the_message():
    signer = _key()
    raw = _transaction(signer.public_bytes, version=None)
    signed = _decode(S.sign_transaction(base64.b64encode(raw).decode(), signer))
    envelope = S.parse_envelope(signed)
    assert envelope.version is None
    signer.key.verify_key.verify(
        signed[envelope.signature_start:envelope.signature_end]
        + signed[envelope.message:])


def test_a_foreign_payer_is_refused():
    with pytest.raises(S.SolanaError, match="not paid by this key"):
        S.sign_transaction(base64.b64encode(_transaction(_key().public_bytes)).decode(),
                           _key())


def test_a_version_one_transaction_is_refused():
    signer = _key()
    raw = _transaction(signer.public_bytes, version=1)
    with pytest.raises(S.SolanaError, match="unsupported"):
        S.sign_transaction(base64.b64encode(raw).decode(), signer)


def test_an_empty_signature_block_is_refused():
    signer = _key()
    raw = bytearray(_transaction(signer.public_bytes))
    raw[0] = 0
    with pytest.raises(S.SolanaError, match="no signature slot"):
        S.sign_transaction(base64.b64encode(bytes(raw)).decode(), signer)


def test_non_base64_is_refused():
    with pytest.raises(S.SolanaError):
        S.sign_transaction("!!!", _key())
