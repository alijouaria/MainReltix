import json
from datetime import datetime

import pytest

from app import orchestrator as orch
from app.clock import clock

pytestmark = pytest.mark.asyncio


@pytest.fixture
def db(conn):
    orch.reset(conn)
    return conn


def _case(conn, case_id):
    return conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()


def _msgs(conn, role=None, channel=None):
    q = "SELECT m.* FROM messages m LEFT JOIN people p ON p.id=m.person_id WHERE 1=1"
    args = []
    if role:
        q += " AND p.role=?"
        args.append(role)
    if channel:
        q += " AND m.channel=?"
        args.append(channel)
    return conn.execute(q, args).fetchall()


async def _to_landlord(conn):
    r = await orch.advance(conn)
    case_id = r["case_id"]
    orch.tenant_answer(conn, case_id, "yes")
    return case_id


async def _to_scheduled(conn):
    case_id = await _to_landlord(conn)
    orch.landlord_decision(conn, case_id, True)
    await orch.advance(conn)
    return case_id


async def test_golden_path(db):
    r = await orch.advance(db)
    case_id = r["case_id"]
    case = _case(db, case_id)
    assert case["unit_id"] == "2a" and case["status"] == "awaiting_tenant"
    assert db.execute("SELECT COUNT(*) FROM alerts WHERE unit_id='2a'").fetchone()[0] == 1
    assert _msgs(db, role="tenant")

    orch.tenant_answer(db, case_id, "yes")
    assert _case(db, case_id)["status"] == "awaiting_landlord"
    appr = db.execute("SELECT * FROM approvals WHERE case_id=?", (case_id,)).fetchone()
    assert appr["amount_eur"] == 380 and appr["status"] == "pending"
    assert _msgs(db, role="owner")

    orch.landlord_decision(db, case_id, True)
    await orch.advance(db)
    jobs = db.execute("SELECT * FROM jobs WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
    assert [(j["craftsman_id"], j["status"]) for j in jobs] == [(1, "declined"), (2, "scheduled")]
    assert _case(db, case_id)["status"] == "scheduled"
    slot = datetime.fromisoformat(jobs[1]["scheduled_at"])
    assert slot.weekday() < 5 and 8 <= slot.hour < 12
    assert len(_msgs(db, role="tenant")) == 2
    assert _msgs(db, channel="voice")

    await orch.advance(db)
    assert clock.now() > slot
    assert _case(db, case_id)["status"] == "completed"
    inv = db.execute("SELECT * FROM documents WHERE case_id=? AND kind='invoice'", (case_id,)).fetchone()
    assert json.loads(inv["payload"])["amount_eur"] == 380
    assert len(_msgs(db, role="tenant")) == 3 and len(_msgs(db, role="owner")) == 2
    write = db.execute("SELECT payload FROM case_events WHERE case_id=? AND kind='centrix_write_result'",
                       (case_id,)).fetchone()
    result = json.loads(write["payload"])
    assert result["service_job"]["invoice"]["amount_eur"] == 380
    assert result["service_job"]["consent"]["data_shared"]

    kinds = {r["kind"] for r in db.execute("SELECT kind FROM alerts")}
    assert "short_cycling" in kinds
    assert db.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 1


async def test_reschedule_outside_availability_reoffers(db):
    case_id = await _to_scheduled(db)
    job = db.execute("SELECT * FROM jobs WHERE status='scheduled'").fetchone()
    r = orch.tenant_reschedule(db, case_id, "2026-11-24T18:00:00")
    assert r["ok"] is False and r["offered_windows"]
    assert db.execute("SELECT scheduled_at FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == job["scheduled_at"]
    assert _case(db, case_id)["status"] == "scheduled"
    r = orch.tenant_reschedule(db, case_id, "2026-11-24T09:00:00")
    assert r["ok"] is True
    assert db.execute("SELECT scheduled_at FROM jobs WHERE id=?", (job["id"],)).fetchone()[0] == "2026-11-24T09:00:00"


async def test_all_decline_needs_pm(db):
    case_id = await _to_landlord(db)
    db.execute("UPDATE craftsmen SET will_accept=0")
    db.commit()
    orch.landlord_decision(db, case_id, True)
    await orch.advance(db)
    assert _case(db, case_id)["status"] == "needs_pm"
    assert all(j["status"] == "declined" for j in db.execute("SELECT status FROM jobs"))


async def test_landlord_reject_on_hold(db):
    case_id = await _to_landlord(db)
    orch.landlord_decision(db, case_id, False)
    assert _case(db, case_id)["status"] == "on_hold"
    assert db.execute("SELECT status FROM approvals").fetchone()[0] == "rejected"
    await orch.advance(db)
    assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


async def test_tenant_no_closes(db):
    r = await orch.advance(db)
    orch.tenant_answer(db, r["case_id"], "no")
    assert _case(db, r["case_id"])["status"] == "closed_no_action"


async def test_reset_mid_flow(db):
    await _to_scheduled(db)
    clock.advance(datetime(2026, 12, 1) - clock.now())
    orch.reset(db)
    assert clock.now().isoformat() == "2026-11-20T08:00:00"
    for t in ("cases", "jobs", "alerts", "messages", "approvals", "case_events"):
        assert db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM units").fetchone()[0] == 6
    r = await orch.advance(db)
    assert _case(db, r["case_id"])["status"] == "awaiting_tenant"
