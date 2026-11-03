# Empório da Música — documentation index

Start with the [root README](../README.md) for the runnable store-agent walkthrough,
technical rationale, interaction examples, limitations and coding-assistant disclosure.
This folder contains both the **original design** and evidence for the
**delivered platform implementation on `main`**, developed as the `test-rag` experiment.
They are not interchangeable specifications.

## Delivered implementation on `main`

The delivered solution implements FastAPI, one OpenAI Agents SDK tool loop, guarded model access,
PostgreSQL/pgvector hybrid document retrieval, typed commerce tools, Redis vault/state,
seed/import commands, tests and evaluation. Chat supports native **OpenAI**, **fake** and
**LangChain** adapters (OpenAI/Anthropic/Ollama); embeddings support native **OpenAI**,
**hash** and **LangChain** (OpenAI/Ollama, fixed at 1536 dimensions). Provider-neutral
model/embedding selection is implemented. Native OpenAI and LangChain with OpenAI were
validated in manual live runs on 2026-11-01; Anthropic and Ollama remain unvalidated live.
The original ADR 001 cascade and comparative benchmark remain unimplemented.

Multi-tenancy, generic uploads and WhatsApp are optional experiments beyond the case.
The deterministic walkthrough uses one seed tenant over the API with fake/hash providers.
The root README also records the limited live-model/embedding validation; it is not a
statistical quality evaluation. The second tenant is synthetic and test-only.

| Document | Purpose |
|---|---|
| [Running the agent](running.md) | Full Docker, host Python/uv, ports, PowerShell, real-model and in-container test instructions |
| [Limitations](limitations.md) | Full limitations and next steps (README keeps the top five) |
| [Platform extensions](platform/extensions.md) | Optional multi-tenancy, WhatsApp, uploads, jobs, rate limits, `/chat` UI, triage menu, welcome message |
| [Coding assistants](coding-assistants.md) | Full per-workstream tool table |
| [Customer-service flows](customer-service-flows.md) | Turn/intent flowcharts derived from code (guardrails, tools, handoffs, channels) for recruiters and the store team |
| [Browser test interface](platform/extensions.md) (section “Browser test interface”) | Local chat evaluation at `/chat`, gated with development docs; no frontend build or end-user authentication |
| [ADR 005](adr/005-multi-tenant-platform-branch.md) | Delivered stack and explicit deviations from the original architecture/token policy |
| [ADR 006](adr/006-langchain-model-layer.md) | LangChain model/embedding adapters under the existing Agents SDK loop and privacy boundary; offline regressions and limited live OpenAI validation |
| [Platform contracts](platform/contracts.md) | Shared interfaces and module ownership |
| [Development process](development-process.md) | Accepted fixes, decisions, current open items and dated integration results (§4.2) |
| [Security policy](../SECURITY.md) / [ADR 002](adr/002-no-pii-external-egress.md) | Normative no-raw-PII boundary, rule-based detection limits and reversible-token implementation divergence |
| [Architecture review R3](reviews/architecture-review.md) | Review of `407d846`, case fit and remaining limitations |
| [Root evaluation cases](../evals/rag_eval_cases.jsonl) | Cases used by the implemented evaluation harness |
| [Conversation examples](../examples/conversations/) (root README, section "Interaction examples") | Five saved deterministic demonstrations with English translations, plus separate live English recordings |

Current host suite (see root README): 1071 passed, 12 xfailed, 130 subtests passed;
deterministic evaluation passes all hard gates with soft metrics at 100%.
Historical X1 integration checkpoint (2026-11-01): 603 tests and 123 subtests —
see [development-process.md](development-process.md) §4.2. Passing fake-mode checks
does not establish LLM quality. The known O-8 flake remains open.

## Historical evidence for the delivered implementation

- [Security review R1](reviews/security-review.md): findings before E2/F2/E3/F3 fixes;
  original snapshot and line numbers are retained, with a status banner.
- [Adversarial findings R2](../tests/adversarial/FINDINGS.md): the original six findings and
  strict-xfail results; acceptance and later fixes are recorded in development process §4.1.
- [Evaluation report G1](../evals/REPORT.md): initial soft-metric failures before E2/E3;
  its banner points to the later integration evidence. Do not read its old scores as current.

## Original design pack (2026-10-31)

The documents below describe the initial provider-neutral plan: explicit Python
orchestration, SQLite and curated policy retrieval, with a proposed local/paid comparison.
They preserve reasoning and alternatives; their roadmap, implementation status and model
comparisons do **not** describe a second completed or benchmarked implementation.
ADR 005 scopes the platform's different choices. Common requirements remain useful, but
follow the implementation documents above for runtime behavior and the security policy for invariants.

1. [Project context](project-context.md): problem, users and original requirements.
2. [Solution architecture](architecture.md): original components, routing and boundaries.
3. [Technology stack](technology-stack.md): original selections and non-selections.
4. [Technical design](technical-design.md): original state-machine/module plan.
5. [Data contracts](data-contracts.md): supplied data and original schema assumptions.
6. [Context specification](context-spec.md): persona and source-precedence design.
7. [Privacy and data protection](privacy-and-data-protection.md): original privacy design;
   use SECURITY/ADR 002/005 for the explicit current policy/implementation distinction.
8. [Model strategy](model-strategy.md): unexecuted candidate/TCO comparison plan.
9. [Evaluation plan](evaluation-plan.md): original release-gate plan, distinct from G1.
10. [Traceability matrix](traceability-matrix.md): original requirement/control mapping.
11. [Implementation roadmap](implementation-roadmap.md): historical roadmap, not current status.

Supporting original artifacts:

- [ADR 001](adr/001-hybrid-provider-neutral-architecture.md): proposed provider-neutral cascade.
- [ADR 003](adr/003-sqlite-and-curated-policy-retrieval.md): SQLite and curated retrieval.
- [ADR 004](adr/004-explicit-orchestration.md): explicit orchestration without an agent framework.
- [Original seed cases](evals/cases.jsonl) and [schema](evals/case.schema.json).
- [Experiment matrix](evals/experiment-matrix.csv) and [human review sheet](evals/human-review.csv).

The earlier statement that only the isolated privacy boundary was implemented is obsolete
for the delivered implementation. Use the root README's pytest/evaluation commands to reproduce it;
the original provider comparisons and production readiness remain unvalidated.
