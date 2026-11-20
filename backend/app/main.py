"""FastAPI app: Guardian API, SSE stream, partner connector, centrix webhook, static page."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, connector, db, events, orchestrator
from app.centrix import MockCentrix
from app.clock import clock

STATIC_DIR = Path(__file__).resolve().parent / "static"
_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(config.DB_PATH)
    db.init_schema(conn)
    if conn.execute("SELECT COUNT(*) FROM buildings").fetchone()[0] == 0:
        orchestrator.reset(conn)
    app.state.conn = conn
    app.dependency_overrides[connector.get_conn] = lambda: conn
    yield
    app.dependency_overrides.pop(connector.get_conn, None)
    conn.close()


app = FastAPI(title="Heating & Energy Guardian", lifespan=lifespan)
app.include_router(connector.router)


def _conn(request: Request):
    return request.app.state.conn


def _rows(conn, sql: str, *args) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args)]


def _guard(fn, *args):
    try:
        return fn(*args)
    except KeyError:
        raise HTTPException(404, "case not found")
    except connector.ConflictError as e:
        raise HTTPException(409, str(e))


class AnswerIn(BaseModel):
    answer: Literal["yes", "no"]


class DecisionIn(BaseModel):
    approve: bool


class RescheduleIn(BaseModel):
    slot: str


class CentrixEventIn(BaseModel):
    event: str
    occurred_at: str
    unit_id: str | None = None
    contact_id: int | str | None = None
    data: dict[str, Any] = {}


@app.get("/api/state")
async def state(request: Request) -> dict:
    conn = _conn(request)
    risk = {r["unit_id"]: r["risk"] for r in conn.execute(
        "SELECT unit_id, MAX(risk) AS risk FROM alerts WHERE unit_id IS NOT NULL GROUP BY unit_id")}
    units = [{**u, "risk": risk.get(u["id"], 0.0)} for u in _rows(conn, "SELECT * FROM units ORDER BY id")]
    cases = _rows(conn, "SELECT * FROM cases ORDER BY id")
    for c in cases:
        c["events"] = _rows(conn, "SELECT * FROM case_events WHERE case_id=? ORDER BY id", c["id"])
    building = conn.execute("SELECT * FROM buildings LIMIT 1").fetchone()
    return {
        "building": dict(building) if building else None,
        "units": units,
        "alerts": _rows(conn, "SELECT * FROM alerts ORDER BY risk DESC"),
        "cases": cases,
        "approvals": _rows(conn, "SELECT * FROM approvals ORDER BY id"),
        "messages": _rows(conn, "SELECT * FROM messages ORDER BY id"),
        "jobs": _rows(conn, "SELECT * FROM jobs ORDER BY id"),
        "clock": clock.now().isoformat(),
    }


@app.post("/api/advance")
async def advance(request: Request) -> dict:
    async with _lock:
        return await orchestrator.advance(_conn(request))


@app.post("/api/tenant/{case_id}/answer")
async def tenant_answer(case_id: int, body: AnswerIn, request: Request) -> dict:
    return _guard(orchestrator.tenant_answer, _conn(request), case_id, body.answer)


@app.post("/api/landlord/{case_id}/decision")
async def landlord_decision(case_id: int, body: DecisionIn, request: Request) -> dict:
    return _guard(orchestrator.landlord_decision, _conn(request), case_id, body.approve)


@app.post("/api/tenant/{case_id}/reschedule")
async def tenant_reschedule(case_id: int, body: RescheduleIn, request: Request) -> dict:
    return _guard(orchestrator.tenant_reschedule, _conn(request), case_id, body.slot)


@app.post("/api/reset")
async def reset(request: Request) -> dict:
    async with _lock:
        orchestrator.reset(_conn(request))
    return {"ok": True, "clock": clock.now().isoformat()}


@app.get("/api/events")
async def sse() -> StreamingResponse:
    return StreamingResponse(events.subscribe(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/centrix/events", status_code=202)
async def centrix_event(body: CentrixEventIn, request: Request) -> dict:
    event_id = MockCentrix(_conn(request)).post_case_event(None, {
        "actor": "centrix", "kind": body.event, "at": body.occurred_at, "payload": body.model_dump()})
    events.publish({"type": "centrix_event", "event": body.event, "unit_id": body.unit_id})
    return {"accepted": True, "id": event_id}


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
