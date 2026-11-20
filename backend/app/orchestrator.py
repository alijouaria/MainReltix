"""Case state machine: detected -> awaiting_tenant -> awaiting_landlord -> booking
-> scheduled -> in_progress -> completed; side states on_hold, needs_pm, closed_no_action.
Talks to centrix only through CentrixClient; partners via connector functions."""
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Literal

from app import agent, config, connector, db, detectors, events, ladder, matching
from app.centrix import CentrixClient, MockCentrix
from app.channels import MessageChannel, SimulatedVoice
from app.clock import clock
from app.seed import seed

JOB_TYPE = "valve_replacement_balancing"
OPEN = ("detected", "awaiting_tenant", "awaiting_landlord", "booking", "scheduled", "in_progress")
CONSENT_SCOPE = ["unit_access", "contact_tenant"]
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
voice = SimulatedVoice()


def _centrix(conn) -> CentrixClient:
    return MockCentrix(conn)


def _now() -> str:
    return clock.now().isoformat()


def _case(conn, case_id: int) -> dict:
    row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if row is None:
        raise KeyError(case_id)
    c = dict(row)
    c["payload"] = json.loads(c["payload"] or "{}")
    return c


def _event(conn, case_id: int | None, kind: str, payload: dict, actor: str = "guardian") -> None:
    _centrix(conn).post_case_event(case_id, {"actor": actor, "kind": kind, "payload": payload})


def _set(conn, case_id: int, status: str, rung: str | None = None, **payload) -> None:
    c = _case(conn, case_id)
    c["payload"].update(payload)
    conn.execute("UPDATE cases SET status=?, rung=COALESCE(?, rung), updated_at=?, payload=? WHERE id=?",
                 (status, rung, _now(), json.dumps(c["payload"], default=str), case_id))
    conn.commit()
    _event(conn, case_id, "status", {"status": status, "rung": rung})
    events.publish({"type": "case_status", "case_id": case_id, "status": status})


def _require(case: dict, status: str) -> None:
    if case["status"] != status:
        raise connector.ConflictError(f"case is {case['status']}, expected {status}")


def _open_case(conn) -> dict | None:
    row = conn.execute(f"SELECT id FROM cases WHERE status IN ({','.join('?' * len(OPEN))}) "
                       "ORDER BY id LIMIT 1", OPEN).fetchone()
    return _case(conn, row["id"]) if row else None


def _ctx(conn, case: dict) -> dict:
    snap = _centrix(conn).get_unit_snapshot(case["unit_id"])
    ev = case["payload"].get("evidence", {})
    evidence = (f"Heat use is {round((1 - ev.get('ratio_vs_neighbours', 1)) * 100)}% below neighbours "
                f"over {ev.get('window_days', 10)} days; frost expected in {ev.get('frost_in_days')} days.")
    hs = snap["property"]["heating_system"] or {}
    return {"snap": snap, "unit_id": case["unit_id"], "evidence": evidence,
            "tenant_name": (snap["tenant"] or {}).get("name", "tenant"),
            "owner_name": (snap["owner"] or {}).get("name", "owner"),
            "address": f"{snap['property']['address']}, {snap['property']['city']}, unit {case['unit_id']}",
            "boiler": f"{hs.get('make', '')} {hs.get('model', '')}".strip()}


def access_windows(prefs: list[dict], days: int = 7) -> list[dict]:
    """Concrete ISO windows for the next `days` days from {"days":"mon-fri","from","to"} prefs."""
    out = []
    now = clock.now()
    for i in range(days + 1):
        d = now.date() + timedelta(days=i)
        for p in prefs:
            lo, _, hi = p.get("days", "mon-sun").partition("-")
            if DAY_NAMES.index(lo) <= d.weekday() <= DAY_NAMES.index(hi or lo):
                start = datetime.fromisoformat(f"{d.isoformat()}T{p['from']}:00")
                end = datetime.fromisoformat(f"{d.isoformat()}T{p['to']}:00")
                if end > now:
                    out.append({"start": start.isoformat(), "end": end.isoformat()})
    return out


