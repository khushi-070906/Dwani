"""Website: /api/crash-report stores reports (rate-limited, size-capped); the team
reads them at /admin/crash-reports?token=… Needs the website's deps + Postgres."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for mod in ("psycopg2", "bcrypt", "razorpay"):
    pytest.importorskip(mod)
if not (os.environ.get("DATABASE_URL", "").startswith("postgres") and os.environ.get("LDST_LICENSE_PRIVATE_KEY")):
    pytest.skip("website tests need DATABASE_URL (Postgres) and LDST_LICENSE_PRIVATE_KEY", allow_module_level=True)

import license_server as ls  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def c(monkeypatch):
    ls._crash_hits.clear()
    monkeypatch.setenv("REPORTS_TOKEN", "team-secret")
    with TestClient(ls.app) as client:
        yield client


def test_report_stored_and_readable_by_the_team(c):
    r = c.post("/api/crash-report", json={"version": "1.14.0", "os": "Windows-11", "error": "<b>boom</b> models damaged",
                                          "log": "line1\nline2"})
    assert r.status_code == 200
    ref = r.json()["ref"]
    assert ref.startswith("DL-ERR-")
    assert c.get("/admin/crash-reports").status_code == 404                    # no token: page doesn't exist
    assert c.get("/admin/crash-reports?token=wrong").status_code == 404
    page = c.get("/admin/crash-reports?token=team-secret").text
    assert ref in page and "&lt;b&gt;boom&lt;/b&gt;" in page and "<b>boom</b>" not in page   # escaped
    one = c.get(f"/admin/crash-reports?token=team-secret&ref={ref.lower()}").text
    assert ref in one and "line2" in one


def test_limits(c):
    assert c.post("/api/crash-report", content=b"x" * (ls.CRASH_MAX_BYTES + 1)).status_code == 413
    assert c.post("/api/crash-report", content=b"not json").status_code == 400
    codes = [c.post("/api/crash-report", json={"error": f"e{i}"}, headers={"x-forwarded-for": "203.0.113.9"}).status_code
             for i in range(ls.CRASH_PER_HOUR + 1)]
    assert codes[:-1] == [200] * ls.CRASH_PER_HOUR and codes[-1] == 429
    assert c.post("/api/crash-report", json={"error": "x"}, headers={"x-forwarded-for": "198.51.100.7"}).status_code == 200


def test_page_disabled_without_token_configured(c, monkeypatch):
    monkeypatch.delenv("REPORTS_TOKEN")
    assert c.get("/admin/crash-reports?token=").status_code == 404
