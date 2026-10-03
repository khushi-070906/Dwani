"""
Offline verifier for DwaniLive Careers documents. Needs only Python + `cryptography` + `pypdf`.

    python careers/tools/verify_credential.py Certificate.pdf --did https://dhwani-elit.onrender.com/careers/did.json
    python careers/tools/verify_credential.py Certificate.pdf --did-file did.json     # fully offline
    python careers/tools/verify_credential.py DL-CERT-2027-0001.jwt --did-file did.json --status

Checks the Ed25519 signature of the W3C Verifiable Credential embedded in the PDF
(or a .jwt file) against the issuer's published public keys, then prints the signed details.
--status additionally asks the issuer whether the document has been revoked.
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
from careers import credentials as vc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="PDF issued by the portal, or a .jwt credential")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--did", help="URL of the issuer's /.well-known/did.json")
    g.add_argument("--did-file", help="a saved copy of did.json (no network needed)")
    ap.add_argument("--status", action="store_true", help="also check revocation online")
    a = ap.parse_args()

    data = Path(a.file).read_bytes()
    token = vc.extract_from_pdf(data) if data.startswith(b"%PDF") else data.decode().strip()
    if not token:
        print("✗ No signed credential found in this file.")
        return 2
    did_doc = json.loads(Path(a.did_file).read_text() if a.did_file else urllib.request.urlopen(a.did, timeout=15).read())
    res = vc.verify(token, vc.keys_from_did_document(did_doc))
    print(("✓ " if res.valid else "✗ ") + res.reason)
    if res.credential:
        subj = res.credential.get("credentialSubject", {})
        print(f"  issuer        : {res.credential.get('issuer', {}).get('name')} ({res.credential.get('issuer', {}).get('id')})")
        for k in ("documentType", "documentNumber", "name", "college", "role", "startDate", "endDate"):
            if subj.get(k):
                print(f"  {k:<14}: {subj[k]}")
        print(f"  issued        : {res.credential.get('validFrom')}")
    if res.valid and a.status:
        url = res.credential.get("credentialStatus", {}).get("id")
        st = json.loads(urllib.request.urlopen(url, timeout=15).read())
        print("✗ REVOKED by the issuer" if st.get("revoked") else "✓ Not revoked")
        return 1 if st.get("revoked") else 0
    return 0 if res.valid else 1


if __name__ == "__main__":
    sys.exit(main())
