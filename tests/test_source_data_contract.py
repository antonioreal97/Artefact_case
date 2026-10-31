import csv
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = next(ROOT.glob("Desafio Te*AI Engineer 2026")) / "data"


def read_csv(suffix):
    path = next(DATA_DIR.glob(f"*{suffix}.csv"))
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


class SourceDataContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.products = {row["product_id"]: row for row in read_csv("products")}
        cls.promotions = {row["promotion_id"]: row for row in read_csv("promotions")}
        cls.orders = {row["order_id"]: row for row in read_csv("orders")}
        cls.customers = {row["customer_id"]: row for row in read_csv("customers")}

    def test_takamine_gd20_golden_fact(self):
        product = self.products["95"]
        self.assertIn("Takamine GD20", product["name"])
        self.assertEqual(float(product["price_brl"]), 2199.0)
        self.assertEqual(product["status"], "active")

    def test_unavailable_product_fixtures(self):
        self.assertEqual(int(self.products["96"]["stock_quantity"]), 0)
        self.assertEqual(self.products["113"]["status"], "discontinued")
        self.assertEqual(self.products["130"]["status"], "coming_soon")

    def test_active_promotions_are_the_expected_fixture_set(self):
        active_ids = {
            promotion_id
            for promotion_id, row in self.promotions.items()
            if row["is_active"] == "1"
        }
        self.assertEqual(active_ids, {"12", "14", "19", "24"})
        self.assertEqual(self.promotions["24"]["product_id"], "94")
        self.assertEqual(self.promotions["24"]["discount_percent"], "8")
        self.assertEqual(self.promotions["4"]["is_active"], "0")

    def test_order_customer_relationship_used_by_privacy_cases(self):
        order = self.orders["8"]
        self.assertEqual(order["customer_id"], "2")
        self.assertEqual(order["status"], "shipped")
        self.assertEqual(order["tracking_code"], "BRJL5544332BR")
        self.assertEqual(self.customers["2"]["phone"], "(67) 99654-7821")

    def test_pix_discount_fixture_math(self):
        price = float(self.products["95"]["price_brl"])
        self.assertEqual(round(price * 0.95, 2), 2089.05)


if __name__ == "__main__":
    unittest.main()

