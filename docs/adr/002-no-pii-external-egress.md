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

