import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main


class HandoverTests(unittest.TestCase):
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
        self.offer_id = main.read_json(main.OFFERS_FILE)["offers"][0]["offer_id"]

    def capture(self, code, result):
        self.code, self.result = code, result

    def view(self, role, unit="unit_4b"):
        self.handler.path = f"/api/state?role={role}&unit={unit}"
        self.handler.do_GET()
        self.assertEqual(self.code, 200)
        return self.result

    def post(self, path, **body):
        self.handler.path = "/api/" + path
        self.handler.read_body = lambda: body
        self.handler.do_POST()
        return self.result

    def order(self, unit):
        order = self.post("orders", unit_id=unit, offer_id=self.offer_id, consent_given=True)["order"]
        self.assertEqual(self.code, 201)
        return "orders/" + order["order_id"] + "/status", order["order_id"]

    def test_complaints_and_phone_handover(self):
        self.assertEqual(self.view("partner")["orders"], [])
        self.assertTrue(self.view("tenant")["offer_available"])
        self.assertFalse(self.view("tenant")["calls"])
        private = self.post("issues", text="Please explain the price.", source="simulated phone")["issue"]
        status, order_id = self.order("unit_4b")
        shared = self.post("issues", text="When will supply start?", shared_order_id=order_id)["issue"]
        self.assertEqual([i["issue_id"] for i in self.view("partner")["issues"]], [shared["issue_id"]])
        self.post("issues/reply", issue_id=private["issue_id"], role="partner", reply="Wrong workspace")
        self.assertEqual(self.code, 400)
        self.post("issues/reply", issue_id=private["issue_id"], role="reltix", reply="The price is a monthly estimate.")
        self.post(status, status="pending")
        self.assertIsNone(self.view("owner")["start"])
        self.post(status, status="received")
        self.post(status, status="confirmed")
        self.assertFalse(self.view("tenant")["issues"][-1].get("reply"))
        self.post("issues/reply", role="partner", issue_id=shared["issue_id"], reply="Confirmed for 1 January.")
        self.assertEqual(self.view("tenant")["issues"][-1]["reply"], "Confirmed for 1 January.")
        self.assertEqual(self.view("owner"), {"unit_id": "unit_4b", "address": main.read_json(main.UNIT_FILE)["property"]["address"],
                                             "status": "confirmed", "start": "2027-01-01"})
        reading = self.view("partner")["orders"][0]["subject"]["meter"]["switch_reading"]
        self.assertEqual((reading["read_at"], reading["value_kwh"]), ("2026-12-31", 18610))
        self.assertEqual(self.view("tenant")["today"], reading["read_at"])
        self.post(status, status="active")
        self.assertEqual(self.view("owner")["status"], "active")
        self.assertEqual(self.view("reltix")["unit"]["consent_log"][0]["channel"], "portal")
        self.post("run")
        self.assertEqual(self.code, 200)

    def test_missing_reading_and_other_flats(self):
        status, order_id = self.order("unit_1a")
        self.post("issues", unit_id="unit_4b", text="This is another flat.", shared_order_id=order_id)
        self.assertEqual(self.code, 400)
        self.post(status, status="received")
        self.post(status, status="confirmed")
        self.post(status, status="active")
        self.assertEqual(self.code, 400)
        self.post("reading", unit_id="unit_1a", value_kwh=17001, read_at="2026-12-01")
        self.assertEqual(self.code, 400)
        self.post("reading", unit_id="unit_1a", value_kwh=17001, read_at="2026-12-10")
        self.assertEqual(self.code, 200)
        self.post(status, status="active")
        self.assertEqual(self.view("owner", "unit_1a")["status"], "active")
        self.assertEqual(self.view("partner")["orders"], [])
        self.post("reset")
        self.assertFalse(self.view("partner", "unit_1a")["orders"])
