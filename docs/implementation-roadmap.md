# Implementation roadmap

## Build sequence

### Phase 0 - Freeze contracts

- Curate the policy PDF into numbered Markdown/JSON sections.
- Preserve the supplied CSV files as immutable fixtures.
- Approve tool schemas, privacy assumption and source precedence.
- Expand the seed corpus to at least 80 cases and split public tuning vs hidden holdout.

Exit criterion: context and test contracts have version IDs; no model code is required.

### Phase 1 - Deterministic domain layer

- Load and validate CSV schemas.
- Implement product, promotion, policy and order repositories.
- Implement tools and code-only identity verification.
- Add unit and integration tests, including failure injection.
- Implement deterministic templates for exact store facts.

Exit criterion: all facts can be retrieved without an LLM and all domain tests pass.

### Phase 2 - One paid baseline

- Implement the provider-neutral `ModelGateway` contract.
- Add one paid provider adapter and the explicit orchestration loop.
- Add structured outputs, bounded retries, timeouts, trace capture and safe failure.
- Run the frozen smoke suite and record a baseline.

Exit criterion: one end-to-end paid baseline passes universal critical gates.

### Phase 3 - Cross-provider and economy benchmark

- Add remaining paid adapters only as needed for the experiment matrix.
- Compare economy and strong candidates using exact model snapshots.
- Select `paid_primary`, `paid_economy` and optional outage secondary.
- Measure prompt caching and context-reduction opportunities.

Exit criterion: `paid_optimized` is the reliable deployable fallback.

### Phase 4 - Local qualification

- Record target hardware and select one or two license-compatible candidates.
- Serve them behind the same gateway contract.
- Run local-only diagnostics, then shadow the paid baseline.
- Enable only task slices that meet their individual quality and latency gates.

Exit criterion: accept `hybrid` only if all hybrid gates pass; otherwise keep
`paid_optimized` and document the negative result.

### Phase 5 - Demonstration and delivery

- Add the CLI and 3-5 representative recorded conversations.
- Generate an evaluation report with quality, cost, latency and failure examples.
- Complete the public README, `.env.example`, threat/limitations section and AI-assistance
  disclosure.
- Pin dependencies and exact model IDs; verify a clean setup from scratch.

## Suggested repository shape

```text
src/emporio_agent/
  app.py
  config.py
  orchestrator.py
  routing.py
  context.py
  validation.py
  models/
    base.py
    openai_adapter.py
    anthropic_adapter.py
    google_adapter.py
    local_adapter.py
  domain/
    products.py
    promotions.py
    orders.py
    policies.py
  tools/
    catalog.py
    order.py
    policy.py
  observability/
    traces.py
    cost.py
knowledge/
  policies.v1.json
config/
  models.example.yaml
  prompts/
tests/
  unit/
  integration/
  eval/
docs/
examples/
```

The exact tree can stay smaller for the case. Keep module boundaries only where they make
provider swaps, deterministic testing or privacy boundaries clearer.

## Configuration

Never commit secrets. Runtime configuration should include:

- active profile (`paid_baseline`, `paid_optimized`, `hybrid_shadow`, `hybrid`);
- exact provider/model IDs and endpoints;
- timeouts, maximum retries and per-turn budget;
- qualified local task slices;
- prompt, policy and dataset versions;
- local hardware/TCO assumptions;
- tracing level and PII redaction mode.

## Definition of done

- The same test corpus runs unchanged against every model adapter.
- All universal release gates pass.
- Hybrid is either supported by measured evidence or explicitly rejected.
- No secret or unmasked customer PII appears in logs or public examples.
- A clean machine can run deterministic tests without any API key.
- Model-backed tests skip clearly when credentials or local inference are unavailable.
- The README states the chosen provider/profile, exact evaluation date and limitations.

