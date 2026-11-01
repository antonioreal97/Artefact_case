"""Deterministic local minimization and the final model egress boundary.

Detection is rule-based, not NER. Covered locally: e-mails, BR phones (whole span incl.
+55/area code/leading 9), tracking codes (seed ``BR…BR`` and UPU S10 ``LX123456789BR``),
CPF (with dots, dashes or spaces, or 11 bare digits with valid check digits), CEP, secrets,
order numbers (``pedido ... 8``, ``nº 8``, ``#8``, and every bare number in a message that
talks about an order), postal address lines (street keyword + number, ``apto``),
self-introductions (``meu nome é``/``me chamo``/``sou a`` + capitalized words) and known
customer names (full name, first+last and contiguous pairs, accent-insensitive, word
boundaries). Names typed in lower case outside a self-introduction are not detected; the
fail-closed classifier (``looks_restricted``) keeps residual identity/address/CPF cues away
from the external model instead.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from functools import lru_cache

from app.services.privacy_vault import TOKEN_RE, PrivacyVault
from emporio_agent.privacy import PrivacyBoundaryError, cpf_spans, find_restricted_data

__all__ = [
    "PrivacyBoundaryError",
    "fold_text",
    "looks_restricted",
    "redact_user_text",
    "scan_outbound",
]

_CAP_WORD = r"[A-ZÀ-Þ][a-zß-ÿ'’-]+"
_GAP = r"\s+(?:(?:d[aeo]s?|e)\s+)?"
_STREET = (
    r"(?:rua|r\.|avenida|av\.?|travessa|tv\.|alameda|al\.|rodovia|rod\.|estrada|"
    r"pra[çc]a|largo|quadra|qd\.)"
)
_COMPLEMENT = r"(?:apto|apartamento|ap\.|bloco|bl\.|casa|sala|conjunto|cj\.|lote|lt\.)"
# A standalone integer: not part of a word, a decimal/thousands number, a token or a price.
_INT_END = r"(?![\w%]|[.,]\d)"

_SECRET = (
    ("SECRET", re.compile(r"(?i)\b(?:sk-[\w-]{12,}|AIza[\w-]{20,}|emp_[\w-]{12,})\b")),
    (
        "SECRET",
        re.compile(r"(?i)(?:bearer\s+|(?:password|senha|api[_ -]?key|token)\s*[:=]\s*)([^\s,;]+)"),
    ),
)
_CONTACT = (
    ("EMAIL", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("TRACKING", re.compile(r"(?i)\b(?:BR[A-Z0-9]{9}BR|[A-Z]{2}\d{9}[A-Z]{2})\b")),
    ("CPF", re.compile(r"(?<!\d)\d{3}[.\s]\d{3}[.\s]\d{3}[\s.-]\d{2}(?!\d)")),
    ("CPF", re.compile(r"(?i:\bcpf\b)\D{0,12}?(\d{3}[.\s-]?\d{3}[.\s-]?\d{3}[.\s-]?\d{2})(?!\d)")),
    ("CEP", re.compile(r"(?i:\bcep\b)\s*[:=]?\s*(\d{2}\.?\d{3}[\s.-]?\d{3})(?!\d)")),
    ("CEP", re.compile(r"(?<!\d)\d{2}\.?\d{3}-\d{3}(?!\d)")),
    (
        "PHONE",
        re.compile(
            r"(?<![\w+])(?:\+\s*)?(?:55[\s.-]*)?(?:\(?\d{2}\)?[\s.-]*)?(?:9[\s.-]*)?"
            r"\d{4}[\s.-]?\d{4}(?!\d)"
        ),
    ),
    ("CEP", re.compile(r"(?<!\d)\d{8}(?!\d)")),
)
_INTRO = (
    "NAME",
    re.compile(
        r"(?i:\b(?:meu nome (?:completo )?[ée]|me chamo|sou (?:o|a)|aqui [ée] (?:o|a)|"
        r"quem fala [ée](?: (?:o|a))?)\s+)"
        rf"({_CAP_WORD}(?:{_GAP}{_CAP_WORD}){{0,4}})"
    ),
)
_ADDRESS = (
    (
        "ADDRESS",
        re.compile(
            rf"(?i:\b{_STREET})\s+[^\n;:?!]{{2,60}}?(?:,\s*|\s+)(?i:n[º°o.]*\s*)?\d{{1,6}}"
            rf"[A-Za-z]?\b(?:\s*[,-]?\s*(?i:{_COMPLEMENT})\s*\w{{1,6}})*"
        ),
    ),
    ("ADDRESS", re.compile(rf"(?i:\b{_COMPLEMENT})\s*\d{{1,5}}[A-Za-z]?\b")),
)
_ORDER_WORDS = re.compile(r"(?i)\b(?:pedidos?|compras?|encomendas?|orders?)\b")
_ORDER = (
    (
        "ORDER",
        re.compile(
            rf"(?i)\b(?:pedido|encomenda|compra|order)s?\b[^\d\n]{{0,15}}?(\d{{1,10}}){_INT_END}"
        ),
    ),
    (
        "ORDER",
        re.compile(
            r"(?i)\bn(?:[úu]mero|[º°]|o\.)\s*(?:d[oa]\s+(?:pedido|compra|encomenda)\s*)?"
            rf"[:=#]?\s*(\d{{1,10}}){_INT_END}"
        ),
    ),
    ("ORDER", re.compile(rf"#\s*(\d{{1,10}}){_INT_END}")),
)
_BARE_NUMBER = ("ORDER", re.compile(rf"(?<![\w.,/$#\[])(?<!R\$\s)(\d{{1,10}}){_INT_END}"))

# Transport metadata is not customer text. Only actual top-level Responses items
# receive these exemptions; JSON embedded in content/arguments cannot impersonate one.
_OUTPUT_TYPES = frozenset(
    {
        "reasoning",
        "function_call",
        "function_call_output",
        "file_search_call",
        "web_search_call",
        "computer_call",
        "computer_call_output",
        "code_interpreter_call",
        "image_generation_call",
        "local_shell_call",
        "local_shell_call_output",
        "compaction",
        "custom_tool_call",
        "custom_tool_call_output",
        "shell_call",
        "shell_call_output",
        "apply_patch_call",
        "apply_patch_call_output",
        "tool_search_call",
        "tool_search_output",
        "program",
        "program_output",
        "additional_tools",
        "mcp_call",
        "mcp_list_tools",
        "mcp_approval_request",
        "mcp_approval_response",
    }
)
_OPAQUE_FIELDS = frozenset({"id", "call_id", "encrypted_content", "status"})
# Local application schemas, never order/customer IDs or generic `id` fields.
_UUID_FIELDS = frozenset(
    {
        "conversation_id",
        "message_id",
        "handoff_id",
        "document_id",
        "document_version_id",
        "chunk_id",
    }
)
_UUID_VALUE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")

# The egress scanner is narrower than the user-text tokenizer: documents and tool outputs
# legitimately say "pedidos acima de R$ 500" or "Lei nº 8.078".
_SCAN_PATTERNS = (
    *_SECRET,
    *_CONTACT,
    _INTRO,
    _ADDRESS[0],
    ("ORDER", re.compile(r"(?i)(?:\b(?:pedido|order)\s*(?:n[ºo°.]*\s*)?[:#]?\s*|#\s*)\d+\b")),
    ("ORDER", re.compile(r"(?i)\bn[úu]mero d[oa] (?:pedido|compra)\s*[:=]?\s*\d")),
    ("ORDER", re.compile(r"(?i)\b(?:pedido|compra) [ée] o\s+\d")),
)

# Residual cues after tokenization: the turn must not reach the external model.
# Residual cues after tokenization (tokens are replaced by "§" first): the turn must not
# reach the external model.
_RESIDUAL = (
    re.compile(rf"(?i:\b{_STREET})\s+(?:(?:d[aeo]s?)\s+)?(?:[A-ZÀ-Þ]|\d)"),
    re.compile(r"(?i)\b(?:meu nome (?:completo )?[ée]|me chamo)\s+[^\W_]"),
    re.compile(r"(?i)\bmeu endere[çc]o (?:[ée]|fica)\s+[^\W_]"),
    re.compile(r"(?i)\b(?:cpf|rg|cnh)\b[^\d§]{0,12}\d"),
    re.compile(r"(?i)\b(?:data de nascimento|nasci em)\b[^\d§]{0,12}\d"),
    re.compile(r"(?<!\w)(?:\d[\s.-]?){11,}\d(?!\w)"),
)


def fold_text(text: str) -> str:
    """Case- and accent-insensitive form (same length mapping not preserved)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


