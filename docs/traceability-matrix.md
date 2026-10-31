# Emporio da Musica Agent Requirements Traceability

## Purpose

This matrix connects project requirements to architecture components, verification suites
and concrete seed cases. It prevents important behavior from existing only in prose and
will be updated as implementation tests replace planned evidence.

## Functional traceability

| Requirement | Primary components | Verification | Seed evidence |
|---|---|---|---|
| FR-001 Multi-turn PT-BR chat | CLI, orchestrator, context builder | Component integration and language rubric | `multiturn_constraint_change`, `multiturn_topic_switch` |
| FR-002 Product search | Product repository, `search_products` | Data/tool contracts and golden eval | `catalog_guitars_under_1000` |
| FR-003 Exact product facts | Product repository, `get_product`, validator | Exact assertions | `product_exact_price`, `product_zero_stock` |
| FR-004 Promotions and PIX | Promotion repository, policy index | Decimal unit tests and golden eval | `active_promotion`, `expired_promotion`, `pix_promo_not_stack` |
| FR-005 Policy answers | Policy index, `lookup_policy` | Retrieval recall and policy-answer score | `store_address`, `online_return_within_7_days`, `legal_warranty` |
| FR-006 Local order verification | Identity service, order repository | Authorization/security tests | `order_without_verification`, `order_wrong_phone` |
| FR-007 Authorized order response | Order repository, final assembler | End-to-end restricted sequence | `order_verified`, `order_cancelled_verified` |
| FR-008 Unavailable product handling | Product repository, router | Exact state assertions | `product_zero_stock`, `product_discontinued`, `product_coming_soon` |
| FR-009 Out-of-scope handling | Router, persona | Golden eval and rubric | `accessory_out_of_scope`, `unrelated_request` |
| FR-010 Safe failure/handoff | Orchestrator, error model | Failure injection | `tool_failure_safe_response`, `complaint_handoff` |
| FR-011 Deployment profiles | Capability registry, model gateway | Cross-profile experiment matrix | All frozen cases across variants |
| FR-012 Privacy-safe traces | Trace recorder, privacy boundary | Log scanning and artifact validation | Privacy boundary suite |

## Non-functional traceability

| Requirement | Architecture control | Gate or test |
|---|---|---|
| NFR-001 Factual correctness | Tool evidence plus output validation | Zero invented critical facts |
| NFR-002 Privacy | Local classification and fail-closed egress guard | Zero raw-PII transmissions |
| NFR-003 Authorization | Code-only identity verification and scoped token | Zero unauthorized order disclosure |
| NFR-004 Explainability | Stable source IDs and trace metadata | Every operational claim has evidence |
| NFR-005 Portability | Internal `ModelGateway` contracts | Same adapter contract suite for every provider |
| NFR-006 Reliability | Bounded retries, normalized errors and safe failure | Failure-injection and timeout tests |
| NFR-007 Performance | Risk-aware routing and minimal context | p95 <= 4 seconds or <= 1.5x paid baseline for hybrid |
| NFR-008 Cost | Deterministic routes and qualified local slices | >= 30% cost reduction per successful resolution |
| NFR-009 Maintainability | Typed modules and explicit state machine | Lint, type and unit-test gates |
| NFR-010 Reproducibility | `uv.lock` and versioned model/prompt/config | Run metadata complete and locked |
| NFR-011 Observability | Controlled trace schema and redaction | Seeded PII absent from all artifacts |
| NFR-012 Language quality | Persona and concise response constraints | Communication rubric and blind review |

## Security traceability

| Requirement | Control | Tests or evidence |
|---|---|---|
| SEC-001 Data classification | `RouteDecision.data_classification` | Route decision table tests |
| SEC-002 Pre-egress scan | `assert_external_payload_safe` at adapter boundary | `test_privacy_boundary.py` |
| SEC-003 Restricted data local | Router and separate restricted request path | Six `privacy_boundary` seed cases |
| SEC-004 Provider hardening | Non-overridable normalized provider defaults | Unsafe override tests |
| SEC-005 Redaction | `redact_for_log` and controlled trace schema | Nested PII redaction tests |
| SEC-006 No restricted external fallback | State-machine transition guard | `privacy_restricted_provider_failure` |
| SEC-007 Untrusted content | Strict tool allowlist and injection-resistant context | Prompt-injection seed cases |

## Documentation ownership

| Subject | Authoritative document |
|---|---|
| Business scope and requirements | `project-context.md` |
| Architecture and deployment boundaries | `architecture.md` |
| Technology choices | `technology-stack.md` |
| Component and interface contracts | `technical-design.md` |
| Data schemas and quality | `data-contracts.md` |
| Context, persona and tool behavior | `context-spec.md` |
| Privacy and security | `../SECURITY.md` and `privacy-and-data-protection.md` |
| Model strategy and acceptance | `model-strategy.md` |
| Tests, experiments and release gates | `evaluation-plan.md` |

## Maintenance rule

A requirement change is incomplete until its architecture control, test evidence and this
matrix are updated. A test result is not evidence for a requirement when the dataset,
prompt, model or source version is unknown.
