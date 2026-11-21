import shutil
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

import main


class OfferConsentRenewalTests(unittest.TestCase):
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

    def view(self, role="reltix", unit="unit_4b"):
        self.handler.path = f"/api/state?role={role}&unit={unit}"
        self.handler.do_GET()
        self.assertEqual(self.code, 200)
        return self.result

    def current_offer(self, offer_id=None):
        offers = main.read_json(main.OFFERS_FILE)["offers"]
        return next((item for item in offers if item["offer_id"] == offer_id), offers[0])

    def review(self, offer):
        self.post("case", unit_id="unit_4b", chosen_offer_id=offer["offer_id"],
                  offer_version=main.offer_version(offer), source="portal")
        self.assertEqual(self.code, 200)

    def sign(self, offer, submission_id="test-sign"):
        result = self.post("orders", unit_id="unit_4b", offer_id=offer["offer_id"],
                           offer_version=main.offer_version(offer), consent_given=True,
                           submission_id=submission_id, signed_name="Lena Hoffmann")
        return self.code, result

    def save_catalogue(self, lines):
        self.post("offers", text="\n".join(lines))
        self.assertEqual(self.code, 200)

    def test_duplicate_name_and_term_stays_bound_to_selected_offer_after_reorder(self):
        lines = ["Green Home 12 | 78.40 | 100 | 12", "Green Home 12 | 99.00 | 100 | 12"]
        self.save_catalogue(lines)
        offers = main.read_json(main.OFFERS_FILE)["offers"]
        selected = next(item for item in offers if item["estimated_monthly_price_eur"] == 99)
        self.review(selected)

        self.save_catalogue(list(reversed(lines)))
        reordered = main.read_json(main.OFFERS_FILE)["offers"]
        after_reorder = next(item for item in reordered if item["offer_id"] == selected["offer_id"])
        self.assertEqual(main.offer_version(after_reorder), main.offer_version(selected))
        status, response = self.sign(after_reorder)
        self.assertEqual(status, 201)
        self.assertEqual(response["order"]["offer_id"], selected["offer_id"])
        self.assertEqual(response["order"]["estimated_monthly_price_eur"], 99)

    def test_price_change_and_plan_change_need_current_reviewed_consent(self):
        self.save_catalogue(["Green Home 12 | 78.40 | 100 | 12", "Flex Home 12 | 74.90 | 50 | 12"])
        first = self.current_offer()
        other = self.current_offer("flex_home_12_12m")
        self.review(first)

        # A stale tab tries to submit a different plan without the tenant reviewing it.
        status, response = self.sign(other, "changed-plan")
        self.assertEqual(status, 409)
        self.assertIn("Review this offer", response["error"])

        old_version = main.offer_version(first)
        self.save_catalogue(["Green Home 12 | 99.00 | 100 | 12", "Flex Home 12 | 74.90 | 50 | 12"])
        repriced = self.current_offer(first["offer_id"])
        self.assertEqual(repriced["offer_id"], first["offer_id"])
        self.assertNotEqual(main.offer_version(repriced), old_version)
        stale = self.post("orders", unit_id="unit_4b", offer_id=first["offer_id"], offer_version=old_version,
                          consent_given=True, submission_id="stale-price")
        self.assertEqual(self.code, 409)
        self.assertIn("offer changed", stale["error"])

        self.review(repriced)
        status, fresh = self.sign(repriced, "fresh-price")
        self.assertEqual(status, 201)
        self.assertEqual(fresh["order"]["estimated_monthly_price_eur"], 99)
        self.assertEqual(fresh["order"]["consent"]["offer_version"], main.offer_version(repriced))

    def test_missing_and_malformed_postcodes_are_flagged_and_block_orders(self):
        offer = self.current_offer()
        for address in ("Musterstraße 12, Aachen", "Musterstraße 12, 5206X Aachen"):
            self.post("reset")
            unit = main.read_json(main.UNIT_FILE)
            unit["property"]["address"] = address
            main.write_json(main.UNIT_FILE, unit)
            self.post("run")
            run_row = next(row for row in self.result["run"]["results"] if row["unit_id"] == "unit_4b")
            self.assertEqual(run_row["decision"], "attention")
            self.assertIn("postcode", run_row["reason"])
            case = self.view()["case"]
            self.assertEqual(case["coverage_review"]["status"], "unknown")
            self.review(offer)
            result = self.post("orders", unit_id="unit_4b", offer_id=offer["offer_id"],
                               offer_version=main.offer_version(offer), consent_given=True)
            self.assertEqual(self.code, 409)
            self.assertIn("postcode", result["error"])

    def test_consent_is_visible_on_submission_and_not_duplicated_on_confirmation(self):
        offer = self.current_offer()
        self.review(offer)
        status, result = self.sign(offer)
        self.assertEqual(status, 201)
        order = result["order"]
        log = self.view()["unit"]["consent_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["order_id"], order["order_id"])
        self.assertEqual(log[0]["recipient"], "Example Green Energy GmbH")
        self.assertEqual(log[0]["signed_name"], "Lena Hoffmann")
        self.assertEqual(log[0]["offer_snapshot"]["estimated_monthly_price_eur"], 78.4)

        self.post(f"orders/{order['order_id']}/status", status="received")
        self.post(f"orders/{order['order_id']}/status", status="confirmed")
        log = self.view()["unit"]["consent_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["offer_id"], offer["offer_id"])

    def test_signed_term_expiry_and_next_renewal_cycle(self):
        offer = self.current_offer()
        self.review(offer)
        status, result = self.sign(offer, "cycle-one")
        self.assertEqual(status, 201)
        first = result["order"]
        self.post(f"orders/{first['order_id']}/status", status="received")
        self.post(f"orders/{first['order_id']}/status", status="needs_input", note="Please send the switch reading before 2026-12-31.")
        self.post("clock", today="2026-12-31")
        self.assertEqual(main.read_orders()[0]["status"], "received")
        self.post(f"orders/{first['order_id']}/status", status="confirmed")
        self.post(f"orders/{first['order_id']}/status", status="active")
        self.assertEqual(self.code, 200, self.result)
        self.assertTrue(any(item.get("status") == "active" for item in main.read_json(main.UNIT_FILE)["contracts"]),
                        main.read_json(main.UNIT_FILE)["contracts"])

        contract = next(item for item in main.read_json(main.UNIT_FILE)["contracts"] if item.get("order_id") == first["order_id"])
        self.assertEqual(contract["start"], "2027-01-01")
        self.assertEqual(contract["expiry_date"], "2027-12-31")
        self.assertEqual(contract["contract_months"], 12)

        self.post("clock", today="2027-12-01")
        row = next(row for row in self.result["run"]["results"] if row["unit_id"] == "unit_4b")
        self.assertEqual(row["decision"], "matched", row)
        next_offer = self.current_offer()
        self.review(next_offer)
        status, second_response = self.sign(next_offer, "cycle-two")
        self.assertEqual(status, 201)
        second = second_response["order"]
        self.assertNotEqual(second["renewal_cycle"], first["renewal_cycle"])
        self.assertEqual(second["requested_start"], "2028-01-01")


if __name__ == "__main__":
    unittest.main()
