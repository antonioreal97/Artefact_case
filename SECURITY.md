# Security and Privacy Policy

## System and Scope

This policy covers the Emporio da Musica customer-support agent, including:

- User messages and session state.
- Customer, order, product and promotion repositories.
- Domain tools and identity verification.
- Local and paid LLM adapters.
- Prompt construction, traces, logs and evaluation artifacts.

Customer data must be treated as real personal data even when the current case-study fixtures are fictitious.

## Data Classification

### Public

- Product catalog without customer information.
- Prices, stock and active promotions.
- Store address, hours and published policies.

### Internal

- System prompts.
- Routing and model configuration.
- Aggregated operational metrics without identifiers.

### Restricted Personal Data

- Names, phone numbers and email addresses.
- Order IDs, items, status, tracking codes and delivery information.
- Conversation history and complaints.
- Any combination capable of identifying or profiling a customer.

### Secrets

- API keys, tokens, credentials and encryption keys.
- Internal authorization and session tokens.

## Threat Model and Trust Boundaries

User messages, retrieved text and model output are untrusted.

External LLM providers, hosted tools, web search, MCP services, telemetry exporters and model logging systems are third-party processing boundaries. Contractual privacy assurances do not replace application-level minimization.

The trusted local boundary contains customer repositories, identity verification, restricted session state, redaction and final response assembly.

## Security and Privacy Invariants

### No Raw PII Egress

Restricted personal data must never be sent to an external LLM provider.

A provider request must be blocked if it contains:

- Names, phone numbers or email addresses.
- Order or customer identifiers.
- Tracking codes or full addresses.
- Raw conversation history containing personal data.
- Tool output containing customer records.

This restriction applies even when the provider offers zero-data-retention controls.

### Local Identity and Order Processing

Identity verification must run in deterministic local code.

The model receives only authorization state such as:

```json
{
  "identity_verified": true,
  "order_status": "shipped",
  "has_tracking": true
}
```

The model must not receive the customer identifier, stored verification value, order ID or tracking code.

After generation, trusted application code may insert authorized order details into a validated response template.

### Data Minimization

Every request must contain only the fields required for its immediate purpose.

External models may process public catalog and policy information. Restricted turns must use local inference or deterministic application logic.

If paid inference is required for language quality, the application may send only an abstract, non-identifying representation such as:

```text
A verified customer has a shipped order and is asking for a concise status explanation.
```

### Pseudonymization

Internal references must use random, short-lived tokens.

Tokens must not be derived from names, emails, phones, order IDs or tracking codes. The token-to-record mapping remains inside the trusted local boundary and expires with the session.

Pseudonymized data is still treated as personal data when re-identification is possible.

### Provider Controls

External providers are disabled for restricted data. For public or internal data, an external provider is allowed only when an approved privacy configuration is active.

Required controls include:

- Paid or enterprise API terms that prohibit training on customer content.
- `store: false` or its provider-equivalent.
- Zero-data-retention configuration where available.
- Provider-side logging and optional data contribution disabled.
- No contributor, feedback or discounted data-sharing tier.
- No file upload, hosted vector store or explicit context cache for customer data.
- No web search, maps grounding, remote MCP or third-party tools on restricted turns.
- Documented retention, processing region, subprocessors and data-processing agreement.

These controls are defense in depth. They do not authorize sending raw PII.

### Logs and Traces

Logs must use masked or hashed identifiers and must not contain prompts, responses, phones, emails, order IDs, tracking codes or authorization tokens.

Debug logging of provider request bodies is disabled by default.

Security audit events may record:

- Trace ID.
- Route and provider.
- Data classification.
- Redaction result.
- Egress allowed or blocked.
- Model and prompt version.
- Error category and latency.

Audit records must have an explicit retention period and access control.

### Conversation Memory

Raw conversation memory remains local and has a bounded TTL.

External-provider state, hosted conversations and server-side threads must not be used for restricted sessions.

Long-term memory must require a defined purpose, retention period, access policy and deletion mechanism.

### Evaluation Data

Model evaluation must use synthetic or irreversibly anonymized customer data.

Production conversations may not be copied into evaluation datasets without a separate approved anonymization and governance process.

Human-review exports must contain no restricted data.

### Secrets

Secrets must come from environment variables or an approved secret manager.

Secrets must never appear in prompts, traces, model responses, evaluation fixtures or repository files.

## Routing Requirements

| Request type | Permitted processing |
|---|---|
| Store policy and public information | Deterministic, local or approved external model |
| Public product catalog | Deterministic, local or approved external model |
| Product recommendation without customer data | Local or approved external model |
| Identity verification | Deterministic local code only |
| Order status and tracking | Deterministic local code; optional local LLM |
| Complaint containing personal or order data | Local redaction before any model; local processing preferred |
| Restricted free-form conversation | Local model or human handoff |
| Unknown classification | Fail closed: local processing or human handoff |

## Fail-Closed Behavior

If classification or redaction is uncertain, the external provider call must not occur.

If the privacy guard fails, the agent must return a safe handoff response without exposing internal details.

Provider failure must never cause automatic rerouting of restricted content to another external provider.

## Reportable Findings

### Critical

- External transmission of raw restricted data.
- Unauthorized order or tracking disclosure.
- Exposure of API keys or authorization tokens.
- Bypass of the identity-verification boundary.

### High

- PII in logs, traces or evaluation artifacts.
- Reversible pseudonymization transmitted externally.
- Use of external tools or model storage on restricted turns.
- Provider configuration enabling training or voluntary data contribution.

### Medium

- Missing retention limits.
- Excessive collection or prompt context.
- Incomplete audit trail for external egress.
- Restricted session state retained longer than necessary.

## Known Limitations

The current project is a prototype and does not yet implement production authentication, consent management, data-subject request workflows, deletion orchestration or a formal data-protection impact assessment.

These limitations do not weaken the no-PII-egress and identity-verification invariants.

## Out of Scope

Provider availability and general model-answer quality are evaluated separately.

The fictitious nature of the supplied dataset is not an accepted privacy exception: implementations must be suitable for replacement with real data.
