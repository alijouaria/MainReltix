# centrix Partner Connector Spec v1

**One plug for every partner.** This spec defines what any ecosystem partner must give centrix, and what it gets back, to be integrated into the property's life without forms. Energy suppliers, internet providers, insurers and craftsmen all plug in the same way. The worked example throughout is the anchor use case: **a tenant's energy contract is about to expire, so a new supply contract is signed and the meter reading is passed on.**

Machine-readable version: [`connector-openapi.yaml`](connector-openapi.yaml).

---

## 0. Principles

1. **centrix fills, partner never asks twice.** Every order arrives pre-filled from the centrix record. A partner may request *only* fields centrix does not hold, and it must declare them up front (see §2, `required_inputs`).
2. **One tap for people.** The tenant confirms with one tap and the landlord approves with one click. Everything else is machine-to-machine.
3. **Minimum data, logged consent.** A partner receives only the fields its offer declares it needs. Each order carries a consent record saying what was shared, with whom, when and through which channel.
4. **Same plug, any channel.** API, email or voice partners all produce the same `Order` and `StatusUpdate` objects.
5. **Close the loop.** Every outcome (a contract, an appointment, an invoice) is written back to the unit in centrix, and tenant and landlord are informed automatically.

## 1. Two partner kinds, one contract

| Kind | Partner… | Example | Result written to the unit |
|---|---|---|---|
| `sell` | sells a service contract | energy supplier, internet, insurance | `service_contract` |
| `deliver` | performs a job on site | craftsman, meter reader, moving company | `job` + `appointment` + `invoice` |

Both kinds use the same five building blocks: **Profile & Offers → Eligibility → Availability → Order → Status updates.**

---

## 2. Profile & Offers

A partner registers one **profile** and publishes one or more **offers**: a catalogue centrix can match against without calling the partner.

```json
{
  "partner_id": "p_stromwerk",
  "name": "Stromwerk Energie GmbH",
  "kind": "sell",
  "category": "energy",
  "channel": "api",
  "webhook_url": "https://api.stromwerk.example/centrix",
  "contact": {"ops_email": "partner-ops@stromwerk.example"},
  "offers": [
    {
      "offer_id": "o_green12",
      "title": "Green Power 12",
      "pricing": {"base_eur_month": 12.00, "unit_price_ct_kwh": 31.9, "bonus_eur": 50},
      "term": {"min_months": 12, "notice_weeks": 4, "price_guarantee_months": 12},
      "attributes": {"green": true},
      "required_inputs": ["tenant.name", "unit.address", "meter.number", "meter.reading", "start_date", "iban_mandate"],
      "valid_until": "2027-01-31"
    }
  ]
}
```

- `required_inputs` is the contract for data minimisation. centrix sends exactly these fields and nothing more.
- `iban_mandate` is never typed into centrix. The supplier collects the direct-debit mandate in its own one-tap confirm step and returns a reference (see §5, `needs_input`).
- For `deliver` partners, an offer is a **service** with a price basis instead of a tariff: `{"pricing": {"callout_eur": 65, "hourly_eur": 70}}`.

## 3. Eligibility rules

Partners publish rules as data, so centrix can pre-check them without a call. Rules are a list of simple conditions over centrix fields:

```json
"eligibility": [
  {"field": "unit.postcode", "op": "prefix_in", "value": ["52", "50", "53"]},
  {"field": "meter.type", "op": "in", "value": ["electricity_standard", "electricity_smart"]},
  {"field": "usage.annual_kwh_estimate", "op": "between", "value": [500, 10000]},
  {"field": "tenant.age_over_18", "op": "eq", "value": true}
]
```

- Allowed ops: `eq`, `in`, `prefix_in`, `between`, `gte`, `lte`, `exists`.
- `field` uses the centrix field catalogue (§8). An unknown field makes the offer invalid when it is registered, not when it is ordered.
- If a rule cannot be decided from data, the partner marks it `"check": "partner"`, and centrix calls `POST /eligibility` (§6) before showing the offer.

## 4. Availability

Availability tells centrix *when* the outcome can happen.

