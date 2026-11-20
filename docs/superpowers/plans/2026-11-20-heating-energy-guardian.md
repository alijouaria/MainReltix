# Heating & Energy Guardian Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local demo where centrix (mocked) detects unit 2a heat drift, escalates via tenant check-in, gets landlord approval for €380, books a craftsman by (simulated) voice call through the partner connector, and closes the case on the unit with invoice — visible live in four role views.

**Architecture:** FastAPI backend with SQLite as the mocked centrix record, a virtual clock, pure-function detectors / ladder / matching, an orchestrator state machine that drives the case, and an LLM layer with canned fallbacks. Partner connector is a real HTTP router the simulated voice adapter calls. Guardian is a service that plugs into the existing centrix through a `CentrixClient` adapter (mocked, since centrix is not reachable); one plain HTML page stands in for the channels.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, pydantic v2, sqlite3 (stdlib), pandas, openai, python-dotenv, pytest, httpx.

**Spec:** `docs/superpowers/specs/2026-11-20-heating-energy-guardian-design.md`

## Global Constraints

- centrix is mocked in this app; no external reltix API. `main.py`, `coding_agent.py` untouched.
- Landlord approval threshold: `APPROVAL_THRESHOLD_EUR = 300` (config, per-owner override).
- Golden-path estimate: €380 = callout 65 + 3.5 h × 70 + parts 70.
- Demo must run with no network and no `OPENAI_API_KEY` (canned fallbacks).
- All time reads go through the virtual clock; never `datetime.now()` in app code.
- Partners receive only job data: address, access window, fault evidence summary, boiler model. Never consumption series.
- Seeded RNG (`seed=42`) so the demo is deterministic.
- Runs locally: one process on `:8000` serving API and the page. No frontend framework.

## Review Focus

1. Tenant reschedules to a slot outside craftsman availability → re-offer next valid slot, case not stuck. (Task 6 test)
2. Both shortlisted craftsmen decline → case goes to `needs_pm` status with message, no crash. (Task 6 test)
3. Landlord rejects approval → case `on_hold`, tenant informed, no call placed. (Task 6 test)
4. Reset pressed mid-flow → DB reseeded, clock reset, SSE clients get `reset` event. (Task 6 test)
5. No OpenAI key / API error → fallback text used, flow continues. (Task 4 test)

---

### Task 1: Backend scaffold, clock, DB, seed data

**Files:**
- Create: `backend/requirements.txt`, `backend/app/__init__.py`, `backend/app/config.py`, `backend/app/clock.py`, `backend/app/db.py`, `backend/app/seed.py`
- Test: `backend/tests/test_seed.py`, `backend/tests/conftest.py`

