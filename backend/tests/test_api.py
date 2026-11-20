import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main.config, "DB_PATH", str(tmp_path / "api.db"))
    with TestClient(main.app) as c:
        yield c


def test_state_endpoint(client):
    s = client.get("/api/state").json()
    assert s["building"]["id"] == "B1" and len(s["units"]) == 6
    assert s["clock"] == "2026-11-20T08:00:00"
    r = client.post("/api/advance").json()
    s = client.get("/api/state").json()
    assert any(c["id"] == r["case_id"] and c["events"] for c in s["cases"])
    assert any(u["id"] == "2a" and u["risk"] for u in s["units"])
    assert s["messages"]


def test_api_flow_and_webhook(client):
    case_id = client.post("/api/advance").json()["case_id"]
    assert client.post(f"/api/tenant/{case_id}/answer", json={"answer": "yes"}).status_code == 200
    assert client.post(f"/api/landlord/{case_id}/decision", json={"approve": True}).status_code == 200
    client.post("/api/advance")
    r = client.post(f"/api/tenant/{case_id}/reschedule", json={"slot": "2026-11-24T18:00:00"})
    assert r.json()["ok"] is False
    w = client.post("/centrix/events", json={"event": "tenant.reported_issue", "occurred_at": "2026-11-20T09:00:00",
                                             "unit_id": "2a", "contact_id": 5, "data": {"text": "cold"}})
    assert w.status_code == 202
    assert client.post("/api/reset").status_code == 200
    assert client.get("/api/state").json()["cases"] == []


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Guardian" in r.text
