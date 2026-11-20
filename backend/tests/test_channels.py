import json
from datetime import datetime

import pytest

from app import seed
from app.channels import ElevenLabsVoice, MessageChannel, SimulatedVoice
from app.clock import clock

JOB = {
    "address": "Musterstraße 12, Aachen", "unit_id": "2a",
    "evidence": "heat use 40% below neighbours for 10 days", "boiler": "Viessmann Vitodens 200-W",
    "access_windows": [
        {"start": "2026-11-19T08:00:00", "end": "2026-11-19T12:00:00"},
        {"start": "2026-11-23T08:00:00", "end": "2026-11-23T12:00:00"},
    ],
}


async def _collect(gen):
    return [ev async for ev in gen]


@pytest.mark.asyncio
async def test_sim_voice_decline():
    evs = await _collect(SimulatedVoice().call({"name": "Krämer", "will_accept": 0}, JOB, "book"))
    lines = [e for e in evs if "speaker" in e]
    assert 4 <= len(lines) <= 6
    assert evs[-1] == {"result": {"accepted": False, "slot": None}}


@pytest.mark.asyncio
async def test_sim_voice_accept_slot_inside_window():
    evs = await _collect(SimulatedVoice().call({"name": "Wärmetechnik", "will_accept": 1}, JOB, "book"))
    res = evs[-1]["result"]
    assert res["accepted"] is True
    slot = datetime.fromisoformat(res["slot"])
    assert slot > clock.now()
    assert any(datetime.fromisoformat(w["start"]) <= slot < datetime.fromisoformat(w["end"])
               for w in JOB["access_windows"])
    assert all(e["speaker"] in ("agent", "partner") for e in evs[:-1])


@pytest.mark.asyncio
async def test_sim_voice_outcome():
    evs = await _collect(SimulatedVoice().call({"name": "W", "will_accept": 1}, JOB, "outcome"))
    assert evs[-1] == {"result": {"fixed": True, "fault_found": True, "invoice_eur": 380}}


@pytest.mark.asyncio
async def test_elevenlabs_stub():
    with pytest.raises(NotImplementedError):
        await _collect(ElevenLabsVoice().call({}, JOB, "book"))
    assert "confirm_slot" in ElevenLabsVoice.__doc__


def test_message_channel_persists(conn):
    seed.seed(conn)
    ch = MessageChannel(conn)
    mid = ch.send("tenant", "2a", "Hello", ["yes", "no"])
    row = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    tenant = conn.execute("SELECT id FROM people WHERE role='tenant' AND unit_id='2a'").fetchone()
    assert row["direction"] == "outbound" and row["channel"] == "portal"
    assert row["person_id"] == tenant["id"] and row["body"] == "Hello"
    assert json.loads(row["payload"])["actions"] == ["yes", "no"]
    assert row["created_at"] == clock.now().isoformat()
    oid = ch.send("owner", "2a", "Approve?", ["approve"])
    orow = conn.execute("SELECT * FROM messages WHERE id=?", (oid,)).fetchone()
    assert orow["channel"] == "email"
    assert orow["person_id"] == conn.execute("SELECT id FROM people WHERE role='owner'").fetchone()["id"]
