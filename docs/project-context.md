# Emporio da Musica Agent Project Context

## Purpose

This document defines the business problem, intended users, scope, requirements,
constraints and success criteria for the Emporio da Musica customer-support agent. It is
the baseline for architecture, implementation and validation decisions. The main decision
is to build a small, auditable text agent that retrieves operational facts through code,
uses language models only where they add value and keeps personal data inside the trusted
local boundary.

## Business problem

Emporio da Musica is a fictional musical-instrument store in Campo Grande, Brazil. Its
human team handles recurring questions about opening hours, products, stock, prices,
promotions, orders and store policies. Repetitive work consumes staff time and creates a
risk of inconsistent answers.

The prototype must demonstrate that a conversational agent can answer these questions
accurately, choose the correct information source and escalate safely when it cannot
complete a request.

## Product outcome

The first release is a Python CLI that supports short PT-BR conversations. It must:

- answer public store and policy questions;
- search products and present only confirmed price, status and stock data;
- apply promotion and payment rules correctly;
- protect customer and order information through local identity verification;
- decline requests outside the store scope without sounding robotic;
- preserve evidence about the source, route and validation result of every answer;
- allow paid and local models to be compared without changing business logic.

## Stakeholders and users

| Actor | Need | System responsibility |
|---|---|---|
| Store customer | Fast, clear and correct answers | Provide concise PT-BR support and safe clarification |
| Store support team | Fewer repetitive contacts | Automate bounded first-level requests and hand off exceptions |
| Store owner or operator | Controlled cost and risk | Make model routing, cost and privacy observable |
| Engineering team | Explainable implementation | Keep contracts, prompts, tools and tests versioned |
| Evaluators | Understand decisions and tradeoffs | Provide reproducible setup, evidence and known limitations |
| Data subject | Privacy and control over personal data | Minimize collection, prevent external PII egress and restrict access |

## Functional requirements

| ID | Requirement | Priority |
|---|---|---:|
| FR-001 | Accept and respond to multi-turn text messages in PT-BR | Must |
| FR-002 | Search available products by name, category, price and stock | Must |
| FR-003 | Return exact product price, status, stock and relevant specifications | Must |
| FR-004 | Return only active promotions and apply the non-cumulative PIX rule | Must |
| FR-005 | Answer hours, address, payment, return, shipping and warranty questions from the policy source | Must |
| FR-006 | Verify order identity locally using order ID plus matching phone or email | Must |
| FR-007 | Return order status or tracking only after successful authorization | Must |
| FR-008 | Handle unavailable, discontinued and coming-soon products transparently | Must |
| FR-009 | Refuse accessories and unrelated requests politely | Must |
| FR-010 | Provide a safe response or human handoff when evidence, authorization or a dependency is unavailable | Must |
| FR-011 | Support paid-only, local-development, hybrid-shadow and qualified-hybrid profiles | Should |
| FR-012 | Record privacy-safe route, source, latency, usage and validation metadata | Should |

## Non-functional requirements

| ID | Requirement | Acceptance direction |
|---|---|---|
| NFR-001 | Factual correctness | No invented price, stock, promotion, order or tracking data |
| NFR-002 | Privacy | No raw restricted data crosses an external provider boundary |
| NFR-003 | Authorization | No order data is returned before code-level identity verification |
| NFR-004 | Explainability | Every operational claim can be traced to a tool or policy section |
| NFR-005 | Provider portability | Domain and tool code contains no provider-specific SDK types |
| NFR-006 | Reliability | Bounded timeouts and retries; restricted requests fail closed |
| NFR-007 | Performance | User-visible p95 target of 4 seconds for the qualified hybrid profile |
| NFR-008 | Cost | Hybrid must reduce cost per successful resolution by at least 30% to justify its complexity |
| NFR-009 | Maintainability | Typed contracts, small modules, explicit orchestration and automated tests |
| NFR-010 | Reproducibility | Locked dependencies, versioned prompts/configuration and exact model IDs in reports |
| NFR-011 | Observability | No prompt or PII logging; privacy-safe operational and evaluation evidence |
| NFR-012 | Language quality | Concise, welcoming, informal-professional PT-BR suitable for messaging |

## Security requirements

| ID | Requirement |
|---|---|
| SEC-001 | Classify outgoing data as public, internal, restricted or secret |
| SEC-002 | Scan the serialized payload immediately before external network egress |
| SEC-003 | Keep identity, order, tracking and restricted conversation processing local |
| SEC-004 | Make provider privacy defaults non-overridable per request |
| SEC-005 | Redact PII and secrets from logs, traces, evals and human-review exports |
| SEC-006 | Prohibit automatic external fallback for restricted content |
| SEC-007 | Treat user input, retrieved content and model output as untrusted |

The repository-wide policy in `../SECURITY.md` is authoritative if this summary diverges.

## Primary use cases

1. Find in-stock guitars within a budget.
2. Check the current price of a named product.
3. Ask for store address or Saturday opening hours.
4. Understand an online return within or outside the seven-day window.
5. Check whether a promotion is active and whether PIX can be combined.
6. Ask for order status after local identity verification.
7. Request an accessory the store does not sell.
8. Report an issue containing personal information without sending it externally.

## Scope

### Included in the prototype

- CLI conversation interface.
- In-memory session history with bounded lifetime.
- CSV-backed product, customer, promotion and order repositories.
- Curated section-based policy retrieval.
- Deterministic tools and local privacy enforcement.
- Provider-neutral paid and local model adapters.
- Evaluation harness and recorded examples.

### Excluded from the prototype

- Real WhatsApp Business integration.
- Checkout, payment or order mutation.
- Production authentication and account recovery.
- Native mobile or production web UI.
- Permanent conversational memory.
- Autonomous refunds, returns or complaint resolution.
- Training or fine-tuning on customer conversations.

## Constraints

- Python is the required primary language.
- The public repository must include a real incremental commit history.
- The README must explain setup, technical decisions, limitations and AI-assisted workflow.
- Three to five interaction examples are required, including a non-trivial data or policy case.
- The supplied operational data and policy manual are the only store-specific sources of truth.
- Simplicity and clarity are more important than a production-scale platform.

## Working assumptions

- The agent replies in the customer's language, PT-BR by default, because the store and
  customers are Brazilian; repository documentation is written in English for the evaluation
  audience.
- `(67) 3341-4444` is the main/store telephone and `(67) 3321-4500` is the remote WhatsApp
  channel; they are separate channels rather than conflicting values.
- The ASCII email `contato@emporiodamusica.com.br` is canonical.
- Business time uses `America/Campo_Grande`.
- Order verification requires order ID plus a matching phone or email. The challenge does
  not define this rule, so it is explicitly documented as a privacy assumption.
- Current CSV values are fictitious but must be treated as if they were real personal data.

## Success criteria

The prototype is successful when all universal gates in `evaluation-plan.md` pass, a clean
environment can run deterministic tests without an API key, the same corpus can compare
all model adapters, and the selected profile is supported by measured quality, privacy,
latency and cost evidence.

Hybrid inference is not an objective by itself. If it misses the quality or operational
gates, the correct outcome is the paid-optimized profile.

## Source material

- `../Desafio Técnico AI Engineer 2026/desafio_tecnico_ai_eng_artefact.pdf`
- `../Desafio Técnico AI Engineer 2026/data/políticas_da_loja.pdf`
- `../Desafio Técnico AI Engineer 2026/data/*.csv`

