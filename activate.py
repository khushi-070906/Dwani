"""
activate.py

Run this ONCE while online — the same moment you download the Whisper/NLLB
models per the README's Setup step. It exchanges a license key (from your
purchase email / Stripe checkout) for a signed token and caches it locally
so `server.py` can validate offline at the venue.

Usage:
    python activate.py --license-key LDST-XXXX-XXXX --server https://license.yourdomain.com
"""

import argparse
import sys
import urllib.request
import json

from licensing import save_cached_license, DEFAULT_CACHE_PATH


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--license-key", required=True, help="From your purchase confirmation email")
    parser.add_argument("--server", required=True, help="License server base URL, e.g. https://license.yourdomain.com")
    parser.add_argument("--email", required=True, help="Presenter email associated with the subscription")
    args = parser.parse_args()

    body = json.dumps({"license_key": args.license_key, "email": args.email}).encode("utf-8")
    req = urllib.request.Request(
        f"{args.server}/activate",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"Activation failed: {exc}", file=sys.stderr)
        print("Check your internet connection and license key, then try again.", file=sys.stderr)
        sys.exit(1)

    if "token" not in data:
        print(f"Activation failed: {data.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)

    save_cached_license(data["token"])
    print(f"Activated. License cached at {DEFAULT_CACHE_PATH}")
    print(f"Tier: {data.get('tier')}  Expires: {data.get('expires_at_human')}")
    print("You can now run server.py fully offline until then.")


if __name__ == "__main__":
    main()
