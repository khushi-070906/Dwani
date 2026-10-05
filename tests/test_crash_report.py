"""Error reports: scrubbed before anyone sees them, sent only on request."""
import http.client
import json
import platform
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crash_report as cr  # noqa: E402


def test_scrub_removes_personal_details(monkeypatch):
    monkeypatch.setattr(cr, "_private_bits", lambda: [(r"C:\Users\Khushi", "~"), ("Khushi", "<user>"), ("KHUSHI-LAPTOP", "<computer>")])
    text = (r"File C:\Users\Khushi\AppData\Local\DwaniLive\server.py line 3; also C:/Users/Khushi/Desktop" "\n"
            "user khushi on KHUSHI-LAPTOP, mail khushi.m@gmail.com, phone at 192.168.1.42, local 127.0.0.1:8000\n"
            "licence DL-AB12-CD34-EF56, token eyJhbGciOiJFZERTQSJ9eyJrZXkiOiJhYmMxMjMifQ0123456789")
    out = cr.scrub(text)
    for leak in ("Khushi", "khushi", "KHUSHI-LAPTOP", "gmail", "192.168.1.42", "DL-AB12", "eyJhbGci"):
        assert leak not in out, (leak, out)
    assert r"~\AppData\Local\DwaniLive\server.py" in out and "~/Desktop" in out
    assert "127.0.0.1:8000" in out and "<ip>" in out and "<email>" in out and out.count("<secret>") == 2
    assert "<user>" in out and "<computer>" in out


def test_build_keeps_diagnostics_drops_folders_and_caps_size():
    system = {"app_version": "1.14.0", "platform": "Windows-11-10.0.22631", "python": "3.12.7", "frozen": True,
              "cpu_count": 8, "ram_gb": 7.8, "free_disk_gb": 40.1, "exe_dir": r"C:\Users\x\AppData", "data_dir": "secret"}
    log = [f"line {i} " + "x" * 400 for i in range(400)]
    r = cr.build("Ran out of memory loading the translation models.", log, system)
    assert r["version"] == "1.14.0" and r["system"]["ram_gb"] == 7.8 and "exe_dir" not in r["system"] and "data_dir" not in r["system"]
    assert r["error"].startswith("Ran out of memory")
    assert len(json.dumps(r).encode()) <= cr.MAX_BYTES and r["log"].rstrip().endswith("x")   # newest lines kept
    assert json.loads(cr.preview(r)) == r


class _Sink(BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        _Sink.got.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        body = json.dumps({"ref": "DL-ERR-0042"}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_send_posts_json_and_returns_reference():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        ref = cr.send({"version": "1", "error": "boom"}, f"http://127.0.0.1:{srv.server_address[1]}/")
        assert ref == "DL-ERR-0042" and _Sink.got[-1] == ("/api/crash-report", {"version": "1", "error": "boom"})
    finally:
        srv.shutdown()


def test_launcher_preview_and_send(monkeypatch):
    import gui
    app = gui.LauncherApp(setup_firewall_if_needed=lambda: None, server_port=8000, license_server_url="http://unused")
    app.state.set(phase="error", error="The downloaded model files are damaged or incompatible.")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), app._make_handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def post(route, token=app.token):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", f"{route}?t={token}", body=b"{}", headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, r.read()
    try:
        assert post("/api/report/preview", token="wrong")[0] == 403
        st, body = post("/api/report/preview")
        preview = json.loads(body)["text"]
        assert st == 200 and "damaged or incompatible" in preview and platform.node() not in preview
        sent = []
        monkeypatch.setattr(gui.crash_report, "send", lambda report, url: sent.append((report, url)) or "DL-ERR-0007")
        assert json.loads(post("/api/report/send")[1]) == {"ok": True, "ref": "DL-ERR-0007"}
        assert sent[0][1] == "http://unused" and sent[0][0]["error"].startswith("The downloaded model")

        def offline(report, url):
            raise OSError("no route to host")
        monkeypatch.setattr(gui.crash_report, "send", offline)
        r = json.loads(post("/api/report/send")[1])
        assert r["ok"] is False and "Copy error report" in r["error"]
    finally:
        httpd.shutdown()
