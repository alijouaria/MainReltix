# Heating & Energy Guardian — Design

Date: 2026-11-20 · reltix challenge "Plug a Partner into centrix" (24h)

## Intent

An AI agent inside centrix that watches every unit's heating and energy data, **catches problems before they become problems**, decides the cheapest sensible response, books the right craftsman by voice call, and keeps tenant and landlord informed until the job is done and the invoice is linked to the unit. Nobody fills a form; the tenant never has to report anything.

Why this partner and moment (rubric: partner choice): heating faults are frequent, expensive, legally urgent in winter (landlord duty to repair), and today take "a dozen calls and emails". Predictive catch in November, before the first frost, is the strongest moment.

## Goals

1. Demo end to end: centrix event (anomaly) → AI triage → resident reached → craftsman receives job via agent call → repair result + invoice appear on the unit.
2. Proactive detection from realistic data sources (below), shown as a risk score with evidence, not a magic diagnosis.
3. Minimal effort for all three parties: tenant ≤ 1 tap, landlord ≤ 1 click, craftsman one phone call.
4. Generic partner connector spec so the next partner (electrician, plumber, energy supplier) plugs in the same way.

## Non-goals (this 24h)

- Real ElevenLabs voice (later; interface prepared).
- Tuned false-positive optimisation (design hook only, see Escalation ladder).
- PM override / liability workflow, consent ledger for partner handoff (explicitly deferred).
- Real integrations with ista/Techem/Viessmann; all telemetry is synthetic.

## Anti-goals

- AI that books a €90+ callout on every blip (false-positive spam). The ladder exists to prevent this.
- Presenting telemetry as universal: units without it must still be covered by calendar/age rules.
- Sending consumption history to partners. Partners get only job-relevant data.

## Realism assumptions (tell judges)

Data sources a German property manager realistically has:

| Source | Signal | Coverage |
|---|---|---|
| Remote-read heat cost allocators / heat meters (ista, Techem) — HKVO requires remote-readable devices + monthly consumption info | per-unit heat use (daily) | high |
| Smart meter gateway (iMSys, MsbG rollout) | electricity 15-min load | partial |
| Connected boiler cloud (Viessmann ViCare, Vaillant) | fault codes, burner starts, pressure, flow temp | newer boilers |
| centrix record | boiler install year, service history, past cases, energy certificate | full |
| Legal calendars | annual service, chimney sweep (KÜO), §72 GEG 30-year boiler rule | full |
| Weather (DWD) | outdoor temp → degree-day normalisation, frost forecast | full |

Legal claims to verify before slides: HKVO dates, iMSys scope, §555a BGB notice, BetrKV cost split.

Access & consent: at move-in a lease annex captures monitoring consent, preferred access windows, preferred channel, optional key-holder/caretaker access. Per job the AI books inside the preferred window and notifies the tenant ("Tue 9–11, tap to change"); silence = accepted (§555a notice satisfied). Emergencies (gas smell, burst pipe) bypass.

Costs: repairs paid by landlord → landlord approval above threshold (default €300, configurable per owner). Routine maintenance is operating cost (BetrKV §2).

## Detection signals (the core)

1. Heat use vs degree days up > X% year-over-year → efficiency loss / hydraulic issue.
2. Unit drift vs neighbours in same building → stuck valve, air, vacancy frost risk.
3. Burner short-cycling (starts/day rising sharply) → ignition/sensor failure soon.
4. Pressure slowly falling (trend toward < 1.0 bar) → leak / expansion vessel.
5. Electricity night baseload jump → faulty heating element/appliance.
6. Calendar/age rules: service overdue + frost forecast; boiler ≥ 30 years → replacement flag to landlord.

Each detector outputs: `{unit_id, signal, risk_score 0–1, evidence (series + numbers), likely_causes[{cause, p}], urgency}`.

## Escalation ladder (false-positive control — design now, tune later)

Cheapest step first; each step gated by confidence:
1. Remote data recheck (wait for next readings / cross-check sources)
2. Tenant check-in message ("Is the living-room radiator cold?") — also the direct-resident channel
3. Remote boiler diagnosis (read fault code via boiler cloud)
4. Craftsman visit

Thresholds are config values. Outcome feedback (craftsman: "fault found y/n") is stored per job so thresholds can be tuned later. Demo shows the ladder progressing; tuning not built.

## Flow after a visit is warranted

1. Shortlist craftsmen from framework partners (Rahmenverträge: fixed callout + hourly rates) by trade match, distance, price, rating, availability → ranked list with reasons.
2. Cost estimate from framework rates + likely cause. Above threshold → landlord one-click approval (portal/email link). Below → proceed.
3. Voice agent calls craftsman #1 with full context (address, access, fault evidence, boiler model, slot windows). Decline/no answer → #2. Confirmation → SMS/email summary as written record.
4. Slot chosen inside tenant's preferred window → tenant notice with one-tap reschedule.
5. After slot, voice agent calls craftsman back for outcome (fixed? fault found? follow-up?) ; invoice arrives by email reply (simulated upload).
6. Result written to unit in centrix: case, appointment, outcome, invoice, cost. Tenant + landlord get summary.

