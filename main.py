"""Local Reltix demo server. Uses only the Python standard library."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import mimetypes
import os
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from threading import RLock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, unquote, urlencode, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
UNIT_FILE = ROOT / "data" / "centrix" / "unit_4b.json"
OFFERS_FILE = ROOT / "data" / "partners" / "electricity_offers.json"
ORDERS_FILE = ROOT / "data" / "centrix" / "orders.json"
PORTFOLIO_DIR = ROOT / "data" / "centrix" / "portfolio"          # every other unit, same schema as unit_4b.json
PORTFOLIO_SEED = ROOT / "data" / "centrix" / "portfolio_seed"
CLOCK_FILE = ROOT / "data" / "centrix" / "demo_clock.json"       # demo stand-in for "today"
RUN_FILE = ROOT / "data" / "centrix" / "trigger_run.json"        # last daily trigger run
CALLS_FILE = ROOT / "data" / "centrix" / "calls.json"            # outbound call queue
# Partner eligibility rule (connector spec §3): postcodes this energy partner serves.
DEFAULT_POSTCODE_PREFIXES = ["52", "50", "53"]
AGENT_ID = "agent_2201m7wp3v2rfrtb1pqbq4rt1a80"  # ElevenLabs agent "Mia"
EVENTS_FILE = ROOT / "data" / "centrix" / "case_events.json"
REVIEWS_FILE = ROOT / "data" / "centrix" / "staff_reviews.json"
# Shared connector field catalogue. Profiles are checked at load time so a partner
# cannot silently introduce fields the property record and consent flow do not know.
CONNECTOR_FIELDS = {
    "unit.address", "unit.postcode", "unit.size_sqm", "unit.rooms", "tenant.name", "tenant.age_over_18",
    "tenant.move_in", "tenant.move_out", "meter.type", "meter.number", "meter.last_reading",
    "meter.switch_reading", "usage.annual_kwh_estimate", "previous_contract.supplier",
    "previous_contract.ends", "previous_contract.monthly_price_eur", "building.year",
    "building.heating_type", "access_windows", "fault_summary", "start_date",
    "iban_mandate",
}
DEFAULT_REQUIRED_INPUTS = ["tenant.name", "unit.address", "meter.number", "start_date"]
CONNECTOR_FIELD_LABELS = {
    "unit.address": "Home address", "unit.postcode": "Postcode", "unit.size_sqm": "Home size",
    "unit.rooms": "Number of rooms", "tenant.name": "Resident name", "tenant.age_over_18": "Adult status",
    "tenant.move_in": "Move-in date", "tenant.move_out": "Move-out date", "meter.type": "Meter type",
    "meter.number": "Meter number", "meter.last_reading": "Last meter reading",
    "meter.switch_reading": "Changeover meter reading", "usage.annual_kwh_estimate": "Estimated annual energy use",
    "previous_contract.supplier": "Current supplier", "previous_contract.ends": "Current contract end date",
    "previous_contract.monthly_price_eur": "Current monthly price", "building.year": "Building year",
    "building.heating_type": "Heating type", "access_windows": "Access preferences",
    "fault_summary": "Problem summary", "start_date": "Requested start date",
}
# Allowed partner status moves for a `sell` partner (connector spec §6). Anything else returns 409.
TRANSITIONS = {
    "submitted": {"received", "pending", "rejected"},
    "received": {"needs_input", "pending", "confirmed", "rejected"},
    "pending": {"received", "needs_input", "confirmed", "rejected"},
    "needs_input": {"received"},
    "confirmed": {"active", "cancelled"},
}
HOST = "127.0.0.1"
PORT = 8765
RESET_DATE = None
DEFAULT_DEMO_DATE = date(2026, 11, 20)
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:0.5b"


ROLE_PAGES = {"/tenant": "tenant.html", "/reltix": "reltix.html", "/supplier": "supplier.html",
              "/owner": "owner.html", "/privacy": "privacy.html"}
DATA_LOCK = RLock()


def code_version(root: Path | None = None) -> str:
    """Fingerprint of this copy of the app, so launch.py can tell an old server from this one."""
    root = root or Path(__file__).resolve().parent
    digest = hashlib.sha1()
    for name in ("main.py", "web/flow.js", "web/app.js"):
        try:
            digest.update((root / name).read_bytes())
        except OSError:
            pass
    return digest.hexdigest()[:12]


CODE_VERSION = code_version()


def meter_id(unit_id: str, meter: dict, index: int) -> str:
    """Stable per-unit identifier, even when a meter number has not been recorded yet."""
    return str(meter.get("meter_id") or f"{unit_id}:{meter.get('type', 'meter')}:{index + 1}")


def electricity_meters(unit: dict) -> list[tuple[int, dict, str]]:
    return [(i, item, meter_id(unit.get("unit_id", "unit_4b"), item, i))
            for i, item in enumerate(unit.get("unit", {}).get("meters", []))
            if item.get("type") == "electricity"]


def selected_electricity_meter(unit: dict, requested_id: str | None = None) -> dict | None:
    meters = electricity_meters(unit)
    selected_id = requested_id or unit.get("unit", {}).get("selected_electricity_meter_id")
    if selected_id:
        return next((meter for _, meter, identity in meters if identity == selected_id), None)
    return meters[0][1] if len(meters) == 1 else None


def view_for(role: str, unit: dict, partner: dict) -> dict:
    """Project sample records for each demo workspace (not an authentication boundary)."""
    contracts = unit.get("contracts", [])
    energy = next((c for c in contracts if c.get("type") == "energy_supply"), {})
    if role == "reltix":
        return {"unit": unit, "partner": partner}
    if role == "tenant":
        return {
            "unit": {
                "unit_id": unit.get("unit_id"),
                "property": unit.get("property", {}),
                "unit": {**unit.get("unit", {}), "meters": [
                    {**{k: v for k, v in m.items() if k != "demo_end_reading"},
                     "meter_id": meter_id(unit.get("unit_id", "unit_4b"), m, i)}
                    for i, m in enumerate(unit.get("unit", {}).get("meters", []))]},
                "owner": {"name": unit.get("owner", {}).get("name")},
                "tenant": (unit.get("tenant") or {}),
                "contracts": [c for c in contracts if c.get("customer_contact_id", (unit.get("tenant") or {}).get("contact_id")) == (unit.get("tenant") or {}).get("contact_id")],
            },
            "partner": partner,
        }
    if role == "partner":
        meters = [(i, m) for i, m in enumerate(unit.get("unit", {}).get("meters", [])) if m.get("type") == "electricity"]
        return {
            "unit": {
                "unit_id": unit.get("unit_id"),
                "property": {"address": unit.get("property", {}).get("address")},
                "unit": {"meters": [{"type": m.get("type"), "number": m.get("number"),
                                      "meter_id": meter_id(unit.get("unit_id", "unit_4b"), m, i),
                                      "switch_reading": m.get("switch_reading")} for i, m in meters]},
                "tenant": {"name": (unit.get("tenant") or {}).get("name")},
                "contracts": [{"type": "energy_supply", "supplier": energy.get("supplier"), "expiry_date": energy.get("expiry_date")}] if energy else [],
            },
            "partner": partner,
        }
    raise ValueError("Unknown role. Use tenant or reltix.")


def read_json(path: Path) -> dict:
    # On Windows a file being swapped in by write_json can't be opened for a split second
    # (PermissionError). Many page polls run in parallel, so retry briefly instead of failing the request.
    for attempt in range(8):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (PermissionError, json.JSONDecodeError):
            if attempt == 7:
                raise
            time.sleep(0.025 * (attempt + 1))


def write_json(path: Path, value: dict) -> None:
    # unique temp file per write, so two threads saving the same flat never share one
    temp = path.with_name(f"{path.stem}.{threading.get_ident()}.{uuid.uuid4().hex[:6]}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(8):
        try:
            temp.replace(path)
            return
        except PermissionError:   # Windows: someone is reading the file right now
            if attempt == 7:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(0.025 * (attempt + 1))


def unit_path(unit_id: str) -> Path:
    unit_id = unit_id or "unit_4b"
    if not re.fullmatch(r"unit_[a-z0-9]+", unit_id):
        raise ValueError("Unknown unit.")
    path = UNIT_FILE if unit_id == "unit_4b" else PORTFOLIO_DIR / f"{unit_id}.json"
    if not path.exists():
        raise ValueError("Unknown unit.")
    return path


def all_unit_ids() -> list:
    ids = [path.stem for path in PORTFOLIO_DIR.glob("unit_*.json")]
    return ["unit_4b"] + sorted(ids, key=lambda uid: (int(re.sub(r"\D", "", uid) or 0), uid))


def demo_today() -> date:
    if CLOCK_FILE.exists():
        return date.fromisoformat(read_json(CLOCK_FILE)["today"])
    return date.today()


def read_list(path: Path, key: str) -> list:
    return read_json(path).get(key, []) if path.exists() else []


def read_orders() -> list:
    return read_json(ORDERS_FILE).get("orders", []) if ORDERS_FILE.exists() else []


def write_orders(orders: list) -> None:
    write_json(ORDERS_FILE, {"orders": orders})


def read_events(unit_id: str | None = None) -> list:
    events = read_json(EVENTS_FILE).get("events", []) if EVENTS_FILE.exists() else []
    return [item for item in events if unit_id is None or item.get("unit_id", "unit_4b") == unit_id]


def _value_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def field_provenance(unit: dict, key: str, value: object) -> dict:
    record = (unit.get("field_provenance") or {}).get(key)
    if not record:
        return {"source": "Legacy record; original source not recorded", "verified_at": None}
    if record.get("value_hash") != _value_hash(value):
        return {"source": "Value changed since last verification", "verified_at": None}
    return record


def unit_data_quality(unit: dict) -> list[dict]:
    """Explain whether workflow-critical facts are usable and where they came from."""
    today = demo_today()
    tenant = unit.get("tenant") or {}
    property_record = unit.get("property") or {}
    energy = next((item for item in unit.get("contracts", [])
                   if item.get("type") == "energy_supply" and item.get("status") == "active"), {})
    meters = electricity_meters(unit)
    selected_id = (unit.get("unit") or {}).get("selected_electricity_meter_id")
    meter = next((item for _, item, identity in meters if identity == selected_id), None)
    if meter is None and len(meters) == 1:
        meter = meters[0][1]
    email = str(tenant.get("email") or "").strip()
    phone = re.sub(r"[^+0-9]", "", str(tenant.get("phone") or ""))
    contact = email or phone or None
    contact_valid = bool((email and re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email))
                         or (phone and re.fullmatch(r"\+?[0-9]{8,15}", phone)))
    fields = [
        ("property.address", "Property address", property_record.get("address"),
         bool(re.search(r"\b\d{5}\b", str(property_record.get("address", "")))), "Add a complete address with postcode."),
        ("tenant.name", "Tenant name", tenant.get("name"), bool(str(tenant.get("name") or "").strip()), "Add the current tenant’s name."),
        ("tenant.contact", "Tenant contact route", contact, contact_valid,
         "Add a valid email address or phone number so the resident can be reached."),
        ("contract.expiry_date", "Energy contract end date", energy.get("expiry_date"),
         _valid_iso_date(energy.get("expiry_date")), "Add or correct the active contract end date."),
        ("contract.annual_consumption_kwh", "Annual energy usage", energy.get("annual_consumption_kwh"),
         _positive_number(energy.get("annual_consumption_kwh")), "Add a positive annual usage estimate."),
        ("meter.number", "Electricity meter number", meter.get("number") if meter else None,
         bool(meter and re.fullmatch(r"[A-Za-z0-9-]{5,32}", str(meter.get("number", "")))), "Select the right meter and verify its number."),
    ]
    result = []
    for key, label, value, valid, guidance in fields:
        provenance = field_provenance(unit, key, value)
        stale = key == "contract.expiry_date" and valid and date.fromisoformat(str(value)) < today
        if not valid:
            status, message, needs_review = "missing_or_invalid", guidance, True
        elif stale:
            status, message, needs_review = "outdated", "The active contract end date has passed; confirm the current supply record.", True
        elif not provenance.get("verified_at"):
            status = "unverified"
            message = (f"Value was reported by {provenance['source']}; staff verification is not recorded."
                       if provenance.get("recorded_at") else "Value is present, but its source and verification date are unknown.")
            needs_review = True
        else:
            status, message, needs_review = "verified", "Verified record.", False
        signature = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:10]
        result.append({"field": key, "label": label, "value": value, "status": status,
                       "message": message, "source": provenance.get("source"),
                       "recorded_at": provenance.get("recorded_at"), "verified_at": provenance.get("verified_at"),
                       "needs_review": needs_review,
                       "signature": signature})
    last_reading = (meter or {}).get("last_reading") or {}
    reading_valid = (_nonnegative_number(last_reading.get("value_kwh")) and _valid_iso_date(last_reading.get("read_at"))
                     and date.fromisoformat(str(last_reading.get("read_at"))) <= today)
    reading_provenance = field_provenance(unit, "meter.last_reading", last_reading)
    reading_age = (today - date.fromisoformat(str(last_reading.get("read_at")))).days if reading_valid else None
    reading_stale = reading_valid and reading_age > 365
    if not reading_valid:
        reading_status, reading_message, reading_needs_review = "missing_or_invalid", "Record a valid meter reading and date before handover.", True
    elif reading_stale:
        reading_status, reading_message, reading_needs_review = "outdated", f"Last reading is {reading_age} days old; collect a current reading before handover.", True
    elif not reading_provenance.get("verified_at"):
        reading_status, reading_message = "unverified", (
            f"Reading was reported by {reading_provenance['source']}; staff verification is not recorded."
            if reading_provenance.get("recorded_at") else "Reading is present, but its source and verification date are unknown.")
        reading_needs_review = True
    else:
        reading_status, reading_message, reading_needs_review = "verified", "Verified meter reading.", False
    result.append({"field": "meter.last_reading", "label": "Last meter reading",
                   "value": f"{last_reading.get('value_kwh')} kWh · {last_reading.get('read_at')}" if reading_valid else None,
                   "provenance_value": last_reading,
                   "status": reading_status, "message": reading_message,
                   "source": reading_provenance.get("source"), "recorded_at": reading_provenance.get("recorded_at"),
                   "verified_at": reading_provenance.get("verified_at"),
                   "needs_review": reading_needs_review,
                   "signature": hashlib.sha256(str(last_reading).encode("utf-8")).hexdigest()[:10]})
    if len(meters) > 1 and not selected_id:
        result.append({"field": "meter.selection", "label": "Electricity meter selection", "value": None,
                       "status": "missing_or_invalid", "message": "More than one electricity meter is listed; select the meter for this unit.",
                       "source": "Centrix record", "verified_at": None, "needs_review": True, "signature": str(len(meters))})
    return result


def _valid_iso_date(value: object) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _positive_number(value: object) -> bool:
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def _nonnegative_number(value: object) -> bool:
    try:
        return math.isfinite(float(value)) and float(value) >= 0
    except (TypeError, ValueError):
        return False


def review_item(unit_id: str, category: str, key: str, title: str, reason: str,
                source: str = "Centrix workflow", severity: str = "attention") -> dict:
    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return {"review_id": f"{unit_id}:{category}:{suffix}", "unit_id": unit_id, "category": category,
            "title": title, "reason": reason, "source": source, "severity": severity}


def portfolio_review_queue() -> list[dict]:
    """Unresolved data-quality and exception tasks for the property team."""
    reviews = read_list(REVIEWS_FILE, "reviews")
    latest = {item.get("review_id"): item for item in reviews}
    pending = []
    run_results = (read_json(RUN_FILE).get("results", []) if RUN_FILE.exists() else [])
    orders = read_orders()
    for unit_id in all_unit_ids():
        unit = read_json(unit_path(unit_id))
        quality = unit_data_quality(unit)
        unverified = [field for field in quality if field["status"] == "unverified"]
        if unverified:
            labels = ", ".join(field["label"] for field in unverified)
            source_summary = ", ".join(f"{field['label']} ({field['source']})" for field in unverified)
            signature = ",".join(f"{field['field']}:{field['signature']}" for field in unverified)
            provenance_item = review_item(unit_id, "provenance", signature, "Review record sources",
                                          f"Check the source and accuracy of: {source_summary or labels}.", "Property data")
            provenance_item["fields"] = [field["field"] for field in unverified]
            pending.append(provenance_item)
        for field in quality:
            if field["needs_review"] and field["status"] != "unverified":
                pending.append(review_item(unit_id, "data", f"{field['field']}:{field['signature']}",
                                           f"Verify {field['label'].lower()}", field["message"], field["source"]))
        coverage = (unit.get("renewal_case") or {}).get("coverage_review")
        if coverage and coverage.get("status") in ("unknown", "unsupported"):
            pending.append(review_item(unit_id, "coverage", str(coverage.get("status")), "Review partner coverage",
                                       coverage.get("reason", "Partner coverage could not be confirmed.")))
        case = unit.get("renewal_case") or {}
        if case.get("needs_person") and not any(i["unit_id"] == unit_id and i["reason"] == case["needs_person"] for i in pending):
            pending.append(review_item(unit_id, "case", str(case.get("needs_person")), "Follow up with resident",
                                       str(case["needs_person"])))
        # Flags from today's daily check (moving out, no contact details, two contracts, unusual usage, …),
        # unless the same reason is already in the queue as a coverage or follow-up item.
        known = {item["reason"] for item in pending if item["unit_id"] == unit_id}
        for row in run_results:
            if row["unit_id"] == unit_id and row["decision"] == "attention" and row["reason"] not in known:
                pending.append(review_item(unit_id, "flag", row["reason"], "Flagged by the daily check", row["reason"], "Daily check"))
        for order in orders:
            if order.get("subject", {}).get("unit_id") != unit_id:
                continue
            mismatch = order.get("start_date_mismatch")
            if mismatch:
                pending.append(review_item(unit_id, "order", f"{order['order_id']}:{mismatch.get('requested')}:{mismatch.get('proposed')}",
                                           "Review proposed contract start date",
                                           f"Partner proposed {mismatch.get('proposed')} instead of {mismatch.get('requested')}. No date has been confirmed."))
            if order.get("start_unconfirmed"):
                pending.append(review_item(unit_id, "order", f"{order['order_id']}:start_unconfirmed", "Confirm whether supply started",
                                           "The supplier has not confirmed that supply began; property team follow-up is due."))
        for issue in unit.get("issues", []):
            if issue.get("reply") or issue.get("owner") == "partner" or issue.get("shared_order_id") or issue.get("resolved"):
                continue
            urgent = issue.get("urgent")
            item = review_item(unit_id, "issue", issue["issue_id"],
                               "Respond to urgent resident report" if urgent else "Reply to resident report",
                               issue.get("text", ""), source=issue.get("source") or "Resident report",
                               severity="urgent" if urgent else "attention")
            pending.append({**item, "issue_id": issue["issue_id"]})
    outstanding = [item for item in pending if item.get("issue_id") or latest.get(item["review_id"], {}).get("status") != "reviewed"]
    return sorted(outstanding, key=lambda item: item["severity"] != "urgent")


def connector_profile(partner: dict) -> dict:
    """Normalize legacy offers to the shared connector contract and reject unknown fields."""
    profile = dict(partner)
    profile.setdefault("kind", "sell")
    profile.setdefault("category", "electricity")
    profile.setdefault("channel", "voice")
    if profile["kind"] not in ("sell", "deliver"):
        raise ValueError("Partner kind must be sell or deliver.")
    if profile["channel"] not in ("api", "email", "voice"):
        raise ValueError("Partner channel must be api, email, or voice.")
    if not isinstance(profile.get("offers", []), list):
        raise ValueError("Partner offers must be a list.")
    if not profile.get("partner_id") or not profile.get("partner") or not profile.get("category"):
        raise ValueError("Connector profile needs a partner_id, partner name, and category.")
    offers = assign_offer_ids(profile.get("offers", []))
    for offer in offers:
        offer.setdefault("required_inputs", list(DEFAULT_REQUIRED_INPUTS))
        if not isinstance(offer.get("required_inputs"), list):
            raise ValueError(f"Offer {offer.get('offer_id')} required_inputs must be a list.")
        if not offer["required_inputs"] or not all(isinstance(field, str) for field in offer["required_inputs"]):
            raise ValueError(f"Offer {offer.get('offer_id')} needs one or more valid required_inputs.")
        unknown = sorted(set(offer.get("required_inputs", [])) - CONNECTOR_FIELDS)
        if unknown:
            raise ValueError(f"Offer {offer.get('offer_id')} uses unknown connector fields: {', '.join(unknown)}")
        if not offer.get("offer_id") or not offer.get("product"):
            raise ValueError("Every partner offer needs an offer_id and product name.")
        if profile["kind"] == "sell":
            if not _positive_number(offer.get("estimated_monthly_price_eur")):
                raise ValueError(f"Offer {offer.get('offer_id')} needs a positive monthly price.")
            if int(offer.get("contract_months") or 0) < 1:
                raise ValueError(f"Offer {offer.get('offer_id')} needs a contract term in months.")
        else:
            pricing = offer.get("pricing") or {}
            if not any(_positive_number(pricing.get(key)) for key in ("callout_eur", "hourly_eur")):
                raise ValueError(f"Service {offer.get('offer_id')} needs a positive callout or hourly price.")
    profile["offers"] = offers
    return profile


def partner_order_view(order: dict, partner: dict, unit: dict) -> dict:
    """Project Centrix's internal order onto the fields declared by the partner offer."""
    offer = next((item for item in partner.get("offers", []) if item.get("offer_id") == order.get("offer_id")), {})
    allowed = set(offer.get("required_inputs", DEFAULT_REQUIRED_INPUTS))
    subject = order.get("subject", {})
    projected = {"unit_id": subject.get("unit_id")}
    if "unit.address" in allowed:
        projected["address"] = subject.get("address")
    if "tenant.name" in allowed:
        projected["tenant"] = {"name": (subject.get("tenant") or {}).get("name"), "contact_via": "centrix"}
    meter_fields = {"meter.type": "type", "meter.number": "number", "meter.switch_reading": "switch_reading"}
    meter = {}
    for catalogue_field, order_field in meter_fields.items():
        if catalogue_field in allowed and order_field in (subject.get("meter") or {}):
            meter[order_field] = subject["meter"][order_field]
    if meter:
        projected["meter"] = meter
    energy = next((item for item in unit.get("contracts", []) if item.get("type") == "energy_supply"), {})
    property_record = unit.get("property") or {}
    unit_record = unit.get("unit") or {}
    record_meter = selected_electricity_meter(unit, order.get("meter_id"))
    address = str(property_record.get("address", ""))
    postcode = re.search(r"\b\d{5}\b", address)
    last_reading = record_meter.get("last_reading") if record_meter else None
    catalogue_values = {
        "unit.address": address or None, "unit.postcode": postcode.group(0) if postcode else None,
        "unit.size_sqm": unit_record.get("size_sqm"), "unit.rooms": unit_record.get("rooms"),
        "tenant.name": (unit.get("tenant") or {}).get("name"),
        "tenant.age_over_18": (unit.get("tenant") or {}).get("age_over_18"),
        "tenant.move_in": (unit.get("tenant") or {}).get("move_in"),
        "tenant.move_out": (unit.get("tenant") or {}).get("move_out"),
        "meter.type": record_meter.get("type") if record_meter else None,
        "meter.number": record_meter.get("number") if record_meter else None,
        "meter.last_reading": last_reading, "meter.switch_reading": (subject.get("meter") or {}).get("switch_reading"),
        "usage.annual_kwh_estimate": energy.get("annual_consumption_kwh"),
        "previous_contract.supplier": energy.get("supplier"), "previous_contract.ends": energy.get("expiry_date"),
        "previous_contract.monthly_price_eur": energy.get("current_monthly_price_eur"),
        "building.year": property_record.get("building_year"), "building.heating_type": property_record.get("heating"),
        "access_windows": (unit.get("tenant") or {}).get("access_windows"),
        "fault_summary": order.get("fault_summary"), "start_date": order.get("requested_start"),
        # Direct-debit details are collected by the supplier and never stored in Centrix.
        "iban_mandate": None,
    }
    partner_data = {field: catalogue_values.get(field) for field in allowed if field in catalogue_values}
    view = {key: value for key, value in order.items()
            if key in ("order_id", "partner_id", "partner", "offer_id", "kind", "status", "status_history", "consent",
                       "start_date_mismatch")}
    view["product"] = order.get("product")
    view["estimated_monthly_price_eur"] = order.get("estimated_monthly_price_eur")
    view["contract_months"] = order.get("contract_months")
    view["subject"] = projected
    view["partner_data"] = partner_data
    view["requested_start"] = order.get("requested_start") if "start_date" in allowed else None
    if view["requested_start"] and "meter.switch_reading" in allowed and order.get("reading_required", True):
        view["switch_reading_date"] = switch_reading_date(view["requested_start"]).isoformat()
    previous = order.get("previous_contract") or {}
    view["previous_contract"] = {
        key: previous.get(key) for field, key in (("previous_contract.supplier", "supplier"),
                                                   ("previous_contract.ends", "ends"))
        if field in allowed
    }
    return view


