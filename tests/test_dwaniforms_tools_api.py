"""Names, addresses and spoken text through the company API: /names/match, /address/parse, /speech/parse."""
import pytest

from dwaniforms import apikeys, b2b


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DWANIFORMS_KEY_SECRET", "test-secret-0123456789-abcdefghij")
    monkeypatch.delenv("DWANIFORMS_REVOKED_KEYS", raising=False)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dwaniforms.service import FormService
    app = FastAPI(); app.mount("/api/v1", b2b.build_api(FormService(None, None)))
    return TestClient(app, raise_server_exceptions=False)


def H(services):
    raw, _ = apikeys.issue("Acme Ltd", services)
    return {"Authorization": f"Bearer {raw}"}


def test_api_names(client):
    r = client.post("/api/v1/names/match", headers=H(["names"]), json={"a": "Mohd Aslam", "b": "मोहम्मद अस्लम"})
    assert r.status_code == 200 and r.json()["verdict"] == "same"


def test_api_address(client):
    r = client.post("/api/v1/address/parse", headers=H(["address"]), json={"address": "H-24, gali no. 3, near Shiv mandir, Uttam Nagar"})
    j = r.json()
    assert r.status_code == 200 and j["parts"]["landmark"] == "Shiv Mandir" and "pincode" in j["missing"]
    assert j["formatted"] == "H-24, Gali No. 3, Near Shiv Mandir, Uttam Nagar"


def test_api_speech(client):
    h = H(["speech"])
    assert client.post("/api/v1/speech/parse", headers=h, json={"text": "pandrah march do hazaar do"}).json()["date"]["dmy"] == "15/03/2002"
    j = client.post("/api/v1/speech/parse", headers=h, json={"text": "do hazaar teen sau paanch", "kind": "number"}).json()
    assert j["number"] == 2305 and "date" not in j
    assert client.post("/api/v1/speech/parse", headers=h, json={"text": "nau aath saat chhe", "kind": "digits"}).json()["digits"] == "9876"


def test_api_tools_need_their_own_scope(client):
    for path, body in (("names/match", {"a": "a", "b": "b"}), ("address/parse", {"address": "x"}), ("speech/parse", {"text": "x"})):
        r = client.post(f"/api/v1/{path}", headers=H(["rti"]), json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "service_not_in_plan"
        assert client.post(f"/api/v1/{path}", json=body).status_code == 401
        assert client.post(f"/api/v1/{path}", headers=H(["*"]), json=body).status_code == 200


def test_api_tools_reject_bad_bodies_without_echoing_them(client):
    r = client.post("/api/v1/names/match", headers=H(["names"]), json={"a": "only one"})
    assert r.status_code == 422 and "only one" not in r.text
    r = client.post("/api/v1/speech/parse", headers=H(["speech"]), json={"text": "x", "kind": "bogus"})
    assert r.status_code == 422
    r = client.post("/api/v1/address/parse", headers=H(["address"]), json={"address": "x" * 1001})
    assert r.status_code == 422


def test_the_new_services_can_be_put_in_a_key():
    raw, k = apikeys.issue("Acme Ltd", ["names", "address", "speech"], key=b"k" * 32)
    assert k.allows_tool("names") and not k.allows_tool("rti") and apikeys.valid_services() >= {"names", "address", "speech"}