## Architecture (approach A, chosen)

- **Backend:** Python, FastAPI, SQLite. Modules: `data` (synthetic generator + seed), `detectors`, `ladder`, `matching` (craftsman ranking), `agent` (OpenAI: triage explanation, message drafting, call script), `channels` (`VoiceChannel` interface: `SimulatedVoice` now, `ElevenLabsVoice` later via ElevenLabs server tools/webhooks; `MessageChannel` for tenant/landlord), `connector` (partner API per spec), event log / case timeline.
- **Frontend:** React + Vite, one app, four role views:
  - centrix PM dashboard: building map of units with risk, alert feed, case timeline, unit record.
  - Tenant portal: check-in question, appointment notice, reschedule, status.
  - Landlord: approval card with evidence + estimate, status.
  - Partner: live call transcript (simulated streaming), job card.
- **Demo control:** "advance time"/"replay scenario" button so the full flow runs in ~3 minutes live.

## Demo data

One building (Musterstraße 12, Aachen, 1972, gas central heating, class E), ~6 units, 12 months synthetic daily data. Planted anomalies: short-cycling (central boiler → affects building), neighbour drift in unit 2a, pressure decline. Plus calendar: service overdue + frost forecast. Tenants have move-in preferences stored. 4–5 craftsmen with framework rates.

## Partner connector spec (deliverable)

Generic, partner-type agnostic. A partner provides:
- **Profile:** services/trades, service area, framework pricing, response SLAs.
- **Eligibility:** which job types/heating systems/brands they handle.
- **Availability:** slots (API) or obtained by voice agent.
- **Job intake:** `POST /jobs` (job type, unit context subset, evidence, access windows, consent scope) — or voice/email adapter producing the same object.
- **Status updates:** `accepted | scheduled | on_site | completed | needs_followup | declined` + outcome + invoice.
Channel adapters (API, voice, email) normalise to the same job object, so a voice-only craftsman and an API partner look identical to centrix. Written as `docs/connector-spec.md` + OpenAPI.

## Golden path (user's choice: unit 2a drift)

1. Detector: unit 2a heat use drops ~40% vs neighbours at same degree days over 10 days; frost forecast in 5 days. Risk 0.72, likely causes: seized thermostatic valve (0.5), air in system (0.3), unit vacancy/low use (0.2).
2. Ladder step 2: tenant check-in in portal ("Is the bedroom radiator cold even on 5?") → tenant taps "yes" → confidence up, skip remote boiler step (boiler is fine; building-wide signals normal).
3. Shortlist + estimate: valve replacement + hydraulic balancing of the riser, framework rates → **€380 (Claude's call: chosen above the €300 threshold so landlord approval shows in the demo)**.
4. Landlord one-click approval.
5. Simulated voice call to craftsman #1 (declines, booked out) → #2 accepts slot inside tenant's preferred window.
6. Tenant notice with one-tap reschedule; silence = accepted.
7. Callback after slot: fixed, fault found = yes (feeds ladder feedback); invoice uploaded; case closed on unit 2a; summary to tenant and landlord.

Second alert (shown on dashboard, not walked through): central boiler short-cycling, service overdue.

## Builder decisions (Claude's calls, user may override)

- **centrix is mocked inside this app** (PDF: no reltix API). SQLite holds the centrix record; "write to centrix" = write case/contract rows shown on the unit. Existing `main.py`/`coding_agent.py` are scratch, ignored.
- **LLM with canned fallback:** OpenAI used live; every LLM output has a recorded fallback so the demo runs without network/key.
- **Demo clock:** scripted scenario steps over a virtual clock (`now()` injectable). "Advance" button jumps to next event; detectors, ladder waits, "silence = accepted" deadline read virtual time.
- **Ladder may skip steps** when confidence is high (e.g. clear boiler fault code → straight to visit; tenant "yes, cold" → raises confidence). Rules in config.
- **Views:** one app, role switcher plus a "judge view" split screen showing all four at once; live updates via SSE.
- **Connector implemented, not only documented:** `POST /partner/jobs` + `POST /partner/jobs/{id}/status` exist; the simulated voice adapter calls them, so voice and API partners share one path.
- **Runs locally** on a laptop; reset/replay command reseeds SQLite.

## Left to the builder

Exact detector math/thresholds, UI styling, DB schema details, OpenAI model choice, how the simulated call transcript is generated (LLM or scripted), file layout.