def _detect(conn) -> dict:
    alerts = detectors.run_all(conn, clock.now())
    best = None
    for a in alerts:
        cur = conn.execute(
            "INSERT INTO alerts (unit_id, building_id, kind, status, risk, created_at, payload) "
            "VALUES (?,?,?,?,?,?,?)",
            (a.unit_id, a.building_id, a.signal, "open", a.risk, _now(), json.dumps(asdict(a))))
        _event(conn, None, "heating.anomaly_detected",
               {"event": "heating.anomaly_detected", "occurred_at": _now(), "unit_id": a.unit_id,
                "data": {"alert_id": cur.lastrowid, **asdict(a)}})
        if a.unit_id and (best is None or a.risk > best[1].risk):
            best = (cur.lastrowid, a)
    conn.commit()
    events.publish({"type": "alerts", "count": len(alerts)})
    if best is None:
        return {"step": "detect", "case_id": None}
    alert_id, a = best
    cur = conn.execute(
        "INSERT INTO cases (unit_id, building_id, alert_id, status, rung, confidence, created_at, "
        "updated_at, payload) VALUES (?,?,?,?,?,?,?,?,?)",
        (a.unit_id, a.building_id, alert_id, "detected", None, a.risk, _now(), _now(),
         json.dumps({"evidence": a.evidence, "likely_causes": a.likely_causes, "answers": {}})))
    conn.commit()
    case_id = cur.lastrowid
    ctx = _ctx(conn, _case(conn, case_id))
    _event(conn, case_id, "alert_explanation", {"text": agent.generate("alert_explanation", ctx)})
    step = ladder.next_step(a.risk, {}, building_signals_normal=True)
    if step == "tenant_checkin":
        MessageChannel(conn).send("tenant", a.unit_id, agent.generate("tenant_checkin", ctx),
                                  ["yes", "no"], case_id)
        _set(conn, case_id, "awaiting_tenant", step)
    else:
        _set(conn, case_id, "closed_no_action" if step == "close_no_action" else "detected", step)
    return {"step": "detect", "case_id": case_id}


def tenant_answer(conn, case_id: int, answer: Literal["yes", "no"]) -> dict:
    case = _case(conn, case_id)
    _require(case, "awaiting_tenant")
    answers = {**case["payload"].get("answers", {}), "tenant": answer}
    _event(conn, case_id, "tenant_answer", {"answer": answer}, actor="tenant")
    # Building-level signals are separate alerts; unit drift cases treat them as normal.
    step = ladder.next_step(case["confidence"], answers, building_signals_normal=True)
    if step != "visit":
        _set(conn, case_id, "closed_no_action" if step == "close_no_action" else "on_hold", step,
             answers=answers)
        return _case(conn, case_id)
    ranked = matching.rank_craftsmen(conn, "heating", JOB_TYPE)
    est = ranked[0]["estimate"]
    conn.execute("UPDATE cases SET confidence=? WHERE id=?",
                 (min(1.0, case["confidence"] + config.TENANT_YES_BOOST), case_id))
    if est["total"] > config.APPROVAL_THRESHOLD_EUR:
        conn.execute("INSERT INTO approvals (case_id, unit_id, amount_eur, status, requested_at, payload) "
                     "VALUES (?,?,?,?,?,?)",
                     (case_id, case["unit_id"], est["total"], "pending", _now(),
                      json.dumps({"estimate": est, "craftsman": ranked[0]["name"]})))
        ctx = {**_ctx(conn, case), "craftsman_name": ranked[0]["name"], "amount_eur": f"{est['total']:.0f}"}
        MessageChannel(conn).send("owner", case["unit_id"], agent.generate("landlord_approval", ctx),
                                  ["approve", "reject"], case_id)
        _set(conn, case_id, "awaiting_landlord", step, answers=answers, shortlist=ranked, estimate=est)
    else:
        _set(conn, case_id, "booking", step, answers=answers, shortlist=ranked, estimate=est)
    return _case(conn, case_id)