| Kind | Format | Energy example |
|---|---|---|
| `sell` | `earliest_start` rule | `{"lead_time_days": 14, "start_on": "any_day"}` |
| `deliver` | slot list or slot API | `[{"start": "2026-12-03T08:00", "end": "2026-12-03T12:00"}]` |

centrix always sends the person's constraint along with the order. For `sell`, that is the required start date (the day after the old contract ends). For `deliver`, it is the tenant's access windows. The partner must answer inside that constraint or decline.

## 5. Order

centrix places an order once the person has confirmed (tenant one tap, plus landlord one click if the landlord's rules require it).

```json
{
  "order_id": "ord_9f2c",
  "partner_id": "p_stromwerk",
  "offer_id": "o_green12",
  "kind": "sell",
  "requested_start": "2027-01-01",
  "subject": {
    "unit_id": "unit_4b",
    "address": "Musterstraße 12, 52062 Aachen",
    "tenant": {"name": "Lena Hoffmann", "contact_via": "centrix"},
    "meter": {"type": "electricity_standard", "number": "1ESY1160123456",
              "reading": {"value_kwh": 18342, "read_at": "2026-12-31", "source": "remote_read"}}
  },
  "previous_contract": {"supplier": "Altversorger AG", "ends": "2026-12-31", "cancellation_by": "new_supplier"},
  "consent": {
    "data_shared": ["tenant.name", "unit.address", "meter.number", "meter.reading", "start_date"],
    "given_at": "2026-11-20T09:21:00Z",
    "channel": "portal",
    "given_by": "c_202"
  },
  "callback_url": "https://centrix.example/connector/v1/orders/ord_9f2c/status"
}
```

Rules:
- An order carries **only** the fields in the offer's `required_inputs`. Any extra field is rejected (`422`). This is enforced in the reference code.
- `tenant.contact_via: "centrix"`: partners do not get the tenant's email or phone. Any question to the tenant goes through centrix, which routes it to the tenant's preferred channel.
- `previous_contract.cancellation_by: "new_supplier"`: in Germany the new supplier normally cancels the old contract and registers the meter switch with the grid operator. The order says who does what.
- The order is idempotent on `order_id`. Resending the same order returns the existing order.

## 6. Status updates

Partners report progress with one object, pushed to `callback_url` (or produced by the email or voice adapter):

```json
{"order_id": "ord_9f2c", "status": "confirmed", "at": "2026-11-20T09:40:00Z",
 "result": {"contract_number": "SW-2026-55812", "start": "2027-01-01",
            "monthly_estimate_eur": 64.80, "document_url": "https://…/contract.pdf"}}
```

**Shared lifecycle.** Both kinds use the same states. `deliver` adds the two on-site states.

| From | Allowed next | Meaning |
|---|---|---|
| `submitted` (set by centrix) | `received`, `rejected` | Partner got the order |
| `received` | `needs_input`, `confirmed`, `rejected` | Checking |
| `needs_input` | `received` | Partner asks centrix for one missing field; centrix asks the person in one tap and resubmits |
| `confirmed` | `scheduled` *(deliver)*, `active` *(sell)*, `cancelled` | Contract signed / job accepted |
| `scheduled` | `scheduled` (reschedule), `in_progress`, `cancelled` | Slot booked inside access window |
| `in_progress` | `completed`, `needs_followup` | On site |
| `active` / `completed` | (terminal) | Supply running / job done |

Any other move returns `409`. Each update is written to the case timeline in centrix, and centrix tells the tenant and landlord in plain language:
- **`confirmed`:** tenant gets "Your new energy contract starts on 1 Jan. Nothing else to do."
- **`active`:** contract document and meter reading are filed on the unit, and the landlord gets a one-line summary.

**Optional pre-check:** `POST /eligibility` with the candidate subject returns `{eligible, reasons[]}` for rules marked `check: partner`.

## 7. Channel adapters: the same plug for partners without an API

| Partner channel | In (centrix → partner) | Out (partner → centrix) |
|---|---|---|
| `api` | `POST {webhook_url}/orders` | `POST callback_url` with `StatusUpdate` |
| `email` | Structured email with the order and a one-click confirm link | The link or a parsed reply becomes a `StatusUpdate`; if parsing fails, a human checks it |
| `voice` | AI agent calls and reads out the order | Spoken answers become a `StatusUpdate`; the call transcript is filed on the case |

centrix never needs to know which channel a partner used. It only sees `Order` and `StatusUpdate`.

## 8. centrix field catalogue (what partners can rely on)

The fields offers may reference in `required_inputs` and eligibility rules. Source: the centrix property record.

| Field | Example | Source in centrix |
|---|---|---|
| `unit.address`, `unit.postcode` | Musterstraße 12, 52062 | property record |
| `unit.size_sqm`, `unit.rooms` | 68, 3 | property record |
| `tenant.name` | Lena Hoffmann | lease |
| `tenant.move_in`, `tenant.move_out` | 2026-12-01 | lease |
| `meter.type`, `meter.number` | electricity_standard, 1ESY1160123456 | meter register |
| `meter.reading` | 18342 kWh @ 2026-12-31 | remote read, or a tenant photo at the switch date |
| `usage.annual_kwh_estimate` | 2100 | previous contract / meter history |
| `previous_contract.supplier`, `.ends` | Altversorger AG, 2026-12-31 | shared by the tenant once (see §9) |
| `building.year`, `building.heating_type` | 1972, central | property record |
| `access_windows` | weekdays 08:00–12:00 | tenant preferences |

## 9. Assumptions (stated for realism)

- **How centrix knows the contract end date.** A tenant's energy contract is the tenant's own, so property managers do not normally see it. Assumption: at move-in the tenant opts in once (photo of their current bill, or consent in the lease annex). centrix extracts supplier, end date and annual usage. Without opt-in, centrix still offers a switch at move-in and move-out, when a contract change happens anyway.
- **Meter reading.** Taken from a remote-read meter where installed. Otherwise centrix asks the tenant for one photo on the switch date, and AI reads the number.
- **Legal.** The new supplier handles cancellation of the old contract and the grid operator switch (supplier-change process). The tenant has the statutory 14-day withdrawal right, which centrix reminds them of in the confirm step.
- **Privacy.** The legal basis is the tenant's consent for the specific order. Consent is logged per order (§5), and revoking it stops future orders, not signed contracts.
- **Landlord role.** For a tenant's own contract, the landlord gets a summary only (no approval). Landlord approval is needed only when the landlord pays: building or common-area contracts and repairs above the owner's limit.

## 10. How the next partner plugs in (checklist)

Proof of genericity: a **craftsman** (a `deliver` partner) uses the same five blocks with different values:

| Block | Energy supplier (`sell`) | Craftsman (`deliver`) |
|---|---|---|
| Offer | tariff, `required_inputs` incl. meter reading | service with callout + hourly rate, `required_inputs` incl. access windows and fault summary |
| Eligibility | postcode, meter type, kWh range | postcode radius, trade, framework contract present |
| Availability | `lead_time_days` | slot list |
| Order | `requested_start` | `access_windows` |
| Status | … → `confirmed` → `active` | … → `confirmed` → `scheduled` → `in_progress` → `completed` + invoice |

To add any new partner:
1. Register a profile with `kind`, `category` and `channel`.
2. Publish offers with `pricing`, `term` and `required_inputs` drawn only from the field catalogue (§8).
3. Express eligibility as rules; mark anything undecidable `check: partner`.
4. Declare availability (`lead_time_days` or slots).
5. Implement `Order` intake and `StatusUpdate` callbacks, or pick the email or voice adapter.
6. Pass the contract test suite: valid order accepted, extra field rejected, illegal transition 409, outcome written back to the unit.

No new endpoint and no schema change on the centrix side.

## Reference implementation

`backend/app/connector.py` implements this contract for `deliver` partners (job intake, the transition table, slot-in-window checks, invoice filing, extra-field rejection), with tests in `backend/tests/test_connector.py`. Its status names map to this spec: `offered → submitted`, `accepted → confirmed`, `on_site → in_progress`, `declined → rejected`. The `sell` flow follows the same pattern.