**Interfaces:**
- Produces:
  - `config.py`: `APPROVAL_THRESHOLD_EUR=300`, `DEMO_START="2026-11-20T08:00:00"`, `DB_PATH`, ladder thresholds (`CHECKIN_MIN_RISK=0.6`, `VISIT_MIN_CONFIDENCE=0.8`, `TENANT_YES_BOOST=0.15`), `SILENCE_ACCEPT_HOURS=24`.
  - `clock.py`: `class VirtualClock: now() -> datetime; advance(td: timedelta) -> datetime; reset() -> None`; module singleton `clock`.
  - `db.py`: `connect(path: str | None = None) -> sqlite3.Connection` (row_factory=Row); `init_schema(conn)`. Tables: `buildings, units, people(role: tenant|owner), tenant_prefs(unit_id, access_windows json, channel, monitoring_consent bool), craftsmen(id, name, trades json, distance_km, callout_eur, hourly_eur, rating, will_accept bool), heat_readings(unit_id, date, kwh), weather(date, temp_c, frost_forecast bool), boiler_metrics(building_id, date, burner_starts, pressure_bar, last_service), alerts, cases, case_events(case_id, at, actor, kind, payload json), jobs, approvals, messages`.
  - `seed.py`: `seed(conn, rng_seed=42) -> None` — building Musterstraße 12 Aachen (1972, gas central, class E), 6 units (1a,1b,2a,2b,3a,3b), owner Maria Becker, 6 tenants with prefs, 4 craftsmen (craftsman #1 `will_accept=False`), 365 days readings ending 2026-11-19; unit 2a last 10 days at ~60% of neighbour-normalised use; boiler burner_starts rising 30→120 over last 3 weeks, last_service 14 months ago; frost forecast 2026-11-25.

- [ ] **Step 1:** Write `test_seed.py`: `test_seed_creates_six_units`, `test_unit_2a_last10_ratio_below_0_7` (2a mean / neighbours mean over last 10 days < 0.7, earlier 60 days ratio between 0.9 and 1.1), `test_seed_deterministic` (two seeds → identical row sums), `test_clock_advance_and_reset`.
- [ ] **Step 2:** Run `cd backend && pytest tests/test_seed.py -v` → FAIL (import errors).
- [ ] **Step 3:** Implement files above. Readings: heat kWh = base_by_size × max(0, 18 − temp_c) × noise(1±0.08).
- [ ] **Step 4:** Run tests → PASS.
- [ ] **Step 5:** Commit `feat: backend scaffold, virtual clock, mocked centrix db and seed`.

### Task 2: Detectors

**Files:**
- Create: `backend/app/detectors.py`
- Test: `backend/tests/test_detectors.py`

**Interfaces:**
- Consumes: `db.connect`, seed data, `clock.now()`.
- Produces: `@dataclass Alert(unit_id: str | None, building_id: str, signal: str, risk: float, evidence: dict, likely_causes: list[dict], urgency: str)`; `detect_neighbour_drift(conn, now) -> list[Alert]`; `detect_short_cycling(conn, now) -> list[Alert]`; `detect_service_due(conn, now) -> list[Alert]`; `run_all(conn, now) -> list[Alert]`.

- [ ] **Step 1:** Tests: `test_drift_flags_only_2a` (one alert, unit_id "2a", signal "neighbour_drift", 0.65 ≤ risk ≤ 0.8, likely_causes first cause "seized_thermostatic_valve" p=0.5, then "air_in_system" 0.3, "low_use_or_vacancy" 0.2; evidence has `ratio_vs_neighbours` ≈ 0.6 ±0.1 and `frost_in_days` == 5); `test_short_cycling_flags_building`; `test_service_due_with_frost`; `test_no_drift_on_flat_data` (seed then overwrite 2a readings with neighbour mean → no alert).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Drift: per-unit kWh/m² per degree-day, last 10 days vs neighbour median; flag if ratio < 0.75 or > 1.3; risk = clamp(0.4 + (0.75 − ratio) × 1.5 + 0.05 if frost ≤ 7 days, 0, 1). Causes list fixed for low-ratio drift.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: drift, short-cycling and service-due detectors`.

### Task 3: Ladder, matching, cost estimate

**Files:**
- Create: `backend/app/ladder.py`, `backend/app/matching.py`
- Test: `backend/tests/test_ladder.py`, `backend/tests/test_matching.py`

**Interfaces:**
- Produces:
  - `ladder.py`: `Step = Literal["recheck","tenant_checkin","remote_boiler","visit","close_no_action"]`; `next_step(risk: float, answers: dict, building_signals_normal: bool) -> Step`. Rules: risk < 0.6 → recheck; no tenant answer → tenant_checkin; tenant "no" → close_no_action; confidence = risk + 0.15 if tenant "yes"; if confidence ≥ 0.8 and building_signals_normal → visit (skip remote_boiler); else remote_boiler.
  - `matching.py`: `estimate(job_type: str, craftsman: Row) -> dict` returning `{callout, labour_hours, labour, parts, total}`; job type `"valve_replacement_balancing"` = 3.5 h, parts 70. `rank_craftsmen(conn, trade: str, job_type: str) -> list[dict]` sorted by score = 0.4·price_norm + 0.3·distance_norm + 0.3·rating_norm, each item with `reasons: list[str]`.

- [ ] **Step 1:** Tests: `test_low_risk_rechecks`, `test_first_step_is_checkin` (risk 0.72, no answers), `test_yes_skips_boiler_to_visit` (0.72, yes, normal=True → "visit"), `test_yes_but_building_abnormal_goes_remote_boiler`, `test_no_closes`; `test_estimate_golden_is_380` (callout 65, hourly 70 craftsman), `test_rank_order_and_reasons`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: escalation ladder and craftsman matching`.

### Task 4: LLM agent with fallbacks + channels

**Files:**
- Create: `backend/app/agent.py`, `backend/app/fallbacks.py`, `backend/app/channels.py`
- Test: `backend/tests/test_agent.py`, `backend/tests/test_channels.py`

**Interfaces:**
- Produces:
  - `agent.py`: `generate(kind: str, context: dict) -> str`. Kinds: `alert_explanation`, `tenant_checkin`, `landlord_approval`, `tenant_notice`, `call_script`, `case_summary`. Uses OpenAI (`OPENAI_MODEL` env, default `gpt-4o-mini`), 8 s timeout; on missing key or any exception returns `fallbacks.TEXT[kind].format(**context)`.
  - `channels.py`: `class VoiceChannel(Protocol): async def call(self, craftsman: dict, job: dict, purpose: Literal["book","outcome"]) -> AsyncIterator[dict]` yielding `{"speaker": "agent"|"partner", "text": str}` then final `{"result": {...}}`. `SimulatedVoice` uses `craftsman["will_accept"]`; booking result `{"accepted": bool, "slot": iso | None}`; outcome result `{"fixed": True, "fault_found": True, "invoice_eur": 380}`. `ElevenLabsVoice` stub raising `NotImplementedError` with docstring naming server tools `get_job_details`, `confirm_slot`, `report_outcome`. `MessageChannel.send(to_role, unit_id, text, actions: list[str]) -> int` writes `messages` row.

- [ ] **Step 1:** Tests: `test_generate_uses_fallback_without_key` (monkeypatch env unset → equals formatted fallback), `test_generate_fallback_on_exception`; `test_sim_voice_decline` / `test_sim_voice_accept_slot_inside_window`; `test_message_channel_persists`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Fallback copy in English, short, e.g. tenant_checkin: "Hi {tenant_name}, our system noticed your flat may be heating less than usual. Is the bedroom radiator cold even on setting 5?"
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: llm agent with fallbacks, simulated voice and message channels`.

### Task 5: Partner connector router

**Files:**
- Create: `backend/app/connector.py`, `docs/connector-spec.md`
- Test: `backend/tests/test_connector.py`

**Interfaces:**
- Produces: FastAPI `router` with
  - `POST /partner/jobs` body `JobIn{case_id, partner_id, job_type, unit: {address, floor, unit_id}, access_windows: list[{start,end}], evidence_summary: str, boiler_model: str, consent_scope: list[str]}` → `{job_id, status:"offered"}`. Rejects extra fields like `heat_readings` (pydantic `extra="forbid"`) — enforces data minimisation.
  - `POST /partner/jobs/{job_id}/status` body `{status: Literal["accepted","scheduled","on_site","completed","needs_followup","declined"], slot?: iso, outcome?: dict, invoice?: {amount_eur, file_name}}` → updated job; appends `case_events`.
  - `GET /partner/jobs/{job_id}`.
- [ ] **Step 1:** Tests: create job → offered; status transitions append events; `test_rejects_consumption_data` (422); invalid status → 422.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement router + write `docs/connector-spec.md` (profile, eligibility, availability, job intake, status updates, channel adapters normalise to same JobIn; consent scope) with the exact schemas above.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat: partner connector api and spec`.

### Task 6: Orchestrator, API, SSE

**Files:**
- Create: `backend/app/orchestrator.py`, `backend/app/events.py`, `backend/app/main.py`
- Test: `backend/tests/test_orchestrator.py`, `backend/tests/test_api.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - Case statuses: `detected → awaiting_tenant → awaiting_landlord → booking → scheduled → in_progress → completed`; side states `on_hold`, `needs_pm`, `closed_no_action`.
  - `orchestrator.py`: `async advance(conn) -> dict` (performs the next automatic step for the open case, or advances clock to next scheduled event); `tenant_answer(conn, case_id, answer: Literal["yes","no"])`; `landlord_decision(conn, case_id, approve: bool)`; `tenant_reschedule(conn, case_id, slot_iso)`; `reset(conn)`.
  - `centrix.py` (integration boundary — real centrix is not reachable, so this is the adapter Guardian talks to): `class CentrixClient(Protocol)`: `get_unit_snapshot(unit_id) -> dict` (PDF property-snapshot shape: unit_id, property, unit{meters}, owner, tenant, contracts), `write_result(unit_id, result: dict) -> None` (PDF write-back shape, here `{"unit_id","contact_id","service_job":{partner, category:"heating_repair", status, appointment, invoice, cost_eur, consent{data_shared, given_at, channel}}}`), `post_case_event(case_id, event: dict)`. `MockCentrix(conn)` implements it over the SQLite store. Inbound webhook `POST /centrix/events` accepts PDF trigger-event shape (`event`, `occurred_at`, `unit_id`, `contact_id`, `data`); Guardian emits its own detections as `event: "heating.anomaly_detected"`. Orchestrator reads/writes centrix ONLY through `CentrixClient`.
  - `events.py`: `publish(event: dict)`, `subscribe() -> AsyncIterator[str]` (in-process asyncio queues).
  - `main.py` routes: `GET /api/state` (building, units with risk, alerts, cases+events, messages, jobs, clock), `POST /api/advance`, `POST /api/tenant/{case_id}/answer`, `POST /api/landlord/{case_id}/decision`, `POST /api/tenant/{case_id}/reschedule`, `POST /api/reset`, `GET /api/events` (SSE); includes connector router and centrix webhook; serves static page.
- [ ] **Step 1:** Tests: `test_golden_path` — reset; advance → alert + case for 2a in `awaiting_tenant`, tenant message exists; answer yes → `awaiting_landlord`, approval amount 380; approve → advance → job created via connector, craftsman #1 declined, #2 accepted, status `scheduled`, slot inside tenant window, tenant notice message; advance (clock past slot) → outcome call, invoice 380 linked, status `completed`, summary messages to tenant and owner; second alert (short_cycling) exists but no case progressed. Review Focus tests: `test_reschedule_outside_availability_reoffers`, `test_all_decline_needs_pm`, `test_landlord_reject_on_hold`, `test_reset_mid_flow`. API smoke `test_state_endpoint`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Voice adapter places jobs by calling connector functions directly (same code path as HTTP). Every state change publishes SSE `{type, case_id, status}`.
- [ ] **Step 4:** Run `pytest -v` (all) → PASS.
- [ ] **Step 5:** Commit `feat: orchestrator state machine, api and sse`.

### Task 7: Simple demo web page (stand-in channels)

We are NOT building an app: centrix already exists. Guardian is a service that plugs into it. The page is only a lightweight stand-in for the channels (tenant portal message, landlord one-click link, partner call) plus a view of what Guardian writes back to centrix.

**Files:**
- Create: `backend/app/static/index.html` (single file, plain HTML + vanilla JS + inline CSS, no build step); served by FastAPI at `/`.
- Test: `backend/tests/test_api.py::test_index_served` (GET `/` → 200, contains "Guardian"); manual checklist below.

**Interfaces:**
- Consumes: Task 6 routes; fetches `/api/state`, re-fetches on every SSE event from `/api/events`.
- [ ] **Step 1:** Mount static dir in `main.py`; write `test_index_served`; run → FAIL then PASS after page exists.
- [ ] **Step 2:** One page, four stacked panels:
  - centrix write-back: unit 2a record with alert (risk, evidence numbers, likely causes %), case timeline, final result payload JSON; Advance + Reset buttons; demo clock.
  - Tenant message (2a): check-in Yes/No, appointment notice with Reschedule.
  - Landlord message: estimate breakdown, Approve/Reject.
  - Partner call: transcript lines, job card (connector fields only).
- [ ] **Step 3:** Verify: `uvicorn app.main:app --port 8000`, open `http://localhost:8000/`, click golden path: alert → Yes → Approve → Advance → decline then accept → notice → Advance → completed with invoice. No console errors.
- [ ] **Step 4:** Commit `feat: simple demo page for channels and centrix write-back`.

### Task 8: Run scripts and README

**Files:**
- Modify: `README.md`
- Create: `.env.example` (`OPENAI_API_KEY=`, `OPENAI_MODEL=gpt-4o-mini`)
- [ ] **Step 1:** README: what it is, assumptions (realism table summary from spec), run commands, demo script (click order), ElevenLabs plug-in point (`channels.ElevenLabsVoice`).
- [ ] **Step 2:** Verify fresh run from README steps works with no key.
- [ ] **Step 3:** Commit `docs: readme, demo script, env example`.
