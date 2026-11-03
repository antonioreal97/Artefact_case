# Empório da Música — customer-service agent

A Python customer-service agent for the fictional Empório da Música store in Campo Grande/MS, built with FastAPI and the OpenAI Agents SDK.
**One agent, seven typed tools, RAG over one store manual and a simple API.**
It answers catalog, price and stock questions through typed tools, retrieves store policies with RAG citations, and checks order status after local contact verification.
It requests human handoff when needed and is instructed to reply in the customer's language, with Portuguese as the default.
Its configured tone is informal, professional and welcoming: “Sua música começa aqui” (“Your music starts here”).

## Main flow

```text
message → agent → typed tools (CSV snapshot) or RAG (policy PDF)
        → validation (grounding / privacy) → reply
```

Operational facts (price, stock, promotions, verified orders) come from typed
`CommerceBackend` queries over an imported CSV snapshot — never invented in the prompt.
Policies come from hybrid retrieval over the store PDF, with citations.
Output validation checks unsupported commercial claims and scans for restricted data
before the reply is persisted.

Core code: `app/agents/`, `app/tools/`, `app/rag/`, `app/services/chat.py`.

## Reviewer's guide (5 minutes)

1. **Run it (no API key, deterministic):** `./scripts/docker_up.sh --smoke` starts the
   stack with a scripted fake model and hash embeddings and checks one answer to the case
   question. Full Docker/offline/ports details: [docs/running.md](docs/running.md).