def add_event(kind: str, text: str, channel: str = "centrix", unit_id: str = "unit_4b") -> None:
    events = read_events()
    events.append({"type": kind, "text": text[:300], "channel": channel, "at": now_iso(), "unit_id": unit_id})
    write_json(EVENTS_FILE, {"events": events[-500:]})


def latest_active_unit() -> str:
    """The flat where a resident or Mia did something most recently (for the staff page's live view)."""
    for event in reversed(read_events()):
        if event.get("channel") != "centrix" and event.get("unit_id") and unit_path(event["unit_id"]).exists():
            return event["unit_id"]
    return "unit_4b"


def offers_summary(partner: dict, current_price: float) -> str:
    """Spoken-friendly offer list, so Mia can present offers even where her tools can't run (a phone line)."""
    parts = []
    for offer in partner.get("offers", []):
        price = float(offer.get("estimated_monthly_price_eur", 0))
        saving = current_price - price
        parts.append(f"{offer.get('product')}: about {round(price)} euros a month"
                     + (f", about {round(saving)} euros less than now" if saving >= 1 else "")
                     + f", {offer.get('renewable_share_percent')}% renewable, {offer.get('contract_months')} months")
    return "; ".join(parts) or "no offers available"


def customer_status(unit: dict) -> str:
    """Plain-text memory for Mia: what this resident chose, ordered, reported and asked for, so she can answer
    "what plan did I pick?" or "the leak is fixed now" on any later call."""
    unit_id = unit.get("unit_id", "")
    case = unit.get("renewal_case") or {}
    offers = {o["offer_id"]: o for o in read_partner().get("offers", [])}
    lines = []
    orders = [o for o in read_orders() if o.get("subject", {}).get("unit_id") == unit_id]
    order = orders[-1] if orders else None
    if order:
        product = (order.get("offer") or {}).get("product") or offers.get(order.get("offer_id"), {}).get("product", order.get("offer_id"))
        lines.append(f"Signed in the portal: {product}. Supplier order {order['order_id']} is {order['status'].replace('_', ' ')}"
                     + (f" (start {order['requested_start']})" if order.get("requested_start") else "") + ".")
    elif case.get("chosen_offer_id"):
        o = offers.get(case["chosen_offer_id"], {})
        lines.append(f"Chose {o.get('product', case['chosen_offer_id'])} (about {o.get('estimated_monthly_price_eur', '?')} EUR a month) "
                     f"but has NOT confirmed or signed yet; the confirm link is in the portal.")
    else:
        lines.append("Has not chosen a new plan yet.")
    blocked = offer_block_reason(unit)
    if blocked and not order:
        lines.append(f"No new plan can be arranged right now: {blocked}.")
    if case.get("declined"):
        lines.append(f"Said no to switching ({case.get('declined_reason') or 'no reason given'}).")
    if case.get("preferred_channel"):
        lines.append(f"Prefers contact by {case['preferred_channel']}.")
    if case.get("callback"):
        lines.append(f"Asked to be called back {case['callback']}.")
    reading = next((m for m in unit.get("unit", {}).get("meters", []) if m.get("type") == "electricity"), {}).get("switch_reading")
    if isinstance(reading, dict) and reading.get("value_kwh"):
        lines.append(f"Meter reading for the switch saved: {reading['value_kwh']} kWh.")
    open_issues = [i for i in unit.get("issues", []) if not i.get("resolved")]
    for i in open_issues[-4:]:
        reply = i.get("reply")
        state = f"the property team replied: {reply}" if isinstance(reply, str) and reply else \
            "shared with the supplier" if i.get("shared_order_id") else "waiting for the property team"
        lines.append(f"Open complaint {i['issue_id']}{' (URGENT)' if i.get('urgent') else ''}, reported {i.get('reported_at', '')[:10]}: "
                     f"{i.get('text', '')} Status: {state}.")
    for i in [i for i in unit.get("issues", []) if i.get("resolved")][-2:]:
        lines.append(f"Fixed complaint {i['issue_id']}: {i.get('text', '')[:80]} (resident confirmed it's fixed).")
    for n in (case.get("notes") or [])[-3:]:
        lines.append(f"Note: {n.get('text', '')}")
    if not open_issues:
        lines.append("No open complaints.")
    return " ".join(lines)


def agent_variables(unit: dict) -> dict:
    energy = next((c for c in unit.get("contracts", []) if c.get("type") == "energy_supply" and c.get("status") == "active"), {})
    name = (unit.get("tenant") or {}).get("name", "")
    expiry = date.fromisoformat(energy["expiry_date"]) if energy.get("expiry_date") else None
    partner = read_partner()
    price = float(energy.get("current_monthly_price_eur") or 0)
    greeting = None
    if (unit.get("tenant") or {}).get("language") == "de" and expiry:
        months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
        greeting = (f"Hallo {name.split(' ')[0]}, hier ist Mia, die digitale Assistentin von reltix, Ihrer Hausverwaltung. "
                    f"Ich rufe an, weil Ihr Stromvertrag mit {energy.get('supplier', 'Ihrem Anbieter')} am {expiry.day}. {months[expiry.month - 1]} endet. "
                    f"Haben Sie zwei Minuten?")
    return {
        "tenant_first_name": name.split(" ")[0], "tenant_last_name": " ".join(name.split(" ")[1:]),
        "full_address": unit.get("property", {}).get("address", ""), "current_supplier": energy.get("supplier", "your supplier"),
        "current_product": energy.get("product", ""), "current_monthly_price": f"{round(price)} euros",
        "expiry_date_spoken": f"{expiry.day} {expiry.strftime('%B')}" if expiry else "soon",
        "days_left": str((expiry - demo_today()).days) if expiry else "", "partner_name": partner.get("partner", ""),
        "meter_last4": str((unit.get("unit", {}).get("meters") or [{}])[0].get("number") or "")[-4:],
        "offers_summary": offers_summary(partner, price),
        "call_reason": "renewal",
        "language": (unit.get("tenant") or {}).get("language", "en"),
        "customer_status": customer_status(unit) if unit.get("tenant") else "Vacant flat.",
        "renewal_offer": (lambda why: "available" if not why else f"NOT available: {why}")(offer_block_reason(unit)),
        "first_message": greeting or (f"Hi {name.split(' ')[0]}, this is Mia, the digital assistant from reltix, your property manager. "
                          f"I'm calling because your electricity contract with {energy.get('supplier', 'your supplier')} ends on "
                          f"{expiry.day} {expiry.strftime('%B')}. Do you have two minutes?") if expiry else
                         f"Hi {name.split(' ')[0]}, this is Mia, the digital assistant from reltix. Do you have two minutes?",
    }


