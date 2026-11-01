"""Per-conversation token vault (trusted local boundary).

Restricted values (order ids, emails, phones, tracking codes, delivery dates ...) are
replaced by opaque tokens like ``[[ORDER_1]]`` before anything reaches the external model.
Tools resolve tokens locally; trusted code reveals tokens in the final answer.
Tokens are sequential per conversation, never derived from the values themselves.
Persistence (Redis with TTL) is handled by a VaultStore implementation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"\[\[([A-Z]+)_(\d+)\]\]")

TokenKind = str  # ORDER | EMAIL | PHONE | TRACKING | DATE | NAME | CPF | CEP | SECRET | VALUE


@dataclass
class PrivacyVault:
    values: dict[str, tuple[TokenKind, str]] = field(default_factory=dict)  # token -> (kind, v)
    counters: dict[TokenKind, int] = field(default_factory=dict)

    def tokenize(self, kind: TokenKind, value: str) -> str:
        kind = kind.upper()
        for token, (k, v) in self.values.items():
            if k == kind and v == value:
                return token
        n = self.counters.get(kind, 0) + 1
        self.counters[kind] = n
        token = f"[[{kind}_{n}]]"
        self.values[token] = (kind, value)
        return token

    def resolve(self, token: str) -> tuple[TokenKind, str] | None:
        token = token.strip()
        if not token.startswith("[["):
            token = f"[[{token}]]"
        return self.values.get(token)

    def reveal(self, text: str) -> str:
        """Replace known tokens with their values. Unknown tokens are left untouched."""

        def sub(m: re.Match[str]) -> str:
            hit = self.values.get(m.group(0))
            return hit[1] if hit else m.group(0)

        return TOKEN_RE.sub(sub, text)

    def to_state(self) -> dict[str, object]:
        return {
            "values": {t: [k, v] for t, (k, v) in self.values.items()},
            "counters": dict(self.counters),
        }

    @classmethod
    def from_state(cls, state: dict[str, object] | None) -> PrivacyVault:
        if not state:
            return cls()
        raw_values = state.get("values") or {}
        raw_counters = state.get("counters") or {}
        assert isinstance(raw_values, dict) and isinstance(raw_counters, dict)
        return cls(
            values={t: (str(kv[0]), str(kv[1])) for t, kv in raw_values.items()},
            counters={str(k): int(v) for k, v in raw_counters.items()},
        )
