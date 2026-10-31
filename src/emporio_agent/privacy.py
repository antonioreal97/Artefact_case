"""Fail-closed privacy controls for model egress and operational logs.

This module has no provider SDK dependency. Every external model adapter must call
``assert_external_payload_safe`` immediately before network serialization and must build
its normalized options through ``harden_provider_options``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping


class DataClassification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    SECRET = "secret"


class PrivacyBoundaryError(ValueError):
    """Raised when content is not safe to cross an external-provider boundary."""


@dataclass(frozen=True)
class PrivacyFinding:
    kind: str
    path: str


_SENSITIVE_KEYS = frozenset(
    {
        "address",
        "authorization_token",
        "customer_id",
        "customer_name",
        "email",
        "full_address",
        "full_name",
        "order_id",
        "phone",
        "recipient",
        "session_token",
        "tracking_code",
    }
)

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email",
        re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    ),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-\s]?\d{4}(?!\d)"),
    ),
    (
        "tracking_code",
        re.compile(r"(?i)\bBR[A-Z0-9]{9}BR\b"),
    ),
    (
        "order_reference",
        re.compile(r"(?i)\b(?:pedido|order)\s*(?:n[ºo°.]*\s*)?#?\s*\d+\b"),
    ),
    (
        "postal_code",
        re.compile(r"(?<!\d)\d{5}-?\d{3}(?!\d)"),
    ),
    (
        "secret",
        re.compile(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})\b"),
    ),
)

_PROVIDER_PRIVACY_DEFAULTS: dict[str, dict[str, bool]] = {
    "openai": {
        "store": False,
        "remote_tools": False,
        "file_uploads": False,
        "explicit_cache": False,
        "contribute_data": False,
    },
    "anthropic": {
        "zdr_required": True,
        "hosted_tools": False,
        "file_uploads": False,
        "explicit_cache": False,
        "contribute_data": False,
    },
    "google": {
        "store": False,
        "logging": False,
        "grounding": False,
        "file_uploads": False,
        "explicit_cache": False,
        "contribute_data": False,
    },
}


def external_processing_allowed(classification: DataClassification) -> bool:
    """Return whether a payload class may cross an external model boundary."""

    return classification in {DataClassification.PUBLIC, DataClassification.INTERNAL}


def find_restricted_data(
    value: Any,
    *,
    known_restricted_values: Iterable[str] = (),
) -> tuple[PrivacyFinding, ...]:
    """Find obvious restricted data without returning the detected values themselves.

    Customer names and other business-specific identifiers must be supplied through
    ``known_restricted_values`` by the trusted local data layer. Findings expose only a
    category and structural path so scanners and logs do not create another PII copy.
    """

    known = tuple(
        candidate.casefold()
        for candidate in known_restricted_values
        if candidate and len(candidate.strip()) >= 3
    )
    findings: list[PrivacyFinding] = []

    def visit(current: Any, path: str) -> None:
        if isinstance(current, Mapping):
            for raw_key, child in current.items():
                key = str(raw_key)
                child_path = f"{path}.{key}"
                if key.casefold() in _SENSITIVE_KEYS and child not in (None, "", [], {}):
                    findings.append(PrivacyFinding("sensitive_field", child_path))
                    continue
                visit(child, child_path)
            return

        if isinstance(current, (list, tuple, set, frozenset)):
            for index, child in enumerate(current):
                visit(child, f"{path}[{index}]")
            return

        if not isinstance(current, str):
            return

        for kind, pattern in _PATTERNS:
            if pattern.search(current):
                findings.append(PrivacyFinding(kind, path))

        folded = current.casefold()
        if any(candidate in folded for candidate in known):
            findings.append(PrivacyFinding("known_restricted_value", path))

    visit(value, "$")
    return tuple(findings)


def assert_external_payload_safe(
    payload: Any,
    *,
    classification: DataClassification,
    known_restricted_values: Iterable[str] = (),
) -> None:
    """Fail closed when a payload contains or is classified as restricted data."""

    if not external_processing_allowed(classification):
        raise PrivacyBoundaryError(
            f"External processing denied for {classification.value} data"
        )

    findings = find_restricted_data(
        payload, known_restricted_values=known_restricted_values
    )
    if findings:
        categories = ", ".join(sorted({finding.kind for finding in findings}))
        raise PrivacyBoundaryError(
            f"External payload blocked by privacy guard: {categories}"
        )


def redact_for_log(
    value: Any,
    *,
    known_restricted_values: Iterable[str] = (),
) -> Any:
    """Return a structurally similar value with restricted content removed."""

    known = tuple(
        candidate
        for candidate in known_restricted_values
        if candidate and len(candidate.strip()) >= 3
    )

    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, child in value.items():
            if str(key).casefold() in _SENSITIVE_KEYS and child not in (None, ""):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_for_log(
                    child, known_restricted_values=known
                )
        return redacted

    if isinstance(value, list):
        return [redact_for_log(item, known_restricted_values=known) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_for_log(item, known_restricted_values=known) for item in value)
    if not isinstance(value, str):
        return value

    result = value
    for kind, pattern in _PATTERNS:
        result = pattern.sub(f"[REDACTED_{kind.upper()}]", result)
    for candidate in sorted(known, key=len, reverse=True):
        result = re.sub(
            re.escape(candidate),
            "[REDACTED_KNOWN_VALUE]",
            result,
            flags=re.IGNORECASE,
        )
    return result


def harden_provider_options(
    provider: str,
    requested: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply non-overridable normalized privacy defaults for an external provider."""

    normalized_provider = provider.casefold()
    if normalized_provider not in _PROVIDER_PRIVACY_DEFAULTS:
        raise PrivacyBoundaryError(f"Unapproved external provider: {provider}")

    protected = _PROVIDER_PRIVACY_DEFAULTS[normalized_provider]
    requested_options = dict(requested or {})
    conflicting = {
        key
        for key, required_value in protected.items()
        if key in requested_options and requested_options[key] != required_value
    }
    if conflicting:
        raise PrivacyBoundaryError(
            "Unsafe provider option override: " + ", ".join(sorted(conflicting))
        )

    return {**requested_options, **protected}

