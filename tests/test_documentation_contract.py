import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


class DocumentationContractTests(unittest.TestCase):
    def test_required_document_set_exists(self):
        required = {
            "project-context.md",
            "architecture.md",
            "technology-stack.md",
            "technical-design.md",
            "data-contracts.md",
            "context-spec.md",
            "privacy-and-data-protection.md",
            "model-strategy.md",
            "evaluation-plan.md",
            "traceability-matrix.md",
            "implementation-roadmap.md",
        }
        existing = {path.name for path in DOCS.glob("*.md")}
        self.assertFalse(required - existing)

    def test_index_local_links_resolve(self):
        index = DOCS / "README.md"
        targets = re.findall(r"\[[^\]]+\]\(([^)]+)\)", index.read_text(encoding="utf-8"))
        for target in targets:
            if "://" in target or target.startswith("#"):
                continue
            with self.subTest(target=target):
                self.assertTrue((index.parent / target).resolve().exists())

    def test_all_requirements_are_traceable(self):
        context = (DOCS / "project-context.md").read_text(encoding="utf-8")
        matrix = (DOCS / "traceability-matrix.md").read_text(encoding="utf-8")
        expected = {
            *(f"FR-{number:03d}" for number in range(1, 13)),
            *(f"NFR-{number:03d}" for number in range(1, 13)),
            *(f"SEC-{number:03d}" for number in range(1, 8)),
        }
        self.assertFalse({item for item in expected if item not in context})
        self.assertFalse({item for item in expected if item not in matrix})

    def test_restricted_data_is_not_documented_as_paid_fallback_eligible(self):
        architecture = (DOCS / "architecture.md").read_text(encoding="utf-8").casefold()
        self.assertNotIn("sensitive turns to the best paid model", architecture)
        self.assertIn(
            "restricted data cannot",
            (DOCS / "technical-design.md").read_text(encoding="utf-8").casefold(),
        )

    def test_authoritative_security_policy_is_linked(self):
        index = (DOCS / "README.md").read_text(encoding="utf-8")
        privacy = (DOCS / "privacy-and-data-protection.md").read_text(encoding="utf-8")
        self.assertIn("../SECURITY.md", index)
        self.assertIn("Raw personal data does not cross", privacy)


if __name__ == "__main__":
    unittest.main()
