"""Deterministic local minimization and the final model egress boundary.

Names cannot reliably be inferred from arbitrary prose. The caller must supply known
customer values (including names) through ``known_restricted_values``. This is not NER.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

from app.services.privacy_vault import TOKEN_RE, PrivacyVault
from emporio_agent.privacy import PrivacyBoundaryError, find_restricted_data

__all__ = ["PrivacyBoundaryError", "redact_user_text", "scan_outbound"]

_PATTERNS = (
    ("SECRET", re.compile(r"(?i)\b(?:sk-[\w-]{12,}|AIza[\w-]{20,}|emp_[\w-]{12,})\b")),
    (
        "SECRET",
        re.compile(r"(?i)(?:bearer\s+|(?:password|senha|api[_ -]?key|token)\s*[:=]\s*)([^\s,;]+)"),
    ),
    ("EMAIL", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("TRACKING", re.compile(r"(?i)\bBR[A-Z0-9]{9}BR\b")),
    ("CPF", re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")),
    ("CPF", re.compile(r"(?i)\bCPF\s*[:=]?\s*(\d{11})(?!\d)")),
    ("CEP", re.compile(r"(?<!\d)\d{5}-\d{3}(?!\d)")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-\s]?\d{4}(?!\d)")),
    ("CEP", re.compile(r"(?<!\d)\d{8}(?!\d)")),
    ("ORDER", re.compile(r"(?i)(?:\b(?:pedido|order)\s*(?:n[ºo°.]*\s*)?#?\s*|#\s*)(\d+)\b")),
)


def redact_user_text(
    text: str,
    vault: PrivacyVault,
    *,
    known_restricted_values: Iterable[str] = (),
) -> str:
    # Select non-overlapping spans in priority order; never re-scan newly inserted tokens.
    spans: list[tuple[int, int, str, str]] = []
    for value in sorted(set(known_restricted_values), key=len, reverse=True):
        if value.strip():
            for match in re.finditer(re.escape(value), text, flags=re.IGNORECASE):
                if not any(
                    match.start() < end and match.end() > start for start, end, _, _ in spans
                ):
                    spans.append((match.start(), match.end(), "NAME", match.group()))
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            group = 1 if match.lastindex else 0
            start, end = match.span(group)
            if not any(start < b and end > a for a, b, _, _ in spans):
                spans.append((start, end, kind, match.group(group)))
    result: list[str] = []
    cursor = 0
    for start, end, kind, value in sorted(spans):
        result.extend((text[cursor:start], vault.tokenize(kind, value)))
        cursor = end
    result.append(text[cursor:])
    return "".join(result)


def scan_outbound(payload: object, *, allowlist: Iterable[str] = ()) -> None:
    """Reject raw restricted fields/text, including JSON-encoded tool results.

    Tokens and exact public business contacts are ignored only for scanning. The
    original payload is never changed. Error messages contain no values or paths.
    """
    public_values = sorted({v for v in allowlist if v.strip()}, key=len, reverse=True)

    def clean(value: object) -> object:
        if isinstance(value, str):
            # Tool outputs and arguments are usually JSON strings in the Responses API.
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, (dict, list, str)):
                return clean(decoded)
            result = TOKEN_RE.sub("", value)
            for public in public_values:
                normalized = r"\s*".join(re.escape(c) for c in public if not c.isspace())
                result = re.sub(
                    r"(?<![\w@.+-])" + normalized + r"(?![\w@.+-])",
                    "",
                    result,
                    flags=re.IGNORECASE,
                )
            # Additional local patterns cover CPF, bare #order refs and generic secrets.
            if any(pattern.search(result) for _, pattern in _PATTERNS):
                raise PrivacyBoundaryError("Outbound payload contains restricted data")
            return result
        if isinstance(value, Mapping):
            return {str(key): clean(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(child) for child in value]
        return value

    if find_restricted_data(clean(payload)):
        raise PrivacyBoundaryError("Outbound payload contains restricted data")