def _fold_map(text: str) -> tuple[str, list[int]]:
    """Folded text plus, for each folded char, the index of its original char."""
    out: list[str] = []
    index: list[int] = []
    for i, char in enumerate(text):
        for folded in fold_text(char):
            out.append(folded)
            index.append(i)
    index.append(len(text))
    return "".join(out), index


def _name_alternatives(value: str) -> set[str]:
    words = [
        w
        for w in re.findall(r"\w+", fold_text(value))
        if w not in {"da", "de", "do", "das", "dos", "e"}
    ]
    if not words:
        return set()
    variants = {tuple(words)}
    if len(words) >= 2:
        variants.add((words[0], words[-1]))
        variants.update(
            tuple(words[i:j]) for i in range(len(words)) for j in range(i + 2, len(words) + 1)
        )
    return {r"\b" + _GAP.join(map(re.escape, v)) + r"\b" for v in variants}


@lru_cache(maxsize=32)
def _known_pattern(values: tuple[str, ...]) -> re.Pattern[str] | None:
    """One folded regex for known names, e-mails and phone numbers."""
    alternatives: set[str] = set()
    for raw in values:
        value = raw.strip()
        if len(value) < 3:
            continue
        digits = re.sub(r"\D", "", value)
        if "@" in value:
            alternatives.add(r"(?<![\w.+-])" + re.escape(fold_text(value)) + r"(?![\w.-])")
        elif len(digits) >= 8 and not re.search(r"[^\d\s().+-]", value):
            if len(digits) in (12, 13) and digits.startswith("55"):
                digits = digits[2:]
            alternatives.add(r"(?<!\d)" + r"[\s().+-]*".join(digits) + r"(?!\d)")
        else:
            alternatives.update(_name_alternatives(value))
    if not alternatives:
        return None
    return re.compile("|".join(sorted(alternatives, key=len, reverse=True)))


