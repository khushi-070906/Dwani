"""Troubleshooting guide: served offline, and every "how to fix" link points at a real section."""
import http.client
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HELP = (ROOT / "static" / "help.html").read_text(encoding="utf-8")
SECTIONS = set(re.findall(r'<details id="([\w-]+)"', HELP))


def test_guide_has_the_sections_we_promised():
    for sid in ("smartscreen", "app-control", "firewall", "phones", "hotspot", "mic", "mic-silent", "slow",
                "phone-mic", "activation", "attendee-reconnect", "attendee-display"):
        assert sid in SECTIONS, sid


def test_every_help_link_points_at_a_real_section():
    refs = set()
    refs |= set(re.findall(r', "([\w-]+)"\)\)?$', (ROOT / "preflight.py").read_text(encoding="utf-8"), re.M))
    refs |= set(re.findall(r'help: "([\w-]+)"', (ROOT / "static" / "host.html").read_text(encoding="utf-8")))
    for page in ("index.html", "host.html"):
        refs |= set(re.findall(r'/help#([\w-]+)', (ROOT / "static" / page).read_text(encoding="utf-8")))
    import gui
    refs |= {anchor for _, anchor in gui._HELP_RULES} | {gui.help_anchor_for("something unexpected")}
    missing = {r for r in refs if r not in SECTIONS}
    assert refs and not missing, f"links to missing help sections: {missing}"


def test_preflight_checks_carry_help_links():
    import preflight as pf
    checks = pf.network_checks(["10.0.0.5"], "10.0.0.5", 1) + [pf.firewall_check(), pf.phones_check(0)]
    warn_or_fail = [c for c in checks if c.status in (pf.WARN, pf.FAIL)]
    assert warn_or_fail and all(c.help in SECTIONS for c in warn_or_fail)


def test_error_messages_map_to_the_right_section():
    import appenv
    import gui
    cases = {
        ImportError("DLL load failed while importing frame: An Application Control policy has blocked this file."): "app-control",
        RuntimeError("Unsupported model binary version"): "models-damaged",
        MemoryError(): "memory",
        OSError("[Errno 28] No space left on device"): "download",
        RuntimeError("DwaniLive is being run from inside the .zip file."): "zip",
    }
    for exc, anchor in cases.items():
        assert gui.help_anchor_for(appenv.friendly_error(exc)) == anchor, (exc, anchor)


def test_server_serves_help():
    import server
    from fastapi.testclient import TestClient
    r = TestClient(server.app).get("/help")
    assert r.status_code == 200 and "Fix common problems" in r.text


def test_launcher_serves_help_without_token_and_blocks_traversal():
    import gui
    from http.server import ThreadingHTTPServer
    app = gui.LauncherApp(setup_firewall_if_needed=lambda: None, server_port=8000, license_server_url="http://x")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app._make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def get(path):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", path)
        r = c.getresponse()
        return r.status, r.read(), r.getheader("Content-Type")
    try:
        st, body, ctype = get("/help")
        assert st == 200 and b"Fix common problems" in body and ctype.startswith("text/html")
        st, body, ctype = get("/static/fonts/AtkinsonHyperlegible-Regular.ttf")
        assert st == 200 and len(body) > 10000
        assert get("/static/../appenv.py")[0] == 404          # path traversal
        assert get("/static/%2e%2e/appenv.py")[0] == 404
        assert get("/api/state")[0] == 403                     # app API still needs the token
    finally:
        httpd.shutdown()
