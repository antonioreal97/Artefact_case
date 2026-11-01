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

## Consequences

Heavier runtime (Postgres, Redis, Docker) than ADR 003. The PII rule means order answers
are assembled from tokens, which limits model phrasing freedom for order details.
