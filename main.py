"""Local Reltix demo server. Uses only the Python standard library."""

from __future__ import annotations

import json
import mimetypes
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
UNIT_FILE = ROOT / "data" / "centrix" / "unit_4b.json"
OFFERS_FILE = ROOT / "data" / "partners" / "electricity_offers.json"
HOST = "127.0.0.1"
PORT = 8765
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:0.5b"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        path = urlparse(self.path).path
        if path == "/api/state":
            self.send_json(200, {"unit": read_json(UNIT_FILE), "partner": read_json(OFFERS_FILE)})
            return

        relative = "index.html" if path == "/" else unquote(path.lstrip("/"))
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

        unit = read_json(UNIT_FILE)
        meters = unit.get("unit", {}).get("meters", [])
        meter = next((item for item in meters if item.get("type") == "electricity"), None)
        if meter is None:
            raise ValueError("No electricity meter is available for this unit.")
        meter["switch_reading"] = {"value_kwh": value, "read_at": reading_date}
        write_json(UNIT_FILE, unit)
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

    def chat(self, body: dict) -> None:
        history = body.get("messages", [])
        if not isinstance(history, list):
            raise ValueError("Chat messages must be a list.")
        history = [
            {"role": item.get("role"), "content": str(item.get("content", ""))[:4000]}
            for item in history[-10:]
            if isinstance(item, dict) and item.get("role") in ("user", "assistant")
        ]
        if not history or history[-1]["role"] != "user":
            raise ValueError("Send a user message first.")

        context = {"unit": read_json(UNIT_FILE), "energy_partner": read_json(OFFERS_FILE)}
        question = history[-1]["content"]
        direct_answer = self.lookup_answer(question, context)
        if direct_answer:
            self.send_json(200, {"answer": direct_answer, "mode": "record_lookup"})
            return

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
            "tenant": unit.get("tenant", {}),
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
            "listed. Offer prices are estimates. You cannot place orders, sign contracts, contact people, or change records.\n\n"
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
        tenant = unit.get("tenant", {})
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
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"Reltix demo is running at http://{HOST}:{PORT}")
    print("Press Ctrl+C to stop it.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Reltix demo.")
    finally:
        server.server_close()