def _known_spans(text: str, values: Iterable[str]) -> list[tuple[int, int]]:
    pattern = _known_pattern(tuple(sorted({v for v in values if v and v.strip()})))
    if pattern is None:
        return []
    folded, index = _fold_map(text)
    return [
        (index[m.start()], index[m.end() - 1] + 1)
        for m in pattern.finditer(folded)
        if m.end() > m.start()
    ]


def redact_user_text(
    text: str,
    vault: PrivacyVault,
    *,
    known_restricted_values: Iterable[str] = (),
) -> str:
    """Tokenize restricted values. ``known_restricted_values`` are customer NAMES."""
    # Existing vault tokens are kept verbatim; spans never overlap and are chosen in
    # priority order; newly inserted tokens are never re-scanned.
    spans: list[tuple[int, int, str, str]] = [
        (m.start(), m.end(), "", m.group()) for m in TOKEN_RE.finditer(text)
    ]

    def add(start: int, end: int, kind: str) -> None:
        if start < end and not any(start < b and end > a for a, b, _, _ in spans):
            spans.append((start, end, kind, text[start:end]))

    def add_pattern(kind: str, pattern: re.Pattern[str]) -> None:
        for match in pattern.finditer(text):
            group = 1 if match.lastindex else 0
            add(*match.span(group), kind)

    for kind, pattern in (*_SECRET, *_CONTACT):
        add_pattern(kind, pattern)
    # After PHONE: a valid mobile shape stays a phone; other check-digit-valid runs are CPF.
    for start, end in cpf_spans(text):
        add(start, end, "CPF")
    for start, end in _known_spans(text, known_restricted_values):
        add(start, end, "NAME")
    for kind, pattern in (_INTRO, *_ADDRESS, *_ORDER):
        add_pattern(kind, pattern)
    if _ORDER_WORDS.search(text):
        add_pattern(*_BARE_NUMBER)

    result: list[str] = []
    cursor = 0
    for start, end, kind, value in sorted(spans):
        result.extend((text[cursor:start], vault.tokenize(kind, value) if kind else value))
        cursor = end
    result.append(text[cursor:])
    return "".join(result)