def phone_ready() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY") and os.environ.get("ELEVENLABS_PHONE_NUMBER_ID"))


def place_phone_call(call: dict, to_number: str) -> tuple[bool, str]:
    """Mia phones a real number through ElevenLabs + Twilio."""
    to_number = re.sub(r"[^+0-9]", "", to_number)
    if not re.fullmatch(r"\+[0-9]{8,15}", to_number):
        return False, "Enter the phone number in international format, e.g. +32470123456."
    unit = read_json(unit_path(call["unit_id"]))
    payload = json.dumps({"agent_id": AGENT_ID, "agent_phone_number_id": os.environ["ELEVENLABS_PHONE_NUMBER_ID"],
                          "to_number": to_number,
                          "conversation_initiation_client_data": {"dynamic_variables": agent_variables(unit)}}).encode("utf-8")
    request = Request("https://api.elevenlabs.io/v1/convai/twilio/outbound-call", data=payload, method="POST",
                      headers={"Content-Type": "application/json", "xi-api-key": os.environ["ELEVENLABS_API_KEY"]})
    try:
        with urlopen(request, timeout=30) as response:
            json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return False, f"ElevenLabs refused the call ({exc.code}): {exc.read().decode('utf-8', 'replace')[:300]}"
    except URLError as exc:
        return False, f"Could not reach ElevenLabs: {exc.reason}"
    calls = read_list(CALLS_FILE, "calls")
    for item in calls:
        if item["call_id"] == call["call_id"]:
            item["channel"] = "phone"
            item["status"] = "ringing"
            item["history"].append({"status": "ringing", "at": now_iso(), "note": "real phone"})
    write_json(CALLS_FILE, {"calls": calls})
    add_event("call", f"Mia is phoning {(unit.get('tenant') or {}).get('name')} on a real phone line.", "phone", call["unit_id"])
    return True, "Calling."


NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60}


def callback_delay_seconds(text: str):
    """'in 10 seconds', 'in five minutes', 'in an hour' -> seconds. None for things like 'tomorrow evening'."""
    t = text.lower().replace("half an hour", "30 minutes")
    m = re.search(r"(\d+|" + "|".join(NUMBER_WORDS) + r")\s*(sec|second|min|minute|hour|hr)", t)
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else NUMBER_WORDS[m.group(1)]
    unit = m.group(2)
    seconds = n * (3600 if unit.startswith("h") else 60 if unit.startswith("min") else 1)
    return max(10, min(seconds, 24 * 3600))


def schedule_callback(unit_id: str, when_text: str):
    """Queue a new call that rings again at the time the resident asked for (if it's a relative time)."""
    delay = callback_delay_seconds(when_text)
    if delay is None:
        return None
    unit = read_json(unit_path(unit_id))
    calls = read_list(mia_calls_file(), "calls")
    for c in calls:  # one pending callback per flat
        if c["unit_id"] == unit_id and c.get("kind") == "callback" and c["status"] == "queued":
            c["status"] = "done"
    due = datetime.now(timezone.utc) + timedelta(seconds=delay)
    due_iso = due.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    calls.append({"call_id": "call_" + uuid.uuid4().hex[:6], "unit_id": unit_id, "tenant": (unit.get("tenant") or {}).get("name"),
                  "case": "callback", "kind": "callback", "status": "queued", "channel": "browser", "urgent": False,
                  "due_at": due_iso, "created_at": now_iso(), "history": [{"status": "queued", "at": now_iso()}]})
    write_json(mia_calls_file(), {"calls": calls})
    return due


def mia_calls_file() -> Path:
    """Mia's calls to residents (renewal, callback, "call now"), kept apart from supplier follow-up calls."""
    return CALLS_FILE.with_name("mia_calls.json")


def next_event_date(after: date):
    """The next demo date on which a renewal window opens (so staff can jump straight to it)."""
    dates = []
    for unit_id in all_unit_ids():
        unit = read_json(unit_path(unit_id))
        if not unit.get("tenant"):
            continue
        for c in unit.get("contracts", []):
            if c.get("type") == "energy_supply" and c.get("status") == "active" and c.get("expiry_date"):
                opens = date.fromisoformat(c["expiry_date"]) - timedelta(days=int(c.get("renewal_notice_days", 30)))
                if opens > after:
                    dates.append(opens)
    return min(dates) if dates else None


def flag(unit_id: str, text: str) -> None:
    """Tell the property manager once (not on every daily run) that a flat needs a person."""
    if not any(e.get("unit_id") == unit_id and e.get("text") == text for e in read_events()):
        add_event("attention", text, "centrix", unit_id)


def run_trigger(source: str = "scheduled") -> dict:
    """Daily centrix check (connector spec §11): open a case when an active energy contract enters its
    renewal window (today >= expiry - renewal_notice_days). Every unit gets a decision and a reason."""
    today = demo_today()
    deliver_requested_remote_readings(today)
    daily_order_follow_ups(today)
    partner = read_partner()
    prefixes = partner.get("eligibility_postcode_prefixes", DEFAULT_POSTCODE_PREFIXES)
    existing_orders = read_orders()
    results = []
    previous = read_json(RUN_FILE).get("results", []) if RUN_FILE.exists() else []
    published = {(r["unit_id"], r["expiry"]) for r in previous if r["decision"] == "matched"}
    for unit_id in all_unit_ids():
        unit = read_json(unit_path(unit_id))
        tenant = unit.get("tenant") or {}
        address = unit.get("property", {}).get("address", "")
        row = {"unit_id": unit_id, "address": address, "tenant": tenant.get("name"), "expiry": None, "days_left": None}

        def decide(decision: str, reason: str) -> None:
            results.append({**row, "decision": decision, "reason": reason})

        def needs_person(log_text: str, reason: str) -> None:
            handled = unit.get("handled")
            if handled and handled.get("reason") == reason:
                decide("skipped", f"Handled by the property team on {handled['at'][:10]}" + (f": {handled['note']}" if handled.get("note") else "."))
                return
            flag(unit_id, log_text)
            decide("attention", reason)

        if not tenant:
            decide("skipped", "Vacant. No resident to contact.")
            continue
        contracts = [c for c in unit.get("contracts", []) if c.get("type") == "energy_supply"]
        active = [c for c in contracts if c.get("status") == "active"]
        energy = active[0] if active else None
        if unit.get("unit", {}).get("usage") == "commercial":
            decide("skipped", "Commercial unit. Household tariffs don't apply; business customers are out of scope.")
            continue
        if energy is None:
            if any(c.get("order_id") or c.get("status") in ("signed", "ending") for c in contracts):
                decide("attention", "No active supplier contract with a reliable end date. Staff must check the supply record.")
            else:
                decide("skipped", "No energy contract on record. Offer at next move-in or move-out.")
            continue
        if not energy.get("expiry_date"):
            needs_person("No end date on file for the electricity contract, so centrix can't time a renewal. Ask the resident for the contract.",
                         "Contract end date is missing. Staff must verify the term before offering renewal.")
            continue
        expiry = date.fromisoformat(energy["expiry_date"])
        notice = int(energy.get("renewal_notice_days", 30))
        row.update(expiry=energy["expiry_date"], days_left=(expiry - today).days)
        if not tenant.get("contract_sharing_opt_in", False):
            decide("skipped", "Resident hasn't opted in to share contract data, so centrix doesn't act on it.")
            continue
        if today < expiry - timedelta(days=notice):
            decide("skipped", f"Too early. Window opens {(expiry - timedelta(days=notice)).isoformat()} ({notice} days before expiry).")
            continue
        if len(active) > 1:
            needs_person(f"Data conflict: {len(active)} active electricity contracts on file ({', '.join(c.get('supplier', '?') for c in active)}). Fix the record before anyone is called.",
                         f"{len(active)} active electricity contracts on file. Data conflict; no call until fixed.")
            continue
        cycle = renewal_cycle(energy)
        open_in_cycle = any(o.get("subject", {}).get("unit_id") == unit_id
                            and order_cycle(o) == cycle
                            and o.get("status") in ("submitted", "received", "pending", "needs_input", "confirmed", "active")
                            for o in existing_orders)
        if open_in_cycle:
            decide("skipped", "Order already in progress.")
            continue
        postcode = re.search(r"\b(\d{5})\b", address)
        if not postcode:
            reason = "In the renewal window, but the postcode is missing or malformed. Staff must verify supplier coverage before ordering."
            decide("attention", reason)
            unit.setdefault("renewal_case", {})["coverage_review"] = {"status": "unknown", "reason": reason, "at": now_iso()}
            unit["renewal_case"]["needs_person"] = reason
            write_json(unit_path(unit_id), unit)
            add_event("coverage_review", reason, "centrix", unit_id)
            continue
        if not any(postcode.group(1).startswith(p) for p in prefixes):
            reason = f"In the window, but no partner serves postcode {postcode.group(1)}. Property manager notified; no order can be placed."
            decide("attention", reason)
            unit.setdefault("renewal_case", {})["coverage_review"] = {"status": "unsupported", "postcode": postcode.group(1), "reason": reason, "at": now_iso()}
            unit["renewal_case"]["needs_person"] = reason
            write_json(unit_path(unit_id), unit)
            add_event("detected", f"Contract ends {energy['expiry_date']}; no partner covers {postcode.group(1)}. Needs a person.", "centrix", unit_id)
            continue
        coverage_review = unit.get("renewal_case", {}).get("coverage_review")
        if coverage_review:
            unit["renewal_case"].pop("coverage_review", None)
            if str(unit.get("renewal_case", {}).get("needs_person", "")).startswith("In the renewal window, but"):
                unit["renewal_case"].pop("needs_person", None)
            write_json(unit_path(unit_id), unit)
        meter = next((m for m in unit.get("unit", {}).get("meters", []) if m.get("type") == "electricity"), {})
        meters = unit.get("unit", {}).get("meters", [])
        if tenant.get("move_out") and date.fromisoformat(tenant["move_out"]) <= expiry:
            needs_person(f"{tenant.get('name')} moves out on {tenant['move_out']}, before the contract ends on {energy['expiry_date']}. Offer the new tariff to the next tenant instead.",
                         f"Resident moves out {tenant['move_out']}, before the contract ends. No renewal call; plan for the next tenant.")
            continue
        if not tenant.get("phone") and not tenant.get("email"):
            needs_person(f"In the renewal window, but there's no phone or email for {tenant.get('name')}. Send a letter or get contact details.",
                         "No phone or email on file. Mia can't reach the resident; letter needed.")
            continue
        if not meter and any(m.get("type") == "electricity_shared" for m in meters):
            needs_person("No individual electricity meter: the flat is supplied through the building's shared meter. Renewal is the landlord's decision.",
                         "Shared building meter, no own meter. Landlord decides; no call.")
            continue
        price_now = float(energy.get("current_monthly_price_eur") or 0)
        cheapest = min((float(o.get("estimated_monthly_price_eur") or 0) for o in partner.get("offers", [])), default=0)
        if cheapest and price_now and price_now <= cheapest:
            decide("skipped", f"Already pays €{price_now:.2f}/month, less than the best partner offer (€{cheapest:.2f}). No call; nothing to gain.")
            continue
        usage = float(energy.get("annual_consumption_kwh") or 0)
        size = float(unit.get("unit", {}).get("size_sqm") or 0)
        if size and usage / size > 100:
            needs_person(f"Unusual usage: {usage:,.0f} kWh a year for {size:.0f} m² ({usage / size:.0f} kWh/m², about 4× normal). Check the meter or electric heating before offering a household tariff.",
                         f"Usage {usage:,.0f} kWh/year for {size:.0f} m² is unusually high. Check before offering a tariff; no call.")
            continue
        notes = []
        if tenant.get("language") == "de":
            notes.append("resident speaks German: Mia calls in German")
        if today > expiry:
            notes.append(f"expired {(today - expiry).days} days ago, likely on a default tariff: urgent")
        if not meter.get("number"):
            notes.append("meter number missing: Mia asks for a meter photo")
        reason = f"Offer available in the tenant portal. Contract ends {energy['expiry_date']}." + (" " + "; ".join(notes) if notes else "")
        case = unit.setdefault("renewal_case", {})
        if case.get("renewal_cycle") != cycle:
            prior_order = next((o for o in reversed(existing_orders) if o["subject"]["unit_id"] == unit_id), {})
            prior_cycle = case.get("renewal_cycle") or order_cycle(prior_order)
            if prior_cycle and prior_cycle != cycle:
                for key in ("chosen_offer_id", "chosen_offer_version", "chosen_offer_snapshot", "declined", "declined_reason"):
                    case.pop(key, None)
            case["renewal_cycle"] = cycle
            write_json(unit_path(unit_id), unit)
        no_calls = tenant.get("contact_preference") == "no_calls"
        reason += " Resident asked not to be called: link sent by SMS." if no_calls else " Mia calls the resident."
        decide("matched", reason)
        if (unit_id, energy["expiry_date"]) not in published:
            add_event("detected", f"Contract ends {energy['expiry_date']}. Renewal offer posted in the tenant portal.", "centrix", unit_id)
        calls = read_list(mia_calls_file(), "calls")
        if not any(c["unit_id"] == unit_id and c.get("case") == energy["expiry_date"] for c in calls):
            calls.append({"call_id": "call_" + uuid.uuid4().hex[:6], "unit_id": unit_id, "tenant": tenant.get("name"), "kind": "renewal",
                          "case": energy["expiry_date"], "status": "sms_sent" if no_calls else "queued",
                          "channel": "sms" if no_calls else "browser", "urgent": today > expiry, "created_at": now_iso(),
                          "history": [{"status": "sms_sent" if no_calls else "queued", "at": now_iso()}]})
            write_json(mia_calls_file(), {"calls": calls})
            add_event("link" if no_calls else "call_queued", "Resident prefers no calls: portal link sent by SMS." if no_calls
                      else f"Outbound call to {tenant.get('name')} queued for Mia.", "sms" if no_calls else "centrix", unit_id)
    # Flags the property team already marked as handled don't come back on every run.
    for r in results:
        if r["decision"] == "attention":
            handled = read_json(unit_path(r["unit_id"])).get("handled") or {}
            if handled.get("reason") == r["reason"]:
                r["decision"] = "skipped"
                r["reason"] = f"Handled by the property team on {handled['at'][:10]}" + (f": {handled['note']}" if handled.get("note") else ".")
    run = {"date": today.isoformat(), "ran_at": now_iso(), "source": source, "scanned": len(results),
           "matched": sum(r["decision"] == "matched" for r in results),
           "attention": sum(r["decision"] == "attention" for r in results), "results": results}
    write_json(RUN_FILE, run)
    return run


def switch_reading_date(requested_start: str) -> date:
    return date.fromisoformat(requested_start) - timedelta(days=1)


