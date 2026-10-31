# ADR 001 Provider Neutral Tool First Cascade

- Status: Proposed, pending evaluation
- Date: 2026-10-31
- Owners: Project team

## Context

The agent must answer store-policy and operational questions accurately. The team wants to
reduce model cost with local inference but will choose paid-only inference if the quality
or user experience gap is material. Model catalogs change frequently, and this prototype
must remain small enough to explain and test.

## Decision

Use one explicit Python orchestrator, typed deterministic tools, section-based policy
retrieval, a provider-neutral model gateway and a risk/capability router. Qualify local
inference per task slice. Keep paid-only and hybrid profiles behind configuration. Do not
adopt a multi-agent network, LLM-generated SQL or local-first fallback as the default.

## Consequences

### Positive

- Business rules and privacy checks remain deterministic and auditable.
- Providers and models can be compared with the same prompts, tools and tests.
- Local inference can be introduced gradually without risking sensitive paths.
- A failed hybrid experiment does not require an architecture rewrite.

### Negative

- Provider adapters and a capability registry require some up-front engineering.
- Local inference adds hardware, deployment and observability work.
- Routing creates another component that must be evaluated for false decisions.

## Alternatives rejected for the first version

- **Multi-agent network:** adds handoff errors and tracing complexity without distinct
  domains or a large tool set.
- **One large prompt with all CSV/PDF content:** expensive, stale and difficult to audit.
- **Vector database from day one:** unnecessary for an eight-page numbered policy manual.
- **Always local then paid fallback:** doubles work and hurts tail latency when local fails.
- **Paid-provider voting:** multiplies cost and does not guarantee factual correctness.
- **Hard-coded provider SDK in domain logic:** makes comparisons and migration expensive.

## Validation

This ADR becomes Accepted only when the universal and hybrid release gates in
`../evaluation-plan.md` pass. Otherwise, accept the same architecture with the
`paid_optimized` profile and keep local inference disabled.