def looks_restricted(redacted: str) -> bool:
    """True when tokenized text still carries address/identity/CPF cues (fail closed)."""
    text = TOKEN_RE.sub("§", redacted)
    return any(pattern.search(text) for pattern in _RESIDUAL)


def public_persona_values(assistant_name: str, store_name: str) -> list[str]:
    """Public persona spellings, scoped to the configured tenant, not arbitrary names."""
    store = re.sub(r"(?i)^(?:o|a)\s+", "", store_name).strip()
    values = [assistant_name, store_name]
    if store:
        for role in ("Assistente", "Assistente virtual"):
            for article in ("", "de ", "da ", "do "):
                values.append(f"{role} {article}{store}")
    return values


def scan_outbound(
    payload: object,
    *,
    allowlist: Iterable[str] = (),
    known_restricted_values: Iterable[str] = (),
) -> None:
    """Reject raw restricted fields/text, including JSON-encoded tool results.

    Tokens and exact public business contacts are ignored only for scanning. The
    original payload is never changed. Error messages contain no values or paths.
    ``known_restricted_values`` are the tenant's customer names, e-mails and phones.
    Top-level provider input items omit opaque transport fields from this scan only;
    decoded customer text never gains those exemptions. Structured local identifier
    fields require canonical UUID values; order/customer IDs remain restricted.
    """
    public_values = sorted({v for v in allowlist if v.strip()}, key=len, reverse=True)
    known = tuple(sorted({v for v in known_restricted_values if v and v.strip()}))
    known_pattern = _known_pattern(known) if known else None

    def clean(value: object, *, schema_ids: bool = False) -> object:
        if isinstance(value, str):
            # Tool outputs and arguments are usually JSON strings in the Responses API.
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, (dict, list, str)):
                return clean(decoded, schema_ids=schema_ids)
            result = TOKEN_RE.sub(" ", value)
            # A role alone is not a personal name. Require a sentence boundary so
            # "Sou o Assistente João Silva" still reaches the name detector.
            result = re.sub(
                r"(?i)\bsou (?:o|a) assistente(?: virtual)?(?=\s*(?:[.!?;,:]|$))",
                " ",
                result,
            )
            for public in public_values:
                normalized = r"\s*".join(re.escape(c) for c in public if not c.isspace())
                result = re.sub(
                    r"(?<![\w@.+-])" + normalized + r"(?![\w@+-]|\.\w)",
                    " ",
                    result,
                    flags=re.IGNORECASE,
                )
            if any(pattern.search(result) for _, pattern in _SCAN_PATTERNS):
                raise PrivacyBoundaryError("Outbound payload contains restricted data")
            if known_pattern is not None and known_pattern.search(fold_text(result)):
                raise PrivacyBoundaryError("Outbound payload contains restricted data")
            return result if result.strip() else ""
        if isinstance(value, Mapping):
            return {
                str(key): clean(child, schema_ids=schema_ids and not isinstance(child, str))
                for key, child in value.items()
                if not (
                    schema_ids
                    and key in _UUID_FIELDS
                    and isinstance(child, str)
                    and _UUID_VALUE.fullmatch(child)
                )
            }
        if isinstance(value, (list, tuple)):
            return [clean(child, schema_ids=schema_ids) for child in value]
        return value

    def clean_item(item: object) -> object:
        if isinstance(item, Mapping) and (
            (isinstance(item.get("type"), str) and item.get("type") in _OUTPUT_TYPES)
            or (item.get("type") == "message" and item.get("role") == "assistant")
        ):
            return {
                str(key): clean(child, schema_ids=key == "output")
                for key, child in item.items()
                if key not in _OPAQUE_FIELDS
            }
        return clean(item)

    # Never log this projection or its original encrypted/provider metadata.
    cleaned: object
    if isinstance(payload, Mapping) and isinstance(payload.get("input"), list):
        cleaned = {
            str(key): [clean_item(item) for item in child] if key == "input" else clean(child)
            for key, child in payload.items()
        }
    elif isinstance(payload, list):
        cleaned = [clean_item(item) for item in payload]
    else:
        cleaned = clean(payload, schema_ids=isinstance(payload, Mapping))
    if find_restricted_data(cleaned):
        raise PrivacyBoundaryError("Outbound payload contains restricted data")
