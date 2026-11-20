"""Partner connector: generic job intake + status updates for any centrix partner."""
import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from app import db
from app.clock import clock

router = APIRouter(prefix="/partner", tags=["partner"])

Status = Literal["accepted", "scheduled", "on_site", "completed", "needs_followup", "declined"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UnitRef(_Strict):
    address: str
    floor: int
    unit_id: str


class Window(_Strict):
    start: str
    end: str


class JobIn(_Strict):
    case_id: int
    partner_id: int
    job_type: str
    unit: UnitRef
    access_windows: list[Window]
    evidence_summary: str
    boiler_model: str
    consent_scope: list[str]


class Invoice(_Strict):
    amount_eur: float
    file_name: str


class StatusIn(_Strict):
    status: Status
    slot: str | None = None
    outcome: dict | None = None
    invoice: Invoice | None = None

    @model_validator(mode="after")
    def _slot_required(self):
        if self.status == "scheduled" and not self.slot:
            raise ValueError("status 'scheduled' requires slot")
        return self


# Allowed lifecycle; mirrors docs/connector-spec.md section 5.
TRANSITIONS: dict[str, set[str]] = {
    "offered": {"accepted", "declined"},
    "accepted": {"scheduled", "declined"},
    "scheduled": {"scheduled", "on_site"},
    "on_site": {"completed", "needs_followup"},
}


class ConflictError(ValueError):
    """Update is valid JSON but conflicts with the job's current state."""


def get_conn():
    """Overridable dependency (app.dependency_overrides) yielding a DB connection."""
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return clock.now().isoformat()


def get_job(conn: sqlite3.Connection, job_id: int) -> dict:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(job_id)
    job = dict(row)
    job["payload"] = json.loads(job["payload"] or "{}")
    return job


def create_job(conn: sqlite3.Connection, job: JobIn) -> dict:
    now = _now()
    cur = conn.execute(
        "INSERT INTO jobs (case_id, unit_id, craftsman_id, status, created_at, updated_at, payload)"
        " VALUES (?, ?, ?, 'offered', ?, ?, ?)",
        (job.case_id, job.unit.unit_id, job.partner_id, now, now, job.model_dump_json()),
    )
    conn.execute(
        "INSERT INTO case_events (case_id, at, actor, kind, payload) VALUES (?, ?, 'centrix', 'job_offered', ?)",
        (job.case_id, now, json.dumps({"job_id": cur.lastrowid, "partner_id": job.partner_id})),
    )
    conn.commit()
    return {"job_id": cur.lastrowid, "status": "offered"}


def update_status(conn: sqlite3.Connection, job_id: int, upd: StatusIn) -> dict:
    job = get_job(conn, job_id)
    if upd.status not in TRANSITIONS.get(job["status"], set()):
        raise ConflictError(f"illegal transition {job['status']} -> {upd.status}")
    if upd.status == "scheduled":
        slot = datetime.fromisoformat(upd.slot)
        windows = job["payload"].get("access_windows", [])
        if not any(datetime.fromisoformat(w["start"]) <= slot <= datetime.fromisoformat(w["end"])
                   for w in windows):
            raise ConflictError("slot outside tenant access_windows")
    now = _now()
    scheduled_at = upd.slot if upd.status == "scheduled" and upd.slot else job["scheduled_at"]
    conn.execute(
        "UPDATE jobs SET status = ?, scheduled_at = ?, updated_at = ? WHERE id = ?",
        (upd.status, scheduled_at, now, job_id),
    )
    if upd.status == "completed" and upd.invoice:
        conn.execute(
            "INSERT INTO documents (case_id, unit_id, kind, title, created_at, payload)"
            " VALUES (?, ?, 'invoice', ?, ?, ?)",
            (job["case_id"], job["unit_id"], upd.invoice.file_name, now, upd.invoice.model_dump_json()),
        )
    conn.execute(
        "INSERT INTO case_events (case_id, at, actor, kind, payload) VALUES (?, ?, ?, ?, ?)",
        (job["case_id"], now, f"partner:{job['craftsman_id']}", f"job_{upd.status}",
         json.dumps({"job_id": job_id, **upd.model_dump(exclude_none=True)})),
    )
    conn.commit()
    return get_job(conn, job_id)


def _or_404(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "job not found")
    except ConflictError as e:
        raise HTTPException(409, str(e))


@router.post("/jobs")
def post_job(job: JobIn, conn=Depends(get_conn)) -> dict:
    return create_job(conn, job)


@router.post("/jobs/{job_id}/status")
def post_status(job_id: int, upd: StatusIn, conn=Depends(get_conn)) -> dict:
    return _or_404(update_status, conn, job_id, upd)


@router.get("/jobs/{job_id}")
def read_job(job_id: int, conn=Depends(get_conn)) -> dict:
    return _or_404(get_job, conn, job_id)
