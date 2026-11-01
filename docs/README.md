# Emporio da Musica Agent Documentation

This folder is the design contract for the Emporio da Musica customer-support agent.
It is intentionally provider-neutral: the same business logic, tools, context and tests
must run against a local model, OpenAI, Anthropic or Google.

## Document control

| Field | Value |
|---|---|
| Baseline | 0.1 pre-implementation |
| Date | 2026-10-31 |
| Status | Ready for owner review; architecture validation pending |
| Language | English documentation; PT-BR agent behavior |
| Change rule | Update requirements, design, tests and traceability together |

## Recommendation in one paragraph

Build one orchestrator with deterministic tools, a provider-neutral model gateway and a
risk-aware, data-aware router. Resolve facts, authorization and privacy in code. Restricted
customer/order turns remain deterministic or local; paid APIs receive only guarded public
or minimized internal data. Use a local model only on task slices it has passed. Keep a
paid-optimized profile available. The hybrid profile ships only if it is non-inferior on
quality and materially cheaper after local infrastructure cost.

## Recommended reading order

1. [Project context](project-context.md): problem, users, scope, requirements and success criteria.
2. [Solution architecture](architecture.md): components, flows, routing, trust and deployment boundaries.
3. [Technology stack](technology-stack.md): selected tools, non-selections and versioning policy.
4. [Technical design](technical-design.md): modules, state machine, interfaces, errors and configuration.
5. [Data contracts](data-contracts.md): schemas, relationships, normalization and data quality.
6. [Context specification](context-spec.md): source precedence, persona, tools and prompt behavior.
7. [Privacy and data protection](privacy-and-data-protection.md): no-PII egress and provider controls.
8. [Model strategy](model-strategy.md): candidate tiers, TCO and hybrid go/no-go rules.
9. [Test and validation strategy](evaluation-plan.md): suites, metrics, experiments and release gates.
10. [Requirements traceability](traceability-matrix.md): requirements mapped to controls and tests.
11. [Implementation roadmap](implementation-roadmap.md): build order and definition of done.

## Supporting artifacts

- [ADR-001](adr/001-hybrid-provider-neutral-architecture.md): provider-neutral tool-first cascade decision.
- [ADR-002](adr/002-no-pii-external-egress.md): local privacy boundary decision.
- [ADR-003](adr/003-sqlite-and-curated-policy-retrieval.md): data and retrieval decision.
- [ADR-004](adr/004-explicit-orchestration.md): explicit state-machine orchestration decision.
- [ADR-005](adr/005-multi-tenant-platform-branch.md): multi-tenant platform variant (`test-rag` branch).
- [Development process and decision log](development-process.md): how the `test-rag` platform was
  built, why each decision was taken, and open items.
- [Seed cases](evals/cases.jsonl): initial executable-style acceptance cases.
- [Case schema](evals/case.schema.json): contract for every evaluation case.
- [Experiment matrix](evals/experiment-matrix.csv): variants to compare.
- [Human review sheet](evals/human-review.csv): blinded review template.
- [Repository security policy](../SECURITY.md): mandatory trust boundaries and privacy invariants.

## Status

The documentation baseline is complete enough to start implementation, but the
architecture is still **proposed until validated**. Model selection remains deliberately
open until the same frozen corpus has run against all candidates. Model names and prices
are a dated discovery snapshot (2026-10-31), never implicit runtime defaults.

The only implementation already present is the isolated privacy boundary plus contract
tests. Repositories, orchestration, provider adapters and CLI remain planned.

## Open decisions before implementation

- Confirm the supported Python version and lock exact dependencies.
- Record available local inference hardware and shortlist models that fit it.
- Confirm which paid-provider credentials are available for the baseline benchmark.
- Approve the initial prompt/policy version and hidden evaluation split.
- Define production retention, legal basis, provider DPA and regional-processing needs if
  the prototype advances to real customer data.

Validate this pack with:

```bash
python -m unittest discover -s tests -v
```