2. **Run it with the real model:** in `.env` set `LLM_PROVIDER=openai`,
   `EMBEDDING_PROVIDER=openai` and `OPENAI_API_KEY`, then run `docker compose down -v` and
   `./scripts/docker_up.sh` to re-index with real embeddings (this deletes the demo data and
   makes billable calls). Details: [Real model](docs/running.md#real-model-mode-optional).
   Then open `http://127.0.0.1:8000/chat` or use the curl example in Quick start.
   In the `/chat` access field, paste the `SEED_API_KEY` that `docker_up.sh` wrote to `.env`.
3. **Read, in this order:** the [five interaction examples](examples/conversations/),
   the [customer-service flows](docs/customer-service-flows.md) and the
   [technical decisions](#technical-decisions).
4. **Scope:** the case core is one agent with seven typed tools, RAG over the store
   manual and a privacy boundary (`app/agents/`, `app/tools/`, `app/rag/`,
   `app/services/chat.py`).

## How the agent works

1. Authenticate the request and load or create the conversation.
2. Classify the message locally, tokenize recognized personal data, and apply input
   guardrails; restricted requests and human-service requests can be handled before the
   model runs.
3. Run one bounded agent loop: typed commerce tools supply catalog, price, stock and
   verified order facts; document retrieval supplies policy excerpts and citations.
4. Check the answer for unsupported prices, stock, commercial claims and citations, and
   scan it for restricted data.
5. Reveal only authorized tokens locally; use a safe fallback or persist a human handoff
   request when the evidence or safety checks require it.
6. Return and persist the reply, sources and tool evidence; include the configured welcome
   message on first contact when appropriate.

See the [customer-service flows](docs/customer-service-flows.md) for intent diagrams,
guardrails and handoff reasons.
The demonstrated path is one store, one API and deterministic providers. The backend
queries a snapshot imported from CSVs, rather than a live ERP. Tests verify integration
and the controls exercised, rather than LLM naturalness or quality.

## Case requirements → where they are met

The [case brief](Desafio%20Técnico%20AI%20Engineer%202026/desafio_tecnico_ai_eng_artefact.pdf) requires Python and leaves the interface, framework and persistence choices to the candidate.
The table separates implemented behavior from delivery obligations that code alone cannot verify.

| Requirement | Implementation / evidence |
|---|---|
| Python text-message agent for the store | [Python project](pyproject.toml), [HTTP chat endpoint](app/api/routes/chat.py), [agent](app/agents/customer_service.py) and [chat tests](tests/agent/test_chat.py) |
| Persona aligned with the store's identity and tone | [Tenant configuration](scripts/create_tenant.py), [versioned instructions](app/agents/instructions.py) and [welcome-message tests](tests/agent/test_welcome.py) |
| Receive and answer messages using the supplied context | [Chat service and history](app/services/chat.py), [CSV/PDF seed](scripts/seed.py), [source-data contract tests](tests/test_source_data_contract.py) |
| Distinguish operational data from policies | [Typed commerce tools](app/tools/), [document retrieval](app/rag/retrieval.py), [catalog tests](tests/tools/test_catalog_tools.py), [order tests](tests/tools/test_order_tools.py) and [RAG tests](tests/rag/test_pipeline.py) |
| Handle questions outside the store's scope appropriately | [Policy](app/agents/instructions.py) requires a polite refusal and an offer of store help, without human handoff; [example 05](examples/conversations/05-out-of-scope-and-address.md) demonstrates it |
| Justify framework/approach, model/provider, interface, history and data treatment | [Technical decisions](#technical-decisions), [real-model mode](docs/running.md#real-model-mode-optional) and [ADR 005](docs/adr/005-multi-tenant-platform-branch.md) explain the choices and their scope |
| Complete run instructions and configuration | [Quick start](#quick-start), [docs/running.md](docs/running.md), [.env.example](.env.example) |
| Known limitations, next steps and documented assumptions | [Limitations](#limitations), [full list](docs/limitations.md) and [Assumptions](#assumptions) |
| Explain coding-assistant choices and workflow | [Use of coding assistants](#use-of-coding-assistants) |
| 3–5 varied saved conversations, including a nontrivial data lookup or policy application | [Five offline examples](#interaction-examples), including verified order status and return-policy application; runtime queries use the local snapshot |
| Public GitHub repository with a genuine commit history; submit its link by email and stop changes at the deadline | [Repository](https://github.com/antonioreal97/Artefact_case) and its commit history are the delivery artifacts; email submission, public visibility and deadline compliance require owner verification |

## Assumptions

The brief asks to document reasonable interpretations where information is missing. The
full list is in [project context](docs/project-context.md#working-assumptions); the ones
that shape behavior are:

- **Data freshness:** the CSVs are an imported snapshot, not a live ERP; "real-time" lookups
  query that snapshot through typed tools.
- **Order privacy:** the brief does not define identity checks, so an order is revealed only
  when the customer gives the order number plus the phone or e-mail of the purchase; three
  wrong contacts within an hour lock the lookup.
- **Personal data:** the CSV values are fictitious but treated as real personal data, so they
  are tokenized before any model call.
- **Conflicting manual rules:** where the manual contradicts itself (for example, minimum
  installment values), the agent states what the sources agree on and hands off instead of
  choosing a rule.
- **Out of scope:** a question unrelated to the store gets a polite refusal and an offer to
  help with the store, without human handoff; a store question the manual cannot answer is
  handed off.
- **Language:** the agent replies in the customer's language, Portuguese by default (the
  store, manual and customers are Brazilian); the repository and documentation are in
  English, as the evaluation requires.
- **Human handoff:** a handoff is a persisted request for the store team; no real
  notification channel exists in this prototype.

## Quick start

```bash
./scripts/docker_up.sh --smoke
```

Requires Docker with Compose v2+. Creates `.env` when absent, migrates, seeds and checks
one answer to the case question with fake/hash providers (no OpenAI calls).

```bash
export SEED_API_KEY="$(grep '^SEED_API_KEY=' .env | tail -n 1 | cut -d= -f2-)"
curl --fail-with-body --silent --show-error http://127.0.0.1:8000/v1/chat/messages \
  -H "X-API-Key: $SEED_API_KEY" -H "Content-Type: application/json" \
  -d '{"message":"Quais opções de violões disponíveis custando até R$1000?"}'
```

**Real model (short switch):** in `.env` set `LLM_PROVIDER=openai`,
`EMBEDDING_PROVIDER=openai` and `OPENAI_API_KEY`, then `docker compose down -v` and
`./scripts/docker_up.sh` (re-indexes; billable; deletes demo data). Defaults use
`gpt-5-mini` / `text-embedding-3-small` with `OPENAI_REASONING_EFFORT=low`.

Host Python/uv, PowerShell, ports, shutdown/reset, LangChain/Anthropic/Ollama and
in-container tests: **[docs/running.md](docs/running.md)**.

## Technical decisions

| Choice | Rationale and scope |
|---|---|
| Framework / approach | One agent with the OpenAI Agents SDK; seven tools cover one domain; the bounded loop avoids unnecessary specialist agents. `PrivacyGuardedModel` inspects every payload before the provider, enforces `store=False` and blocks hosted state ([ADR 005](docs/adr/005-multi-tenant-platform-branch.md)). |
| Model / provider | `gpt-5-mini` through the native OpenAI adapter, validated live; reasoning effort `low` by default for latency. The provider is configurable (fake for offline tests; LangChain adapters, of which Anthropic/Ollama were not run live) without changing the agent loop ([ADR 006](docs/adr/006-langchain-model-layer.md); details in [docs/running.md](docs/running.md#real-model-mode-optional)). |
| Interface | FastAPI HTTP chat — reproducible with curl; exercises authentication, errors and isolation. A visual interface is unnecessary to evaluate the agent; optional `/chat` is a demo surface only ([extensions](docs/platform/extensions.md#browser-test-interface)). |
| History persistence | Postgres stores conversations, original and redacted messages, sources and tool executions; only the redacted form goes to the model. Redis stores the vault with a sliding 24-hour TTL. |
| Data treatment | Types, references and uniqueness are validated on CSV import; import uses per-tenant upsert. Restricted data stays local; recognized spans become reversible tokens resolved by local tools. This is pseudonymization, not anonymization. |
| Retrieval | Document-only RAG: policies, FAQ and manuals require excerpts and citations; the seed includes the store PDF. Hybrid pgvector/cosine + Portuguese full-text search fused with Reciprocal Rank Fusion; sufficiency thresholds withhold insufficient sources. Embeddings do not replace identity checks. Superiority over lexical search alone has not been measured. |
| Prompt strategy | Versioned `commerce-privacy-v11` combines a configurable persona with mandatory rules plus an ESTILO block (answer first, short, no greeting loops, light citations; prompt-only, so live replies still sometimes use em dashes, long policy answers or a templated closing question). Documents arrive in `<untrusted_document>` as untrusted data. Guardrails and authorization remain in code. Unbacked handoff claims are rejected in code. Money validation reads amounts from digits only. |

The detailed turn diagram is in [docs/customer-service-flows.md](docs/customer-service-flows.md).

The case data is fictional but treated as personal data; it is no exception to the rule
against raw PII in external models and logs. Detection uses rules and known tenant
values, with documented gaps. Ambiguous restricted turns should fail closed; the
implementation recognizes only some of these ambiguities. External use of reversible,
sequential tokens diverges from the original policy;
[ADR 002](docs/adr/002-no-pii-external-egress.md) and
[ADR 005](docs/adr/005-multi-tenant-platform-branch.md) record that divergence.

The original design proposed a smaller solution: SQLite, curated sections and explicit
orchestration ([ADR 003](docs/adr/003-sqlite-and-curated-policy-retrieval.md),
[ADR 004](docs/adr/004-explicit-orchestration.md)).
[ADR 001](docs/adr/001-hybrid-provider-neutral-architecture.md) records the original
provider strategy, which remains proposed. ADR 005 scopes the delivered platform's
choices. See the [process and decisions](docs/development-process.md), the
[documentation index](docs/README.md) and the [customer-service flows](docs/customer-service-flows.md).

## Interaction examples

The five offline conversations are **deterministic demonstrations with a fake model**,
rather than real-LLM transcripts. Customer and agent messages are in Portuguese, with
English annotations and an English translation of every turn. They demonstrate the
pipeline and queries against the local snapshot:

1. [Available acoustic guitars under R$ 1,000](examples/conversations/01-catalog-guitars-under-1000.md).
2. [Takamine GD20 price](examples/conversations/02-price-takamine-gd20.md).
3. [Return policy](examples/conversations/03-return-policy.md).
4. [Order with verified contact](examples/conversations/04-verified-order-status.md).
5. [Out-of-scope question and store address](examples/conversations/05-out-of-scope-and-address.md): a polite refusal without human handoff for the out-of-scope question, as required by policy.

The verified order and return-policy application cover nontrivial situations.
Conversation artifacts must not publish customer identifiers. The generator rejects
unknown English translations; tests regenerate all five files twice and compare them
except for the date. Examples 04 and 05 include deterministic order-status and
public-contact triage. Regenerate with Postgres available:

```bash
uv run python -m scripts.demo_conversations
```

[Live English conversations](evals/live/english-conversations/) were re-recorded against
`gpt-5-mini` under `commerce-privacy-v9` (catalog/price/stock: attempt 2; return/order/scope:
attempt 3; earlier attempts archived). Attempts 1–2 of the order conversation were refused
because the deliberate wrong-contact turns of earlier recordings had locked order 1 (three
failed verifications in one hour), which is the lockout control working as designed. They
are nondeterministic observations—not automated quality gates—and remain separate from the
reproducible offline demonstrations. See
[live conversation notes](evals/live/english-conversations/README.md) for what worked, what
failed and remaining multilingual limits.

## Tests and evaluation

```bash
APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0 uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app
APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash uv run python -m scripts.eval_rag --json-out /dev/null
```

Current host suite: **1071 passed, 12 xfailed, 130 subtests passed**. Integration tests
use disposable Postgres databases; they may be skipped if Postgres is unavailable — check
skips before interpreting a successful run. Evaluation creates and removes its own
database, with Empório and a synthetic tenant used only to test isolation.

Deterministic evaluation has 23 cases and three mandatory hard gates: cross-tenant
leakage = 0, privacy violations = 0 and harness errors = 0. Exit code 1 indicates a gate
failure; 2 indicates an environment problem. Soft metrics reach 100% with fake/hash;
even that does not measure understanding, tone, paraphrase robustness or a real LLM.

Docker test walkthrough and historical Docker checkpoint (603 tests / 123 subtests on
2026-11-01): [docs/running.md](docs/running.md#tests-and-evaluation-inside-docker).
Earlier integration snapshots: [development process](docs/development-process.md#42-state-at-integration-2026-11-01),
[initial evaluation](evals/REPORT.md), [R1 security](docs/reviews/security-review.md),
[adversarial findings](tests/adversarial/FINDINGS.md), [R3 architecture](docs/reviews/architecture-review.md).

### Live-model validation

On 2026-11-01, the Docker stack ran with `gpt-5-mini` and `text-embedding-3-small`
against a manual battery of 27 turns (24 standalone questions and one 3-turn conversation).
Each fix round (R4–R8) was followed by another full run. Condensed results:

| Run | Useful answers (out of 27) |
|---|---|
| Before fixes | 8 |
| R4 (first fix round) | 15 |
| R8 native / R8 LangChain (`openai:gpt-5-mini`) | 19 / 22 |
| R9 / R10 native (`commerce-privacy-v9` / `v10`, provider-default reasoning) | 24 / 24 |
| R11 native, `OPENAI_REASONING_EFFORT=low` (latency comparison; **shipped effort**) | 23 |
| R11 native, `minimal` (rejected) | 21 |
| R12 native, `low`, `commerce-privacy-v11` + triage UX (2026-11-03, latest) | 22 |

In every run, “quero falar com um atendente” was correctly handed off. The shipped
reasoning effort cut median latency from 11.8s to 9.3s versus the provider default.
Full per-question history, causes and fixes:
[evals/live/2026-11-01-gpt-5-mini.md](evals/live/2026-11-01-gpt-5-mini.md).
Anthropic and Ollama were not run. Behavior still varies between runs: in R12, two
questions answered in every earlier run missed, and both answered correctly when re-run
right after the battery; installment questions can require human confirmation because the
manual has conflicting minimum-installment rules. This battery finds defects; it is not a
statistical quality measurement.

## Limitations

Top issues for reviewers (full list and next steps: [docs/limitations.md](docs/limitations.md)):

1. **LLM quality / variance** — the Portuguese live battery peaked at 24/27 useful
   answers (R9/R10); R11 with `OPENAI_REASONING_EFFORT=low` reached 23/27. Lexical
   grounding reduces fabrication but also rejects correct answers; a citation does not
   prove stock, lead-time or condition consistency. Held-out questions, more multi-turn
   conversations and conflicting-source evaluation are still missing.
2. **Latency** — a reply takes several seconds because each turn makes 2–3 sequential
   `gpt-5-mini` calls; catalog tools take milliseconds and knowledge search about 2s.
   Measured median with shipped `low` effort: 9.3s (p90 12.0s). No streaming, so the
   customer waits for the whole answer.
3. **Lexical grounding false positives** — correct promotion denials can be blocked;
   money validation reads digits only, so budgets written in words are not exempt and
   worded prices may go unchecked (`xfail` cases in
   `tests/agent/test_humanized_tone_review_fixes.py`).
4. **Concurrency (A-5)** — vault GET/SET and turn commits are not atomic; simultaneous
   requests or retries may lose state or duplicate actions. Per-conversation
   serialization and idempotency are needed before concurrent use.
5. **Distance from production** — tenant filters are a tested convention without RLS;
   retention/observability are incomplete; queues and WhatsApp lack durable delivery
   guarantees; several requests load entire customer/product sets at seed scale
   (50 customers / 65 products).

## Use of coding assistants

I used **Claude Code (Anthropic)**, **Cursor**, **Hermes** and **OpenAI Codex** in agent
sessions, with parallel terminals managed by **Orca** and local Python environments to run
code, tests and evaluation. I, `antonio-real`, defined the objective and constraints, chose
the platform direction (ADR 005), decided what to accept and approved every commit. All
commits name the owner as author; that authorship does not mean all code was written
manually. Throughout every stage, I also used **Cursor** myself to monitor all files
modified by each agent, call the tools I needed and debug the work as it progressed.

Claude Code coordinated the work: it divided workstreams with frozen contracts and
exclusive file ownership, wrote task specifications, answered workers' questions, reran
every check before accepting each delivery and proposed commits. The workflow was:
**contracts → parallel workstreams → independent review (R1) → adversarial tests (R2) →
evaluation with gates (G1) → fixes by owners → independent architecture review (R3) →
README**. Not everything worked on the first attempt; usage limits interrupted
workstreams, and independent reviews plus tests verified deliveries before human
acceptance. The full per-workstream tool table is in
[docs/coding-assistants.md](docs/coding-assistants.md).

## Optional platform experiments

The repository also contains optional platform experiments, documented separately in
[docs/platform/extensions.md](docs/platform/extensions.md) (rationale: [ADR 005](docs/adr/005-multi-tenant-platform-branch.md)).
One of them, an optional numbered triage menu, answers routine requests (hours, guided order
status, returns summary, human or sales handoff) without a model call.
