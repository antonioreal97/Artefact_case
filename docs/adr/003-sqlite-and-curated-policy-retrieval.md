# ADR 003 SQLite and Curated Policy Retrieval

- Status: Accepted for implementation
- Date: 2026-10-31
- Owners: Project team

## Context

The supplied operational dataset contains six small related CSV files and 191 total rows.
The policy manual is an eight-page PDF with stable numbered sections. Operational
facts require exact joins and filters; policy answers require preservation of conditions
and exceptions.

## Decision

Validate immutable CSV inputs and load them into an in-memory or disposable SQLite
database. Repository code owns parameterized queries; models do not generate SQL.

Curate the policy manual into versioned JSON/Markdown sections and retrieve them through
intent mapping plus deterministic keyword search. Do not introduce embeddings or a vector
database in the first implementation.

## Consequences

### Positive

- Exact relational behavior with no database service.
- Small dependency and operational footprint.
- Auditable policy section IDs and deterministic regression tests.
- Reproducible derived data that can be discarded and rebuilt.

### Negative

- Curated policy content requires review when the source manual changes.
- Keyword/intent retrieval may not scale to a large or heterogeneous corpus.
- SQLite is not the target for high-concurrency distributed production workloads.

## Alternatives rejected or deferred

- pandas as the main repository layer: convenient analysis but weaker domain/query boundary.
- LLM-generated SQL: unnecessary correctness and security risk.
- Vector database: corpus is too small to justify it before a retrieval failure is measured.
- PostgreSQL: no current concurrency or persistence requirement.

## Validation

Ingestion must reject schema, type, enum, duplicate-key and orphan-reference errors.
Retrieval must meet the frozen policy-section recall gate. Embeddings may be reconsidered
only if deterministic retrieval fails that gate on representative paraphrases.

