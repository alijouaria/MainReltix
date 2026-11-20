"""Local Reltix demo server. Uses only the Python standard library."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, unquote, urlparse


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
# Allowed partner status moves for a `sell` partner (connector spec §6). Anything else returns 409.
TRANSITIONS = {
    "submitted": {"received", "rejected"},
    "received": {"needs_input", "confirmed", "rejected"},
    "needs_input": {"received"},
    "confirmed": {"active", "cancelled"},
}
HOST = "127.0.0.1"
PORT = 8765
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:0.5b"


ROLE_PAGES = {"/tenant": "tenant.html", "/reltix": "reltix.html"}


def view_for(role: str, unit: dict, partner: dict) -> dict:
    """Return only the records a role may see (data minimisation per connector spec)."""
    contracts = unit.get("contracts", [])
    energy = next((c for c in contracts if c.get("type") == "energy_supply"), {})
    if role == "reltix":
        return {"unit": unit, "partner": partner}
    if role == "tenant":
        return {
            "unit": {
                "unit_id": unit.get("unit_id"),
                "property": unit.get("property", {}),
                "unit": unit.get("unit", {}),
                "owner": {"name": unit.get("owner", {}).get("name")},
                "tenant": (unit.get("tenant") or {}),
                "contracts": [c for c in contracts if c.get("customer_contact_id", (unit.get("tenant") or {}).get("contact_id")) == (unit.get("tenant") or {}).get("contact_id")],
            },
            "partner": partner,
        }
    if role == "partner":
        meters = [m for m in unit.get("unit", {}).get("meters", []) if m.get("type") == "electricity"]
        return {
            "unit": {
                "unit_id": unit.get("unit_id"),
                "property": {"address": unit.get("property", {}).get("address")},
                "unit": {"meters": [{"type": m.get("type"), "number": m.get("number"), "switch_reading": m.get("switch_reading")} for m in meters]},
                "tenant": {"name": (unit.get("tenant") or {}).get("name")},
                "contracts": [{"type": "energy_supply", "supplier": energy.get("supplier"), "expiry_date": energy.get("expiry_date")}] if energy else [],
            },
            "partner": partner,
        }
    raise ValueError("Unknown role. Use tenant or reltix.")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def add_event(kind: str, text: str, channel: str = "centrix", unit_id: str = "unit_4b") -> None:
    events = read_events()
    events.append({"type": kind, "text": text[:300], "channel": channel, "at": now_iso(), "unit_id": unit_id})
    write_json(EVENTS_FILE, {"events": events[-500:]})


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


def agent_variables(unit: dict) -> dict:
    energy = next((c for c in unit.get("contracts", []) if c.get("type") == "energy_supply" and c.get("status") == "active"), {})
    name = (unit.get("tenant") or {}).get("name", "")
    expiry = date.fromisoformat(energy["expiry_date"]) if energy.get("expiry_date") else None
    partner = read_json(OFFERS_FILE)
    price = float(energy.get("current_monthly_price_eur") or 0)
    return {
        "tenant_first_name": name.split(" ")[0], "tenant_last_name": " ".join(name.split(" ")[1:]),
        "full_address": unit.get("property", {}).get("address", ""), "current_supplier": energy.get("supplier", "your supplier"),
        "current_product": energy.get("product", ""), "current_monthly_price": f"{round(price)} euros",
        "expiry_date_spoken": f"{expiry.day} {expiry.strftime('%B')}" if expiry else "soon",
        "days_left": str((expiry - demo_today()).days) if expiry else "", "partner_name": partner.get("partner", ""),
        "meter_last4": str((unit.get("unit", {}).get("meters") or [{}])[0].get("number") or "")[-4:],
        "offers_summary": offers_summary(partner, price),
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


def run_trigger(source: str = "scheduled") -> dict:
    """Daily centrix check (connector spec §11): open a case when an active energy contract enters its
    renewal window (today >= expiry - renewal_notice_days). Every unit gets a decision and a reason."""
    today = demo_today()
    partner = read_json(OFFERS_FILE)
    prefixes = partner.get("eligibility_postcode_prefixes", DEFAULT_POSTCODE_PREFIXES)
    open_orders = {o.get("subject", {}).get("unit_id") for o in read_orders() if o.get("status") in ("submitted", "received", "needs_input", "confirmed")}
    calls = read_list(CALLS_FILE, "calls")
    results = []
    results_started = now_iso()
    for unit_id in all_unit_ids():
        unit = read_json(unit_path(unit_id))
        tenant = unit.get("tenant") or {}
        address = unit.get("property", {}).get("address", "")
        row = {"unit_id": unit_id, "address": address, "tenant": tenant.get("name"), "expiry": None, "days_left": None}

        def decide(decision: str, reason: str) -> None:
            results.append({**row, "decision": decision, "reason": reason})

        if not tenant:
            decide("skipped", "Vacant. No resident to contact.")
            continue
        contracts = [c for c in unit.get("contracts", []) if c.get("type") == "energy_supply"]
        energy = next((c for c in contracts if c.get("status") == "active"), None)
        if energy is None:
            if any(c.get("status") in ("signed", "ending") for c in contracts):
                decide("skipped", "Already renewed. New contract on file.")
            else:
                decide("skipped", "No energy contract on record. Offer at next move-in or move-out.")
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
        if unit_id in open_orders:
            decide("skipped", "Order already in progress.")
            continue
        if any(c["unit_id"] == unit_id and c.get("case") == energy["expiry_date"] for c in calls):
            decide("skipped", "Already contacted for this renewal.")
            continue
        postcode = re.search(r"\b(\d{5})\b", address)
        if postcode and not any(postcode.group(1).startswith(p) for p in prefixes):
            decide("attention", f"In the window, but no partner serves postcode {postcode.group(1)}. Property manager notified; no call.")
            add_event("detected", f"Contract ends {energy['expiry_date']}; no partner covers {postcode.group(1)}. Needs a person.", "centrix", unit_id)
            continue
        meter = next((m for m in unit.get("unit", {}).get("meters", []) if m.get("type") == "electricity"), {})
        notes = []
        if today > expiry:
            notes.append(f"expired {(today - expiry).days} days ago, likely on a default tariff: urgent")
        if not meter.get("number"):
            notes.append("meter number missing: Mia asks for a meter photo")
        reason = f"Contract ends {energy['expiry_date']} ({(expiry - today).days} days). Call queued" + (f" ({'; '.join(notes)})" if notes else "") + "."
        decide("matched", reason)
        calls.append({"call_id": "call_" + uuid.uuid4().hex[:6], "unit_id": unit_id, "tenant": tenant.get("name"),
                      "case": energy["expiry_date"], "status": "queued", "channel": "browser", "urgent": today > expiry,
                      "created_at": now_iso(), "history": [{"status": "queued", "at": now_iso()}]})
        add_event("detected", f"Renewal window open: contract with {energy.get('supplier')} ends {energy['expiry_date']}. Case opened automatically.", "centrix", unit_id)
        add_event("call_queued", f"Outbound call to {tenant.get('name')} queued for Mia.", "centrix", unit_id)
    new_calls = [c for c in calls if c["status"] == "queued" and c["created_at"] >= results_started]
    write_json(CALLS_FILE, {"calls": calls})
    demo_number = os.environ.get("DEMO_PHONE_NUMBER", "")
    auto_dial = None
    if new_calls and demo_number and phone_ready():
        # Demo: every resident "lives" at your number, so only the most urgent new call is dialled per run.
        target = sorted(new_calls, key=lambda c: (not c["urgent"], c["created_at"]))[0]
        ok, message = place_phone_call(target, demo_number)
        auto_dial = {"call_id": target["call_id"], "unit_id": target["unit_id"], "ok": ok, "message": message}
    run = {"date": today.isoformat(), "auto_dial": auto_dial, "ran_at": now_iso(), "source": source, "scanned": len(results),
           "matched": sum(r["decision"] == "matched" for r in results),
           "attention": sum(r["decision"] == "attention" for r in results), "results": results}
    write_json(RUN_FILE, run)
    return run


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class DemoHandler(BaseHTTPRequestHandler):
    def send_json(self, status: int, value: dict) -> None:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 100_000:
            raise ValueError("Request is too large.")
        raw = self.rfile.read(length).decode("utf-8").strip()
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/api/state":
            query = parse_qs(parsed.query)
            role = query.get("role", [""])[0]
            unit_id = query.get("unit", ["unit_4b"])[0]
            try:
                view = view_for(role, read_json(unit_path(unit_id)), read_json(OFFERS_FILE))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            view["unit_id"] = unit_id
            view["today"] = demo_today().isoformat()
            if role in ("tenant", "reltix"):
                view["case"] = read_json(unit_path(unit_id)).get("renewal_case", {})
            if role == "tenant" and (read_json(unit_path(unit_id)).get("tenant")):
                view["agent_variables"] = agent_variables(read_json(unit_path(unit_id)))
            calls = [c for c in read_list(CALLS_FILE, "calls") if c["unit_id"] == unit_id]
            view["calls"] = calls
            orders = [o for o in read_orders() if o.get("subject", {}).get("unit_id") == unit_id]
            if role == "tenant":
                # The resident sees their own order's progress, not the partner payload or the staff log.
                view["orders"] = [{key: order.get(key) for key in ("order_id", "partner", "product", "requested_start",
                                                                  "status", "status_history", "previous_contract")}
                                  for order in orders]
            else:
                view["orders"] = orders
                view["events"] = read_events(unit_id)
            self.send_json(200, view)
            return
        if path == "/api/portfolio":
            run = read_json(RUN_FILE) if RUN_FILE.exists() else None
            self.send_json(200, {"today": demo_today().isoformat(), "run": run, "calls": read_list(CALLS_FILE, "calls"),
                                 "phone_ready": phone_ready(), "auto_dial_on": bool(os.environ.get("DEMO_PHONE_NUMBER")) and phone_ready()})
            return
        if path.startswith("/api/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            order = next((item for item in read_orders() if item.get("order_id") == order_id), None)
            if order is None:
                self.send_json(404, {"error": "Unknown order."})
                return
            self.send_json(200, {"order": order})
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
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urlparse(self.path).path
        try:
            body = self.read_body()
            if path == "/api/reading":
                self.save_switch_reading(body)
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
            elif path == "/api/clock":
                today = date.fromisoformat(str(body.get("today")))
                write_json(CLOCK_FILE, {"today": today.isoformat()})
                self.send_json(200, {"ok": True, "run": run_trigger("date changed")})
            elif path == "/api/run":
                self.send_json(200, {"ok": True, "run": run_trigger("manual")})
            elif re.fullmatch(r"/api/calls/call_[a-z0-9]+/phone", path):
                self.phone_call(path.split("/")[3], body)
            elif re.fullmatch(r"/api/calls/call_[a-z0-9]+", path):
                self.update_call(path.split("/")[3], body)
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
        if value < 0:
            raise ValueError("Meter reading must be zero or greater.")
        reading_date = str(body.get("read_at") or date.today().isoformat())
        date.fromisoformat(reading_date)

        unit_id = str(body.get("unit_id", "unit_4b"))
        unit = read_json(unit_path(unit_id))
        meters = unit.get("unit", {}).get("meters", [])
        meter = next((item for item in meters if item.get("type") == "electricity"), None)
        if meter is None:
            raise ValueError("No electricity meter is available for this unit.")
        meter["switch_reading"] = {"value_kwh": value, "read_at": reading_date, "source": str(body.get("source", "portal"))[:20]}
        write_json(unit_path(unit_id), unit)
        add_event("reading", f"Meter reading {value:g} kWh saved ({meter['switch_reading']['source']}).", meter["switch_reading"]["source"], unit_id)
        # If the partner was waiting for this reading, centrix passes it on and resubmits (spec §6 needs_input → received).
        orders = read_orders()
        for order in orders:
            if order.get("status") == "needs_input" and order.get("subject", {}).get("unit_id") == unit_id:
                order["subject"]["meter"]["switch_reading"] = {**meter["switch_reading"], "source": "tenant_entry"}
                order["status"] = "received"
                order["status_history"].append({"status": "received", "at": now_iso(), "by": "centrix",
                                                "note": f"Meter reading {value:g} kWh added and resubmitted"})
                write_orders(orders)
                add_event("received", f"Resident added the meter reading ({value:g} kWh). centrix resubmitted order {order['order_id']}.", "portal", unit_id)
        self.send_json(200, {"ok": True, "switch_reading": meter["switch_reading"]})

    def save_offers(self, body: dict) -> None:
        text = str(body.get("text", "")).strip()
        if not text:
            raise ValueError("Enter at least one offer first.")

        partner = read_json(OFFERS_FILE)
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
            slug = re.sub(r"[^a-z0-9]+", "_", product.lower()).strip("_") or f"offer_{line_number}"
            offers.append({
                "offer_id": f"{slug}_{months}m",
                "product": product,
                "estimated_monthly_price_eur": round(price, 2),
                "contract_months": months,
                "renewable_share_percent": renewable,
                "available_from": None,
                "requires_meter_reading": True,
            })
        if not offers:
            raise ValueError("Enter at least one offer first.")
        partner["offers"] = offers
        write_json(OFFERS_FILE, partner)
        self.send_json(200, {"ok": True, "partner": partner})

    def reset_demo(self) -> None:
        """Restore the sample records so the demo can be run again."""
        write_json(UNIT_FILE, read_json(UNIT_FILE.with_suffix(".seed.json")))
        for seed in PORTFOLIO_SEED.glob("unit_*.json"):
            write_json(PORTFOLIO_DIR / seed.name, read_json(seed))
        for path in (CALLS_FILE, RUN_FILE, CLOCK_FILE):
            path.unlink(missing_ok=True)
        write_json(OFFERS_FILE, read_json(OFFERS_FILE.with_suffix(".seed.json")))
        write_orders([])
        write_json(EVENTS_FILE, {"events": []})
        run_trigger("reset")
        self.send_json(200, {"ok": True})

    def update_case(self, body: dict) -> None:
        """What the resident told Mia or the portal, stored once on the unit so every page shows it."""
        unit_id = str(body.get("unit_id", "unit_4b"))
        path = unit_path(unit_id)
        unit = read_json(path)
        case = unit.setdefault("renewal_case", {})
        source = str(body.get("source", "voice"))[:20]
        changes = []
        partner = read_json(OFFERS_FILE)
        if "chosen_offer_id" in body:
            offer = next((o for o in partner.get("offers", []) if o.get("offer_id") == body["chosen_offer_id"]), None)
            if offer is None:
                raise ValueError("Unknown offer_id.")
            case["chosen_offer_id"] = offer["offer_id"]
            changes.append(f"chose {offer.get('product')}")
        if "preferred_channel" in body:
            case["preferred_channel"] = "email" if str(body["preferred_channel"]).lower().startswith("e") else "sms"
            changes.append(f"prefers {case['preferred_channel'].upper()}")
        if "callback" in body:
            case["callback"] = str(body["callback"])[:80]
            changes.append(f"callback {case['callback']}")
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

    def update_call(self, call_id: str, body: dict) -> None:
        """The resident's phone/browser reports what happened to an outbound call."""
        status = str(body.get("status", ""))
        if status not in ("ringing", "answered", "missed", "declined", "done"):
            raise ValueError("Unknown call status.")
        calls = read_list(CALLS_FILE, "calls")
        call = next((c for c in calls if c["call_id"] == call_id), None)
        if call is None:
            self.send_json(404, {"error": "Unknown call."})
            return
        if call["status"] != status:
            call["status"] = status
            call["history"].append({"status": status, "at": now_iso()})
            write_json(CALLS_FILE, {"calls": calls})
            text = {"answered": f"{call['tenant']} answered Mia's call.", "missed": f"{call['tenant']} didn't answer. SMS link sent instead.",
                    "declined": f"{call['tenant']} declined the call. SMS link sent instead.", "done": "Call ended."}.get(status)
            if text:
                add_event("call", text, "voice", call["unit_id"])
        self.send_json(200, {"call": call})

    def phone_call(self, call_id: str, body: dict) -> None:
        """Optional: Mia calls a real phone (needs ELEVENLABS_API_KEY and ELEVENLABS_PHONE_NUMBER_ID)."""
        if not phone_ready():
            self.send_json(400, {"error": "Phone calls aren't set up. See README: ELEVENLABS_API_KEY and ELEVENLABS_PHONE_NUMBER_ID."})
            return
        call = next((c for c in read_list(CALLS_FILE, "calls") if c["call_id"] == call_id), None)
        if call is None:
            self.send_json(404, {"error": "Unknown call."})
            return
        ok, message = place_phone_call(call, str(body.get("to_number") or os.environ.get("DEMO_PHONE_NUMBER", "")))
        self.send_json(200 if ok else 502, {"ok": ok, "message": message} if ok else {"error": message})

    def create_order(self, body: dict) -> None:
        """centrix places an order after the tenant confirmed (spec §5). Only declared fields are sent."""
        unit_id = str(body.get("unit_id", "unit_4b"))
        unit = read_json(unit_path(unit_id))
        partner = read_json(OFFERS_FILE)
        offer = next((item for item in partner.get("offers", []) if item.get("offer_id") == body.get("offer_id")), None)
        if offer is None:
            raise ValueError("Unknown offer_id.")
        if not body.get("consent_given"):
            raise ValueError("The tenant has not given consent; nothing is shared with the partner.")

        meter = next((item for item in unit.get("unit", {}).get("meters", []) if item.get("type") == "electricity"), {})
        reading = meter.get("switch_reading", {})
        if any(item.get("status") in ("submitted", "received", "needs_input") and item.get("subject", {}).get("unit_id") == unit_id
               for item in read_orders()):
            raise ValueError("An order for this unit is already open.")

        energy = next((item for item in unit.get("contracts", []) if item.get("type") == "energy_supply" and item.get("status") == "active"), {})
        expiry = energy.get("expiry_date")
        # Day after the old contract ends; if it already ended (urgent case), the next day from today.
        requested_start = max(date.fromisoformat(expiry) + timedelta(days=1), demo_today() + timedelta(days=1)).isoformat() if expiry else None
        tenant = (unit.get("tenant") or {})
        channel = str(body.get("channel") or "portal")
        data_shared = ["tenant.name", "unit.address", "meter.number", "meter.switch_reading", "start_date"]

        order_id = "ord_" + uuid.uuid4().hex[:6]
        order = {
            "order_id": order_id,
            "partner_id": partner.get("partner_id"),
            "partner": partner.get("partner"),
            "offer_id": offer["offer_id"],
            "product": offer.get("product"),
            "estimated_monthly_price_eur": offer.get("estimated_monthly_price_eur"),
            "kind": "sell",
            "requested_start": requested_start,
            "subject": {
                "unit_id": unit.get("unit_id"),
                "address": unit.get("property", {}).get("address"),
                "tenant": {"name": tenant.get("name"), "contact_via": "centrix"},
                "meter": {"type": "electricity", "number": meter.get("number"),
                          "switch_reading": ({**reading, "source": "tenant_entry"} if reading.get("value_kwh") is not None
                                             else {"value_kwh": None, "note": "sent on the switch date"})},
            },
            "previous_contract": {"contract_id": energy.get("contract_id"), "supplier": energy.get("supplier"),
                                  "ends": expiry, "cancellation_by": "new_supplier"},
            "consent": {"data_shared": data_shared, "given_at": now_iso(), "channel": channel,
                        "given_by": tenant.get("contact_id"), "signed_name": str(body.get("signed_name", ""))[:100],
                        "conversation": str(body.get("session_id", ""))[:40]},
            "status": "submitted",
            "status_history": [{"status": "submitted", "at": now_iso(), "by": "centrix"}],
        }
        orders = read_orders()
        orders.append(order)
        write_orders(orders)
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
        if new_status not in TRANSITIONS.get(order["status"], set()):
            self.send_json(409, {"error": f"Cannot move an order from {order['status']} to {new_status}."})
            return
        entry = {"status": new_status, "at": now_iso(), "by": order.get("partner_id")}
        note = str(body.get("note", ""))[:200]
        if note:
            entry["note"] = note
        if new_status == "confirmed":
            entry["result"] = {"contract_number": "EGE-" + order_id[-6:].upper(), "start": order.get("requested_start"),
                               "monthly_estimate_eur": order.get("estimated_monthly_price_eur")}
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
        add_event(new_status, messages.get(new_status, new_status), "partner", order["subject"]["unit_id"])
        if new_status in ("confirmed", "active"):
            DemoHandler.write_back(order)
        self.send_json(200, {"order": order})

    @staticmethod
    def write_back(order: dict) -> None:
        """Close the loop (spec §6): file the new contract and the consent log on the unit."""
        path = unit_path(order["subject"]["unit_id"])
        unit = read_json(path)
        contracts = unit.setdefault("contracts", [])
        existing = next((item for item in contracts if item.get("order_id") == order["order_id"]), None)
        if existing:
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
            "current_monthly_price_eur": result.get("monthly_estimate_eur"),
        })
        unit.setdefault("consent_log", []).append({"order_id": order["order_id"], "partner": order.get("partner"),
                                                   **order["consent"]})
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

        tenant_view = view_for("tenant", read_json(unit_path(str(body.get("unit_id", "unit_4b")))), read_json(OFFERS_FILE))
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

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


if __name__ == "__main__":
    run = run_trigger("server start")
    print(f"Daily trigger run for {run['date']}: {run['scanned']} units scanned, {run['matched']} calls queued, {run['attention']} need a person.")
    print("Real phone calls:", ("ON, auto-dialling " + os.environ["DEMO_PHONE_NUMBER"][:4] + "…") if os.environ.get("DEMO_PHONE_NUMBER") and phone_ready()
          else "ON (manual)" if phone_ready() else "off (see README)")
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"Reltix demo is running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Reltix demo.")
    finally:
        server.server_close()
