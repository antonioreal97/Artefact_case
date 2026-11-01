import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "docs" / "evals" / "cases.jsonl"
SCHEMA_PATH = ROOT / "docs" / "evals" / "case.schema.json"


class EvalCaseContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.cases = [
            json.loads(line)
            for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_seed_pack_has_meaningful_coverage(self):
        self.assertGreaterEqual(len(self.cases), 30)
        categories = {case["category"] for case in self.cases}
        self.assertTrue(
            {"catalog", "product", "promotion", "order_privacy", "adversarial", "multi_turn"}
            <= categories
        )

    def test_ids_are_unique_and_stable(self):
        ids = [case["id"] for case in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for case_id in ids:
            self.assertRegex(case_id, re.compile(r"^[a-z0-9_-]+$"))

    def test_required_fields_and_enums(self):
        required = set(self.schema["required"])
        for case in self.cases:
            with self.subTest(case=case.get("id")):
                self.assertFalse(required - set(case))
                self.assertIn(case["risk"], {"low", "medium", "high"})
                self.assertIsInstance(case["critical"], bool)
                self.assertTrue(case["source_refs"])
                self.assertTrue(case["messages"])
                self.assertIsInstance(case["expected_tools"], list)
                self.assertIsInstance(case["assertions"], dict)

    def test_message_roles_are_supported(self):
        for case in self.cases:
            for message in case["messages"]:
                with self.subTest(case=case["id"], message=message):
                    self.assertIn(message["role"], {"user", "assistant"})
                    self.assertTrue(message["content"].strip())

    def test_critical_privacy_cases_exist(self):
        critical_privacy = [
            case for case in self.cases if case["category"] == "order_privacy" and case["critical"]
        ]
        self.assertGreaterEqual(len(critical_privacy), 4)
        self.assertTrue(
            any(case["assertions"].get("must_require_verification") for case in critical_privacy)
        )

    def test_explicit_no_egress_privacy_cases_exist(self):
        blocked = [
            case
            for case in self.cases
            if case.get("external_egress_allowed") is False and case["critical"]
        ]
        self.assertGreaterEqual(len(blocked), 5)
        self.assertTrue(
            all(
                case.get("expected_route")
                in {"deterministic_local", "local_model", "human_handoff"}
                for case in blocked
            )
        )


if __name__ == "__main__":
    unittest.main()
