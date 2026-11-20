import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main


class MeterHandoverTests(unittest.TestCase):
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
        self.offer_id = self.offer["offer_id"]

    def capture(self, code, result):
        self.code, self.result = code, result

    def post(self, path, **body):
        self.handler.path = "/api/" + path
        self.handler.read_body = lambda: body
        self.handler.do_POST()
        return self.result

    def unit(self, unit_id):
        return main.read_json(main.unit_path(unit_id))

    def save_unit(self, unit_id, value):
        main.write_json(main.unit_path(unit_id), value)

    def order(self, unit_id="unit_4b", chosen_meter_id=None):
        self.post("case", unit_id=unit_id, chosen_offer_id=self.offer_id,
                  offer_version=main.offer_version(self.offer), source="portal")
        self.assertEqual(self.code, 200)
        payload = {"unit_id": unit_id, "offer_id": self.offer_id, "offer_version": main.offer_version(self.offer),
                   "submission_id": "meter-" + unit_id, "consent_given": True}
        if chosen_meter_id:
            payload["meter_id"] = chosen_meter_id
        result = self.post("orders", **payload)
        self.assertEqual(self.code, 201, result)
        return result["order"]

    def status(self, order, new_status, **extra):
        return self.post(f"orders/{order['order_id']}/status", status=new_status, **extra)

    def test_4b_requested_remote_reading_arrives_once_on_december_31(self):
        order = self.order()
        self.status(order, "received")
        self.status(order, "needs_input", note="Please send the switch reading before 2026-12-31.")
        self.post("clock", today="2026-12-30")
        waiting = main.read_orders()[0]
        self.assertEqual(waiting["status"], "needs_input")
        self.assertNotIn("remote_reading_delivered", waiting)

        self.post("clock", today="2026-12-31")
        delivered = main.read_orders()[0]
        self.assertEqual(delivered["status"], "received")
        self.assertTrue(delivered["remote_reading_delivered"])
        self.assertEqual(delivered["subject"]["meter"]["switch_reading"], {
            "value_kwh": 18610, "read_at": "2026-12-31", "source": "simulated_remote_meter"})
        self.assertEqual(sum(e["type"] == "reading" and "18610" in e["text"] for e in main.read_events("unit_4b")), 1)

        self.post("run")
        self.assertEqual(main.read_orders()[0]["status"], "received")
        self.assertEqual(sum(e["type"] == "reading" and "18610" in e["text"] for e in main.read_events("unit_4b")), 1)
        self.status(delivered, "confirmed")
        self.status(delivered, "active")
        self.assertEqual(main.read_orders()[0]["status"], "active")

    def test_flat_1a_manual_reading_completes_handover(self):
        order = self.order("unit_1a")
        self.status(order, "received")
        self.status(order, "needs_input", note="Please send the switch reading.")
        meter = main.electricity_meters(self.unit("unit_1a"))[0]
        result = self.post("reading", unit_id="unit_1a", meter_id=meter[2], value_kwh=17001, read_at="2026-12-10")
        self.assertEqual(self.code, 200, result)
        self.assertEqual(main.read_orders()[0]["status"], "received")
        self.status(order, "confirmed")
        self.status(order, "active")
        self.assertEqual(main.read_orders()[0]["status"], "active")

    def test_multiple_meters_require_explicit_selection_and_isolate_readings(self):
        unit = self.unit("unit_4b")
        second = {"type": "electricity", "number": "1ESY1160999999",
                  "last_reading": {"value_kwh": 25, "read_at": "2026-10-31"},
                  "switch_reading": {"value_kwh": None, "read_at": None}}
        unit["unit"]["meters"].append(second)
        self.save_unit("unit_4b", unit)
        self.post("case", unit_id="unit_4b", chosen_offer_id=self.offer_id,
                  offer_version=main.offer_version(self.offer), source="portal")
        self.post("orders", unit_id="unit_4b", offer_id=self.offer_id, offer_version=main.offer_version(self.offer),
                  submission_id="missing-meter-order", consent_given=True)
        self.assertEqual(self.code, 400)
        first_id = main.electricity_meters(unit)[0][2]
        second_id = main.electricity_meters(unit)[1][2]
        order = self.order("unit_4b", first_id)
        self.assertEqual(order["meter_id"], first_id)
        self.status(order, "received")
        self.status(order, "needs_input", note="Please send the switch reading.")

        self.post("reading", unit_id="unit_4b", meter_id=second_id, value_kwh=50, read_at="2026-12-31")
        self.assertEqual(self.code, 200)
        self.assertEqual(main.read_orders()[0]["status"], "needs_input")
        self.assertIsNone(main.read_orders()[0]["subject"]["meter"]["switch_reading"]["value_kwh"])
        self.status(order, "received")
        self.status(order, "confirmed")
        self.assertIn("selected meter’s switch reading", self.status(order, "active")["error"])

        self.post("reading", unit_id="unit_4b", meter_id=first_id, value_kwh=18630, read_at="2026-12-31")
        self.assertEqual(self.code, 200)
        self.assertEqual(main.read_orders()[0]["status"], "confirmed")
        self.assertEqual(main.read_orders()[0]["subject"]["meter"]["switch_reading"]["value_kwh"], 18630)
        self.status(order, "confirmed")
        self.status(order, "active")
        self.assertEqual(main.read_orders()[0]["status"], "active")

    def test_missing_meter_and_identifier_are_recoverable_but_block_activation(self):
        unit = self.unit("unit_3b")
        unit["unit"]["meters"] = []
        self.save_unit("unit_3b", unit)
        self.post("case", unit_id="unit_3b", chosen_offer_id=self.offer_id,
                  offer_version=main.offer_version(self.offer), source="portal")
        self.post("orders", unit_id="unit_3b", offer_id=self.offer_id, offer_version=main.offer_version(self.offer),
                  submission_id="no-meter-order", consent_given=True)
        self.assertEqual(self.code, 400)
        self.post("meter", unit_id="unit_3b", number="1ESY116000003B")
        self.assertEqual(self.code, 200)
        unit = self.unit("unit_3b")
        meter = main.electricity_meters(unit)[0]
        self.assertEqual(meter[1]["number"], "1ESY116000003B")

        unit["unit"]["meters"][0]["number"] = None
        self.save_unit("unit_3b", unit)
        order = self.order("unit_3b", meter[2])
        self.status(order, "received")
        self.status(order, "confirmed")
        missing_id = self.status(order, "active")
        self.assertIn("meter identifier is missing", missing_id["error"])

        self.post("meter", unit_id="unit_3b", meter_id=meter[2], number="1ESY116000003B")
        self.assertEqual(self.code, 200)
        self.assertIn("switch reading", self.status(order, "active")["error"])
        self.post("reading", unit_id="unit_3b", meter_id=meter[2], value_kwh=18400, read_at="2026-12-05")
        self.assertEqual(self.code, 200)
        self.status(order, "active")
        self.assertEqual(main.read_orders()[0]["status"], "active")


if __name__ == "__main__":
    unittest.main()
