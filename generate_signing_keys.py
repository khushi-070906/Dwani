"""
generate_signing_keys.py

Run this ONCE, on your own machine, to create your Ed25519 keypair.

    python generate_signing_keys.py

It prints two values:
  - PRIVATE key: set this as LDST_LICENSE_PRIVATE_KEY on license_server.py's
    host (Render env vars) ONLY. Never put it in server.py, never ship it to
    a presenter's machine, never commit it to git.
  - PUBLIC key: this is safe to expose. Set it as LDST_LICENSE_PUBLIC_KEY
    wherever server.py/activate.py run (the presenter's machine), or bake
    it directly into the compiled .exe as a constant if you'd rather not
    rely on an env var being set correctly on every presenter's laptop.

If you already have real customers on the OLD HMAC-based tokens, note that
switching schemes invalidates all previously-issued tokens -- every
presenter will need to re-run activate.py once after you deploy this change.
"""

from licensing import generate_keypair

private_key_b64, public_key_b64 = generate_keypair()

print("=" * 70)
print("PRIVATE KEY (license_server.py env var LDST_LICENSE_PRIVATE_KEY only)")
print("NEVER commit this, NEVER ship this to a presenter's machine:")
print("=" * 70)
print(private_key_b64)
print()
print("=" * 70)
print("PUBLIC KEY (safe to embed in the presenter app / server.py)")
print("=" * 70)
print(public_key_b64)
print()
print("Save both somewhere safe right now -- this script won't show them again.")
