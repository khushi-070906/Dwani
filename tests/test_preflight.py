"""Pre-flight checks: network classification, model detection, endpoint access."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import preflight as pf  # noqa: E402


def test_classify_ip():
    assert pf.classify_ip("192.168.137.1")[0] == "hotspot-laptop"
    assert pf.classify_ip("172.20.10.3")[0] == "hotspot-phone"
    assert pf.classify_ip("192.168.43.20")[0] == "hotspot-phone"
    assert pf.classify_ip("169.254.10.2")[0] == "linklocal"
    assert pf.classify_ip("100.101.1.2")[0] == "vpn"
    assert pf.classify_ip("10.0.0.5")[0] == "lan"
    assert pf.classify_ip("8.8.8.8")[0] == "public"


def test_network_checks():
    assert pf.network_checks([], "", 8000)[0].status == pf.FAIL
    c = {x.id: x for x in pf.network_checks(["169.254.3.3"], "169.254.3.3", 8000)}
    assert c["network"].status == pf.FAIL
    c = {x.id: x for x in pf.network_checks(["192.168.137.1"], "192.168.137.1", 8000)}
    assert c["network"].status == pf.OK
    c = {x.id: x for x in pf.network_checks(["10.0.0.5", "100.90.1.1"], "10.0.0.5", 8000)}
    assert c["network"].status == pf.WARN and "client isolation" in c["network"].fix
    assert c["adapters"].status == pf.INFO and "10.0.0.5" in c["adapters"].fix


def test_port_check_real_socket():
    import socket
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    assert pf.port_check("127.0.0.1", port).status == pf.OK
    srv.close()
    assert pf.port_check("127.0.0.1", port).status == pf.FAIL


class Inner:  # real-looking backend
    pass


class FakeASRBackend:
    pass


class Wrapper:
    def __init__(self, inner):
        self._inner = inner


def test_model_checks_detect_demo_mode_through_wrappers():
    c = {x.id: x for x in pf.model_checks(Inner(), Wrapper(Wrapper(Inner())))}
    assert c["asr"].status == pf.OK and c["translation"].status == pf.OK
    c = {x.id: x for x in pf.model_checks(Wrapper(FakeASRBackend()), None)}
    assert c["asr"].status == pf.FAIL and "Demo mode" in c["asr"].detail
    assert c["translation"].status == pf.FAIL


def test_phones_and_summary():
    assert pf.phones_check(0).status == pf.WARN and pf.phones_check(3).status == pf.OK
    s = pf.summarize([pf.Check("a", "A", pf.OK, ""), pf.Check("b", "B", pf.WARN, "")])
    assert s["overall"] == pf.WARN and len(s["checks"]) == 2
    assert pf.summarize([pf.Check("a", "A", pf.FAIL, "")])["overall"] == pf.FAIL


def test_endpoint_is_local_only():
    import phone_mic
    import server
    from fastapi.testclient import TestClient

    local = TestClient(server.app)
    r = local.get("/preflight")
    assert r.status_code == 200
    body = r.json()
    ids = {c["id"] for c in body["checks"]}
    assert {"asr", "translation", "phones", "firewall"} <= ids and body["overall"] in ("ok", "warn", "fail")
    assert next(c for c in body["checks"] if c["id"] == "asr")["status"] == "fail"  # test server runs fake models
    remote = TestClient(server.app, client=("192.168.1.50", 50000))
    assert remote.get("/preflight").status_code == 404
    assert remote.get(f"/preflight?key={phone_mic.PRESENTER_KEY}").status_code == 200
