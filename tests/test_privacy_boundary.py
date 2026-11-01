import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from emporio_agent.privacy import (  # noqa: E402
    DataClassification,
    PrivacyBoundaryError,
    assert_external_payload_safe,
    external_processing_allowed,
    harden_provider_options,
    redact_for_log,
)


class PrivacyBoundaryTests(unittest.TestCase):
    def assert_blocked(self, payload, **kwargs):
        with self.assertRaises(PrivacyBoundaryError):
            assert_external_payload_safe(
                payload,
                classification=DataClassification.INTERNAL,
                **kwargs,
            )

    def test_restricted_class_never_crosses_external_boundary(self):
        self.assertFalse(external_processing_allowed(DataClassification.RESTRICTED))
        with self.assertRaises(PrivacyBoundaryError):
            assert_external_payload_safe(
                {"answer": "generic"},
                classification=DataClassification.RESTRICTED,
            )

    def test_blocks_structured_customer_and_order_fields(self):
        for payload in (
            {"phone": "(67) 99654-7821"},
            {"email": "customer@example.com"},
            {"order_id": 8},
            {"tracking_code": "BRJL5544332BR"},
            {"customer_name": "Example Customer"},
        ):
            with self.subTest(payload=payload):
                self.assert_blocked(payload)

    def test_blocks_pii_embedded_in_free_text(self):
        for text in (
            "Meu telefone é (67) 99654-7821",
            "Escreva para customer@example.com",
            "Rastreio BRJL5544332BR",
            "Verifique o pedido 8",
            "CEP 79202-333",
        ):
            with self.subTest(text=text):
                self.assert_blocked({"message": text})

    def test_blocks_known_customer_name_without_logging_it(self):
        self.assert_blocked(
            {"message": "Ajude Ana Carolina Ferreira com a entrega"},
            known_restricted_values=["Ana Carolina Ferreira"],
        )

    def test_allows_minimized_non_identifying_order_state(self):
        payload = {
            "identity_verified": True,
            "order_status": "shipped",
            "has_tracking": True,
        }
        assert_external_payload_safe(
            payload,
            classification=DataClassification.INTERNAL,
        )

    def test_log_redaction_removes_nested_pii(self):
        event = {
            "trace_id": "trace-safe",
            "phone": "(67) 99654-7821",
            "message": "Ana Carolina Ferreira pediu o rastreio BRJL5544332BR",
        }
        redacted = redact_for_log(
            event,
            known_restricted_values=["Ana Carolina Ferreira"],
        )
        rendered = repr(redacted)
        self.assertNotIn("99654", rendered)
        self.assertNotIn("BRJL5544332BR", rendered)
        self.assertNotIn("Ana Carolina Ferreira", rendered)
        self.assertEqual(redacted["trace_id"], "trace-safe")

    def test_provider_options_enforce_privacy_defaults(self):
        self.assertIs(harden_provider_options("openai")["store"], False)
        self.assertIs(harden_provider_options("google")["logging"], False)
        self.assertIs(harden_provider_options("google")["contribute_data"], False)
        self.assertIs(harden_provider_options("anthropic")["zdr_required"], True)

    def test_unsafe_provider_option_override_is_rejected(self):
        for provider, options in (
            ("openai", {"store": True}),
            ("google", {"logging": True}),
            ("google", {"contribute_data": True}),
            ("anthropic", {"zdr_required": False}),
        ):
            with self.subTest(provider=provider, options=options):
                with self.assertRaises(PrivacyBoundaryError):
                    harden_provider_options(provider, options)

    def test_unknown_provider_fails_closed(self):
        with self.assertRaises(PrivacyBoundaryError):
            harden_provider_options("unknown-provider")


if __name__ == "__main__":
    unittest.main()
