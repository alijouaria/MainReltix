import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import connector
from app.seed import seed

JOB = {
    "case_id": 1,
    "partner_id": 1,
    "job_type": "valve_replacement_balancing",
    "unit": {"address": "Musterstr. 1", "floor": 2, "unit_id": "U-2-1"},
    "access_windows": [{"start": "2026-11-21T08:00:00", "end": "2026-11-21T12:00:00"}],
    "evidence_summary": "Unit 40% below building median for 5 days",
    "boiler_model": "Viessmann Vitodens 200",
    "consent_scope": ["unit_access", "contact_tenant"],
}


@pytest.fixture
def client(conn):
    seed(conn)
    app = FastAPI()
    app.include_router(connector.router)
    app.dependency_overrides[connector.get_conn] = lambda: conn
    return TestClient(app)


def _events(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM case_events ORDER BY id")]


def test_create_job_offered(client, conn):
    r = client.post("/partner/jobs", json=JOB)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "offered" and body["job_id"]
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (body["job_id"],)).fetchone()
    assert row["craftsman_id"] == 1 and row["unit_id"] == "U-2-1"
    assert json.loads(row["payload"])["job_type"] == JOB["job_type"]
    g = client.get(f"/partner/jobs/{body['job_id']}").json()
    assert g["status"] == "offered"


def test_status_transitions_append_events(client, conn):
    jid = client.post("/partner/jobs", json=JOB).json()["job_id"]
    n0 = len(_events(conn))
    client.post(f"/partner/jobs/{jid}/status", json={"status": "accepted"})
    r = client.post(f"/partner/jobs/{jid}/status",
                    json={"status": "scheduled", "slot": "2026-11-21T09:00:00"})
    assert r.json()["scheduled_at"] == "2026-11-21T09:00:00"
    r = client.post(f"/partner/jobs/{jid}/status", json={
        "status": "completed", "outcome": {"fixed": True},
        "invoice": {"amount_eur": 380.0, "file_name": "inv-1.pdf"}})
    assert r.json()["status"] == "completed"
    ev = _events(conn)[n0:]
    assert [e["kind"] for e in ev] == ["job_accepted", "job_scheduled", "job_completed"]
    assert all(e["actor"] == "partner:1" for e in ev)
    doc = conn.execute("SELECT * FROM documents WHERE kind='invoice' AND case_id=1 "
                       "ORDER BY id DESC").fetchone()
    assert json.loads(doc["payload"])["amount_eur"] == 380.0


def test_rejects_consumption_data(client):
    bad = dict(JOB, heat_readings=[{"date": "2026-11-01", "kwh": 3.2}])
    assert client.post("/partner/jobs", json=bad).status_code == 422


def test_invalid_status_422(client):
    jid = client.post("/partner/jobs", json=JOB).json()["job_id"]
    assert client.post(f"/partner/jobs/{jid}/status", json={"status": "teleported"}).status_code == 422


def test_unknown_job_404(client):
    assert client.get("/partner/jobs/9999").status_code == 404
