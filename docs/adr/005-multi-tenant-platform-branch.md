# ADR 005 Multi-Tenant Platform Variant (pgvector RAG + OpenAI Agents SDK)

- Status: Accepted for the `test-rag` branch (experiment); does not replace ADR 003/004 on `main`
- Date: 2026-10-31
- Owners: Project team

## Context

The owner requested a multi-tenant commerce-support platform: FastAPI, OpenAI Agents SDK,
PostgreSQL + pgvector, Redis, Docker, hybrid RAG over merchant documents and typed tools for
dynamic data. This contradicts ADR 003 (SQLite + curated retrieval) and ADR 004 (no agent
framework). The owner chose to build it while **keeping ADR 002 / SECURITY.md (no raw PII
egress)** intact. Seed data covers only Empório da Música; a second synthetic tenant exists
only inside tests to prove isolation.

## Decision

- Agents SDK is used for the tool loop only, behind a privacy-guarded `Model` wrapper that
  scans every outbound payload (`emporio_agent.privacy`) and fails closed.
- User text is tokenized locally (`[[ORDER_1]]`, `[[EMAIL_1]]` ...) before reaching the
  model; tools resolve tokens locally; order tools return only an abstract status view;
  trusted code reveals tokens in the final answer (`app/services/privacy_vault.py`).
- Agents SDK tracing export is disabled by default; `store=False` on every model call.
- RAG: pgvector cosine + Postgres FTS (`portuguese`), RRF fusion, min-similarity gate,
  tenant filter in SQL on every query.
- Commerce data lives in tenant-scoped Postgres tables seeded from the CSV export and is
  reached only through the `CommerceBackend` protocol.

## Scope and privacy qualification (2026-11-01)

Multi-tenancy, generic uploads and WhatsApp are **optional platform experiments**, not
requirements of the Empório da Música case. The submission's supported walkthrough is one
seed tenant over the HTTP API, with deterministic providers; no real-model quality claim or
measured advantage over the smaller ADR 003/004 design is established (A-4 / A-6).

Tokenization is **pseudonymization with scoped linkability, not anonymization**. Tokens are
sequential per conversation, not random; repeated values reuse their placeholder. The
mapping stays in local Redis, keyed by tenant and conversation, with a sliding 24-hour TTL.
Final reveal is limited to current-turn values or an authorized order lookup in that turn.

This implemented token flow diverges from the original random-token and external reversible-
pseudonym restrictions in [SECURITY.md](../../SECURITY.md) and
[ADR 002](002-no-pii-external-egress.md). The platform decision did not remove those rules;
the divergence remains explicit under A-3. Raw PII, mappings and customer records must never
reach external models or logs. Tokenization does not make arbitrary restricted free text
eligible for external processing.

Detection is rule-based, with known tenant values and residual-cue checks. Ambiguous
restricted turns must fail closed to local handling or human handoff, but unknown lower-case
names without those cues can pass undetected (O-9). Multiple scans do not prove absence of
PII. See [the architecture review](../reviews/architecture-review.md) for the structured-input
boundary recommended to close the gap; this documentation round changes no runtime rule.

## Consequences

Heavier runtime (Postgres, Redis, Docker) than ADR 003. The PII rule means order answers
are assembled from tokens, which limits model phrasing freedom for order details.
