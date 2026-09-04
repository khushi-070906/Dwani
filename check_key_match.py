"""
Run this on the machine where license_server.py runs, with the same
environment active (so LDST_LICENSE_PRIVATE_KEY is set the same way).

It derives the public key from your current private key and compares it
to the public key baked into server.py. It never prints the private key.
"""
import os
import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

EXPECTED_PUBLIC_KEY = "-50neXrC2PCdrcUKNyq0cYyswqM-O23gThTebrkzC7M"  # from server.py


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64decode(data: str) -> bytes:
    data = data.strip()
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


private_key_b64 = os.environ.get("LDST_LICENSE_PRIVATE_KEY", "")
if not private_key_b64:
    raise SystemExit("LDST_LICENSE_PRIVATE_KEY is not set in this environment.")

private_key = Ed25519PrivateKey.from_private_bytes(b64decode(private_key_b64))
derived_public_key = b64encode(private_key.public_key().public_bytes_raw())

print(f"Public key derived from your current private key: {derived_public_key}")
print(f"Public key baked into server.py:                  {EXPECTED_PUBLIC_KEY}")

if derived_public_key == EXPECTED_PUBLIC_KEY:
    print("\nMATCH -- these are the same keypair. The mismatch is elsewhere.")
else:
    print(
        "\nMISMATCH -- the private key currently signing tokens does NOT "
        "correspond to the public key server.py is verifying against.\n"
        "Fix: either update DEFAULT_LICENSE_PUBLIC_KEY / LDST_LICENSE_PUBLIC_KEY "
        "in server.py to match the key above, or set LDST_LICENSE_PRIVATE_KEY "
        "back to the original private key that pairs with the hardcoded one, "
        "then re-run activate.py to get a token signed with the matching key."
    )
