# ADR 002 No PII External Egress

- Status: Accepted for implementation
- Date: 2026-10-31
- Owners: Project team

## Context

Customer and order sources contain names, phones, emails, identifiers, tracking codes and
conversation data. Paid LLM providers offer different training, retention, logging,
caching and regional-processing controls. These controls can change and do not eliminate
the need for data minimization.

## Decision

Raw restricted data never crosses an external model-provider boundary. Identity,
authorization, order lookup, tracking and final restricted-field assembly remain local.
External models may receive only public data or a minimized non-identifying internal
representation that passes a fail-closed scan immediately before network serialization.

Provider ZDR, no-training terms, disabled logging and `store=false` are required defense in
depth where applicable, but they never create an exception to the no-PII rule.

## Branch implementation clarification (2026-11-01)

The no-raw-PII-egress requirement remains normative for both designs. The original decision
rejects externally transmitted reversible pseudonyms (below). The `test-rag` implementation
in [ADR 005](005-multi-tenant-platform-branch.md) nevertheless sends sequential, reversible
conversation placeholders through the guarded model. This is a documented branch deviation,
not anonymization and not a silent relaxation of [SECURITY.md](../../SECURITY.md).

The local tenant/conversation vault provides pseudonymization with scoped linkability:
repeated values reuse a token within a conversation; mappings stay local with a sliding
24-hour TTL. Identity verification and authorized final reveal remain local. Neither
raw values nor mappings may be sent externally or logged. The original random-token and
no-reversible-egress rules are not fully implemented by this branch.

Discovery uses rules and known tenant values; unknown lower-case names without identifying
cues and contextual identifiers can be missed. Ambiguous restricted turns must fail closed;
the implementation blocks recognized residual cues, but a passing scan cannot establish
that all ambiguous text was detected. [A-3](../reviews/architecture-review.md) and O-9 in
[the development log](../development-process.md) remain open. Prefer allowlisted structured
public/abstract inputs before claiming the universal policy is enforced.

## Consequences

### Positive

- Reduces exposure to provider retention, logging, subprocessors and configuration drift.
- Makes restricted-data handling testable without trusting model behavior.
- Supports local-only order workflows and clear incident boundaries.

### Negative

- Requires a separate local restricted-data path and final response assembler.
- Limits use of paid-model language quality for raw complaint and order conversations.
- PII detection requires layered controls; regex alone is not sufficient for names or
  context-dependent identifiers.

## Alternatives rejected

- Sending PII under provider ZDR: contractual and feature eligibility can vary.
- Reversible pseudonyms sent externally: linkage remains possible.
- Provider-specific redaction only: enforcement must happen before the provider boundary.

## Validation

Critical tests must block seeded phones, emails, names, order references, tracking codes,
postal codes, secrets and sensitive fields. Logs and evaluation exports must contain none
of the seeded values. Restricted local failure must never trigger external fallback.