def deliver_requested_remote_readings(today: date) -> None:
    """Supply a requested switch reading from the demo remote meter once its read date arrives."""
    orders = read_orders()
    changed = False
    for order in orders:
        if order.get("status") != "needs_input" or order.get("remote_reading_delivered"):
            continue
        request = next((entry for entry in reversed(order.get("status_history", []))
                       if entry.get("status") == "needs_input"), {})
        if not any(word in str(request.get("note", "")).lower() for word in ("meter", "reading")):
            continue
        unit_id = order.get("subject", {}).get("unit_id")
        unit = read_json(unit_path(unit_id))
        meters = electricity_meters(unit)
        selected_id = order.get("meter_id") or order.get("subject", {}).get("meter", {}).get("meter_id")
        meter = next((m for _, m, identity in meters if identity == selected_id), None)
        if meter is None or not meter.get("number"):
            continue
        boundary = switch_reading_date(order["requested_start"])
        sample = meter.get("demo_end_reading") or {}
        switch = order.get("subject", {}).get("meter", {}).get("switch_reading", {})
        if (today < boundary or sample.get("read_at") != boundary.isoformat()
                or sample.get("value_kwh") is None or switch.get("value_kwh") is not None):
            continue
        delivered = {**sample, "source": "simulated_remote_meter"}
        meter["switch_reading"] = delivered
        order["subject"]["meter"].update(meter_id=selected_id, number=meter["number"], switch_reading=delivered)
        order["remote_reading_delivered"] = True
        order["status"] = "received"
        order.setdefault("status_history", []).append({"status": "received", "at": now_iso(), "by": "centrix",
                                                          "note": "Supplier-requested remote switch reading supplied."})
        write_json(unit_path(unit_id), unit)
        add_event("reading", f"Supplier-requested remote reading {sample['value_kwh']:g} kWh received on meter {meter['number']}.",
                  "centrix", unit_id)
        add_event("received", f"Order {order['order_id']} resumed after the remote meter reading arrived.", "centrix", unit_id)
        changed = True
    if changed:
        write_orders(orders)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def offer_version(offer: dict) -> str:
    fields = {key: offer.get(key) for key in ("offer_id", "product", "estimated_monthly_price_eur",
                                                "contract_months", "renewable_share_percent", "requires_meter_reading",
                                                "required_inputs")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def offer_fingerprint(offer: dict) -> str:
    fields = {key: offer.get(key) for key in ("product", "estimated_monthly_price_eur", "contract_months",
                                               "renewable_share_percent", "requires_meter_reading")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def assign_offer_ids(offers: list, previous: list | None = None) -> list:
    """Retain IDs across edits and row reorders; distinguish plans sharing a name and term."""
    previous = [dict(item) for item in (previous or []) if item.get("offer_id")]
    rows = [dict(item) for item in offers]
    used_ids = {item.get("offer_id") for item in rows if item.get("offer_id")}
    unmatched_old = list(previous)
    for row in rows:
        if row.get("offer_id"):
            unmatched_old = [old for old in unmatched_old if old["offer_id"] != row["offer_id"]]
            continue
        match = next((old for old in unmatched_old if offer_fingerprint(old) == offer_fingerprint(row)), None)
        if match:
            row["offer_id"] = match["offer_id"]
            unmatched_old.remove(match)
            used_ids.add(row["offer_id"])
    old_groups: dict[tuple, list] = {}
    new_groups: dict[tuple, list] = {}
    for old in unmatched_old:
        old_groups.setdefault((old.get("product"), old.get("contract_months")), []).append(old)
    for row in rows:
        if not row.get("offer_id"):
            new_groups.setdefault((row.get("product"), row.get("contract_months")), []).append(row)
    for group, new_rows in new_groups.items():
        old_rows = sorted(old_groups.get(group, []), key=lambda item: item["offer_id"])
        for row, old in zip(sorted(new_rows, key=offer_fingerprint), old_rows):
            row["offer_id"] = old["offer_id"]
            used_ids.add(row["offer_id"])
    for row in sorted((item for item in rows if not item.get("offer_id")), key=offer_fingerprint):
        slug = re.sub(r"[^a-z0-9]+", "_", str(row.get("product", "offer")).lower()).strip("_") or "offer"
        stem = f"{slug}_{int(row.get('contract_months') or 0)}m_{offer_fingerprint(row)[:10]}"
        candidate = stem
        suffix = 1
        while candidate in used_ids:
            candidate = f"{stem}_{suffix}"
            suffix += 1
        row["offer_id"] = candidate
        used_ids.add(candidate)
    return rows


def read_partner() -> dict:
    """Load a validated connector profile, retaining compatibility with legacy offers."""
    partner = read_json(OFFERS_FILE)
    normalized = connector_profile(partner)
    if normalized != partner:
        partner = normalized
        write_json(OFFERS_FILE, partner)
    return partner


def renewal_cycle(contract: dict) -> str | None:
    expiry = contract.get("expiry_date") or contract.get("ends")
    contract_id = contract.get("contract_id") or contract.get("contractId")
    return f"{contract_id or 'legacy'}:{expiry}" if expiry else None


def order_cycle(order: dict) -> str | None:
    return order.get("renewal_cycle") or renewal_cycle(order.get("previous_contract", {}))


def postcode_coverage(unit: dict, partner: dict) -> tuple[str, str | None]:
    address = str(unit.get("property", {}).get("address", ""))
    match = re.search(r"(?<!\d)(\d{5})(?!\d)", address)
    if not match:
        return "unknown", None
    postcode = match.group(1)
    prefixes = partner.get("eligibility_postcode_prefixes", DEFAULT_POSTCODE_PREFIXES)
    return ("eligible" if any(postcode.startswith(str(prefix)) for prefix in prefixes) else "unsupported", postcode)


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    day = min(start.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                          31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def close_supplier_follow_up(order: dict, reason: str) -> None:
    call_id = order.get("supplier_follow_up_id")
    if not call_id:
        return
    calls = read_list(CALLS_FILE, "calls")
    changed = False
    for call in calls:
        if call.get("call_id") == call_id and call.get("status") not in ("done", "closed"):
            call["status"] = "done"
            call["closed_at"] = now_iso()
            call["close_reason"] = reason
            call.setdefault("history", []).append({"status": "done", "at": now_iso(), "note": reason})
            changed = True
    if changed:
        write_json(CALLS_FILE, {"calls": calls})


def daily_order_follow_ups(today: date) -> None:
    """Queue one supplier callback per unanswered order and advance switch-day readings."""
    orders = read_orders()
    calls = read_list(CALLS_FILE, "calls")
    changed_orders = False
    changed_calls = False
    for order in orders:
        if order.get("status") not in ("submitted", "received", "pending", "needs_input", "confirmed"):
            continue
        unit_id = order.get("subject", {}).get("unit_id")
        unit = read_json(unit_path(unit_id))
        requested_start = date.fromisoformat(order["requested_start"])
        boundary = switch_reading_date(order["requested_start"])
        reading = order.get("subject", {}).get("meter", {}).get("switch_reading", {})
        if (order.get("status") == "needs_input" and today >= boundary
                and reading.get("value_kwh") is None and not order.get("manual_reading_prompted")):
            order["manual_reading_prompted"] = True
            add_event("reading_requested", f"Please enter the switch reading taken on {boundary.isoformat()}.", "centrix", unit_id)
            changed_orders = True
        callback_date = order.get("callback_date")
        definitive = order.get("status") in ("confirmed", "active", "rejected", "cancelled")
        if definitive:
            close_supplier_follow_up(order, f"Order {order['status']}.")
        elif callback_date and today >= date.fromisoformat(callback_date) and not order.get("supplier_follow_up_id"):
            call_id = "call_" + uuid.uuid4().hex[:6]
            call = {"call_id": call_id, "unit_id": unit_id, "tenant": (unit.get("tenant") or {}).get("name"),
                    "case": "supplier_follow_up", "kind": "supplier_follow_up", "order_id": order["order_id"],
                    "partner": order.get("partner"), "status": "queued", "channel": "simulated supplier call",
                    "due_date": today.isoformat(), "callback_date": callback_date, "owner": "supplier",
                    "created_at": now_iso(), "history": [{"status": "queued", "at": now_iso()}]}
            calls.append(call)
            order["supplier_follow_up_id"] = call_id
            order["supplier_follow_up_queued_at"] = now_iso()
            changed_orders = True
            changed_calls = True
            add_event("supplier_follow_up", f"Supplier callback queued for unanswered order {order['order_id']}.", "centrix", unit_id)
        if not definitive and today > requested_start and not order.get("start_unconfirmed"):
            order["start_unconfirmed"] = True
            case = unit.setdefault("renewal_case", {})
            case["start_unconfirmed"] = True
            case["needs_person"] = "Supplier has not confirmed whether supply started. Property team owns the overdue callback."
            case["updated_at"] = now_iso()
            write_json(unit_path(unit_id), unit)
            add_event("handoff", "Start date passed without supplier confirmation. Property team now owns the callback.", "centrix", unit_id)
            if order.get("supplier_follow_up_id"):
                for call in calls:
                    if call.get("call_id") == order["supplier_follow_up_id"]:
                        call["owner"] = "property team"
                        call["escalated_at"] = now_iso()
                        changed_calls = True
            changed_orders = True
    if changed_orders:
        write_orders(orders)
    if changed_calls:
        closed_ids = {c.get("call_id"): c for c in read_list(CALLS_FILE, "calls") if c.get("status") in ("done", "closed")}
        for call in calls:
            if call.get("call_id") in closed_ids:
                call.update(closed_ids[call["call_id"]])
        write_json(CALLS_FILE, {"calls": calls})


def offer_block_reason(unit: dict) -> str:
    """Why reltix can't arrange a new plan for this flat right now ("" when it can). Mia says this instead of
    promising offers or a confirm link that will never come."""
    unit_id = unit.get("unit_id", "")
    if not unit.get("tenant"):
        return "the flat is vacant"
    if not (unit.get("tenant") or {}).get("contract_sharing_opt_in", False):
        return "the resident has turned off contract sharing, so reltix can't arrange a new contract for them"
    coverage, postcode = postcode_coverage(unit, read_partner())
    if coverage == "unsupported":
        return f"no energy partner of reltix supplies postcode {postcode} yet, so there is no offer and no confirm link; the property manager is looking at other options and will get in touch"
    if coverage == "unknown":
        return "the postcode on file needs checking before any offer can be made; the property manager will get in touch"
    rows = read_json(RUN_FILE)["results"] if RUN_FILE.exists() else []
    row = next((r for r in rows if r["unit_id"] == unit_id), None)
    if row and row["decision"] == "attention":
        return f"{row['reason']} The property manager will get in touch"
    if not portal_offer_available(unit_id):
        return "the contract isn't in its renewal window yet, so there is no offer to confirm right now"
    return ""


def portal_offer_available(unit_id):
    unit = read_json(unit_path(unit_id))
    if not (unit.get("tenant") or {}).get("contract_sharing_opt_in", False):
        return False
    rows = read_json(RUN_FILE)["results"] if RUN_FILE.exists() else []
    active = next((c for c in unit.get("contracts", []) if c.get("type") == "energy_supply" and c.get("status") == "active"), {})
    return any(row["unit_id"] == unit_id and row["decision"] == "matched" and row.get("expiry") == active.get("expiry_date") for row in rows)


def partner_consent_disclosure(offer: dict) -> dict:
    """Describe the fields and standard order metadata visible in the partner workspace."""
    required = list(offer.get("required_inputs", DEFAULT_REQUIRED_INPUTS))
    return {
        "required_fields": [{"key": field, "label": CONNECTOR_FIELD_LABELS.get(field, field)} for field in required],
        "standard_fields": [
            "Unit, order, partner and offer references",
            "Selected offer name, price and contract term",
            "Order status and status timestamps",
            "Consent timestamp and the approved-field list",
            "Contact route through Centrix when a resident name is shared",
            "Any supplier-proposed start-date change if one occurs",
        ],
    }


def reassign_shared_issues_for_order(order: dict, reason: str) -> list[str]:
    """Return open complaints shared with a declined/cancelled supplier to the property team."""
    order_id = order.get("order_id")
    unit_id = order.get("subject", {}).get("unit_id")
    if not order_id or not unit_id:
        return []
    path = unit_path(unit_id)
    unit = read_json(path)
    changed = []
    for issue in unit.get("issues", []):
        if issue.get("shared_order_id") != order_id or issue.get("reply"):
            continue
        at = now_iso()
        history = issue.setdefault("ownership_history", [])
        history.append({"owner": "partner", "partner": order.get("partner"), "order_id": order_id,
                        "ended_at": at, "reason": reason})
        issue["shared_order_id"] = None
        issue["owner"] = "property_team"
        issue["ownership_returned_at"] = at
        issue["ownership_reason"] = reason
        issue["status"] = "reported"
        history.append({"owner": "property_team", "at": at, "reason": reason})
        changed.append(issue.get("issue_id"))
        add_event("issue_returned", f"Supplier complaint {issue.get('issue_id')} returned to the property team: {reason}.",
                  "centrix", unit_id)
    if changed:
        write_json(path, unit)
    return changed


class DemoHandler(BaseHTTPRequestHandler):
    def send_json(self, status: int, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(payload)

    def local_client_only(self) -> bool:
        """Keep this unauthenticated sample app loopback-only, even if rebound later."""
        address = getattr(self, "client_address", ("127.0.0.1",))[0]
        try:
            return ipaddress.ip_address(address).is_loopback
        except ValueError:
            return False

    def same_origin_post(self) -> bool:
        headers = getattr(self, "headers", None) or {}   # unit tests build the handler without headers
        origin = headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            # https is the phone coming through the cloudflared tunnel (launch.py); the host must still match.
            if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != headers.get("Host", "").lower():
                return False
        return headers.get("Sec-Fetch-Site", "same-origin") not in ("cross-site", "none")

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100_000:
            raise ValueError("Request is too large.")
        raw = self.rfile.read(length).decode("utf-8").strip()
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self.local_client_only():
            self.send_json(403, {"error": "This sample app is available on this computer only."})
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/phone":
            unit_id = parse_qs(parsed.query).get("unit", ["unit_4b"])[0]
            self.send_response(302)
            self.send_header("Location", "/tenant?" + urlencode({"unit": unit_id}) + "#complaint-title")
            self.end_headers()
            return
        if path == "/api/state":
            query = parse_qs(parsed.query)
            role = query.get("role", [""])[0]
            following = role == "reltix" and "unit" not in query
            unit_id = query.get("unit", [latest_active_unit() if following else "unit_4b"])[0]
            try:
                unit = read_json(unit_path(unit_id))
                partner = read_partner()
                orders = [o for o in read_orders() if o["subject"]["unit_id"] == unit_id]
                if role == "owner":
                    order = orders[-1] if orders else {}
                    status = order.get("status") or ("declined" if unit.get("renewal_case", {}).get("declined")
                              else "offered" if portal_offer_available(unit_id) else "idle")
                    self.send_json(200, {"unit_id": unit_id, "address": unit["property"]["address"], "status": status,
                                         "start": order.get("requested_start") if status in ("confirmed", "active") else None,
                                         "start_unconfirmed": bool(order.get("start_unconfirmed")),
                                         "start_date_mismatch": order.get("start_date_mismatch"),
                                         "supplier_known": (order.get("status") in ("confirmed", "active") or bool(next((c for c in unit.get("contracts", [])
                                             if c.get("type") == "energy_supply" and c.get("status") in ("active", "ending")
                                             and (not c.get("expiry_date") or c.get("expiry_date") >= demo_today().isoformat())), None)))})
                    return
                if role == "partner":
                    ids = {o["order_id"] for o in orders if o.get("partner_id") == partner.get("partner_id")}
                    visible = []
                    for o in orders:
                        if o["order_id"] in ids:
                            partner_view = partner_order_view(o, partner, unit)
                            partner_view["consent"] = {k: o["consent"].get(k)
                                                       for k in ("data_shared", "channel", "given_at")}
                            visible.append(partner_view)
                    self.send_json(200, {"unit_id": unit_id, "unit": {"unit_id": unit_id}, "partner": {"partner": partner["partner"]},
                                         "orders": visible, "issues": [i for i in unit.get("issues", []) if i.get("shared_order_id") in ids],
                                         "calls": [c for c in read_list(CALLS_FILE, "calls") if c.get("unit_id") == unit_id
                                                   and c.get("kind") == "supplier_follow_up"]})
                    return
                view = view_for(role, unit, partner)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            view["unit_id"] = unit_id
            view["following"] = following
            view["today"] = max(demo_today().isoformat(), (orders[-1].get("handover_date") or "") if orders else "")
            view["offer_available"] = portal_offer_available(unit_id)
            view["partner"]["offers"] = [{**item, "offer_version": offer_version(item),
                                             "consent_disclosure": partner_consent_disclosure(item)}
                                            for item in partner.get("offers", [])]
            if role == "reltix":
                view["data_quality"] = unit_data_quality(unit)
            if role in ("tenant", "reltix"):
                view["issues"] = read_json(unit_path(unit_id)).get("issues", [])
            if role in ("tenant", "reltix"):
                view["case"] = read_json(unit_path(unit_id)).get("renewal_case", {})
            if role == "tenant" and (read_json(unit_path(unit_id)).get("tenant")):
                tenant_unit = read_json(unit_path(unit_id))
                view["agent_variables"] = agent_variables(tenant_unit)
                view["privacy_consents"] = [
                    {key: entry.get(key) for key in ("purpose", "granted", "at", "channel", "recipient", "data_categories")}
                    for entry in tenant_unit.get("privacy_consent_log", [])
                ]
            view["mia_calls"] = [c for c in read_list(mia_calls_file(), "calls") if c["unit_id"] == unit_id]
            calls = [c for c in read_list(CALLS_FILE, "calls") if c["unit_id"] == unit_id]
            view["calls"] = calls
            if role == "tenant":
                # The resident sees their own order's progress, not the partner payload or the staff log.
                decision_orders = orders
                if view["offer_available"]:
                    active = next(c for c in unit["contracts"] if c.get("type") == "energy_supply" and c.get("status") == "active")
                    decision_orders = [o for o in orders if order_cycle(o) == renewal_cycle(active)]
                view["decision_order_id"] = decision_orders[-1]["order_id"] if decision_orders else None
                view["orders"] = [{key: order.get(key) for key in ("order_id", "partner", "product", "requested_start",
                                                                  "status", "status_history", "previous_contract", "reading_required",
                                                                  "meter_id", "start_unconfirmed", "proposed_start_date", "start_date_mismatch",
                                                                  "callback_date", "manual_reading_prompted", "contract_months")}
                                  | {"switch_reading": order["subject"]["meter"]["switch_reading"],
                                     "meter_number": order["subject"]["meter"].get("number")}
                                  for order in orders]
                for tenant_order in view["orders"]:
                    if tenant_order.get("status") == "cancelled":
                        tenant_order["requested_start"] = None
            else:
                view["orders"] = orders
                view["events"] = read_events(unit_id)
            for order in view["orders"]:
                if order.get("requested_start") and order.get("reading_required", True):
                    order["switch_reading_date"] = switch_reading_date(order["requested_start"]).isoformat()
            self.send_json(200, view)
            return
        if path == "/api/version":
            self.send_json(200, {"version": CODE_VERSION, "root": str(ROOT)})
            return
        if path == "/api/tunnel":
            # Written by launch.py when the cloudflared phone link is up.
            tunnel = ROOT / "data" / "tunnel.json"
            fresh = tunnel.exists() and time.time() - tunnel.stat().st_mtime < 20   # launch.py refreshes it every 5 s
            self.send_json(200, {"url": read_json(tunnel).get("url") if fresh else None})
            return
        if path == "/api/portfolio":
            run = read_json(RUN_FILE) if RUN_FILE.exists() else None
            orders = read_orders()
            signed = [o for o in orders if o.get("status") in ("submitted", "received", "needs_input", "confirmed", "active")]
            saving = 0.0
            for o in signed:
                prev = float((o.get("previous_contract") or {}).get("monthly_price_eur") or 0)
                new = float(o.get("estimated_monthly_price_eur") or 0)
                if prev and new:
                    saving += prev - new
            issues = [i for uid in all_unit_ids() for i in read_json(unit_path(uid)).get("issues", [])]
            kpis = {"signed": len(signed), "active": sum(o.get("status") == "active" for o in orders),
                    "monthly_saving_eur": round(saving, 2), "issues_open": sum(not i.get("reply") and not i.get("resolved") for i in issues),
                    "issues_urgent": sum(bool(i.get("urgent")) and not i.get("reply") and not i.get("resolved") for i in issues),
                    "support_calls": len(issues) + sum(s.get("channel") == "phone" for o in orders for s in o["status_history"])
                                     + sum(bool(i.get("reply") and i.get("shared_order_id")) for i in issues)}
            names = {}
            inbox = []
            for uid in all_unit_ids():
                u = read_json(unit_path(uid))
                names[uid] = (u.get("tenant") or {}).get("name") or "Vacant"
                for i in u.get("issues", []):
                    if not i.get("reply") and not i.get("handled") and not i.get("resolved"):
                        inbox.append({"kind": "issue", "unit_id": uid, "tenant": names[uid], "urgent": bool(i.get("urgent")),
                                      "issue_id": i.get("issue_id"), "text": i.get("text"), "at": i.get("reported_at")})
                need = (u.get("renewal_case") or {}).get("needs_person")
                flagged = any(r["unit_id"] == uid and r["decision"] == "attention" and r["reason"] == need for r in (run or {}).get("results", []))
                if need and not flagged and not (u.get("renewal_case") or {}).get("needs_person_handled"):
                    inbox.append({"kind": "needs_person", "unit_id": uid, "tenant": names[uid], "urgent": False, "text": need,
                                  "at": (u.get("renewal_case") or {}).get("updated_at")})
            for r in (run or {}).get("results", []):
                if r["decision"] == "attention":
                    inbox.append({"kind": "flag", "unit_id": r["unit_id"], "tenant": r.get("tenant") or "Vacant", "urgent": False,
                                  "text": r["reason"], "at": (run or {}).get("ran_at")})
            inbox.sort(key=lambda x: (not x["urgent"], x["kind"] != "issue"))
            feed = [{**e, "tenant": names.get(e.get("unit_id"), "")} for e in read_events()
                    if e.get("channel") != "centrix"][-20:]
            nxt = next_event_date(demo_today())
            # Partner board + renewal pipeline (staff dashboard)
            board = []
            for o in orders:
                hist = o.get("status_history") or []
                ouid = (o.get("subject") or {}).get("unit_id")
                board.append({"order_id": o["order_id"], "unit_id": ouid, "tenant": names.get(ouid, ""),
                              "product": o.get("product"), "partner": o.get("partner"), "status": o.get("status"),
                              "requested_start": o.get("requested_start"), "price": o.get("estimated_monthly_price_eur"),
                              "previous_price": (o.get("previous_contract") or {}).get("monthly_price_eur"),
                              "updated_at": hist[-1]["at"] if hist else None, "start_date_mismatch": bool(o.get("start_date_mismatch"))})
            results = (run or {}).get("results", [])
            mia = read_list(mia_calls_file(), "calls")
            chosen = 0
            for uid in all_unit_ids():
                case = read_json(unit_path(uid)).get("renewal_case") or {}
                chosen += bool(case.get("chosen_offer_id"))
            pipeline = {"due": sum(r["decision"] != "skipped" for r in results),
                        "called": len({c["unit_id"] for c in mia if c.get("status") in ("answered", "done")}),
                        "chosen": max(chosen, len({(o.get("subject") or {}).get("unit_id") for o in orders})),
                        "signed": len({(o.get("subject") or {}).get("unit_id") for o in signed}),
                        "active": len({(o.get("subject") or {}).get("unit_id") for o in orders if o.get("status") == "active"})}
            self.send_json(200, {"today": demo_today().isoformat(), "run": run, "orders": board, "pipeline": pipeline, "calls": read_list(CALLS_FILE, "calls"), "mia_calls": read_list(mia_calls_file(), "calls"), "kpis": kpis,
                                 "inbox": inbox, "feed": feed, "next_event": nxt.isoformat() if nxt else None,
                                 "supplier_followups": [c for c in read_list(CALLS_FILE, "calls")
                                                        if c.get("kind") == "supplier_follow_up" and c.get("status") != "done"],
                                 "review_queue": portfolio_review_queue(),
                                 "phone_ready": phone_ready(), "auto_dial_on": bool(os.environ.get("DEMO_PHONE_NUMBER")) and phone_ready()})
            return
        if path.startswith("/api/orders/"):
            # There is no authenticated order-detail API in this local demo. Order
            # details are exposed only through the deliberately projected role views.
            self.send_json(404, {"error": "Use the role-specific demo view for order status."})
            return

        relative = "index.html" if path == "/" else ROLE_PAGES.get(path) or unquote(path.lstrip("/"))
        requested = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in requested.parents and requested != WEB_ROOT.resolve():
            self.send_error(403)
            return
        if not requested.is_file():
            self.send_error(404)
            return
        payload = requested.read_bytes()
        content_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self.local_client_only():
            self.send_json(403, {"error": "This sample app is available on this computer only."})
            return
        if not self.same_origin_post():
            self.send_json(403, {"error": "Cross-origin requests are not allowed."})
            return
        # Case, order and event updates are read/modify/write operations shared by the role pages.
        if urlparse(self.path).path == "/api/chat":
            self.dispatch_post()
            return
        if urlparse(self.path).path == "/api/shutdown":
            # launch.py stops an older copy of the app before starting this one
            self.send_json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        with DATA_LOCK:
            self.dispatch_post()

    def dispatch_post(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self.read_body()
            if path == "/api/reading":
                self.save_switch_reading(body)
            elif path == "/api/meter":
                self.update_meter(body)
            elif path == "/api/meter-identifier":
                self.save_meter_identifier(body)
            elif path == "/api/offers":
                self.save_offers(body)
            elif path == "/api/chat":
                self.chat(body)
            elif path == "/api/orders":
                self.create_order(body)
            elif re.fullmatch(r"/api/orders/[\w-]+/status", path):
                self.update_order_status(path.split("/")[3], body)
            elif path == "/api/events":
                kind = re.sub(r"[^a-z_]", "", str(body.get("type", "note")).lower())[:40] or "note"
                unit_id = str(body.get("unit_id", "unit_4b"))
                add_event(kind, str(body.get("text", "")), str(body.get("channel", "centrix"))[:20], unit_id)
                self.send_json(200, {"ok": True})
            elif path == "/api/case":
                self.update_case(body)
            elif path == "/api/privacy/consent":
                self.update_privacy_consent(body)
            elif path == "/api/clock":
                step = str(body.get("advance", ""))
                if step == "next":
                    today = next_event_date(demo_today())
                    if today is None:
                        raise ValueError("Nothing else is scheduled.")
                elif step in ("day", "week"):
                    today = demo_today() + timedelta(days=1 if step == "day" else 7)
                else:
                    today = date.fromisoformat(str(body.get("today")))
                write_json(CLOCK_FILE, {"today": today.isoformat()})
                self.send_json(200, {"ok": True, "run": run_trigger("date changed")})
            elif path == "/api/handled":
                self.mark_handled(body)
            elif path == "/api/calls":
                self.call_now(body)
            elif path == "/api/run":
                self.send_json(200, {"ok": True, "run": run_trigger("manual")})
            elif re.fullmatch(r"/api/calls/call_[a-z0-9]+/phone", path):
                self.phone_call(path.split("/")[3], body)
            elif re.fullmatch(r"/api/calls/call_[a-z0-9]+", path):
                self.update_call(path.split("/")[3], body)
            elif path == "/api/issues":
                self.report_issue(body)
            elif path == "/api/issues/reply":
                self.reply_to_issue(body)
            elif re.fullmatch(r"/api/reviews/[^/]+", path):
                self.review_task(unquote(path.rsplit("/", 1)[-1]), body)
            elif path == "/api/reset":
                self.reset_demo()
            else:
                self.send_json(404, {"error": "Unknown endpoint."})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.send_json(400, {"error": str(exc) or "Invalid request."})
        except OSError as exc:
            self.send_json(500, {"error": f"Could not save the demo data: {exc}"})

    def save_switch_reading(self, body: dict) -> None:
        value = float(body.get("value_kwh", 0))
        if not math.isfinite(value) or not 0 <= value < 1_000_000_000:
            raise ValueError("Enter a valid meter reading in kWh.")
        reading_date = str(body.get("read_at") or demo_today().isoformat())
        date.fromisoformat(reading_date)

        unit_id = str(body.get("unit_id", "unit_4b"))
        unit = read_json(unit_path(unit_id))
        meters = unit.get("unit", {}).get("meters", [])
        meter = selected_electricity_meter(unit, str(body.get("meter_id") or "") or None)
        if meter is None:
            raise ValueError("Select the electricity meter for this reading.")
        chosen_meter_id = next(identity for _, item, identity in electricity_meters(unit) if item is meter)
        previous = meter.get("last_reading") or {}
        if reading_date < (previous.get("read_at") or "") or value < (previous.get("value_kwh") or 0):
            raise ValueError("The switch reading must follow the last recorded reading.")
        orders = read_orders()
        pending = [o for o in orders if o["subject"]["unit_id"] == unit_id
                   and (o.get("meter_id") or o.get("subject", {}).get("meter", {}).get("meter_id")) == chosen_meter_id
                   and o["status"] in ("submitted", "received", "needs_input", "confirmed")]
        for order in pending:
            boundary = switch_reading_date(order["requested_start"]).isoformat()
            if reading_date != boundary:
                raise ValueError(f"Enter the switch reading taken on {boundary}.")
        meter["meter_id"] = chosen_meter_id
        meter["switch_reading"] = {"value_kwh": value, "read_at": reading_date, "source": str(body.get("source", "portal"))[:20]}
        meter["last_reading"] = {"value_kwh": value, "read_at": reading_date}
        unit.setdefault("field_provenance", {})["meter.last_reading"] = {
            "source": meter["switch_reading"]["source"], "recorded_at": now_iso(),
            "value_hash": _value_hash(meter["last_reading"]),
        }
        write_json(unit_path(unit_id), unit)
        add_event("reading", f"Meter reading {value:g} kWh saved ({meter['switch_reading']['source']}).", meter["switch_reading"]["source"], unit_id)
        # If the partner was waiting for this reading, centrix passes it on and resubmits (spec §6 needs_input → received).
        for order in pending:
            order["subject"]["meter"].update(meter_id=chosen_meter_id, number=meter.get("number"))
            order["subject"]["meter"]["switch_reading"] = meter["switch_reading"]
            if order["status"] == "needs_input":
                order["status"] = "received"
                order["status_history"].append({"status": "received", "at": now_iso(), "by": "centrix",
                                                "note": f"Meter reading {value:g} kWh added and resubmitted"})
                add_event("received", f"Resident added the meter reading ({value:g} kWh). centrix resubmitted order {order['order_id']}.", "portal", unit_id)
        if pending:
            write_orders(orders)
        self.send_json(200, {"ok": True, "meter_id": chosen_meter_id, "switch_reading": meter["switch_reading"]})

    def update_meter(self, body: dict) -> None:
        """Select an electricity meter, correct its identifier, or add a missing meter record."""
        unit_id = str(body.get("unit_id", "unit_4b"))
        unit = read_json(unit_path(unit_id))
        unit_record = unit.setdefault("unit", {})
        meters = unit_record.setdefault("meters", [])
        candidates = electricity_meters(unit)
        selected_id = str(body.get("meter_id") or "").strip()
        selected = next((m for _, m, identity in candidates if identity == selected_id), None) if selected_id else None
        if selected_id and selected is None:
            raise ValueError("That electricity meter is no longer available. Refresh and select it again.")
        if selected is None:
            if len(candidates) > 1:
                raise ValueError("Select which electricity meter you mean.")
            if candidates:
                _, selected, selected_id = candidates[0]
            else:
                selected_id = selected_id or f"{unit_id}:electricity:1"
                selected = {"type": "electricity", "meter_id": selected_id,
                            "switch_reading": {"value_kwh": None, "read_at": None}}
                meters.append(selected)
        selected["meter_id"] = selected_id
        unit_record["selected_electricity_meter_id"] = selected_id
        number = re.sub(r"\s+", "", str(body.get("number") or "").strip())
        if number:
            if not re.fullmatch(r"[A-Za-z0-9-]{5,32}", number):
                raise ValueError("Enter a valid meter number from the meter label (5–32 letters or digits).")
            selected["number"] = number
            unit.setdefault("field_provenance", {})["meter.number"] = {
                "source": str(body.get("source", "tenant portal"))[:40], "recorded_at": now_iso(),
                "value_hash": _value_hash(number),
            }
            orders = read_orders()
            for order in orders:
                if (order.get("subject", {}).get("unit_id") == unit_id
                        and (order.get("meter_id") or order.get("subject", {}).get("meter", {}).get("meter_id")) == selected_id
                        and order.get("status") in ("submitted", "received", "needs_input", "confirmed")):
                    order["subject"]["meter"].update(meter_id=selected_id, number=number)
            write_orders(orders)
            add_event("meter_identifier", f"Meter identifier {number} saved for the selected electricity meter.", "portal", unit_id)
        write_json(unit_path(unit_id), unit)
        self.send_json(200, {"ok": True, "meter_id": selected_id, "number": selected.get("number")})

    def save_meter_identifier(self, body: dict) -> None:
        unit_id = str(body.get("unit_id", "unit_4b"))
        unit = read_json(unit_path(unit_id))
        selected_id = str(body.get("meter_id") or unit.get("unit", {}).get("selected_electricity_meter_id") or "")
        if not selected_id:
            candidates = electricity_meters(unit)
            if len(candidates) == 1:
                selected_id = candidates[0][2]
        self.update_meter({**body, "unit_id": unit_id, "meter_id": selected_id})

    def save_offers(self, body: dict) -> None:
        text = str(body.get("text", "")).strip()
        if not text:
            raise ValueError("Enter at least one offer first.")

        partner = read_partner()
        old_offers = partner.get("offers", [])
        offers = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            columns = [part.strip() for part in line.split("|")]
            if len(columns) != 4:
                raise ValueError(f"Line {line_number}: use Product | monthly price | renewable % | months.")
            product, price_text, renewable_text, months_text = columns
            price = float(price_text.replace(",", ".").replace("€", "").strip())
            renewable = int(renewable_text.rstrip("% "))
            months = int(months_text)
            if not product or price < 0 or not 0 <= renewable <= 100 or months < 1:
                raise ValueError(f"Line {line_number}: check the product, price, renewable share and term.")
            offers.append({
                "product": product,
                "estimated_monthly_price_eur": round(price, 2),
                "contract_months": months,
                "renewable_share_percent": renewable,
                "available_from": None,
                "requires_meter_reading": True,
            })
        if not offers:
            raise ValueError("Enter at least one offer first.")
        saved_offers = assign_offer_ids(offers, old_offers)
        previous_by_id = {item.get("offer_id"): item for item in old_offers}
        for offer in saved_offers:
            previous = previous_by_id.get(offer.get("offer_id"), {})
            for key in ("required_inputs", "eligibility", "availability", "attributes"):
                if key in previous:
                    offer[key] = previous[key]
        partner["offers"] = saved_offers
        write_json(OFFERS_FILE, partner)
        self.send_json(200, {"ok": True, "partner": partner})

    def reset_demo(self) -> None:
        """Restore the sample records so the demo can be run again."""
        write_json(UNIT_FILE, read_json(UNIT_FILE.with_suffix(".seed.json")))
        for seed in PORTFOLIO_SEED.glob("unit_*.json"):
            write_json(PORTFOLIO_DIR / seed.name, read_json(seed))
        for path in (CALLS_FILE, mia_calls_file(), RUN_FILE, CLOCK_FILE, REVIEWS_FILE):
            path.unlink(missing_ok=True)
        if RESET_DATE:
            write_json(CLOCK_FILE, {"today": RESET_DATE.isoformat()})
        write_json(OFFERS_FILE, read_json(OFFERS_FILE.with_suffix(".seed.json")))
        write_orders([])
        write_json(EVENTS_FILE, {"events": []})
        run_trigger("reset")
        self.send_json(200, {"ok": True})

    def review_task(self, review_id: str, body: dict) -> None:
        """Record an auditable human acknowledgement for the current version of an exception."""
        unit_id = str(body.get("unit_id", ""))
        if not unit_id:
            raise ValueError("A unit_id is required to record this review.")
        task = next((item for item in portfolio_review_queue() if item["review_id"] == review_id
                     and item["unit_id"] == unit_id), None)
        if task and task.get("issue_id"):
            raise ValueError("Send a reply to resolve this resident report.")
        if task is None:
            self.send_json(409, {"error": "This review item changed or has already been reviewed. Refresh the queue."})
            return
        note = str(body.get("note", ""))[:300].strip()
        if len(note) < 5:
            raise ValueError("Add a short note about what you checked or did.")
        reviewed_at = now_iso()
        if task["category"] == "provenance":
            provenance = unit_path(unit_id)
            unit = read_json(provenance)
            fields = unit.setdefault("field_provenance", {})
            current_values = {item["field"]: item.get("provenance_value", item.get("value"))
                              for item in unit_data_quality(unit)}
            for field in task.get("fields", []):
                fields[field] = {"source": f"Property team review: {note}"[:100], "verified_at": reviewed_at,
                                 "recorded_at": reviewed_at, "value_hash": _value_hash(current_values.get(field))}
            write_json(provenance, unit)
        if task["category"] in ("flag", "coverage", "case"):
            unit = read_json(unit_path(unit_id))
            unit["handled"] = {"at": reviewed_at, "reason": task["reason"], "note": note}
            if (unit.get("renewal_case") or {}).get("needs_person") == task["reason"]:
                unit["renewal_case"]["needs_person_handled"] = reviewed_at
            write_json(unit_path(unit_id), unit)
        reviews = read_list(REVIEWS_FILE, "reviews")
        record = {"review_id": review_id, "unit_id": unit_id, "status": "reviewed", "note": note,
                  "reviewed_by": "Property team", "reviewed_at": reviewed_at, "category": task["category"]}
        reviews.append(record)
        write_json(REVIEWS_FILE, {"reviews": reviews[-1000:]})
        add_event("staff_review", f"{task['title']}: {note}", "property team", unit_id)
        if task["category"] in ("flag", "coverage", "case"):
            run_trigger("review recorded")   # the flat's row shows it was handled right away
        self.send_json(200, {"review": record})

    def update_case(self, body: dict) -> None:
        """What the resident told Mia or the portal, stored once on the unit so every page shows it."""
        unit_id = str(body.get("unit_id", "unit_4b"))
        path = unit_path(unit_id)
        unit = read_json(path)
        case = unit.setdefault("renewal_case", {})
        source = str(body.get("source", "voice"))[:20]
        changes = []
        partner = read_partner()
        if "chosen_offer_id" in body:
            offer = next((o for o in partner.get("offers", []) if o.get("offer_id") == body["chosen_offer_id"]), None)
            if offer is None:
                raise ValueError("Unknown offer_id.")
            current_version = offer_version(offer)
            if body.get("offer_version") != current_version:
                self.send_json(409, {"error": "This offer changed. Review the current terms before giving consent.",
                                     "stale_offer": True, "offer_version": current_version})
                return
            case["chosen_offer_id"] = offer["offer_id"]
            case["chosen_offer_version"] = current_version
            case["chosen_offer_snapshot"] = {key: offer.get(key) for key in ("offer_id", "product", "estimated_monthly_price_eur",
                                                                               "contract_months", "renewable_share_percent",
                                                                               "requires_meter_reading", "required_inputs")}
            changes.append(f"reviewed {offer.get('product')} at €{float(offer.get('estimated_monthly_price_eur') or 0):.2f} for {offer.get('contract_months')} months")
        if "preferred_channel" in body:
            case["preferred_channel"] = "email" if str(body["preferred_channel"]).lower().startswith("e") else "sms"
            changes.append(f"prefers {case['preferred_channel'].upper()}")
        if "callback" in body:
            case["callback"] = str(body["callback"])[:80]
            due = schedule_callback(unit_id, case["callback"])
            changes.append(f"asked for a callback {case['callback']}" + (f" (Mia calls back at {due.astimezone().strftime('%H:%M:%S')})" if due else ""))
        if "declined" in body:
            case["declined"] = bool(body["declined"])
            case["declined_reason"] = str(body.get("reason", ""))[:80]
            changes.append("declined the offer" if case["declined"] else "reopened the offers")
        if "needs_person" in body:
            case["needs_person"] = str(body["needs_person"])[:160]
            changes.append(f"needs a person: {case['needs_person']}")
        if body.get("note"):
            note = {"text": str(body["note"])[:200], "at": now_iso(), "source": source}
            case.setdefault("notes", []).append(note)
            changes.append(f"note: {note['text']}")
        if not changes:
            raise ValueError("Nothing to update.")
        case["updated_at"] = now_iso()
        write_json(path, unit)
        add_event("case", f"Resident {'; '.join(changes)}.", source, unit_id)
        self.send_json(200, {"case": case})

    def update_privacy_consent(self, body: dict) -> None:
        """Record a resident's contract-sharing preference or per-call voice consent."""
        unit_id = str(body.get("unit_id", "unit_4b"))
        path = unit_path(unit_id)
        unit = read_json(path)
        purpose = str(body.get("purpose", ""))
        granted = body.get("granted")
        if not isinstance(granted, bool):
            raise ValueError("Consent must be true or false.")
        if purpose == "contract_sharing":
            tenant = unit.setdefault("tenant", {})
            tenant["contract_sharing_opt_in"] = granted
            entry = {"purpose": purpose, "granted": granted, "at": now_iso(), "channel": "tenant_portal",
                     "resident_contact_id": tenant.get("contact_id")}
            if not granted:
                case = unit.setdefault("renewal_case", {})
                case.pop("chosen_offer_id", None)
                case.pop("chosen_offer_version", None)
                case.pop("chosen_offer_snapshot", None)
                case["declined"] = False
            message = "Resident enabled contract sharing for renewal checks." if granted else \
                      "Resident withdrew contract sharing for future renewal checks."
        elif purpose == "outbound_phone":
            tenant = unit.setdefault("tenant", {})
            tenant["outbound_phone_opt_in"] = granted
            entry = {"purpose": purpose, "granted": granted, "at": now_iso(), "channel": "tenant_portal",
                     "recipient": "ElevenLabs and Twilio", "data_categories": ["phone number", "resident name",
                         "home address", "current supplier", "energy plan", "monthly price", "expiry date",
                         "last four meter digits", "available offers"],
                     "resident_contact_id": tenant.get("contact_id")}
            message = "Resident enabled outbound AI phone calls." if granted else \
                      "Resident withdrew consent for outbound AI phone calls."
        elif purpose == "live_voice" and granted is True:
            entry = {"purpose": purpose, "granted": True, "at": now_iso(), "channel": "tenant_portal",
                     "recipient": "ElevenLabs", "data_categories": ["microphone audio", "conversation", "resident name",
                         "home address", "energy contract details", "offer details", "last four meter digits"],
                     "resident_contact_id": (unit.get("tenant") or {}).get("contact_id")}
            message = "Resident consented to an ElevenLabs live voice session."
        else:
            raise ValueError("Unknown consent purpose.")
        history = unit.setdefault("privacy_consent_log", [])
        history.append(entry)
        unit["privacy_consent_log"] = history[-500:]
        write_json(path, unit)
        add_event("privacy_consent", message, "tenant_portal", unit_id)
        if purpose == "contract_sharing":
            run_trigger("contract sharing preference changed")
        self.send_json(200, {"consent": entry, "contract_sharing_opt_in":
                             (unit.get("tenant") or {}).get("contract_sharing_opt_in", False),
                             "outbound_phone_opt_in": (unit.get("tenant") or {}).get("outbound_phone_opt_in", False),
                             "offer_available": portal_offer_available(unit_id)})

    def report_issue(self, body: dict) -> None:
        """A resident reports a problem at home (via Mia or the portal). New issue, or update one by issue_id."""
        unit_id = str(body.get("unit_id", "unit_4b"))
        path = unit_path(unit_id)
        unit = read_json(path)
        issues = unit.setdefault("issues", [])
        issue = next((i for i in issues if i["issue_id"] == body.get("issue_id")), None)
        source = str(body.get("source", "voice"))[:20]
        session_id = str(body.get("call_session_id", ""))[:80]
        event_id = str(body.get("client_event_id", ""))[:100]
        event_key = f"{session_id}:{event_id}" if session_id and event_id else ""
        if issue is None and session_id:
            issue = next((i for i in issues if i.get("call_session_id") == session_id), None)
        if issue is None and event_key:
            issue = next((i for i in issues if event_key in i.get("client_event_ids", [])), None)
        if body.get("shared_order_id") and (not issue or not issue.get("shared_order_id")):
            order = next((o for o in read_orders() if o["order_id"] == body["shared_order_id"]
                          and o["subject"]["unit_id"] == unit_id and o.get("consent")
                          and o["status"] not in ("cancelled", "rejected")), None)
            if not order or (issue and issue.get("reply")):
                raise ValueError("Share an open complaint with a supplier on an accepted order for this flat.")
        duplicate_event = bool(issue and event_key and event_key in issue.get("client_event_ids", []))
        if issue is None:
            if body.get("issue_id"):
                raise ValueError("This complaint is no longer on the unit.")
            text = str(body.get("text", "")).strip()[:300]
            if not text:
                raise ValueError("Describe the problem.")
            issue = {"issue_id": "iss_" + uuid.uuid4().hex[:6], "text": text, "urgent": bool(body.get("urgent")),
                     "status": "reported", "source": source, "reported_at": now_iso(), "owner": "property_team",
                     "ownership_history": [{"owner": "property_team", "at": now_iso(), "reason": "Complaint received"}]}
            if session_id:
                issue["call_session_id"] = session_id
            if event_key:
                issue["client_event_ids"] = [event_key]
            issues.append(issue)
            add_event("issue", f"Problem reported: {text}" + (" (urgent)" if issue["urgent"] else ""), source, unit_id)
        elif body.get("resolved"):
            # The resident tells Mia (or the portal) that the problem is fixed.
            note = str(body.get("note", "")).strip()[:200]
            issue["resolved"] = {"at": now_iso(), "by": "resident", "note": note, "source": source}
            issue["status"] = "resolved"
            add_event("issue_resolved", f"Resident says it's fixed: {issue['text'][:80]}" + (f" ({note})" if note else ""), source, unit_id)
        elif not duplicate_event:
            if body.get("text"):
                issue["text"] = f"{issue['text']} {str(body['text']).strip()}"[:600]
            if body.get("urgent") and not issue["urgent"]:
                issue["urgent"] = True
                add_event("issue", f"Marked urgent: {str(body.get('reason') or issue['text'])[:200]}", source, unit_id)
            if session_id and not issue.get("call_session_id"):
                issue["call_session_id"] = session_id
            if event_key:
                issue.setdefault("client_event_ids", []).append(event_key)
        if body.get("shared_order_id") and not issue.get("shared_order_id"):
            issue["shared_order_id"] = body["shared_order_id"]
            issue["owner"] = "partner"
            issue.setdefault("ownership_history", []).append({"owner": "partner", "order_id": body["shared_order_id"],
                "partner": order.get("partner"), "at": now_iso(), "reason": "Tenant chose to share this complaint"})
            add_event("issue_shared", "Tenant asked the agent to call the supplier about this complaint.", "portal", unit_id)
        write_json(path, unit)
        self.send_json(200, {"issue": issue})

    def reply_to_issue(self, body: dict) -> None:
        path = unit_path(str(body.get("unit_id", "unit_4b")))
        unit = read_json(path)
        issue = next((i for i in unit.get("issues", []) if i["issue_id"] == body.get("issue_id")), None)
        if not issue:
            raise ValueError("Unknown complaint for this flat.")
        assigned = "partner" if issue.get("owner") == "partner" or issue.get("shared_order_id") else "reltix"
        if body.get("role") != assigned:
            raise ValueError("This complaint is assigned to another workspace.")
        message = body.get("reply")
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 500:
            raise ValueError("Enter a reply of 1 to 500 characters.")
        if issue.get("reply"):
            self.send_json(409, {"error": "This complaint already has a reply."})
            return
        channel = "phone" if assigned == "partner" else "centrix"
        issue.update(reply=message.strip(), replied_at=now_iso(), reply_channel=channel, status="answered")
        write_json(path, unit)
        add_event("issue_reply", issue["reply"], channel, unit["unit_id"])
        self.send_json(200, {"issue": issue})

    def mark_handled(self, body: dict) -> None:
        """Staff clears an item from "Needs you": a flag from the daily run, a complaint, or a request for a person."""
        unit_id = str(body.get("unit_id", ""))
        path = unit_path(unit_id)
        unit = read_json(path)
        note = str(body.get("note", "")).strip()[:160]
        kind = str(body.get("kind", "flag"))
        if kind == "issue":
            issue = next((i for i in unit.get("issues", []) if i.get("issue_id") == body.get("issue_id")), None)
            if issue is None:
                raise ValueError("Unknown complaint.")
            issue["handled"] = {"at": now_iso(), "note": note}
            text = f"Complaint handled by the property team{': ' + note if note else ''}."
        elif kind == "needs_person":
            unit.setdefault("renewal_case", {})["needs_person_handled"] = now_iso()
            text = f"Request for a person handled{': ' + note if note else ''}."
        else:
            run = read_json(RUN_FILE) if RUN_FILE.exists() else {}
            row = next((r for r in run.get("results", []) if r["unit_id"] == unit_id and r["decision"] == "attention"), None)
            if row is None:
                raise ValueError("Nothing to handle for this flat.")
            unit["handled"] = {"at": now_iso(), "reason": row["reason"], "note": note}
            if (unit.get("renewal_case") or {}).get("needs_person") == row["reason"]:
                unit["renewal_case"]["needs_person_handled"] = now_iso()
            text = f"Flag handled by the property team{': ' + note if note else ''}."
        write_json(path, unit)
        add_event("handled", text, "staff", unit_id)
        if kind == "flag":
            run_trigger("flag handled")
        self.send_json(200, {"ok": True})

    def call_now(self, body: dict) -> None:
        """Staff asks Mia to call a resident right away (rings on the resident's page)."""
        unit_id = str(body.get("unit_id", ""))
        unit = read_json(unit_path(unit_id))
        tenant = unit.get("tenant") or {}
        if not tenant:
            raise ValueError("No resident lives in this flat.")
        calls = read_list(mia_calls_file(), "calls")
        if any(c["unit_id"] == unit_id and c.get("kind") in ("renewal", "callback", "manual") and c["status"] in ("queued", "ringing") for c in calls):
            raise ValueError("Mia is already calling this resident.")
        calls.append({"call_id": "call_" + uuid.uuid4().hex[:6], "unit_id": unit_id, "tenant": tenant.get("name"), "kind": "manual",
                      "case": "manual", "status": "queued", "channel": "browser", "urgent": False, "created_at": now_iso(),
                      "history": [{"status": "queued", "at": now_iso()}]})
        write_json(mia_calls_file(), {"calls": calls})
        add_event("call_queued", f"Staff asked Mia to call {tenant.get('name')} now.", "staff", unit_id)
        self.send_json(200, {"ok": True})

    def update_call(self, call_id: str, body: dict) -> None:
        """The resident's phone/browser reports what happened to an outbound call."""
        status = str(body.get("status", ""))
        if status not in ("ringing", "answered", "missed", "declined", "done"):
            raise ValueError("Unknown call status.")
        source = mia_calls_file()
        calls = read_list(source, "calls")
        call = next((c for c in calls if c["call_id"] == call_id), None)
        if call is None:
            source = CALLS_FILE
            calls = read_list(source, "calls")
            call = next((c for c in calls if c["call_id"] == call_id), None)
        if call is None:
            self.send_json(404, {"error": "Unknown call."})
            return
        if call["status"] != status:
            call["status"] = status
            call["history"].append({"status": status, "at": now_iso()})
            write_json(source, {"calls": calls})
            text = {"answered": f"{call['tenant']} answered Mia's call.", "missed": f"{call['tenant']} didn't answer. SMS link sent instead.",
                    "declined": f"{call['tenant']} declined the call. SMS link sent instead.", "done": "Call ended."}.get(status)
            if text:
                add_event("call", text, "voice", call["unit_id"])
        self.send_json(200, {"call": call})

    def phone_call(self, call_id: str, body: dict) -> None:
        """Optional: Mia calls a real phone (needs ELEVENLABS_API_KEY and ELEVENLABS_PHONE_NUMBER_ID)."""
        call = next((c for c in read_list(CALLS_FILE, "calls") if c["call_id"] == call_id), None)
        if call is None:
            self.send_json(404, {"error": "Unknown call."})
            return
        unit = read_json(unit_path(call["unit_id"]))
        if not unit or not (unit.get("tenant") or {}).get("outbound_phone_opt_in", False):
            self.send_json(403, {"error": "The resident has not enabled outbound AI phone calls."})
            return
        if not phone_ready():
            self.send_json(400, {"error": "Phone calls aren't set up. See README: ELEVENLABS_API_KEY and ELEVENLABS_PHONE_NUMBER_ID."})
            return
        tenant = unit.get("tenant") or {}
        configured_number = str(os.environ.get("DEMO_PHONE_NUMBER") or tenant.get("phone") or "")
        requested_number = str(body.get("to_number") or configured_number)
        normalize_number = lambda value: re.sub(r"[^+0-9]", "", value)
        if not configured_number or normalize_number(requested_number) != normalize_number(configured_number):
            self.send_json(403, {"error": "Outbound calls can only use the resident number on file or the configured demo number."})
            return
        ok, message = place_phone_call(call, configured_number)
        self.send_json(200 if ok else 502, {"ok": ok, "message": message} if ok else {"error": message})

    def create_order(self, body: dict) -> None:
        """centrix places an order after the tenant confirmed (spec §5). Only declared fields are sent."""
        unit_id = str(body.get("unit_id", "unit_4b"))
        unit = read_json(unit_path(unit_id))
        partner = read_partner()
        offer = next((item for item in partner.get("offers", []) if item.get("offer_id") == body.get("offer_id")), None)
        if offer is None:
            raise ValueError("Unknown offer_id.")
        current_version = offer_version(offer)
        if body.get("offer_version") != current_version:
            self.send_json(409, {"error": "This offer changed. Review the current terms and give consent again.",
                                 "stale_offer": True, "offer": {**offer, "offer_version": current_version}})
            return
        case = unit.get("renewal_case", {})
        if case.get("chosen_offer_id") != offer["offer_id"] or case.get("chosen_offer_version") != current_version:
            self.send_json(409, {"error": "Review this offer and its current terms before giving consent.",
                                 "stale_offer": True, "offer": {**offer, "offer_version": current_version}})
            return
        if body.get("consent_given") is not True or body.get("channel", "portal") != "portal":
            raise ValueError("The tenant has not given consent; nothing is shared with the partner.")

        meter = selected_electricity_meter(unit, str(body.get("meter_id") or "") or None)
        available_meters = electricity_meters(unit)
        if not available_meters:
            raise ValueError("No electricity meter is on file. Add the meter information before ordering.")
        if meter is None:
            raise ValueError("Choose which electricity meter this order is for.")
        chosen_meter_id = next(identity for _, item, identity in available_meters if item is meter)
        reading = meter.get("switch_reading") or {}
        coverage, postcode = postcode_coverage(unit, partner)
        if coverage != "eligible":
            if coverage == "unknown":
                detail = "The postcode is missing or malformed. Staff must verify supplier coverage before ordering."
            else:
                detail = f"No supplier covers postcode {postcode}. Staff must review coverage before ordering."
            self.send_json(409, {"error": detail})
            return
        if not (unit.get("tenant") or {}).get("contract_sharing_opt_in") or not portal_offer_available(unit_id):
            raise ValueError("No renewal offer is due for this flat.")
        energy = next((item for item in unit.get("contracts", []) if item.get("type") == "energy_supply" and item.get("status") == "active"), {})
        expiry = energy.get("expiry_date")
        cycle = renewal_cycle(energy)
        orders = read_orders()
        submission_id = str(body.get("submission_id", ""))[:64]
        if submission_id:
            retry = next((item for item in orders if item.get("submission_id") == submission_id
                          and item.get("subject", {}).get("unit_id") == unit_id), None)
            if retry:
                self.send_json(200, {"order": retry, "replayed": True})
                return
        duplicate = next((item for item in orders if item.get("subject", {}).get("unit_id") == unit_id
                          and order_cycle(item) == cycle
                          and item.get("status") in ("submitted", "received", "pending", "needs_input", "confirmed", "active")), None)
        if duplicate:
            raise ValueError("An order for this renewal is already on file.")
        # Day after the old contract ends; if it already ended (urgent case), the next day from today.
        requested_start = max(date.fromisoformat(expiry) + timedelta(days=1), demo_today() + timedelta(days=1)).isoformat() if expiry else None
        boundary = switch_reading_date(requested_start).isoformat()
        if reading.get("value_kwh") is None and meter.get("last_reading", {}).get("read_at") == boundary:
            reading = meter["last_reading"]
        if reading.get("read_at") != boundary:
            reading = {}
        tenant = (unit.get("tenant") or {})
        meter["meter_id"] = chosen_meter_id
        unit.setdefault("unit", {})["selected_electricity_meter_id"] = chosen_meter_id
        write_json(unit_path(unit_id), unit)
        channel = str(body.get("channel") or "portal")
        data_shared = list(offer.get("required_inputs", DEFAULT_REQUIRED_INPUTS))
        given_at = now_iso()
        callback_date = (demo_today() + timedelta(days=1)).isoformat()

        order_id = "ord_" + uuid.uuid4().hex[:6]
        order = {
            "order_id": order_id,
            "partner_id": partner.get("partner_id"),
            "partner": partner.get("partner"),
            "offer_id": offer["offer_id"],
            "offer_version": current_version,
            "offer_snapshot": {key: offer.get(key) for key in ("offer_id", "product", "estimated_monthly_price_eur",
                                                                 "contract_months", "renewable_share_percent", "requires_meter_reading",
                                                                 "required_inputs")},
            "submission_id": submission_id or None,
            "renewal_cycle": cycle,
            "contract_months": int(offer.get("contract_months") or 0),
            "product": offer.get("product"),
            "estimated_monthly_price_eur": offer.get("estimated_monthly_price_eur"),
            "kind": "sell",
            "requested_start": requested_start,
            "meter_id": chosen_meter_id,
            "reading_required": offer.get("requires_meter_reading", True),
            "subject": {
                "unit_id": unit.get("unit_id"),
                "address": unit.get("property", {}).get("address"),
                "tenant": {"name": tenant.get("name"), "contact_via": "centrix"},
                "meter": {"type": "electricity", "meter_id": chosen_meter_id, "number": meter.get("number"),
                          "switch_reading": ({**reading, "source": "tenant_entry"} if reading.get("value_kwh") is not None
                                             else {"value_kwh": None, "note": "sent on the switch date"})},
            },
            "previous_contract": {"contract_id": energy.get("contract_id"), "supplier": energy.get("supplier"),
                                  "start": energy.get("start"), "ends": expiry,
                                  "status_before_replacement": energy.get("status"),
                                  "cancellation_by": "new_supplier",
                                  "monthly_price_eur": energy.get("current_monthly_price_eur")},
            "consent": {"data_shared": data_shared, "approved_fields": data_shared, "recipient": partner.get("partner"),
                        "offer_id": offer["offer_id"],
                        "offer_version": current_version, "offer_snapshot": {key: offer.get(key) for key in ("offer_id", "product", "estimated_monthly_price_eur",
                                                                                                                "contract_months", "renewable_share_percent",
                                                                                                                "required_inputs")},
                        "given_at": given_at, "channel": channel,
                        "given_by": tenant.get("contact_id"), "signed_name": str(body.get("signed_name", ""))[:100],
                        "conversation": str(body.get("session_id", ""))[:40]},
            "status": "submitted",
            "callback_date": callback_date,
            "status_history": [{"status": "submitted", "at": now_iso(), "by": "centrix"}],
        }
        orders.append(order)
        write_orders(orders)
        consent_log = unit.setdefault("consent_log", [])
        if not any(entry.get("order_id") == order_id for entry in consent_log):
            consent_log.append({"order_id": order_id, "partner": partner.get("partner"), **order["consent"]})
        write_json(unit_path(unit_id), unit)
        add_event("consent", f"{tenant.get('name')} gave consent and signed for {offer.get('product')} ({channel}). "
                  f"Shared with {partner.get('partner')}: {', '.join(data_shared)}.", channel, unit_id)
        add_event("order", f"Order {order_id} sent to {partner.get('partner')}, start {requested_start}.", "centrix", unit_id)
        self.send_json(201, {"order": order})

    def update_order_status(self, order_id: str, body: dict) -> None:
        """Partner StatusUpdate (spec §6), sent from the partner inbox (stand-in for a call or webhook)."""
        new_status = str(body.get("status", ""))
        orders = read_orders()
        order = next((item for item in orders if item.get("order_id") == order_id), None)
        if order is None:
            self.send_json(404, {"error": "Unknown order."})
            return
        if new_status == "pending" and order["status"] in ("submitted", "received", "pending"):
            if order["status_history"][-1].get("status") != "pending":
                order["status_history"].append({"status": "pending", "by": order["partner_id"], "channel": "phone",
                                               "at": now_iso(), "note": "Supplier is checking the start date."})
                order["status"] = "pending"
                order.setdefault("callback_date", (demo_today() + timedelta(days=1)).isoformat())
                write_orders(orders)
                add_event("pending", "Supplier is checking the start date.", "phone", order["subject"]["unit_id"])
            self.send_json(200, {"order": order})
            return
        if new_status not in TRANSITIONS.get(order["status"], set()):
            self.send_json(409, {"error": f"Cannot move an order from {order['status']} to {new_status}."})
            return
        entry = {"status": new_status, "at": now_iso(), "by": order.get("partner_id"), "channel": "phone"}
        note = str(body.get("note", ""))[:200]
        if note:
            entry["note"] = note
        if new_status == "active":
            unit = read_json(unit_path(order["subject"]["unit_id"]))
            selected_id = order.get("meter_id") or order.get("subject", {}).get("meter", {}).get("meter_id")
            meter = next((m for _, m, identity in electricity_meters(unit) if identity == selected_id), None)
            if meter is None or not meter.get("number"):
                raise ValueError("The electricity meter identifier is missing. Add or correct it before activating supply.")
            order["subject"]["meter"].update(meter_id=selected_id, number=meter["number"])
            if order.get("reading_required", True) and order["subject"]["meter"].get("switch_reading", {}).get("value_kwh") is None:
                raise ValueError("Waiting for the selected meter’s switch reading before completing the handover.")
        if new_status == "confirmed":
            start = date.fromisoformat(str(body.get("start_date") or order.get("requested_start")))
            requested_start = date.fromisoformat(order["requested_start"])
            if start != requested_start:
                proposed = start.isoformat()
                mismatch = order.get("start_date_mismatch") or {}
                if mismatch.get("requested") != requested_start.isoformat() or mismatch.get("proposed") != proposed:
                    order.setdefault("start_date_mismatch_history", []).append({
                        "requested": requested_start.isoformat(), "proposed": proposed, "at": now_iso(),
                    })
                    order["start_date_mismatch"] = {
                        "requested": requested_start.isoformat(),
                        "proposed": proposed,
                        "at": now_iso(),
                    }
                    order["proposed_start_date"] = proposed
                    write_orders(orders)
                    add_event("start_date_mismatch",
                              f"Supplier proposed {proposed}; requested start remains {requested_start.isoformat()}. Neither date is confirmed.",
                              "phone", order["subject"]["unit_id"])
                self.send_json(409, {
                    "error": (f"Start date mismatch: you requested {requested_start.isoformat()}, but the supplier proposed {proposed}. "
                              "Neither date is confirmed. The supplier can decline or confirm the requested date."),
                    "start_date_mismatch": True,
                    "requested_start": requested_start.isoformat(),
                    "proposed_start": proposed,
                })
                return
            if start < demo_today() and body.get("supply_started") is not True:
                raise ValueError("Confirm that supply actually began on this past date before recording it.")
            order.pop("proposed_start_date", None)
            order.pop("start_date_mismatch", None)
            order["start_unconfirmed"] = False
            entry["result"] = {"contract_number": "EGE-" + order_id[-6:].upper(), "start": start.isoformat(),
                               "monthly_estimate_eur": order.get("estimated_monthly_price_eur"),
                               "contract_months": order.get("contract_months")}
            if start < demo_today():
                entry["supply_started_confirmed"] = True
            unit = read_json(unit_path(order["subject"]["unit_id"]))
            selected_id = order.get("meter_id") or order.get("subject", {}).get("meter", {}).get("meter_id")
            meter = next((m for _, m, identity in electricity_meters(unit) if identity == selected_id), None)
            if meter is None:
                raise ValueError("The selected electricity meter is no longer on file.")
            order["subject"]["meter"].update(meter_id=selected_id, number=meter.get("number"))
            order["handover_date"] = switch_reading_date(start.isoformat()).isoformat()
        if new_status == "active":
            order["handover_date"] = order["requested_start"]
        order["status_history"].append(entry)
        order["status"] = new_status
        write_orders(orders)
        messages = {
            "received": f"{order.get('partner')} accepted order {order_id}.",
            "needs_input": f"{order.get('partner')} needs more info: {note or 'meter reading'}. Asked the resident.",
            "rejected": f"{order.get('partner')} declined order {order_id}{': ' + note if note else ''}. Resident will be offered other plans.",
            "confirmed": f"{order.get('partner')} confirmed the contract. Resident told: starts {order.get('requested_start')}.",
            "active": f"Supply is running with {order.get('partner')}. Landlord sent a one-line summary.",
            "cancelled": f"Order {order_id} cancelled.",
        }
        add_event(new_status, messages.get(new_status, new_status), "phone", order["subject"]["unit_id"])
        if new_status in ("rejected", "cancelled"):
            reassign_shared_issues_for_order(order, f"Supplier {new_status} the order.")
        if new_status in ("confirmed", "active"):
            DemoHandler.write_back(order)
        elif new_status == "cancelled":
            DemoHandler.write_back(order)
        if new_status in ("confirmed", "rejected", "cancelled"):
            close_supplier_follow_up(order, f"Supplier gave a definitive {new_status} answer.")
        if new_status == "confirmed":
            unit_path_value = unit_path(order["subject"]["unit_id"])
            unit_record = read_json(unit_path_value)
            case = unit_record.setdefault("renewal_case", {})
            case.pop("start_unconfirmed", None)
            if case.get("needs_person", "").startswith("Supplier has not confirmed whether supply started"):
                case.pop("needs_person", None)
            case["updated_at"] = now_iso()
            write_json(unit_path_value, unit_record)
        if new_status == "rejected":
            run_trigger("supplier rejected")
        if new_status == "cancelled":
            run_trigger("supplier cancelled")
        self.send_json(200, {"order": order})

    @staticmethod
    def write_back(order: dict) -> None:
        """Close the loop (spec §6): file the new contract and the consent log on the unit."""
        path = unit_path(order["subject"]["unit_id"])
        unit = read_json(path)
        reading = order["subject"]["meter"]["switch_reading"]
        if reading.get("source") == "simulated_remote_meter":
            selected_id = order.get("meter_id") or order.get("subject", {}).get("meter", {}).get("meter_id")
            for index, meter in enumerate(unit["unit"]["meters"]):
                if meter.get("type") == "electricity" and meter_id(unit.get("unit_id", "unit_4b"), meter, index) == selected_id:
                    meter["switch_reading"] = dict(reading)
        contracts = unit.setdefault("contracts", [])
        existing = next((item for item in contracts if item.get("order_id") == order["order_id"]), None)
        if existing:
            if order["status"] == "cancelled":
                existing["status"] = "cancelled"
                existing["cancelled_at"] = now_iso()
                for item in contracts:
                    if item.get("replaced_by_order") == order["order_id"]:
                        item.pop("replaced_by_order", None)
                        prior = order.get("previous_contract", {})
                        if prior.get("contract_id") and item.get("contract_id") != prior.get("contract_id"):
                            continue
                        expiry = item.get("expiry_date") or prior.get("ends")
                        start = item.get("start") or prior.get("start")
                        today = demo_today()
                        if expiry and date.fromisoformat(expiry) < today:
                            item["status"] = "ended"
                        elif start and date.fromisoformat(start) > today:
                            item["status"] = "signed"
                        else:
                            item["status"] = prior.get("status_before_replacement") or "active"
                write_json(path, unit)
                add_event("filed", f"Cancellation for {existing.get('contract_id')} filed; current supply requires date-based review.", "centrix", order["subject"]["unit_id"])
                return
            if order["status"] == "active":
                existing["status"] = "active"
                for item in contracts:
                    if item.get("replaced_by_order") == order["order_id"]:
                        item["status"] = "ended"
                write_json(path, unit)
                add_event("filed", f"Contract {existing.get('contract_id')} marked active on the unit record.", "centrix", order["subject"]["unit_id"])
            return
        result = next((item.get("result") for item in order["status_history"] if item.get("result")), {})
        for item in contracts:
            if item.get("type") == "energy_supply" and item.get("status") == "active":
                item["status"] = "ending"
                item["replaced_by_order"] = order["order_id"]
        contracts.append({
            "type": "energy_supply",
            "category": "electricity",
            "contract_id": result.get("contract_number"),
            "order_id": order["order_id"],
            "customer_contact_id": order["consent"]["given_by"],
            "supplier": order.get("partner"),
            "product": order.get("product"),
            "status": "signed",
            "start": result.get("start"),
            "expiry_date": (add_months(date.fromisoformat(result["start"]), int(order.get("contract_months") or result.get("contract_months") or 0)) - timedelta(days=1)).isoformat()
                if result.get("start") and int(order.get("contract_months") or result.get("contract_months") or 0) > 0 else None,
            "contract_months": int(order.get("contract_months") or result.get("contract_months") or 0) or None,
            "renewal_notice_days": 30,
            "current_monthly_price_eur": result.get("monthly_estimate_eur"),
        })
        consent_log = unit.setdefault("consent_log", [])
        if not any(item.get("order_id") == order["order_id"] for item in consent_log):
            consent_log.append({"order_id": order["order_id"], "partner": order.get("partner"), **order["consent"]})
        write_json(path, unit)
        add_event("filed", f"Signed contract {result.get('contract_number')} and consent log filed on unit {unit.get('unit_id')}.", "centrix", unit.get("unit_id"))

    def chat(self, body: dict) -> None:
        history = body.get("messages", [])
        if not isinstance(history, list):
            raise ValueError("Chat messages must be a list.")
        recent_history = [
            {"role": item.get("role"), "content": str(item.get("content", ""))[:1200]}
            for item in history[-16:]
            if isinstance(item, dict) and item.get("role") in ("user", "assistant")
        ]
        history = []
        remaining_characters = 6000
        for item in reversed(recent_history):
            if len(item["content"]) > remaining_characters and history:
                break
            history.append(item)
            remaining_characters -= len(item["content"])
        history.reverse()
        if not history or history[-1]["role"] != "user":
            raise ValueError("Send a user message first.")

        tenant_view = view_for("tenant", read_json(unit_path(str(body.get("unit_id", "unit_4b")))), read_partner())
        context = {"unit": tenant_view["unit"], "energy_partner": tenant_view["partner"]}
        unit = context["unit"]
        partner = context["energy_partner"]
        energy = next((item for item in unit.get("contracts", []) if item.get("type") == "energy_supply"), {})
        lease = next((item for item in unit.get("contracts", []) if item.get("type") == "lease"), {})
        meter = next((item for item in unit.get("unit", {}).get("meters", []) if item.get("type") == "electricity"), {})
        facts = {
            "home": {
                "unit_id": unit.get("unit_id"),
                "address": unit.get("property", {}).get("address"),
                "building_year": unit.get("property", {}).get("building_year"),
                "heating": unit.get("property", {}).get("heating"),
                "floor": unit.get("unit", {}).get("floor"),
                "size_sqm": unit.get("unit", {}).get("size_sqm"),
                "rooms": unit.get("unit", {}).get("rooms"),
            },
            "tenant": (unit.get("tenant") or {}),
            "owner": unit.get("owner", {}),
            "lease": lease,
            "electricity_contract": energy,
            "electricity_meter": meter,
            "energy_partner": partner,
        }
        system_message = (
            "You are a concise property and energy assistant. Answer the user's question directly in 1-4 short sentences. "
            "The facts below are the complete records available to you. Use them to answer questions about the home, tenant, "
            "owner, lease, electricity contract, meter, and energy offers. If asked to show or summarize the contract, list its "
            "provider, product, status, start date, expiry date, monthly price, and annual usage when present. Never say you "
            "cannot access a record that is present below. Do not invent missing values. If a value is absent, say it is not "
            "listed. Offer prices are estimates. You cannot place orders, sign contracts, contact people, or change records. "
            "The conversation messages after the facts are part of the current chat. Resolve short follow-up questions such as "
            "'when?', 'which one?', or 'what about it?' from that conversation before asking for clarification.\n\n"
            "CURRENT JSON FACTS:\n" + json.dumps(facts, ensure_ascii=False, indent=2)
        )
        payload = json.dumps({
            "model": OLLAMA_MODEL,
            "stream": False,
            "messages": [{"role": "system", "content": system_message}, *history],
            "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 220},
        }).encode("utf-8")
        request = Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            self.send_json(502, {"error": f"Ollama could not answer ({exc.code}): {detail}"})
            return
        except URLError as exc:
            self.send_json(503, {"error": "Local model is not connected. Start Ollama and install qwen2.5:0.5b to enable LLM replies."})
            return
        answer = result.get("message", {}).get("content", "").strip()
        if not answer:
            self.send_json(502, {"error": "The local model returned an empty answer."})
            return
        self.send_json(200, {"answer": answer, "model": OLLAMA_MODEL, "mode": "ollama"})

    @staticmethod
    def lookup_answer(question: str, context: dict) -> str | None:
        text = question.casefold()
        unit = context["unit"]
        partner = context["energy_partner"]
        property_record = unit.get("property", {})
        unit_record = unit.get("unit", {})
        tenant = (unit.get("tenant") or {})
        owner = unit.get("owner", {})
        contracts = unit.get("contracts", [])
        energy = next((item for item in contracts if item.get("type") == "energy_supply"), {})
        lease = next((item for item in contracts if item.get("type") == "lease"), {})
        meter = next((item for item in unit_record.get("meters", []) if item.get("type") == "electricity"), {})

        if any(word in text for word in ("offer", "tariff", "plans")):
            offers = partner.get("offers", [])
            if not offers:
                return "There are no energy offers in the partner records yet."
            lines = [f"{partner.get('partner', 'Energy partner')} offers:"]
            for offer in offers:
                lines.append(
                    f"• {offer.get('product', 'Unnamed offer')}: €{float(offer.get('estimated_monthly_price_eur', 0)):.2f}/month, "
                    f"{offer.get('renewable_share_percent', '—')}% renewable, {offer.get('contract_months', '—')} months."
                )
            return "\n".join(lines)

        if "meter" in text or "reading" in text or "kwh" in text:
            reading = meter.get("switch_reading", {})
            if reading.get("value_kwh") is not None:
                return f"The saved changeover reading is {reading['value_kwh']} kWh, recorded on {reading.get('read_at', 'a date not listed')}."
            previous = meter.get("last_reading", {})
            return (
                f"No changeover reading has been entered for meter {meter.get('number', 'not listed')}. "
                f"The last known reading is {previous.get('value_kwh', 'not listed')} kWh from {previous.get('read_at', 'a date not listed')}."
            )

        if "lease" in text or "rent" in text:
            if not lease:
                return "Lease details are not listed in the unit records."
            return (
                f"Lease for unit {unit.get('unit_id', 'not listed')}: €{float(lease.get('rent_cold_eur', 0)):.2f} cold rent per month, "
                f"starting {lease.get('start', 'a date not listed')}."
            )

        if any(word in text for word in ("contract", "agreement", "supplier", "provider")):
            if not energy:
                return "No energy supply contract is listed for this unit."
            details = [
                f"Your {energy.get('category', 'energy')} supply contract is with {energy.get('supplier', 'a provider not listed')}.",
                f"Product: {energy.get('product', 'not listed')}.",
                f"Status: {energy.get('status', 'not listed')}.",
                f"Start: {energy.get('start', 'not listed')}; expiry: {energy.get('expiry_date', 'not listed')}.",
            ]
            if energy.get("current_monthly_price_eur") is not None:
                details.append(f"Current monthly price: €{float(energy['current_monthly_price_eur']):.2f}.")
            if energy.get("annual_consumption_kwh") is not None:
                details.append(f"Annual usage on record: {energy['annual_consumption_kwh']} kWh.")
            return "\n".join(details)

        if any(word in text for word in ("tenant", "resident", "who lives")):
            return (
                f"The tenant record is {tenant.get('name', 'not listed')}, moved in {tenant.get('move_in', 'on a date not listed')}. "
                f"Contact: {tenant.get('email', 'email not listed')}."
            )

        if "owner" in text or "landlord" in text:
            return f"The owner record lists {owner.get('name', 'no owner name')}."

        if any(word in text for word in ("property", "home", "address", "rooms", "heating", "unit")):
            return (
                f"Unit {unit.get('unit_id', 'not listed')} is at {property_record.get('address', 'an address not listed')}. "
                f"It has {unit_record.get('rooms', 'an unlisted number of')} rooms and is {unit_record.get('size_sqm', 'an unlisted size')} m²; "
                f"the building uses {property_record.get('heating', 'an unlisted heating system')}."
            )
        return None

    def log_request(self, code: object = "-", size: object = "-") -> None:
        # Pages poll every few seconds. Printing every request floods the Windows console, and while
        # someone selects text in that window, printing blocks and the whole app stops answering.
        if str(code).isdigit() and int(code) < 400:
            return
        super().log_request(code, size)

    def log_message(self, format: str, *args: object) -> None:
        try:
            print(f"{self.address_string()} - {format % args}", flush=True)
        except Exception:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--demo-date", type=date.fromisoformat, help="Demo day to start on and to return to on reset (default 2026-11-20)")
    args = parser.parse_args()
    # A fixed demo calendar, so the scenarios in DEMO.md happen on the same dates whatever day we present.
    RESET_DATE = args.demo_date or DEFAULT_DEMO_DATE
    if args.demo_date or not CLOCK_FILE.exists():
        write_json(CLOCK_FILE, {"today": RESET_DATE.isoformat()})
    run = run_trigger("server start")
    print(f"Demo date {run['date']}: {run['scanned']} flats checked, {run['matched']} renewals (Mia calls), {run['attention']} need a person.")
    server = ThreadingHTTPServer((HOST, args.port), DemoHandler)
    print(f"Reltix demo is running at http://{HOST}:{server.server_port}")
    print("Press Ctrl+C to stop it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Reltix demo.")
    finally:
        server.server_close()