def landlord_decision(conn, case_id: int, approve: bool) -> dict:
    _require(_case(conn, case_id), "awaiting_landlord")
    conn.execute("UPDATE approvals SET status=?, decided_at=? WHERE case_id=? AND status='pending'",
                 ("approved" if approve else "rejected", _now(), case_id))
    conn.commit()
    _event(conn, case_id, "landlord_decision", {"approve": approve}, actor="owner")
    _set(conn, case_id, "booking" if approve else "on_hold")
    return _case(conn, case_id)


async def _call(conn, case_id: int, craftsman: dict, job: dict, purpose: str) -> dict:
    transcript, result = [], {}
    async for line in voice.call(craftsman, job, purpose):
        if "result" in line:
            result = line["result"]
            continue
        transcript.append(line)
        events.publish({"type": "call_line", "case_id": case_id, "craftsman_id": craftsman["id"],
                        "purpose": purpose, **line})
    conn.execute(
        "INSERT INTO messages (case_id, unit_id, person_id, direction, channel, body, status, created_at, "
        "payload) VALUES (?,?,?,?,?,?,?,?,?)",
        (case_id, job["unit_id"], None, "outbound", "voice", f"{purpose} call to {craftsman['name']}",
         "completed", _now(), json.dumps({"craftsman_id": craftsman["id"], "purpose": purpose,
                                          "transcript": transcript, "result": result})))
    conn.commit()
    return result


async def _book(conn, case: dict) -> dict:
    ctx = _ctx(conn, case)
    snap = ctx["snap"]
    windows = access_windows(snap["tenant"]["access_windows"])
    job = {"unit_id": case["unit_id"], "access_windows": windows, "address": ctx["address"],
           "evidence": ctx["evidence"], "boiler": ctx["boiler"]}
    for c in matching.rank_craftsmen(conn, "heating", JOB_TYPE):  # fresh availability
        job_in = connector.JobIn(
            case_id=case["id"], partner_id=c["id"], job_type=JOB_TYPE,
            unit={"address": ctx["address"], "floor": snap["unit"]["floor"], "unit_id": case["unit_id"]},
            access_windows=windows, evidence_summary=ctx["evidence"], boiler_model=ctx["boiler"],
            consent_scope=CONSENT_SCOPE)
        job_id = connector.create_job(conn, job_in)["job_id"]
        res = await _call(conn, case["id"], c, job, "book")
        if not res.get("accepted"):
            connector.update_status(conn, job_id, connector.StatusIn(status="declined"))
            continue
        connector.update_status(conn, job_id, connector.StatusIn(status="accepted"))
        connector.update_status(conn, job_id, connector.StatusIn(status="scheduled", slot=res["slot"]))
        notice = {**ctx, "craftsman_name": c["name"], "slot": res["slot"]}
        MessageChannel(conn).send("tenant", case["unit_id"], agent.generate("tenant_notice", notice),
                                  ["reschedule"], case["id"])
        deadline = (clock.now() + timedelta(hours=config.SILENCE_ACCEPT_HOURS)).isoformat()
        _set(conn, case["id"], "scheduled", job_id=job_id, craftsman=c, slot=res["slot"],
             silence_accept_at=deadline, data_shared=job_in.model_dump())
        return {"step": "book", "case_id": case["id"], "job_id": job_id}
    _set(conn, case["id"], "needs_pm")
    return {"step": "book", "case_id": case["id"], "job_id": None}


