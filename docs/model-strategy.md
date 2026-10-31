# Model and cost strategy

## Principle

Choose models by **task-slice evaluation and total cost per successful resolution**, not
by vendor reputation or token price. "ChatGPT" is a product; the implementation must use
an explicit OpenAI API model ID. The same distinction applies to consumer Claude and
Gemini applications versus their APIs.

## Candidate tiers (discovery snapshot: 2026-10-31)

The first benchmark should cover one economy and one stronger paid model per provider,
plus one or two local models that fit the actual hardware.

| Provider | Economy/high-volume candidate | Strong candidate | Notes |
|---|---|---|---|
| OpenAI | GPT-6 Luna or GPT-6.1 Terra | GPT-6.1 Sol; Astra only if the hard slice needs it | use explicit API IDs and the Responses API for tool workflows |
| Anthropic | Claude Haiku 5.5 | Claude Sonnet 5.5 or Opus 5.5 | current lineup supports tools; benchmark concise PT-BR behavior |
| Google | Gemini 2.5 Flash-Lite or a current Flash tier | current Pro/flagship tier, including Gemini 4 Argon if justified | verify current model list and price at run time |
| Local | instruction-tuned model fitting one GPU/host | larger quantization only if latency and memory allow | must support PT-BR, structured output and reliable tool calling |

This table is a shortlist, not a recommendation. APIs and model catalogs change quickly;
the evaluation run must save the exact model ID and price sheet date.

Official discovery sources:

- OpenAI model guidance: <https://developers.openai.com/api/docs/guides/latest-model>
- OpenAI agents model/provider guidance: <https://developers.openai.com/api/docs/guides/agents/models>
- Anthropic model overview: <https://platform.claude.com/docs/en/models/overview>
- Anthropic API pricing: <https://platform.claude.com/docs/en/about-claude/pricing>
- Gemini API pricing/model catalog: <https://ai.google.dev/gemini-api/docs/pricing>
- vLLM OpenAI-compatible server: <https://docs.vllm.ai/en/latest/serving/openai_compatible_server/>

## Selecting a local model

Do not freeze a model name before hardware is known. Record:

- GPU/accelerator type, VRAM/RAM, CPU and concurrency target;
- model license and commercial-use terms;
- parameter count and quantization;
- supported chat template, JSON/grammar constraints and tool-call parser;
- PT-BR quality, context length and tokenizer behavior;
- tokens/second at concurrency 1 and target concurrency;
- memory use, cold-start time and failure rate.

Start with the smallest instruction-tuned model that can satisfy the tool and safety
tests. A 7B-14B class candidate is a reasonable experiment on constrained hardware, not
an architectural commitment.

## Routing options to compare

1. **Paid baseline:** strongest paid candidate on every generative turn.
2. **Paid optimized:** deterministic/restricted local answers + economy paid model; strong
   paid model for externally eligible high-risk or failed-validation paths.
3. **Hybrid routed:** deterministic answers + local model only on qualified low-risk
   slices; paid primary for everything else.
4. **Local-first fallback:** local first, paid on validation failure. Test this variant,
   but expect worse tail latency when fallback frequency is high.
5. **Local only:** diagnostic benchmark, not a release profile unless all gates pass.

"Every generative turn" excludes restricted customer/order content. Paid variants may
process only public/internal payloads that pass the privacy egress guard. Provider privacy
settings are minimum eligibility criteria, not a substitute for local minimization.

Do not run multiple paid providers in a voting quorum for normal traffic. It multiplies
cost and can still produce correlated errors. Keep a secondary provider for outages or
offline adjudication.

## Total cost model

Paid request cost:

```text
C_api = input_tokens * input_rate
      + cached_tokens * cached_rate
      + output_tokens * output_rate
      + tool/search charges
```

Local request cost:

```text
C_local = (gpu_hourly + host_hourly + power_hourly + ops_hourly) / successful_requests_per_hour
        + amortized_setup_and_maintenance
```

Hybrid cost:

```text
C_hybrid = deterministic_cost
         + p_local * C_local
         + p_paid * C_api
         + p_fallback * (C_local + C_api)
```

Use **successful resolutions** as the denominator. A cheaper answer that causes a repeat
contact or human correction is not a saving.

Break-even monthly volume for a dedicated local host:

```text
V_break_even = monthly_local_fixed_cost / (paid_cost_per_success - local_variable_cost_per_success)
```

If no GPU is already available and traffic is small, a cheap paid API is likely cheaper
than operating local inference. Measure before buying hardware.

## Acceptance rule for hybrid

Ship the `hybrid` profile only when all conditions hold on the frozen test set:

- zero critical privacy, price/stock fabrication or unauthorized-order failures;
- zero raw-PII egress attempts and zero PII in provider payloads, logs or eval exports;
- weighted quality is at least `max(0.95, paid_baseline - 0.02)`;
- no important task slice drops by more than 5 percentage points;
- blinded human review rates hybrid as win or tie in at least 90% of paired cases;
- end-to-end cost per successful resolution falls by at least 30%, including local TCO;
- p95 user-visible latency is at most 4 seconds or no more than 1.5x the paid baseline;
- fallback rate on locally routed traffic is below 10%.

If any critical condition fails, use `paid_optimized`. If quality is good but savings are
below 30%, the extra operational complexity is not justified for this prototype.

## Paid-only fallback strategy

`paid_optimized` uses the same tools, context and validators. Pick one primary provider
from the benchmark. Use an economy model for routine language generation and a stronger
model for high-risk or failed cases. Configure one cross-provider secondary only for
availability, with explicit timeout and budget limits. Restricted payloads must never be
rerouted externally during provider or local-model failure.

