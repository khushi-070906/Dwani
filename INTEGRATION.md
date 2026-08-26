# Wiring this into `server.py`

## 1. One-time activation (presenter, while online)

Same moment as the README's model download step:

```
python activate.py --license-key LDST-XXXX-XXXX --email you@university.edu --server https://license.yourdomain.com
```

This writes a signed token to `~/.ldst/license.token`. Nothing else on the
presenter's machine needs internet after this.

## 2. Gate session start in `server.py`

Near the top of `server.py`, before `presenter_page()` builds the QR code:

```python
from licensing import check_license, LicenseError

LICENSE_SIGNING_KEY = bytes.fromhex(os.environ["LDST_LICENSE_SIGNING_KEY"])
# ^ presenter's build ships with the PUBLIC verification key baked in,
#   not the server's private signing key — see "before you ship" note below.

def presenter_page():
    try:
        license = check_license(
            LICENSE_SIGNING_KEY,
            requested_flags={
                "semantic_cache": args.semantic_cache,
                "glossary_file": args.glossary_file,
                "qa": args.qa,
                "isl": args.isl,
            },
        )
    except LicenseError as e:
        print(f"[license] {e}")
        sys.exit(1)

    # ... existing presenter_page() body, unchanged
```

That's the only change to the core pipeline. Every module you already built
(`glossary.py`, `translation_cache.py`, `qa_pipeline.py`, `isl_matching.py`)
stays exactly as-is — `check_license` just decides whether their flags are
allowed to be passed at all.

## Before you actually ship this

The `sign_license` / `verify_license` split above uses one shared HMAC key,
which is fine for a pilot but means anyone who extracts the key from a
distributed binary could forge tokens. Before charging real money:

- Switch to asymmetric signing (Ed25519) — the server holds the private key,
  presenter builds only ever contain the public key, so extraction doesn't
  let anyone mint their own license.
- Consider whether you even want hard enforcement vs. an honor-system key
  (common for research-tool audiences) plus usage analytics on `/activate`
  calls — much less friction for a hackathon-adjacent tool, at the cost of
  some revenue leakage.
