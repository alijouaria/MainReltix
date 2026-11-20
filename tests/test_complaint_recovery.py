import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main


class ComplaintRecoveryTests(unittest.TestCase):
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

    def view(self, role):
        self.handler.path = f"/api/state?role={role}&unit=unit_4b"
        self.handler.do_GET()
        self.assertEqual(self.code, 200)
        return self.result

    def post(self, path, **body):
        self.handler.path = "/api/" + path
        self.handler.read_body = lambda: body
        self.handler.do_POST()
        return self.result

    def create_order(self, order_id, status="received"):
        partner = main.read_json(main.OFFERS_FILE)
        order = {"order_id": order_id, "partner_id": partner["partner_id"], "partner": partner["partner"],
                 "subject": {"unit_id": "unit_4b", "meter": {"switch_reading": {"value_kwh": None}}},
                 "previous_contract": {},
                 "consent": {"given_at": main.now_iso(), "channel": "portal"}, "status": status,
                 "status_history": [{"status": status, "at": main.now_iso()}], "requested_start": "2027-01-01"}
        orders = main.read_orders()
        orders.append(order)
        main.write_orders(orders)
        return order

    def test_lost_ack_retry_returns_same_ticket_without_duplicate_text(self):
        first = self.post("issues", unit_id="unit_4b", text="The hallway light is out.", source="voice",
                          call_session_id="voice-session-1", client_event_id="voice-session-1-1")["issue"]
        retry = self.post("issues", unit_id="unit_4b", text="The hallway light is out.", source="voice",
                          call_session_id="voice-session-1", client_event_id="voice-session-1-1")["issue"]
        self.assertEqual(first["issue_id"], retry["issue_id"])
        self.assertEqual(first["text"], retry["text"])
        self.assertEqual(len(main.read_json(main.UNIT_FILE)["issues"]), 1)

        updated = self.post("issues", unit_id="unit_4b", text="It is by the stairs.", source="voice",
                            call_session_id="voice-session-1", client_event_id="voice-session-1-2")["issue"]
        repeated_update = self.post("issues", unit_id="unit_4b", text="It is by the stairs.", source="voice",
                                    call_session_id="voice-session-1", client_event_id="voice-session-1-2")["issue"]
        self.assertEqual(updated["issue_id"], first["issue_id"])
        self.assertEqual(repeated_update["text"], "The hallway light is out. It is by the stairs.")

    def test_supplier_decline_returns_open_issue_and_new_sharing_needs_choice(self):
        first_order = self.create_order("ord_first")
        issue = self.post("issues", unit_id="unit_4b", text="The start date is unclear.",
                          shared_order_id=first_order["order_id"])["issue"]
        self.assertEqual(issue["owner"], "partner")

        self.handler.update_order_status(first_order["order_id"], {"status": "rejected"})
        self.assertEqual(self.code, 200)
        saved = main.read_json(main.UNIT_FILE)["issues"][0]
        self.assertIsNone(saved["shared_order_id"])
        self.assertEqual(saved["owner"], "property_team")
        self.assertEqual([entry["owner"] for entry in saved["ownership_history"]], ["property_team", "partner", "partner", "property_team"])
        self.assertEqual(self.view("partner")["issues"], [])
        self.assertEqual(self.view("tenant")["issues"][0]["owner"], "property_team")

        self.post("issues/reply", unit_id="unit_4b", issue_id=issue["issue_id"], role="partner", reply="Supplier reply")
        self.assertEqual(self.code, 400)
        self.post("issues/reply", unit_id="unit_4b", issue_id=issue["issue_id"], role="reltix", reply="Property team reply")
        self.assertEqual(self.code, 200)
        self.assertEqual(self.view("tenant")["issues"][0]["reply"], "Property team reply")

    def test_cancellation_returns_only_open_complaints_and_another_share_is_explicit(self):
        order = self.create_order("ord_cancel")
        opened = self.post("issues", unit_id="unit_4b", text="Please check the changeover.",
                           shared_order_id=order["order_id"])["issue"]
        already_answered = self.post("issues", unit_id="unit_4b", text="Already answered.",
                                     shared_order_id=order["order_id"])["issue"]
        self.post("issues/reply", unit_id="unit_4b", issue_id=already_answered["issue_id"], role="partner", reply="Resolved")
        self.assertEqual(self.code, 200)

        main.reassign_shared_issues_for_order(order, "Order cancelled")
        data = main.read_json(main.UNIT_FILE)
        returned = next(item for item in data["issues"] if item["issue_id"] == opened["issue_id"])
        answered = next(item for item in data["issues"] if item["issue_id"] == already_answered["issue_id"])
        self.assertIsNone(returned["shared_order_id"])
        self.assertEqual(returned["owner"], "property_team")
        self.assertEqual(answered["shared_order_id"], order["order_id"])

        replacement = self.create_order("ord_replacement")
        # The cancellation helper does not silently move the issue to a replacement supplier.
        self.assertIsNone(returned["shared_order_id"])
        self.post("issues", unit_id="unit_4b", issue_id=opened["issue_id"], shared_order_id=replacement["order_id"])
        self.assertEqual(self.code, 200)
        resubmitted = main.read_json(main.UNIT_FILE)["issues"][0]
        self.assertEqual(resubmitted["shared_order_id"], replacement["order_id"])
        self.assertEqual(resubmitted["owner"], "partner")
        self.assertEqual(resubmitted["ownership_history"][-1]["reason"], "Tenant chose to share this complaint")


if __name__ == "__main__":
    unittest.main()
