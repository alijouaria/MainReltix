import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main


class OrderLifecycleTests(unittest.TestCase):
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
        self.offer = main.read_json(main.OFFERS_FILE)["offers"][0]

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

    def create_order(self, submission_id):
        self.post("case", unit_id="unit_4b", chosen_offer_id=self.offer["offer_id"],
                  offer_version=main.offer_version(self.offer), source="portal")
        result = self.post("orders", unit_id="unit_4b", offer_id=self.offer["offer_id"],
                           offer_version=main.offer_version(self.offer), submission_id=submission_id,
                           consent_given=True)
        self.assertEqual(self.code, 201)
        return result["order"]

    def status(self, order, state, **body):
        return self.post(f"orders/{order['order_id']}/status", status=state, **body)

    def test_supplier_start_mismatch_is_visible_and_never_confirmed(self):
        order = self.create_order("mismatch")
        self.status(order, "received")
        mismatch = self.status(order, "confirmed", start_date="2027-01-03")
        self.assertEqual(self.code, 409)
        self.assertTrue(mismatch["start_date_mismatch"])
        self.assertEqual((mismatch["requested_start"], mismatch["proposed_start"]), ("2027-01-01", "2027-01-03"))

        saved = main.read_orders()[0]
        self.assertEqual(saved["status"], "received")
        self.assertEqual(saved["requested_start"], "2027-01-01")
        self.assertEqual(saved["proposed_start_date"], "2027-01-03")
        self.assertFalse(any(item.get("result") for item in saved["status_history"]))
        self.assertEqual(self.view("owner")["start"], None)
        tenant_order = self.view("tenant")["orders"][-1]
        self.assertEqual(tenant_order["requested_start"], "2027-01-01")
        self.assertEqual(tenant_order["start_date_mismatch"]["proposed"], "2027-01-03")

        self.status(saved, "rejected", note="Cannot honor requested start date")
        self.assertEqual(self.code, 200)
        self.assertEqual(main.read_orders()[0]["status"], "rejected")

    def test_cancel_after_old_contract_expiry_reassesses_actual_dates(self):
        order = self.create_order("late-cancel")
        self.status(order, "received")
        self.status(order, "confirmed")
        self.set_day("2027-01-05")
        self.status(order, "cancelled")
        self.assertEqual(self.code, 200)

        unit = main.read_json(main.UNIT_FILE)
        replacement = next(c for c in unit["contracts"] if c.get("order_id") == order["order_id"])
        previous = next(c for c in unit["contracts"] if c.get("contract_id") == "energy_c_88")
        self.assertEqual(replacement["status"], "cancelled")
        self.assertEqual(previous["status"], "ended")
        self.assertNotIn("replaced_by_order", previous)
        self.assertIsNone(self.view("owner")["start"])
        self.assertIsNone(self.view("tenant")["orders"][-1]["requested_start"])


if __name__ == "__main__":
    unittest.main()
