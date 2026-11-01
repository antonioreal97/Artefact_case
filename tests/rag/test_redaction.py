"""Review M-6 regressions: ingestion redaction keeps policy text and original formatting,
redacts only personal-looking values, and matches known values with word boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.rag.extraction import extract_document
from app.rag.ingestion import (
    KNOWN_VALUE_MARKER,
    PERSONAL_DATA_MARKER,
    known_privacy_values,
    redact_document_text,
)
from scripts.commerce import DATA_DIR, load_commerce
from scripts.create_tenant import PUBLIC_CONTACT_VALUES

POLICY = Path(DATA_DIR) / "políticas_da_loja.pdf"


def _seed_known() -> list[str]:
    exports = load_commerce(DATA_DIR)
    return known_privacy_values(
        [(c["name"], c["email"], c["phone"]) for c in exports["customers"]],
        [(o["tracking_code"], o["notes"]) for o in exports["orders"]],
    )


def test_real_policy_keeps_delivery_text_and_phone_formatting() -> None:
    """Review repro: '• Prazo de entrega: 1 a 3 dias úteis.' became
    '• Prazo de [REDACTED_PERSONAL_DATA]' and '(67) 3341-4444' became '(67) 3341 - 4444'."""
    known = _seed_known()
    blocks = extract_document(POLICY.read_bytes(), filename=POLICY.name, source_type="pdf")
    original = "\n".join(b.text for b in blocks)
    stored = "\n".join(redact_document_text(b.text, known, PUBLIC_CONTACT_VALUES) for b in blocks)
    assert "Prazo de entrega: 1 a 3 dias úteis." in stored
    assert "(67) 3341-4444" in stored and "(67) 3341 - 4444" not in stored
    assert "[REDACTED_" not in stored  # the policy holds no customer data
    assert stored == original
    embedded = " ".join(
        redact_document_text(b.text, known, PUBLIC_CONTACT_VALUES, for_embedding=True)
        for b in blocks
    )
    assert "3341" not in embedded and "[PUBLIC_CONTACT]" in embedded
    assert "Prazo de entrega: 1 a 3 dias úteis." in embedded


def test_order_ids_and_short_notes_are_not_known_values() -> None:
    known = known_privacy_values(
        [("Joana Prado", "joana@example.test", "(67) 99999-0000")],
        [("BRAB1234567BR", "curta"), (None, "Endereço de entrega inválido informado")],
    )
    assert "curta" not in known
    assert "Endereço de entrega inválido informado" in known
    assert not any(v.isdigit() for v in known)
    text = "Frete grátis acima de R$ 299,00. Seção 101: pedidos acima de 500."
    assert redact_document_text(text, known, []) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Cliente: deve apresentar nota. Entrega: em até 10 dias úteis.", None),
        ("• Prazo de entrega: 1 a 3 dias úteis.", None),
        ("1. Saudação: cumprimentar o cliente pelo nome (se disponível).", None),
        ("Nome Fantasia Empório da Música", None),
        (
            "Destinatário: Joana Prado da Silva, entregar à tarde",
            f"Destinatário: {PERSONAL_DATA_MARKER}, entregar à tarde",
        ),
        (
            "Cliente: Ana Carolina Ferreira\nPrazo: 7 dias",
            f"Cliente: {PERSONAL_DATA_MARKER}\nPrazo: 7 dias",
        ),
        (
            "Moro na Rua das Acácias, 150, apto 32, Jardim dos Estados",
            f"Moro na {PERSONAL_DATA_MARKER}, Jardim dos Estados",
        ),
        ("Av. Afonso Pena, 1000 - Centro", f"{PERSONAL_DATA_MARKER} - Centro"),
        ("CPF 123.456.789-01 informado", f"{PERSONAL_DATA_MARKER} informado"),
        ("meu cpf é 123 456 789 01.", f"meu {PERSONAL_DATA_MARKER}."),
    ],
)
def test_contextual_pii_only_redacts_personal_values(text: str, expected: str | None) -> None:
    assert redact_document_text(text, [], []) == (expected or text)


def test_known_values_use_word_boundaries() -> None:
    known = ["Ana Paula", "Pagamento recusado pela operadora"]
    text = "Ana Paula ligou. Anapaula não. Nota: pagamento recusado pela operadora."
    out = redact_document_text(text, known, [])
    assert out == f"{KNOWN_VALUE_MARKER} ligou. Anapaula não. Nota: {KNOWN_VALUE_MARKER}."


def test_reserved_marker_is_rejected() -> None:
    with pytest.raises(ValueError):
        redact_document_text("PUBLICCONTACTTOKENAAEND", [], ["(67) 3341-4444"])
