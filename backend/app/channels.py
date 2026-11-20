"""Outbound channels: voice calls to craftsmen and persisted messages to people."""
import asyncio
import json
import sqlite3
from datetime import datetime
from typing import AsyncIterator, Literal, Protocol

from app.clock import clock

Purpose = Literal["book", "outcome"]


class VoiceChannel(Protocol):
    def call(self, craftsman: dict, job: dict, purpose: Purpose) -> AsyncIterator[dict]: ...


def _first_slot(job: dict) -> str | None:
    now = clock.now()
    for w in job.get("access_windows", []):
        start = datetime.fromisoformat(w["start"])
        if start > now:
            return start.isoformat()
    return None


class SimulatedVoice:
    """Scripted call; outcome decided by craftsman["will_accept"]."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay

    async def call(self, craftsman: dict, job: dict, purpose: Purpose) -> AsyncIterator[dict]:
        address = job.get("address", "the property")
        if purpose == "outcome":
            lines = [
                ("agent", f"Hi, Guardian for reltix property management, following up on the job at {address}."),
                ("partner", "Yes, we were there. Thermostatic valve on the bedroom radiator was stuck."),
                ("agent", "Is the heating working again?"),
                ("partner", "Valve replaced, system bled, radiator is hot. Invoice is 380 euros."),
            ]
            result = {"fixed": True, "fault_found": True, "invoice_eur": 380}
        else:
            slot = _first_slot(job)
            accepted = bool(craftsman.get("will_accept")) and slot is not None
            lines = [
                ("agent", f"Hello {craftsman.get('name', '')}, this is Guardian calling on behalf of reltix property management."),
                ("agent", f"We have a heating fault at {address}: {job.get('evidence', 'reduced heat output')}. "
                          f"Boiler is a {job.get('boiler', 'gas condensing unit')}."),
                ("agent", f"Could you send a technician{' on ' + slot if slot else ''}?"),
            ]
            if accepted:
                lines += [("partner", "Yes, we can do that, I'll book it in."),
                          ("agent", "Great, confirmed. Thank you!")]
            else:
                lines += [("partner", "Sorry, we're fully booked this week."),
                          ("agent", "Understood, thanks anyway.")]
            result = {"accepted": accepted, "slot": slot if accepted else None}
        for speaker, text in lines:
            yield {"speaker": speaker, "text": text}
            await asyncio.sleep(self.delay)
        yield {"result": result}


class ElevenLabsVoice:
    """Real voice agent via ElevenLabs Conversational AI (not implemented).

    The agent would be configured with server tools `get_job_details`,
    `confirm_slot` and `report_outcome`, which call back into this service.
    """

    async def call(self, craftsman: dict, job: dict, purpose: Purpose) -> AsyncIterator[dict]:
        raise NotImplementedError("ElevenLabs voice not wired up; use SimulatedVoice")
        yield {}  # pragma: no cover - makes this an async generator


class MessageChannel:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def send(self, to_role: str, unit_id: str, text: str, actions: list[str],
             case_id: int | None = None) -> int:
        if to_role == "tenant":
            person = self.conn.execute(
                "SELECT id FROM people WHERE role='tenant' AND unit_id=?", (unit_id,)).fetchone()
            pref = self.conn.execute(
                "SELECT channel FROM tenant_prefs WHERE unit_id=?", (unit_id,)).fetchone()
            channel = (pref["channel"] if pref else None) or "portal"
        else:
            person = self.conn.execute(
                "SELECT p.id FROM people p JOIN units u ON u.building_id = p.building_id "
                "WHERE p.role=? AND u.id=?", (to_role, unit_id)).fetchone()
            channel = "email"
        cur = self.conn.execute(
            "INSERT INTO messages (case_id, unit_id, person_id, direction, channel, body, status, "
            "created_at, payload) VALUES (?,?,?,?,?,?,?,?,?)",
            (case_id, unit_id, person["id"] if person else None, "outbound", channel, text,
             "sent", clock.now().isoformat(), json.dumps({"actions": actions})),
        )
        self.conn.commit()
        return cur.lastrowid