async def _outcome(conn, case: dict) -> dict:
    p = case["payload"]
    job_id, c = p["job_id"], p["craftsman"]
    slot = datetime.fromisoformat(connector.get_job(conn, job_id)["scheduled_at"])
    if clock.now() < slot + timedelta(hours=2):
        clock.advance(slot + timedelta(hours=2) - clock.now())
    if case["status"] == "scheduled":
        connector.update_status(conn, job_id, connector.StatusIn(status="on_site"))
        _set(conn, case["id"], "in_progress")
    ctx = _ctx(conn, case)
    res = await _call(conn, case["id"], c, {"unit_id": case["unit_id"], "address": ctx["address"]}, "outcome")
    fixed = bool(res.get("fixed"))
    invoice = {"amount_eur": float(res.get("invoice_eur", 0)), "file_name": f"invoice-job-{job_id}.pdf"}
    connector.update_status(conn, job_id, connector.StatusIn(
        status="completed" if fixed else "needs_followup", outcome=res, invoice=invoice if fixed else None))
    _event(conn, case["id"], "ladder_feedback", {"fault_found": res.get("fault_found")})
    tenant = ctx["snap"]["tenant"] or {}
    _centrix(conn).write_result(case["unit_id"], {
        "unit_id": case["unit_id"], "contact_id": tenant.get("id"),
        "service_job": {"reference": case["id"], "partner": c["name"], "category": "heating_repair",
                        "status": "completed" if fixed else "needs_followup",
                        "appointment": slot.isoformat(), "invoice": invoice,
                        "cost_eur": invoice["amount_eur"],
                        "consent": {"data_shared": p.get("data_shared", {}), "given_at": case["updated_at"],
                                    "channel": tenant.get("channel", "portal")}}})
    status = "completed" if fixed else "needs_pm"
    summary = agent.generate("case_summary", {**ctx, "status": status})
    for role in ("tenant", "owner"):
        MessageChannel(conn).send(role, case["unit_id"], summary, [], case["id"])
    conn.execute("UPDATE alerts SET status='resolved' WHERE id=?", (case["alert_id"],))
    _set(conn, case["id"], status, outcome=res)
    return {"step": "outcome", "case_id": case["id"]}


async def advance(conn) -> dict:
    """Run the next automatic step, or report what the open case is waiting on."""
    if conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0:
        return _detect(conn)
    case = _open_case(conn)
    if case is None:
        return {"step": "idle", "case_id": None}
    if case["status"] == "booking":
        return await _book(conn, case)
    if case["status"] in ("scheduled", "in_progress"):
        return await _outcome(conn, case)
    return {"step": "waiting", "case_id": case["id"], "status": case["status"]}


def tenant_reschedule(conn, case_id: int, slot_iso: str) -> dict:
    case = _case(conn, case_id)
    _require(case, "scheduled")
    job_id = case["payload"]["job_id"]
    try:
        connector.update_status(conn, job_id, connector.StatusIn(status="scheduled", slot=slot_iso))
    except (connector.ConflictError, ValueError):
        windows = connector.get_job(conn, job_id)["payload"]["access_windows"]
        MessageChannel(conn).send("tenant", case["unit_id"],
                                  "That time is outside your access windows. Please pick one of: "
                                  + ", ".join(w["start"] for w in windows), ["reschedule"], case_id)
        _event(conn, case_id, "reschedule_reoffered", {"requested": slot_iso}, actor="tenant")
        events.publish({"type": "reschedule_reoffered", "case_id": case_id, "status": case["status"]})
        return {"ok": False, "offered_windows": windows}
    _set(conn, case_id, "scheduled", slot=slot_iso)
    return {"ok": True, "slot": slot_iso}


def reset(conn) -> None:
    """Drop and recreate every table, reseed, rewind the clock (shared conn stays open)."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        conn.execute(f'DROP TABLE IF EXISTS "{t}"')
    conn.commit()
    db.init_schema(conn)
    seed(conn)
    conn.commit()
    clock.reset()
    events.publish({"type": "reset"})
