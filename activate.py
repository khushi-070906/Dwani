"""
activate.py

Run this ONCE while online -- the same moment you download the Whisper/NLLB
models per the README's Setup step. It exchanges a license key (from your
purchase confirmation, shown once in the pricing page's popup after
checkout) for a signed token and caches it locally so `server.py` can
validate offline at the venue.

Usage:
    python activate.py --license-key abc123def456ghi789jk --server https://dhwanilive-api.onrender.com --email you@example.com
"""

import argparse
import socket
import sys
import urllib.request
import urllib.error
import json

from licensing import save_cached_license, DEFAULT_CACHE_PATH

# Render (and most PaaS hosts) can take a while to respond to the first
# request after the service has been idle -- a cold database connection,
# a persistent disk remounting, etc. 15s was too aggressive and could time
# out on a perfectly healthy server that just hadn't been hit in a while.
REQUEST_TIMEOUT_SECONDS = 60


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--license-key", required=True, help="From your purchase confirmation popup")
    parser.add_argument("--server", required=True, help="License server base URL, e.g. https://dhwanilive-api.onrender.com")
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
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read())
    except socket.timeout:
        print(
            f"Activation failed: no response from the server within {REQUEST_TIMEOUT_SECONDS}s.",
            file=sys.stderr,
        )
        print(
            "This often means the server was cold (idle and slow to wake up) rather than "
            "actually broken -- try running this exact command again once more before "
            "digging further.",
            file=sys.stderr,
        )
        sys.exit(1)
    except urllib.error.HTTPError as exc:
        # The server responded, just not with success -- read its actual
        # error message rather than just printing the generic HTTP code,
        # since check_license()-style errors are meant to be presenter-safe
        # to read directly.
        try:
            error_body = json.loads(exc.read())
            detail = error_body.get("detail", str(exc))
        except (ValueError, TypeError):
            detail = str(exc)
        print(f"Activation failed ({exc.code}): {detail}", file=sys.stderr)
        if exc.code == 404:
            print("-> Double check --license-key and --email match exactly what you used at checkout.", file=sys.stderr)
        elif exc.code == 402:
            print("-> The subscription isn't active yet -- check Razorpay/Render logs to confirm the webhook fired.", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Activation failed: couldn't reach {args.server} -- {exc.reason}", file=sys.stderr)
        print("Check the --server URL is correct and that you have an internet connection.", file=sys.stderr)
        sys.exit(1)
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
