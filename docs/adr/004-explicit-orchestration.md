# ADR 004 Explicit Orchestration

- Status: Accepted for implementation
- Date: 2026-10-31
- Owners: Project team

## Context

The prototype has one business domain, approximately six tools, short conversations and
strict privacy/routing requirements. General-purpose agent frameworks and multi-agent
networks add abstractions, handoffs and dependencies that can obscure these boundaries.

## Decision

Implement one explicit Python turn state machine. It coordinates classification,
authorization, evidence retrieval, context building, model invocation, validation, final
assembly and tracing. Provider SDKs remain inside adapters. Do not start with LangChain,
LLM-generated plans or a multi-agent network.

## Consequences

### Positive

- Control flow, retries and privacy transitions are visible and unit-testable.
- The project remains explainable for the technical case.
- Provider and local-model changes do not affect domain logic.

### Negative

- The team owns orchestration code and normalized adapter contracts.
- Framework-provided tracing or handoff helpers are not available automatically.
- The state machine must be kept small and documented to avoid becoming a custom framework.

## Alternatives rejected or deferred

- Large agent framework: no demonstrated reduction in complexity for this scope.
- Multi-agent routing: introduces handoff nondeterminism without separate large domains.
- ReAct loop with unrestricted tools: conflicts with bounded permissions and retries.

## Validation

Component tests must cover every state transition, timeout and failure path. Multi-agent
or framework adoption can be reconsidered only when traces show persistent tool confusion,
instruction interference or orchestration code that the proposed abstraction measurably
simplifies.
