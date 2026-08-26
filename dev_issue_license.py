"""
dev_issue_license.py

Local-only helper for testing server.py without running a real license_server.py.

It mints a signed license token directly, using the SAME LDST_LICENSE_SIGNING_KEY
that server.py reads from .env at startup -- so the token it writes is guaranteed
to pass check_license()'s signature check. This replaces activate.py + a live
:8443 server for local/dev testing only. For real customers you still need an
actual license_server.py that signs with a key it keeps private.

Usage (run from the same directory as your .env, e.g. the ldst/ project root):
    python dev_issue_license.py --tier institution --email test@example.com --days 30
"""

import argparse
import os
import sys
import time

from dotenv import load_dotenv

from licensing import License, sign_license, save_cached_license, DEFAULT_CACHE_PATH, TIER_FEATURES


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", choices=sorted(TIER_FEATURES.keys()), default="institution")
    parser.add_argument("--email", required=True)
    parser.add_argument("--days", type=int, default=30, help="Validity window from now, in days")
    parser.add_argument("--max-attendees", type=int, default=None)
    args = parser.parse_args()

    load_dotenv()
    key = os.environ.get("LDST_LICENSE_SIGNING_KEY")
    if not key:
        print(
            "LDST_LICENSE_SIGNING_KEY is not set in your environment/.env -- "
            "set it to any secret string first (server.py needs this same value "
            "to verify tokens, so pick it once and don't change it).",
            file=sys.stderr,
        )
        sys.exit(1)

    now = int(time.time())
    license = License(
        tier=args.tier,
        presenter_email=args.email,
        issued_at=now,
        expires_at=now + args.days * 86400,
        max_attendees=args.max_attendees,
    )

    token = sign_license(license, key.encode("utf-8"))
    save_cached_license(token)

    print(f"Issued a local '{args.tier}' test license for {args.email}.")
    print(f"Valid {args.days} day(s) from now. Cached at {DEFAULT_CACHE_PATH}")
    print("You can now run server.py directly -- no activate.py or license server needed.")


if __name__ == "__main__":
    main()
