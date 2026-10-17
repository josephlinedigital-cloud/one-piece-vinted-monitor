import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import scanner

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / "config.json").read_text())
item = json.loads((ROOT / "demo" / "listings.json").read_text())["result"]["data"][0]
mock = json.loads((ROOT / "demo" / "valuation.json").read_text())


class TestCardIdentity(unittest.TestCase):
    def test_card_code(self):
        self.assertEqual(scanner.code_of("Shanks OP01-120 Manga"), "OP01-120")
        self.assertEqual(scanner.code_of("ST01 - 001"), "ST01-001")
        self.assertEqual(scanner.code_of("PRB01-003"), "PRB01-003")
        self.assertEqual(scanner.code_of("P-001"), "P-001")

    def test_art_variant(self):
        self.assertEqual(scanner.variant_of("Manga Rare"), "manga")
        self.assertEqual(scanner.variant_of("Super Alternate Art"), "manga")
        self.assertEqual(scanner.variant_of("Parallel Alt Art"), "alt")
        self.assertEqual(scanner.variant_of("(SP)"), "sp")
        self.assertIsNone(scanner.variant_of("Normal card".replace("Normal", "")))

    def test_skip_bundle_japanese_graded_unclear(self):
        self.assertIsNone(scanner.listing_identity({"title": "Japanese OP01-120 Manga Rare"}, cfg))
        self.assertIsNone(scanner.listing_identity({"title": "One Piece mixed collection bundle"}, cfg))
        self.assertIsNone(scanner.listing_identity({"title": "OP01-120 PSA 10 Manga"}, cfg))
        self.assertIsNone(scanner.listing_identity({"title": "OP01-120 Shanks"}, cfg))

    def test_variant_market_match(self):
        api_result = [{"product_id": 1, "name": "Shanks (120) (Super Alternate Art)", "card_number": "OP01-120", "market": 170.0},
                      {"product_id": 2, "name": "Shanks (120)", "card_number": "OP01-120", "market": 2.0}]
        with patch("requests.get") as request:
            request.return_value.json.return_value = api_result
            request.return_value.raise_for_status.return_value = None
            result = scanner.ticker_market("OP01-120", "manga")
        self.assertAlmostEqual(result[0], 170.0)

    def test_ambiguous_variant_fails_closed(self):
        api_result = [{"product_id": 1, "name": "Shanks (Super Alternate Art)", "card_number": "OP01-120", "market": 170},
                      {"product_id": 2, "name": "Shanks (Manga)", "card_number": "OP01-120", "market": 200}]
        with patch("requests.get") as request:
            request.return_value.json.return_value = api_result
            request.return_value.raise_for_status.return_value = None
            self.assertIsNone(scanner.ticker_market("OP01-120", "manga"))


class TestValuation(unittest.TestCase):
    def test_demo_profitable(self):
        result = scanner.evaluate(item, mock["uk_sold_gbp"], mock["benchmark_usd"], mock["usd_gbp_rate"], cfg)
        self.assertGreaterEqual(result["score"], 80)
        self.assertAlmostEqual(result["net_profit"], 58.34)
        self.assertEqual(result["sold_count"], 4)

    def test_no_unverified_sold_data(self):
        self.assertIsNone(scanner.evaluate(item, [130], 170, 0.75, cfg))

    def test_missing_buyer_protection_fee(self):
        altered = dict(item, total_price=None)
        self.assertIsNone(scanner.evaluate(altered, [120, 125, 128], 170, 0.75, cfg))

    def test_too_cheap_suspicious(self):
        suspicious = dict(item, displayed_price={"amount": 1, "currency": "GBP"}, total_price={"amount": 2, "currency": "GBP"})
        self.assertIsNone(scanner.evaluate(suspicious, [120, 125, 128], 170, 0.75, cfg))

    def test_uk_sold_filters(self):
        when = datetime.now(timezone.utc).date().isoformat()
        def sold(title, price, **kw):
            return {"title": title, "sold_date": when, "displayed_price": {"amount": price, "currency": "GBP"}, **kw}
        rows = [
            sold("Shanks OP01-120 manga English", 120),
            sold("Shanks OP01-120 manga Japanese", 99),
            sold("Shanks OP01-120 manga PSA 10", 500),
            sold("Shanks OP01-120 standard card", 20),
            sold("Shanks OP01-120 manga English", 150, best_offer_accepted=True),
            sold("Shanks OP01-120 manga English", 122),
        ]
        self.assertEqual(scanner.uk_sold_prices(rows, "OP01-120", "manga", True), [120, 122])

    def test_api_result_structure(self):
        self.assertEqual(len(scanner.extract_search_results({"data": [{"product_id": 1}]})), 1)
        self.assertEqual(len(scanner.extract_search_results([{"product_id": 1}])), 1)


if __name__ == "__main__":
    unittest.main()
