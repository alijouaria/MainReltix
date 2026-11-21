import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main


class RenewalEdgeCaseTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        data = Path(stack.enter_context(tempfile.TemporaryDirectory())) / "data"
        shutil.copytree(main.ROOT / "data", data)
        for key in ("UNIT_FILE", "OFFERS_FILE", "ORDERS_FILE", "PORTFOLIO_DIR", "PORTFOLIO_SEED",
                    "CLOCK_FILE", "RUN_FILE", "CALLS_FILE", "EVENTS_FILE"):
            stack.enter_context(patch.object(main, key, data / getattr(main, key).relative_to(main.ROOT / "data")))
        stack.enter_context(patch.object(main, "RESET_DATE", date(2026, 12, 1)))
        self.handler = object.__new__(main.DemoHandler)
        self.handler.send_json = self.capture
        self.handler.reset_demo()

    def capture(self, code, result):
        self.code, self.result = code, result

    def post(self, path, **body):
        self.handler.path = "/api/" + path
        self.handler.read_body = lambda: body
        self.handler.do_POST()
        return self.result

    def view(self, role, unit="unit_4b"):
        self.handler.path = f"/api/state?role={role}&unit={unit}"
        self.handler.do_GET()
        self.assertEqual(self.code, 200)
        return self.result

    def set_day(self, value):
        self.post("clock", today=value)
        self.assertEqual(self.code, 200)

    def offer(self, offer_id=None):
        partner = main.read_json(main.OFFERS_FILE)
        item = next((o for o in partner["offers"] if o["offer_id"] == offer_id), partner["offers"][0])
        return item

    def create_order(self, unit="unit_4b", offer_id=None, submission_id="edge-submit"):
        offer = self.offer(offer_id)
        self.post("case", unit_id=unit, chosen_offer_id=offer["offer_id"],
                  offer_version=main.offer_version(offer), source="portal")
        self.assertEqual(self.code, 200)
        result = self.post("orders", unit_id=unit, offer_id=offer["offer_id"], offer_version=main.offer_version(offer),
                           submission_id=submission_id, consent_given=True, signed_name="Demo Tenant")
        self.assertIn(self.code, (200, 201))
        return result["order"]

    def order_status(self, order, status, **extra):
        result = self.post(f"orders/{order['order_id']}/status", status=status, **extra)
        return result

    def test_overdue_callback_is_single_and_staff_owned_until_supplier_answers(self):
        order = self.create_order()
        self.assertEqual(order["requested_start"], "2027-01-01")
        self.assertEqual(order["callback_date"], "2026-12-02")
        self.order_status(order, "pending")
        self.set_day("2026-12-02")
        self.post("run")
        self.post("run")
        calls = [c for c in main.read_list(main.CALLS_FILE, "calls") if c.get("order_id") == order["order_id"]]
        self.assertEqual(len(calls), 1)
        self.set_day("2027-01-05")
        saved = next(o for o in main.read_orders() if o["order_id"] == order["order_id"])
        self.assertTrue(saved["start_unconfirmed"])
        self.assertIn("Property team owns", main.read_json(main.UNIT_FILE)["renewal_case"]["needs_person"])
        self.assertEqual(main.read_list(main.CALLS_FILE, "calls")[0]["owner"], "property team")
        self.assertEqual(self.view("tenant")["orders"][-1]["status"], "pending")
        self.assertTrue(self.view("tenant")["orders"][-1]["start_unconfirmed"])
        mismatch = self.order_status(saved, "confirmed", start_date="2027-01-03")
        self.assertEqual(self.code, 409)
        self.assertTrue(mismatch["start_date_mismatch"])
        self.assertEqual(mismatch["requested_start"], "2027-01-01")
        self.assertEqual(mismatch["proposed_start"], "2027-01-03")
        self.assertEqual(main.read_orders()[0]["status"], "pending")
        self.assertEqual(main.read_orders()[0]["requested_start"], "2027-01-01")
        self.order_status(saved, "confirmed", start_date="2027-01-01", supply_started=True)
        self.assertEqual(self.code, 200)
        self.assertEqual(main.read_orders()[0]["requested_start"], "2027-01-01")
        self.assertEqual(main.read_list(main.CALLS_FILE, "calls")[0]["status"], "done")
        self.assertFalse(self.view("tenant")["orders"][-1]["start_unconfirmed"])

    def test_stale_offer_requires_fresh_terms_and_reordering_preserves_selection(self):
        selected = self.offer("green_home_12_12m")
        self.post("case", unit_id="unit_4b", chosen_offer_id=selected["offer_id"],
                  offer_version=main.offer_version(selected), source="portal")
        self.assertEqual(self.code, 200)
        stale_version = main.offer_version(selected)
        self.post("offers", text="Green Home 12 | 99.00 | 100 | 12\nFlex Home 12 | 74.90 | 50 | 12")
        self.assertEqual(self.code, 200)
        stale = self.post("orders", unit_id="unit_4b", offer_id=selected["offer_id"], offer_version=stale_version,
                          submission_id="stale", consent_given=True)
        self.assertEqual(self.code, 409)
        self.assertTrue(stale["stale_offer"])
        self.assertEqual(stale["offer"]["estimated_monthly_price_eur"], 99.0)
        self.assertEqual(main.read_orders(), [])
        partner = main.read_json(main.OFFERS_FILE)
        partner["offers"].reverse()
        main.write_json(main.OFFERS_FILE, partner)
        current = self.offer("green_home_12_12m")
        self.assertEqual(main.offer_version(current), stale["offer"]["offer_version"])
        self.assertEqual(main.read_json(main.UNIT_FILE)["renewal_case"]["chosen_offer_id"], current["offer_id"])
        order = self.create_order(offer_id=current["offer_id"], submission_id="fresh")
        self.assertEqual(order["estimated_monthly_price_eur"], 99.0)
        self.assertEqual(order["consent"]["offer_snapshot"]["contract_months"], 12)

    def test_complaint_retry_creates_one_ticket_and_sharing_is_explicit(self):
        session, event = "voice-session-1", "voice-note-1"
        saved = self.post("issues", unit_id="unit_4b", text="The power start is unclear.", source="simulated voice event",
                          call_session_id=session, client_event_id=event)["issue"]
        retry = self.post("issues", unit_id="unit_4b", text="The power start is unclear.", source="simulated voice event",
                          call_session_id=session, client_event_id=event)["issue"]
        self.assertEqual(saved["issue_id"], retry["issue_id"])
        self.assertEqual(len(main.read_json(main.UNIT_FILE)["issues"]), 1)
        self.assertNotIn("shared_order_id", saved)
        order = self.create_order(submission_id="complaint-share-order")
        self.post("issues", unit_id="unit_4b", issue_id=saved["issue_id"], shared_order_id=order["order_id"])
        self.assertEqual(self.code, 200)
        self.assertEqual(self.view("partner")["issues"][0]["issue_id"], saved["issue_id"])
        self.assertEqual(len(main.read_json(main.UNIT_FILE)["issues"]), 1)

    def test_rejection_refreshes_alternatives_and_replacement_needs_new_consent(self):
        order = self.create_order(submission_id="declined-order")
        self.post("run")  # The daily check sees the first order still open.
        self.order_status(order, "rejected", note="Plan unavailable")
        self.assertEqual(self.code, 200)
        tenant = self.view("tenant")
        self.assertTrue(tenant["offer_available"])
        self.assertEqual(tenant["orders"][-1]["status"], "rejected")
        before = len(main.read_json(main.UNIT_FILE).get("consent_log", []))
        replacement = self.create_order(offer_id="flex_home_12_12m", submission_id="replacement-order")
        self.assertNotEqual(replacement["order_id"], order["order_id"])
        self.assertEqual(len(main.read_json(main.UNIT_FILE)["consent_log"]), before + 1)

    def test_cancelled_confirmed_order_keeps_history_and_does_not_invent_supply(self):
        order = self.create_order(submission_id="cancelled-order")
        self.order_status(order, "received")
        self.order_status(order, "confirmed")
        self.order_status(order, "cancelled")
        self.assertEqual(self.code, 200)
        unit = main.read_json(main.UNIT_FILE)
        filed = next(c for c in unit["contracts"] if c.get("order_id") == order["order_id"])
        previous = next(c for c in unit["contracts"] if c.get("contract_id") == "energy_c_88")
        self.assertEqual(filed["status"], "cancelled")
        self.assertEqual(previous["status"], "active")
        self.assertEqual(len(unit["consent_log"]), 1)
        self.assertEqual(main.read_orders()[0]["status_history"][-1]["status"], "cancelled")
        self.set_day("2027-01-05")
        owner = self.view("owner")
        tenant = self.view("tenant")
        self.assertEqual(owner["status"], "cancelled")
        self.assertIsNone(owner["start"])
        self.assertFalse(owner["supplier_known"])
        self.assertEqual(tenant["orders"][-1]["status"], "cancelled")

    def test_missing_meter_identifier_can_be_added_after_reading(self):
        order = self.create_order("unit_3b", submission_id="missing-meter")
        self.order_status(order, "received")
        self.post("reading", unit_id="unit_3b", value_kwh=18400, read_at="2026-12-05")
        self.assertEqual(self.code, 200)
        self.order_status(order, "confirmed")
        failed = self.order_status(order, "active")
        self.assertEqual(self.code, 400)
        self.assertIn("meter identifier", failed["error"])
        self.post("meter-identifier", unit_id="unit_3b", number="1ESY116000003B")
        self.assertEqual(self.code, 200)
        saved_order = next(o for o in main.read_orders() if o["order_id"] == order["order_id"])
        self.assertEqual(saved_order["subject"]["meter"]["number"], "1ESY116000003B")
        self.assertEqual(saved_order["subject"]["meter"]["switch_reading"]["value_kwh"], 18400)
        self.order_status(saved_order, "active")
        self.assertEqual(self.code, 200)

    def test_remote_and_manual_switch_reading_date_paths(self):
        remote = self.create_order(submission_id="remote-reading")
        self.order_status(remote, "received")
        self.order_status(remote, "needs_input", note="switch reading")
        self.set_day("2026-12-31")
        remote_saved = next(o for o in main.read_orders() if o["order_id"] == remote["order_id"])
        self.assertEqual(remote_saved["subject"]["meter"]["switch_reading"]["value_kwh"], 18610)
        self.assertEqual(remote_saved["status"], "received")
        self.post("run")
        remote_saved = next(o for o in main.read_orders() if o["order_id"] == remote["order_id"])
        self.assertEqual(remote_saved["subject"]["meter"]["switch_reading"]["value_kwh"], 18610)
        remote_events = [e for e in main.read_events("unit_4b") if e["type"] == "reading" and "remote reading" in e["text"].lower()]
        self.assertEqual(len(remote_events), 1)

        self.set_day("2026-12-01")
        manual = self.create_order("unit_1a", submission_id="manual-reading")
        self.order_status(manual, "received")
        self.order_status(manual, "needs_input", note="switch reading")
        self.set_day("2026-12-10")
        saved_manual = next(o for o in main.read_orders() if o["order_id"] == manual["order_id"])
        self.assertTrue(saved_manual["manual_reading_prompted"])
        requested = [e for e in main.read_events("unit_1a") if e["type"] == "reading_requested"]
        self.assertEqual(len(requested), 1)
        self.assertIn("2026-12-10", requested[0]["text"])
        self.post("run")
        requested = [e for e in main.read_events("unit_1a") if e["type"] == "reading_requested"]
        self.assertEqual(len(requested), 1)

    def test_consent_is_logged_at_submission_and_retries_do_not_duplicate(self):
        offer = self.offer()
        self.post("case", unit_id="unit_4b", chosen_offer_id=offer["offer_id"],
                  offer_version=main.offer_version(offer), source="portal")
        self.assertEqual(self.code, 200)
        body = dict(unit_id="unit_4b", offer_id=offer["offer_id"], offer_version=main.offer_version(offer),
                    submission_id="consent-retry", consent_given=True)
        first = self.post("orders", **body)["order"]
        self.assertEqual(self.code, 201)
        retry = self.post("orders", **body)["order"]
        self.assertEqual(self.code, 200)
        self.assertEqual(first["order_id"], retry["order_id"])
        log = self.view("reltix")["unit"]["consent_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["recipient"], main.read_json(main.OFFERS_FILE)["partner"])
        self.assertTrue(log[0]["approved_fields"])
        self.assertEqual(log[0]["channel"], "portal")
        self.order_status(first, "received")
        self.order_status(first, "needs_input", note="Please send the switch reading before 2026-12-31.")
        self.set_day("2026-12-31")
        self.order_status(first, "confirmed")
        self.order_status(first, "active")
        self.assertEqual(len(self.view("reltix")["unit"]["consent_log"]), 1)

    def test_next_renewal_cycle_uses_saved_term_and_allows_second_order(self):
        order = self.create_order(submission_id="cycle-one")
        self.order_status(order, "received")
        self.order_status(order, "needs_input", note="Please send the switch reading before 2026-12-31.")
        self.set_day("2026-12-31")
        self.order_status(order, "confirmed")
        self.order_status(order, "active")
        unit = main.read_json(main.UNIT_FILE)
        contract = next(c for c in unit["contracts"] if c.get("order_id") == order["order_id"])
        self.assertEqual(contract["start"], "2027-01-01")
        self.assertEqual(contract["expiry_date"], "2027-12-31")
        self.assertEqual(contract["contract_months"], 12)
        self.set_day("2027-12-01")
        self.assertTrue(self.view("tenant")["offer_available"])
        next_order = self.create_order(submission_id="cycle-two")
        self.assertEqual(next_order["requested_start"], "2028-01-01")
        self.assertEqual(len(main.read_orders()), 2)
