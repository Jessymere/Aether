"""Generate the Ed25519 key pair Robinhood's Trading API expects.

Robinhood authenticates with a key pair, not a secret alone: you upload the
public half in the app and sign every request with the private half. This
prints both, base64-encoded, in the order the app asks for them.

    python -m desk_keys

Writes nothing — paste the private value into ``ROBINHOOD_PRIVATE_KEY`` in
``.env`` and upload the public value to Robinhood.
"""

from __future__ import annotations

import base64

from nacl.signing import SigningKey

#: Environment lines, printed last so they can be pasted straight into .env.
TEMPLATE = "ROBINHOOD_PRIVATE_KEY={private}"


def generate() -> tuple[str, str]:
    """Return ``(public_b64, private_b64)`` for a fresh pair."""
    key = SigningKey.generate()
    public = base64.b64encode(bytes(key.verify_key)).decode()
    private = base64.b64encode(bytes(key)).decode()
    return public, private


def main() -> int:
    public, private = generate()
    print("Upload this public key in the Robinhood app (API credentials):")
    print(f"  {public}")
    print()
    print("Keep this private key secret. Add to .env:")
    print("  " + TEMPLATE.format(private=private))
    print()
    print("The API key from the app goes in ROBINHOOD_API_KEY.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
