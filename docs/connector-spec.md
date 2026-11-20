# centrix Partner Connector Spec

How any partner (heating engineer, electrician, plumber, energy supplier, metering provider) plugs into centrix. One contract for every partner. Only the profile values and `job_type` catalogue change. Reference implementation: `backend/app/connector.py`.

## 1. Partner profile (offers / services)

Each partner registers one profile. Today it is stored as a `craftsmen` row; `partner_id` = that row's `id`.

| Field | Meaning |
|---|---|
| `id` | `partner_id` used in every call |
| `name` | Display name |
| `trades` | Service categories offered, e.g. `heating`, `plumbing`, `electrical`, `energy_supply` |
| `job_types` | Catalogue of orderable services, e.g. `valve_replacement_balancing`, `boiler_service` |
| `callout_eur`, `hourly_eur` | Price basis for estimates (parts quoted per job type) |
| `distance_km`, `rating` | Inputs to ranking |
| `channel` | `api`, `voice`, or `email` (see 6) |

## 2. Eligibility rules

centrix only offers a job to a partner when all of these hold:
- `job_type` belongs to one of the partner's `trades`.
- Partner is inside the service radius for the building.
- Partner has an active `framework` or `maintenance` contract, or the estimate is under the owner auto-approval limit (above that, owner approval comes first).
- Tenant consent covers what the job needs (`consent_scope`).

Eligible partners are ranked by cost, distance and rating (`matching.rank_craftsmen`), and every ranking includes human-readable reasons.

## 3. Availability

centrix sends the tenant's `access_windows` with the job. The partner answers with `accepted` and then with `scheduled` plus a `slot` inside one of those windows. If no window fits, the partner sends `declined`, and centrix offers the job to the next partner in the ranking.

## 4. Job intake: `POST /partner/jobs`

Body `JobIn` (all fields required, unknown fields rejected):

```json
{
  "case_id": 1,
  "partner_id": 1,
  "job_type": "valve_replacement_balancing",
  "unit": {"address": "Musterstr. 1", "floor": 2, "unit_id": "U-2-1"},
  "access_windows": [{"start": "2026-11-21T08:00:00", "end": "2026-11-21T12:00:00"}],
  "evidence_summary": "Unit 40% below building median for 5 days, radiators cold",
  "boiler_model": "Viessmann Vitodens 200",
  "consent_scope": ["unit_access", "contact_tenant"]
}
```

Response: `{"job_id": 7, "status": "offered"}`. `GET /partner/jobs/{job_id}` returns the current job.

## 5. Status updates: `POST /partner/jobs/{job_id}/status`

```json
{"status": "scheduled", "slot": "2026-11-21T09:00:00"}
{"status": "completed", "outcome": {"fixed": true, "notes": "2 valves replaced"},
 "invoice": {"amount_eur": 380.0, "file_name": "inv-2026-114.pdf"}}
```

Allowed `status` values (any other value returns 422):

```
offered (set by centrix) -> accepted -> scheduled -> on_site -> completed
                         \-> declined                        \-> needs_followup
```

Effects in centrix:
- Every update appends a `case_events` row: actor `partner:<id>`, kind `job_<status>`.
- `scheduled` sets the job's `scheduled_at` from `slot`.
- `completed` with `invoice` files an `invoice` document on the case and unit.
- `needs_followup` reopens the case for the next step on the escalation ladder.

## 6. Consent and data minimisation

- `JobIn` and `StatusIn` use `extra="forbid"`. A request that includes consumption data (for example `heat_readings` or kWh series) is rejected with 422. Partners get the *conclusion* (`evidence_summary`), never the raw tenant data.
- `consent_scope` lists what the tenant agreed to. The partner may only act inside that list.
- Tenant contact details are not part of `JobIn`. All tenant contact goes through centrix channels.

## 7. Channel adapters

Partners without an API still use the same contract. Each adapter converts its channel into the same `JobIn` and `StatusIn` and calls the same functions (`create_job`, `update_status`):
- **API**: direct HTTP calls, as shown above.
- **Voice agent**: the call script reads out `JobIn`. The partner's spoken answers ("yes, Thursday 9am") map to `StatusIn{status: "scheduled", slot}`.
- **Email**: the job is sent as a structured email. A reply is parsed into a `StatusIn` and validated. If parsing fails, centrix escalates to a human.

## 8. Checklist: how the next partner plugs in

Example: an electrician, or an energy supplier offering tariff switches.

1. Create a profile row with trades, job types, prices and channel.
2. Add the partner's `job_type` values to the catalogue, with an estimate formula.
3. Define the eligibility inputs: radius, contract kind, auto-approval limit.
4. List the `consent_scope` values the job types need.
5. Choose the channel. Use the API, or a voice or email adapter that emits `StatusIn`.
6. Run the contract tests in `backend/tests/test_connector.py` against the new partner.

No schema change and no new endpoint are needed.
