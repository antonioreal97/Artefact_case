# Development process and decision log (`test-rag` branch)

- Owner: antonio-real
- Scope: the multi-tenant platform variant described in [ADR 005](adr/005-multi-tenant-platform-branch.md)
- Status: living document, updated at every integration checkpoint
- Last update: 2026-11-01

This document explains **how** the platform was built and **why** each significant decision
was taken, so that a reviewer can follow the reasoning from the git history and the code.
Architecture-level decisions live in the ADRs (`docs/adr/`). This file records the process
and the smaller decisions that came from implementation, review and evaluation.

## 1. Starting point

`main` holds the original design pack (ADR 001–004): one explicit orchestrator, SQLite,
curated policy retrieval and no agent framework. The `test-rag` branch explores a
different product shape: a multi-tenant commerce-support platform (FastAPI, OpenAI Agents
SDK, PostgreSQL + pgvector, Redis, Docker). ADR 005 records that trade-off. The one rule
carried over without change is [ADR 002](adr/002-no-pii-external-egress.md) /
[SECURITY.md](../SECURITY.md): **no raw personal data may reach the external model or the logs.**

## 2. How the work was organized

### 2.1 Contracts first, then parallel workstreams

Before any feature code, the shared interfaces were written and frozen
([docs/platform/contracts.md](platform/contracts.md)): settings, ORM models, the tool result
envelope, the commerce backend protocol, the RAG types, the privacy vault, the tool context
and the base test fixtures (commits `e7df118`, `7da10a5`).

**Why:** the platform has five largely independent parts. Once their interfaces are fixed,
the parts can be built at the same time without blocking each other, and integration
problems show up as type errors rather than late surprises. A contract changes only through
an explicit, recorded decision (see D-09).

### 2.2 Workstreams with exclusive file ownership

Each workstream owned a disjoint set of files and its own test directory. Commit messages
use the same labels:

| Label | Workstream | Main files |
|---|---|---|
| B | Infra: migrations, idempotent seed, Docker, env | `alembic/`, `scripts/`, `Dockerfile` |
| C | RAG: extraction, chunking, embeddings, versioned ingestion, hybrid retrieval, citations | `app/rag/`, `app/services/ingestion_jobs.py` |
| D | Commerce backend and typed tools | `app/integrations/local_commerce.py`, `app/tools/` |
| E | Customer-service agent, privacy gateway, chat service | `app/agents/`, `app/services/{chat,privacy,model_gateway,vault_store}.py` |
| F | Versioned HTTP API, API-key auth, logging, metrics, WhatsApp webhook | `app/api/`, `app/core/{security,logging,metrics}.py` |
| R1 | Security and architecture review (report only) | `docs/reviews/security-review.md` |
| R2 | Adversarial black-box tests (tests only) | `tests/adversarial/` |
| E2 / F2 | Review fixes, split along the E/F ownership line | as E / as D+C+F+infra |
| G1 | RAG/agent evaluation harness | `scripts/eval_rag.py`, `evals/`, `tests/eval/` |
| E3 / F3 | Adversarial regression fixes and retrieval sufficiency | agent / platform ownership as above |
| R3 | Independent architecture review (report only) | `docs/reviews/architecture-review.md` |
| X1 / X2 | Saved conversation examples / submission README and documentation alignment | examples and demo generator / root README and scoped docs |

**Why:** exclusive ownership means no two changes touch the same file at the same time.
Every change has one clear owner. Review findings can be routed to the owner of the
affected file.

### 2.3 Definition of done for every workstream

- `ruff check`, `ruff format`, `mypy --strict` and the workstream's `pytest` suite pass.
- Tests never call a paid API. Deterministic fakes (`LLM_PROVIDER=fake`,
  `EMBEDDING_PROVIDER=hash`) are used instead, and an optional live test must be explicitly
  opted into (`RUN_OPENAI_LIVE_TESTS=1`).
- Every query touching tenant data filters by `tenant_id` in SQL.
- Integration tests run against a throw-away Postgres database per test session.
- The completion report lists changed files, the exact commands run with their real results,
  limitations, and any contract questions.

A single integrator reviews each report, re-runs the checks and makes the commit. Workstreams
do not commit on their own.

### 2.4 Separation between building, reviewing and attacking

After the first implementation wave (B–F) the code went through three separate checks.
None of them was done by the workstream that wrote the code:

1. **Review pass (R1), report only.** A full read of every request path, with local
   experiments as evidence. Each finding is tagged VERIFIED or SUSPECTED and has a repro. The
   review was not allowed to change code, so the report describes the code as it was.
   Result: 1 critical, 2 high, 10 medium, 12 low
   ([security-review.md](reviews/security-review.md)).
2. **Adversarial tests (R2), tests only.** Black-box tests through the real HTTP app, two
   tenants and the real chat service. They cover cross-tenant ids, retrieval and cache
   leakage, privacy egress across turns, forged vault tokens, order enumeration, prompt
   injection in documents and upload abuse. This workstream may not change product code: a
   failing test is a finding, not something to patch around.
3. **Evaluation harness (G1).** Runs end to end over fixed cases (see D-13).

**Why:** the person who writes the code is the worst-placed to find its blind spots.
Keeping review, attack and fix separate means a fix is checked by a test written by
someone else.

### 2.5 Fixes are routed by ownership, and each fix has a regression test

Review findings were split into two fix lists along the ownership line: agent-side (E2) and
platform-side (F2). Each fix must come with a regression test that reproduces the review's
repro, and each completion report must mark every finding id as fixed or not fixed, with a
reason.

## 3. Decision log

| ID | Date | Decision | Why | Evidence |
|---|---|---|---|---|
| D-01 | 2026-10-31 | Build the multi-tenant platform on a separate branch and keep the no-PII-egress rule unchanged | The new stack contradicts ADR 003/004, so it is evaluated as a variant rather than replacing `main`. Privacy is not up for trade-off | ADR 005 |
| D-02 | 2026-10-31 | Freeze shared contracts before parallel work, including the `ChatReply` schema | Lets the API, agent, tools and RAG be built at the same time against stable types | `e7df118`, `7da10a5`, `docs/platform/contracts.md` |
| D-03 | 2026-10-31 | Unique `(tenant, order, product)` on order items | Makes the CSV seed idempotent (re-running it cannot duplicate lines) and enforces data integrity in the database, not only in code | `320dbbc` |
| D-04 | 2026-10-31 | Hybrid retrieval: pgvector cosine + Postgres full-text (`portuguese`), RRF fusion, a minimum-similarity gate, tenant filter in SQL, versioned ingestion | Policy questions mix exact terms (Pix, SEDEX, section numbers) and paraphrase. Neither lexical nor vector search alone covers both. Versioning keeps old answers traceable to the document they cite | `0035ef2`, ADR 005 |
| D-05 | 2026-10-31 | Order lookup takes vault tokens, not raw values, and returns an abstract status view | The model must be able to drive an order lookup without ever seeing the order id, contact or tracking code. Trusted code reveals tokens only in the final answer | `0372e52`, `app/services/privacy_vault.py` |
| D-06 | 2026-10-31 | The tenant is derived only from the API key (hashed with a pepper, scoped). Logs are structured and redacted | Tenant ids in headers, query params or text can be forged. A credential cannot | `b82fb64` |
| D-07 | 2026-10-31 | Full-text query uses OR semantics over the query lexemes | With AND semantics, a full natural-language question almost never shares every lexeme with the answer chunk, so the lexical side of the hybrid search returned nothing. `ts_rank_cd` still rewards chunks matching more terms, and the similarity gate still applies. **Known side effect:** retrieval now almost always returns something, so "no answer" detection must not rely on the chunk count (open item O-1) | `21ce09f` |
| D-08 | 2026-10-31 | Stop feature work after the first wave and run a full review pass before continuing | The privacy and tenancy guarantees are the product's core promise. They had to be verified before more code was built on top of them | `docs/reviews/security-review.md` |
| D-09 | 2026-10-31 | Change the frozen contracts once, explicitly, to enable the fixes: `APP_ENV` defaults to `prod` (fail closed), user-ref HMAC key, rate-limit/lockout/retention settings, DB statement timeout, `AttemptLimiter` in `ToolContext` | Several findings (H-1, M-8, M-10, L-8) needed new shared settings or interfaces. Changing the contracts in one commit, before the fixes, kept the fix workstreams independent | `3cdb3a5` |
| D-10 | 2026-10-31 | Commit the agent workstream as a checkpoint before applying the review fixes | The agent workstream was interrupted before its own completion check. Committing its state, with one known failing test (L-1), gave the fix pass a clean baseline and kept the history honest about what was already done | `37d69af` |
| D-11 | 2026-10-31 | Split the review fixes into an agent-side list (E2) and a platform-side list (F2) | Follows file ownership (2.2), so both lists can be fixed in parallel without conflicts | task specs; this document |
| D-12 | 2026-11-01 | Order-lookup lockout key for the actor = the end user's external reference when present. Otherwise `actor_ref` is empty and the per-conversation key is used. A tenant- or channel-wide hash is rejected | A tenant-wide key would let a single caller trigger the lockout for every API user of that tenant (denial of service). Brute force on one order is still stopped by the per-order lockout, and API abuse by the per-API-key rate limit | coordination between E2/F2 |
| D-13 | 2026-11-01 | The evaluation runs deterministically by default with three hard gates (tenant leakage = 0, privacy violations = 0, harness errors = 0). Quality metrics are reported but do not gate. Live mode is optional | The deterministic fake model is rule-based, so its quality numbers measure the pipeline (retrieval, tools, guardrails, citations, privacy), not language quality. Gating on them would be misleading. Leakage and privacy are binary properties that must hold in every mode | `scripts/eval_rag.py`, `evals/REPORT.md` |
| D-14 | 2026-11-01 | Defects found by the evaluation go to the owner of the affected file. Eval cases are not edited to make them pass | An eval that bends to the code stops measuring anything. All 23 cases were checked against the policy PDF and the CSVs and left unchanged | `evals/REPORT.md` |
| D-15 | 2026-11-01 | Adversarial findings are committed as **strict** `xfail` tests, and the fix removes the marker | A strict xfail fails the suite once the bug is fixed, so the marker cannot be forgotten. A test that exposes an open bug documents it in executable form without breaking CI | `tests/adversarial/FINDINGS.md` |
| D-16 | 2026-11-01 | Amounts in retrieved documents may be quoted only as policy values (fees, thresholds, percentages, deadlines) in sentences that name no catalog product. Any amount next to a product must match this turn's catalog tool result | Two requirements pull in opposite directions. The evaluation needs policy amounts from the PDF (Pix discount, minimum installment, free-shipping threshold) to pass the guardrail. The adversarial suite showed that accepting every document amount lets a planted document set a product's price (FINDING-5). Splitting by "names a product or not" keeps both | E3 task; FINDING-5; eval cases `rag-pix-discount`, `rag-free-shipping` |
| D-17 | 2026-11-01 | Chunks flagged as suspicious at ingestion are excluded from retrieval, and blanket commercial claims need backing from a catalog tool result | The suspicious-instruction flag was computed but never used. A document must not be able to make store-wide commercial promises (FINDING-4) | E3 task; FINDING-4 |
| D-18 | 2026-11-01 | Retrieval is "sufficient" only when the best chunk reaches the similarity threshold, or when a lexical hit covers enough of the question. Lexical coverage lowers the similarity floor for that chunk. `search_knowledge` withholds insufficient sources from the model | Fixes the side effect of D-07 (O-1) without losing its recall gain. It also explains O-6: a correct chunk with strong term overlap but weak embedding similarity is kept, while an off-topic question with incidental term overlap is not | `app/rag/retrieval.py`, `tests/rag/test_retrieval_sufficiency.py` |
| D-19 | 2026-11-01 | Disclose coding assistants and their workflow in the root README, with explicit owner-confirmation placeholders where the tool record is missing | The case requires naming coding assistants and explaining their use; owner authorship of commits does not describe who generated the code | Case p.2; README “Uso de assistentes de código” (“Use of coding assistants”) |
| D-20 | 2026-11-01 | Present multi-tenancy, generic uploads and WhatsApp as optional platform experiments; keep one seed-tenant/API walkthrough instead of rewriting before submission | R3 A-6 finds the breadth disproportionate to demonstrated case needs; clear scope and evidence have more value than a late rewrite | ADR 005; R3 §6; README |
| D-21 | 2026-11-01 | Recommend a complete Docker run path, with an idempotent setup script, configurable loopback ports and a separate opt-in test image | Reproduce the whole application on the evaluator’s machine with only Docker/Compose; preserve the host Python/uv alternative and keep customer exports out of every image | Task D1; `scripts/docker_up.sh`; README “Executar com Docker” (“Run with Docker”); Docker validation below |
| D-22 | 2026-11-01 | Use LangChain only for provider-neutral chat/embedding adapters beneath the Agents SDK loop and mandatory PrivacyGuardedModel; preserve tools, vault, guardrails and hybrid retrieval | Owner decision L1 restores model-provider choice from ADR 001 without a LangGraph/retrieval rewrite; explicit factories avoid the top-level langchain/LangGraph dependency, and unavoidable transitive LangSmith tracing is forbidden in every environment | [ADR 006](adr/006-langchain-model-layer.md); L1 validation §4.4; no live provider calls |
| D-23 | 2026-11-01 | Fix first-live-run false positives using schema-aware transport scanning, current-turn budget echoes, unanimous generic stock evidence and question-aware stock checks; bump Portuguese policy to commerce-privacy-v3 | Native gpt-5-mini exposed failures hidden by fake output; preserve customer-text scanning and product-price grounding. Coordinator re-scoped F6 to applying general documented policies, after confirming strong vector matches already pass | R4 offline regressions and validation §4.5; live rerun remains coordinator-owned |
| D-24 | 2026-11-01 | Repair catalog intent/partial matching, tenant public address/persona scanning and Custom-number price parsing; prompt commerce-privacy-v4 explains geography and partial agreement | Second native-model round still exposed query and lexical false positives; preserve tenant filters, private-data scans and grounded answer + handoff | R5 evidence and offline validation §4.6; live rerun remains coordinator-owned |
| D-25 | 2026-11-01 | Distinguish lookup offers/contact requests from answer claims; ignore vault indices and allow configured persona phrases; prompt commerce-privacy-v5 hides internal contact/tool names | R5 live answers were rejected for capability lists, placeholder digits and introductions; unsupported stock, commercial values and unrelated numbers must still fail | R6 evidence and offline validation §4.7; live rerun remains coordinator-owned |
| D-26 | 2026-11-01 | Normalize pt-BR singular/plural catalog queries, separate per-model and agent-loop deadlines, structurally classify stock assertions, and narrow tool/handoff prompt rules in commerce-privacy-v6 | R6 live runs exposed singular category misses, 30s aggregate turn failures, a scope-list stock false positive and unrelated conflicting_sources handoffs | R7 evidence and offline validation §4.8; live rerun remains coordinator-owned |
| D-27 | 2026-11-01 | Give factory-built LangChain OpenAI models dedicated SDK-compatible HTTP clients and close only explicitly owned clients; leave shared Anthropic clients open; require fresh stock tools on follow-up questions in commerce-privacy-v7 | J1: the LangChain path introduced by L1 / PR #1 was broken after the first request, discovered by the first live LangChain run; J2: R7 follow-up availability answers used history without current-turn stock evidence | R8 regression evidence and validation §4.9; post-fix live OpenAI rerun remains coordinator-owned, Anthropic/Ollama unexecuted live |
| D-28 | 2026-11-01 | Serve a vanilla HTML/CSS/JS chat test page from FastAPI at `/chat`, gated by `expose_api_docs`, with a page-scoped CSP and session-only API key | Local evaluation and demos need zero build steps/dependencies and same-origin API calls; the production gate avoids presenting a developer tool as an authenticated customer widget | U1; `tests/api/test_chat_ui.py`; README “Interface de teste (navegador)” (“Browser test interface”) |
| D-29 | 2026-11-01 | Store an optional welcome per tenant and emit it once at first contact; greeting-only turns skip the provider, questions retain their normal answer | Strategic, consistent store onboarding without spending an LLM call on greetings; tenant ownership keeps brand, hours and tone configurable and preserves behavior for unconfigured tenants. Input safety/privacy run first; refusals, failures and handoffs omit the welcome | W1; `AgentSettings.welcome_message`; `tests/agent/test_welcome.py`; `tests/agent/test_chat.py`; `docs/platform/contracts.md` |
| D-30 | 2026-11-02 | Separate scope refusals from missing store information: a shared public `OUT_OF_SCOPE` reply, a bounded whole-reply recognizer (`is_out_of_scope_refusal`) kept by `ChatService` without grounding or handoff, offline fake routing only when an empty result has no store-domain signal; policy `commerce-privacy-v8` prescribes the fixed pt-BR refusal | The policy already forbade handoffs for unrelated questions, but the offline model answered `NO_INFORMATION` after empty retrieval and the lexical factual-claim fallback could read a scope list as an unsupported answer; demo 05 and its test encoded the wrong handoff, and live runs showed wording-dependent failures on the World Cup question. A bounded grammar is auditable and cannot hide neighboring factual claims. Genuine store gaps keep `NO_INFORMATION` + `no_information`; the refusal stays Portuguese for English customers | `tests/agent/test_scope_refusal.py`; `tests/agent/test_chat.py`; `tests/test_demo_conversations.py`; `examples/conversations/05-out-of-scope-and-address.md`; `evals/live/english-conversations/` |
| D-31 | 2026-11-02 | Make English-customer paths work without coaching: accept en-US money formats and English budget cues in price validation; recognize English no-information admissions; add `OUT_OF_SCOPE_EN` and require customer-language replies; instruct Portuguese `search_knowledge` queries against the Portuguese manual; forbid offering tool-less actions; bump policy to `commerce-privacy-v9` | Live English recordings under v8 failed a budget catalog turn on the `price` rule (`R$1,000` was invisible to `customer_amounts`, so budget echoes were treated as product prices), missed English “I couldn't find…” admissions, kept a Portuguese-only fixed scope refusal, needed coached Portuguese retrieval for returns, mixed languages, and offered reservation/contact updates with no tool. Fixes stay conservative: wrong English-format prices still fail; Portuguese regressions are covered offline; tenant welcome stays pt-BR | `tests/agent/test_english_customer_fixes.py`; `evals/live/english-conversations/`; R9 native 24/27 in `evals/live/2026-11-01-gpt-5-mini.md`; R9 #14 `privacy_guard` traced to the order lockout set off by the recordings' deliberate wrong contacts, re-run alone after expiry: verified answer |
| D-32 | 2026-11-02 | Humanize Empório customer-service tone without weakening guardrails: ESTILO block + `commerce-privacy-v10`, first-contact welcome note, lighter citation footers, warm fixed messages/welcome/handoff, stronger no-CEP/tool-less offers, explicit `--refresh-welcome-and-handoff` | Owner live samples under v9 greeted again after the welcome, stacked `[Sn]` and a formal Fontes block, used em dashes/headings/generic closers and offered CEP shipping quotes with no tool. Changes stay prompt/render/copy only; [Sn] grounding and scope/privacy rules unchanged; seed never overwrites operator welcome/handoff unless the refresh flag is passed | `app/agents/instructions.py`; `app/rag/citations.py`; `app/agents/guardrails.py`; `scripts/create_tenant.py`; offline demos; R10 native in `evals/live/2026-11-01-gpt-5-mini.md` |
| D-33 | 2026-11-02 | Close v10 review gaps: sync handoff-success grammar with the new suffix, validate the identical per-turn prompt (welcome note), restore the privacy prohibition, ban unsupported return/freight offers, accept English colon/list scope punctuation, parse budget number words (`mil`), keep multi-source footer labels, drop generic SAFE_FALLBACK closer and slogan from seed handoff | Independent review rejected E3: success-claim and prompt-leak validators lagged the new copy; privacy sentence was dropped; live samples still offered tool-less return initiation; English `policies!` failed the grammar; `mil reais` budgets tripped price; format check failed on CRLF | `app/agents/guardrails.py`; `app/services/chat.py`; `app/agents/instructions.py`; `app/rag/citations.py`; offline regressions; R10 native recheck |
| D-34 | 2026-11-02 | Make number-word money parsing span-aware and fail closed; share those spans with budget-echo exemption; narrow `unsupported_offer` to affirmative assistant clauses (negation, customer instructions, and human referrals pass); keep welcome note tenant-truthful without asserting hours | V3b rejected E3b: “vinte mil”/“1,5 mil” authorized false R$ policy amounts; offer regex rejected checkout advice/referrals while missing “inicie sua devolução”/“cotação de frete”; word-form budget echoes were rejected; welcome note claimed hours for every tenant | `app/agents/guardrails.py`; `app/agents/instructions.py`; `tests/agent/test_humanized_tone_review_fixes.py`; docs/README; R10 native recheck |
| D-35 | 2026-11-02 | (Money/budget part superseded by D-36.) Supersede the D-33/D-34 number-word parser and the `unsupported_offer` output rule. Money is read from digits only (main's behavior; a digit inside a number-word expression such as `mil e 500` no longer counts). Number words are parsed only in the current customer message, only as a budget, with a whole-expression grammar (`mil`, `um mil`, `<digits> mil`, `dois`…`dez mil`); an answer may echo that budget in a budget phrase, and any other monetary number-word expression is a `price` violation. Offers of return initiation/freight quotes are prompt-only. `search_knowledge` retries once without the category when a category-filtered search is insufficient | V3c rejected D-34: unsupported spans were skipped in answers, so `R$1,5 mil` passed as a grounded false price; partial-span matching read `vinte e dois mil` as 2000 and `duzentas mil` as 1000; the offer filter rejected safe guidance and let affirmative offers through after two rounds of patches. In R10 #23 the model passed `category="shipping"` while the manual is filed as `politicas`, so retrieval returned nothing | `app/agents/guardrails.py`; `app/agents/customer_service.py`; `tests/agent/test_humanized_tone_review_fixes.py`; README; flows; R10 native recheck |
| D-36 | 2026-11-03 | Revert money/budget validation to `main` (HEAD 21eb986): digit-only `money_amounts`, digit-only budget echo checked against the prefix. Remove the D-35 customer-budget word grammar and whole-clause/colon echo logic. Number-word budgets and prices are a documented limitation; offers of return initiation/freight quotes stay prompt-only | Owner decision after five review rounds (V3–V3e): each number-word iteration closed the reported bypasses and opened new ones. `main`'s parser is the known baseline. It rejects every digit false price the reviews found; word-only prices ("custa mil reais") and "mil e 500" documents remain unchecked, and an answer that repeats a word budget as digits falls back to a human | `app/agents/guardrails.py` (money definitions identical to HEAD); `xfail(strict=True)` limitation tests in `tests/agent/test_humanized_tone_review_fixes.py`; replay of the 32 E3d replies: only the owner sample "tem violão até mil reais?" would now be rejected (`price`); R10 note |
| D-37 | 2026-11-03 | Add `OPENAI_REASONING_EFFORT` (`minimal`/`low`/`medium`/`high`, empty = provider default), passed as `ModelSettings.reasoning` in the run config for the native OpenAI adapter only, and ship `low` as the default in `Settings` and `.env.example`. `minimal` stays available but is not recommended | The owner measured 17–24s per browser reply. Tools take milliseconds (catalog) or about 2s (knowledge search); the rest is 2–3 sequential `gpt-5-mini` calls at the provider's default reasoning effort. One 27-turn battery per setting: provider default (R10) 24/27, median 11.8s, p90 17.7s, max 44.2s, mean 11.9s; `low` 23/27, 9.3s / 12.0s / 27.6s / 9.4s; `minimal` 21/27, 4.0s / 5.0s / 5.8s / 3.5s. `low` kept quality within run-to-run variance (it lost #9 and #25 and recovered #27). `minimal` lost five answers and gave wrong replies counted as useful: it refused "Vcs entregam em Brasilia?" as out of scope and answered follow-ups without tools. The LangChain adapter does not map the setting; documented instead of adding a second mapping | `app/core/config.py`; `app/services/chat.py`; `tests/agent/test_chat.py::test_reasoning_effort_reaches_native_model_only`; `tests/agent/test_live_model_round_four.py`; R11 columns in `evals/live/2026-11-01-gpt-5-mini.md` |
| D-38 | 2026-11-03 | Add opt-in deterministic triage before the agent, after input safety checks; persist steps and opaque order references in existing assistant-message metadata; reuse the verified order tool and final local reveal | Routine hours/contact, online withdrawal summary, guided order lookup and human handoff need no model; specific questions retain the agent, reducing routine latency/cost while preserving privacy and tenant compatibility | `app/services/triage.py`; `tests/agent/test_triage.py`; contracts T1; five regenerated examples; isolated fake/hash HTTP smoke |


## 4. Open items and known limitations

| ID | Item | Status |
|---|---|---|
| O-1 | "No answer" detection: retrieval signalled sufficiency as "returned any chunk", which together with D-07 meant an off-topic question (case `noans-parking`) got an answer instead of a handoff | fixed in E3 (D-18) |
| O-2 | Price guardrail rejected R$ amounts quoted from cited policy chunks (cases about Pix discount, minimum installment, free shipping, SEDEX time) | fixed in E2 (amounts must match this turn's tool prices or the cited chunks) |
| O-3 | "cobrado duas vezes" (double charge) was not classified as a sensitive topic | fixed in E2 (part of M-4) |
| O-4 | A first coordinator-run native gpt-5-mini/text-embedding-3-small test found material failures; R4 fixes are validated offline, with live rerun and held-out quality evaluation still pending | open; first live evidence does not establish acceptable quality |
| O-5 | Adversarial findings FINDING-1…6 (see 4.1) | all fixed: FINDING-3/6 in F3, FINDING-1/2/4/5 in E3; no strict xfail left |
| O-6 | Case `rag-mouthpiece`: the answer missed the expected hygiene term. Cause: the right chunk (section 4.4) had cosine similarity 0.096 with hash embeddings, below the lexical-hit floor | fixed in E3 (D-18) |
| O-7 | Older lint debt outside the fix scopes | fixed at integration: `ruff check` and `ruff format --check` clean on the whole repo; `ToolResult` moved to PEP 695 generic syntax |
| O-8 | `test_embedding_failure_is_safe` embedded a random UUID in user free text; digit runs could trigger privacy_guard before the intended embedding failure. R4 uses an alphabetic cache nonce and separately covers opaque provider/schema IDs | fixture flake fixed in R4; UUID-like free text remains scanned, not exempted |
| O-9 | Lower-case names typed outside a self-introduction and outside the tenant's customer list are not detected (rule-based detection) | open, documented limitation |
| O-10 | The commercial-claim output rule is lexical and conservative: it can block a correct sentence that negates a promotion | open, fails safe |
| O-11 | A-2: `grounded` checks evidence presence and selected lexical facts, not full claim consistency (stock, dates, policy conditions); need typed evidence/local rendering and contradiction regressions | open |
| O-12 | A-3: sequential reversible conversation tokens diverge from the original random-token/no-reversible-egress policy; rule-based detection cannot establish universal no-raw-PII coverage (also O-9) | wording aligned in X2; structured external-input boundary and runtime/policy gap remain open |
| O-13 | A-4: fake routing, single-turn evaluation and soft quality metrics do not validate live tool choice, multi-turn behavior, tone or policy conflicts (also O-4) | open; require held-out real/local-model evidence before quality claims |
| O-14 | A-5: concurrent turns and retries have no conversation lock, vault CAS or coherent transaction/idempotency boundary | open; walkthrough restricted to one active turn per conversation |
| O-15 | A-7: document versions lack embedding/extractor/chunker generation identity; reprocessing can remove serving chunks before success | open; offline-to-live reindex requirement documented |
| O-16 | A-8: similarity/lexical coverage is not answer completeness; source authority, contradictions, rule/exception structure, OCR and complex layouts remain limited | open; calibrate on representative documents and real embeddings |
| O-17 | A-9: ChatService combines persistence, orchestration, evidence validation and rendering; lower-layer dependencies and mutable audit dictionaries leak across boundaries | open; refactor around typed turn/evidence state and explicit transactions |
| O-18 | A-10: chat/handoff scan tenant customer data, and catalog filtering loads complete tenant datasets | open; seed-scale only, measure before larger tenants |
| O-19 | A-11: SQL tenant filters lack RLS/composite tenant relationship enforcement; direct HTTP chat lacks end-user/session binding | open; use trusted-server credentials and add signed session ownership |
| O-20 | A-12: enqueue/delivery are not durable completion; Redis has no Compose volume, WhatsApp acknowledges failures and uses global credentials | open; optional channel only, inbox/outbox and pending-ingestion recovery needed |
| O-21 | A-13: ordinary turns do not consistently update conversation activity; current/failed document retention and deletion propagation are incomplete | open; define and test lifecycle semantics |
| O-22 | A-14: prompt version alone cannot reproduce effective configuration; usage/cost and distinct model failures are missing, metrics are per-process | documentation aligned in X2; operational observability remains open |

### 4.1 Accepted results

**E2: agent-side review fixes (accepted 2026-11-01).**
- Fixed: C-1, H-2, M-1, M-2, M-3, M-4, M-5, L-1, L-2, L-4, L-10, L-12b, each with a regression test.
- Checks re-run at acceptance:
  - `ruff check`, `ruff format --check` and `mypy --strict` are clean on the agent files;
  - `pytest tests/agent tests/eval` gives 187 passed (before the fixes: 61 passed, 1 failed);
  - the deterministic evaluation passes all hard gates.
- Evaluation before → after:

| Metric | Before E2 | After E2 |
|---|---|---|
| citation_coverage | 6/12 | 12/12 |
| groundedness | 12/19 | 18/19 |
| handoff_reason | 1/2 | 2/2 |
| tool_use | 4/4 | 4/4 |
| no_answer_safety | 1/2 | 1/2 (O-1) |

- Limitations the fix pass reported:
  - names typed in lower case outside a self-introduction or the known-customer list are not detected; recognized residual identity cues fail closed, but names without those cues can still pass (R3 A-3 / O-9);
  - bare numbers in messages that mention an order are over-tokenized;
  - a tenant document containing a non-public street address would be blocked by the egress scanner.

**R2: adversarial suite (accepted 2026-11-01).**
- Black-box tests through the real HTTP app in `tests/adversarial/` (91 passed, 8 strict xfail; lint clean).
- The 6 findings are each tracked as a strict xfail (D-15):

| Finding | Severity | Summary |
|---|---|---|
| FINDING-1 | High | A bare 11-digit CPF reaches the model |
| FINDING-2 | High | S10 tracking codes that do not start with `BR` reach the model |
| FINDING-3 | Medium | The API handoff summary keeps customer names and order ids |
| FINDING-4 | Medium | A document-planted commercial claim is repeated |
| FINDING-5 | Medium | Document amounts are accepted as product prices; this regression came from the E2 price fix |
| FINDING-6 | Low | A NUL byte gives HTTP 500 |

- It also confirmed, with tests kept as regression guards:
  - the cross-conversation lockout fix (H-1);
  - the rejection of the tenant-wide actor bucket (D-12).

**F2: platform-side review fixes (accepted 2026-11-01).**
- Fixed: H-1, L-3, L-4, L-5, L-6, L-7, L-8, L-9, L-11, L-12a, M-6, M-7, M-8, M-9 (partial: extraction off the event loop), M-10.
- Checks re-run at acceptance:
  - `mypy` clean on all 63 files of `app/`;
  - `pytest -q` (whole repo) gives 509 passed, 8 xfailed, on two consecutive runs;
  - `docker compose build` succeeds, and the image contains no customer dataset.
- Behaviour changes worth knowing:
  - a phone without area code no longer verifies an order;
  - auth-failure audit rows are sampled per IP;
  - non-UUID/non-hex request ids are replaced.
- Not done: per-tenant WhatsApp credentials and asynchronous webhook enqueue (L-7 remainder).

**F3: adversarial FINDING-3 and FINDING-6 (accepted 2026-11-01).**
- FINDING-3: handoff summaries created through the API are now redacted with the same rules as the chat path. The tenant's own customer names and order ids are applied with a tenant filter, so another tenant's order number is left alone.
- FINDING-6: a NUL character in any JSON field or multipart form field returns 422 without echoing the input. The WhatsApp webhook strips the character and still acknowledges with 200, as the provider protocol requires. Ingestion strips it from metadata as a second line of defence.
- Checks re-run at acceptance:
  - lint and `mypy` clean on the changed files;
  - `pytest tests/adversarial` gives 95 passed and 4 strict xfail, the 4 remaining belonging to E3.
- Known cost: each API handoff loads the tenant's customer names and order ids, the same per-request cost as the chat path.

**E3: adversarial FINDING-1/2/4/5 and retrieval sufficiency (accepted 2026-11-01).**
- FINDING-1: bare 11-digit CPFs are tokenized when the check digits are valid. Valid phone shapes stay phones.
- FINDING-2: S10 tracking codes in any two-letter prefix are tokenized; the legacy `BR…BR` shape is kept.
- Both fixes are applied in both the user-text redaction and the egress scanner.
- FINDING-5: implemented as decided in D-16.
- FINDING-4: implemented as decided in D-17.
- O-1 and O-6: fixed by D-18.

**R3: independent architecture review (accepted 2026-11-01; snapshot `407d846`).**
- [Full report](reviews/architecture-review.md): a substantial, tested platform experiment,
  with missing case deliverables and material gaps in behavioral evidence and guarantees.
- Scores (out of 5): requirement fit **3**, proportionality **2**, layering **3**, agent
  design **3**, privacy architecture **3**, RAG **3**, multi-tenancy/operations **2**,
  quality strategy **4**, documentation **2**. These assess the reviewed snapshot, not
  production certification or a re-score after documentation fixes.
- Highest findings: missing README/examples (A-1), evidence presence mistaken for factual
  grounding (A-2), heuristic PII detection and inconsistent token policy (A-3), no live
  model evidence (A-4), and concurrent-turn state integrity (A-5).
- R3 independently reproduced 572 passing tests and 114 subtests, mypy clean on 63 files,
  and all deterministic evaluation gates/soft metrics passing; it made no paid API calls.
- This submission round addresses A-1 through X1's saved examples and X2's root README /
  assistant disclosure; clarifies A-3 wording without claiming a runtime fix; bounds the
  optional platform scope for A-6 (D-20); and aligns historical/current docs for A-14.
- A-2 through A-14 runtime/evidence limits remain tracked in O-4, O-8–O-22 as applicable.
  The root README also documents serial demo turns and offline-to-live reindexing. No
  late platform rewrite or production-readiness claim follows from accepting the review.

### 4.2 State at integration (2026-11-01)

**Re-measured after integrating X1 and X2 (2026-11-01).** The previous snapshot `407d846` had
572 tests. In-flight checks taken while parallel workstreams were still editing are not
recorded as results. O-8 remains an intermittent known failure.

| Check | Result |
|---|---|
| `uv run ruff check .` / `uv run ruff format --check .` | clean (whole repo) |
| `uv run mypy app` | no issues in 63 files |
| `uv run pytest -q` | 603 passed, 123 subtests passed, 0 xfail, on two consecutive runs |
| `uv run python -m scripts.eval_rag` (deterministic) | all hard gates pass (leakage 0, privacy 0/23, errors 0); all six soft metrics 100% |

Evaluation progression over the fix waves:

| Metric | After first wave | After E2 | After E3 |
|---|---|---|---|
| source_hit | 12/12 | 12/12 | 12/12 |
| citation_coverage | 6/12 | 12/12 | 12/12 |
| no_answer_safety | 1/2 | 1/2 | 2/2 |
| groundedness | 12/19 | 18/19 | 19/19 |
| tool_use | 4/4 | 4/4 | 4/4 |
| handoff_reason | 1/2 | 2/2 | 2/2 |

All six metrics are now at 100% in deterministic mode. That shows the pipeline is consistent,
not that language quality is good: the fake model is rule-based (D-13), and the live run is
still pending (O-4).

### 4.3 Docker run path validation (2026-11-01)

Task D1 used only its own Compose project, `emporiod1`, with `API_PORT=8010`,
`DB_PORT=5444` and `REDIS_PORT=6390`; existing `test-rag` and `emporiofresh`
containers were preserved. The first run began without a project database volume
or repository `.env`; the script created `.env` with mode 600 and generated the
key inside the runtime container without printing it. No real OpenAI calls were made.

Environment: macOS/arm64, Bash 3.2.57, Docker Engine 29.7.2, Compose v5.5.1,
Linux/arm64 image with Python 3.12.16; host-only checks used uv 0.11.18.
Commands below inherited the project name and port exports above.

| Check | Measured result |
|---|---|
| `./scripts/docker_up.sh --smoke` (first run) | exit 0; build, migration, seed, API and worker started; HTTP 200 and reply containing `R$` |
| `./scripts/docker_up.sh --smoke` (second run) | exit 0; same smoke result; `.env` byte-for-byte unchanged, same key reused |
| Seed, both runs | categories=9, products=65, promotions=25, customers=50, orders=20, order_items=22 |
| Host `GET http://127.0.0.1:8010/health` | HTTP 200, `{"status":"ok"}` |
| `docker compose --profile test build test` | exit 0; separate test target with development dependencies |
| `docker compose -p emporiod1 --profile test run --rm test` | 603 passed, 123 subtests passed in 24.15s; no skips, warnings or xfails |
| `docker compose -p emporiod1 --profile test run --rm test python -m scripts.eval_rag` | exit 0; all hard gates pass: tenant leakage 0, privacy violations 0/23, harness errors 0; all six soft metrics 100% |
| `docker compose config -q`, with default ports and with overrides | both exit 0 |
| `bash -n scripts/docker_up.sh` | exit 0 on Bash 3.2.57 |
| `docker run --rm emporio-platform:local ls /app` | only alembic, alembic.ini, app, pyproject.toml, scripts, src, uv.lock; separate assertions confirmed no tests, challenge data or pytest |
| Test image inspection without mounts | challenge data absent; data enters only the read-only test/seed bind mounts |
| `uv run pytest tests/test_documentation_contract.py -q` | 5 passed, 31 subtests passed |
| `uv run ruff check scripts tests` | all checks passed |
| `docker compose -p emporiod1 down -v` | removed only the verification project's containers, network and database volume |

The first test attempt exposed three skipped Redis tests because their fixture uses
`TEST_REDIS_URL`, independently of `REDIS_URL`; the test service now explicitly sets
both. A writable pytest cache directory removed the initial cache-permission warning.
The final full-suite result above was measured after these corrections. The coordinator
explicitly extended ownership to update the existing container-privacy regression test:
only profiled `seed` and `test` may mount the dataset, both read-only; the image exclusion
and all other privacy assertions remain enforced.

Reset semantics were checked against `scripts.seed` and `scripts.create_tenant`: the
same `.env` key can be provisioned again on an empty database; a different key is refused
on an already seeded database. The deterministic providers remain the default.

Not executed: OpenAI/live mode, a native Linux or Windows/WSL host run, PowerShell's
manual recipe, or the optional `linux/amd64` build. ShellCheck was unavailable. Existing
O-8 intermittent behavior remains open; these passing results do not fix it or establish
real-model quality. No open implementation questions remain for D1.

### 4.4 LangChain adapter validation (L1, 2026-11-01)

Implemented under D-22 / [ADR 006](adr/006-langchain-model-layer.md).
The coordinator explicitly revised the requested dependency plan after inspection:
`langchain` would pull in LangGraph, so explicit factories replace `init_chat_model` /
`init_embeddings`. Only four direct packages were added; `langsmith` is accepted solely
as a transitive dependency, with startup tracing rejection and no application tracer.
The ADR 001 cascade and provider benchmark remain unimplemented.

| Dependency | Compatible range | Locked version |
|---|---|---|
| langchain-core | >=1.2.22,<2 | 1.7.3 |
| langchain-openai | >=1,<2 | 1.7.3 |
| langchain-anthropic | >=1,<2 | 1.7.7 |
| langchain-ollama | >=1,<2 | 1.1.0 |
| langsmith (transitive only) | resolved through langchain-core | 0.14.16 |

`uv tree --depth 1`, `uv tree --invert --package langsmith` and parsed lockfile inspection
confirmed no `langchain`, `langgraph*` or `langchain-postgres` package. Native OpenAI,
Agents SDK, tools, guardrails, vault and retrieval behavior were preserved. Added 49 offline
regression tests; the README assistant-disclosure row adds one documentation subtest.

| Command / check | Measured result |
|---|---|
| `uv sync --frozen` | exit 0; locked environment reproducible |
| `uv run ruff check .` | all checks passed |
| `uv run ruff format --check .` | all 161 files already formatted |
| `uv run mypy app` | no issues in 64 source files, strict configuration |
| `uv run pytest -q` | 652 passed, 124 subtests passed in 35.67s; no skips, warnings or xfails |
| `uv run python -m scripts.eval_rag` | all hard gates pass: tenant leakage 0, privacy 0/23, harness errors 0; all six soft metrics unchanged at 100% |
| `docker build --target runtime -t emporio-platform:l1 .` | exit 0; native linux/arm64 runtime built, original `emporio-platform:local` tag preserved |
| `docker compose -p emporiol1 --profile test build test` | exit 0; test image built from the frozen lock |
| `API_PORT=8040 DB_PORT=5474 REDIS_PORT=6420 docker compose -p emporiol1 --profile test run --rm test` | 652 passed, 124 subtests passed in 30.53s; no skips, warnings or xfails |
| `docker compose -p emporiol1 down -v` | exit 0; removed only emporiol1 containers, network and volume |
| Post-cleanup container inventory | test-rag-db-1/test-rag-redis-1 and all emporiov services, including API on :8020, remained running |

Runtime image measurements used `docker image inspect IMAGE --format '{{.Id}} {{.Size}}'`
on the same Docker Engine; the values below are its reported `Size` field in bytes,
not `docker image ls`'s displayed disk-usage figure.

| Image | Image ID | Size (bytes) |
|---|---|---|
| Before: emporio-platform:local | sha256:56e2c84863754c002cfef7281a587ebe9e7f8b7326ea89742ab42d643ac8099d | 158192570 |
| After: emporio-platform:l1 | sha256:451aece5fcc4662b829fa0a719bc8ad7ae4c1419417d396d7b1f6001d225ff9d | 180471582 |
| Delta | +14.08% | +22279012 (+21.25 MiB) |

No real OpenAI, Anthropic or Ollama endpoint was called, and no live-provider quality,
retention or performance validation was executed. LangChain streaming, SDK structured
output and SDK handoff definitions are explicitly unsupported; the current ChatService
uses non-streaming Runner.run and its existing local human-handoff tool. LangChain uses
the global model string, not native per-tenant model overrides. Embeddings must remain
1536-dimensional; new dimensions require a migration, and changing models requires
reindexing. Provider terms/retention still require separate review. A-3/O-9 privacy limits
and the known O-8 flake remain open; this run does not claim to resolve them.
No implementation questions remain for L1, and no commit was created.

### 4.5 First live-model fixes (R4, 2026-11-01)

The coordinator's first native OpenAI run with gpt-5-mini and text-embedding-3-small
found useful-answer failures that fake/hash tests had not exposed. R4 was implemented
and tested only offline; no real provider was called by this worker.
The coordinator owns the subsequent live rerun and its quality numbers.

- F1: scanning projects out `id`, `call_id`, `encrypted_content` and `status` only
  from top-level known provider output items (including fake/LangChain equivalents).
  Instructions, message content, call arguments, tool outputs and reasoning text
  remain scanned, including JSON-encoded content. Local schema identifier fields
  require a canonical UUID before exemption; order/customer IDs never qualify.
  This projection never changes the provider request or logs opaque/encrypted data;
  existing tracing remains disabled or enabled without data.
- F2: the current redacted customer message supplies allowed budget amounts, with
  equivalent Portuguese currency formats. Only budget/filter echoes before product
  attribution qualify; a wrong product price still triggers `price` even when it
  equals the customer's budget. Historical budgets do not grant an exemption.
- F3/F4: generic availability requires all candidate products to agree; quantities
  still need one product with a matching stock quantity. Terminal question marks
  survive stock parsing, and search offers are not inventory claims. Existing X1
  stock-zero/99-unit contradictions remain covered.
- F5/F6: policy version `commerce-privacy-v3` adds product-name-only presentation,
  search-first catalog guidance, lowest returned available prices for cheap options,
  knowledge lookup for store scope/accessories, contact collection before order lookup,
  polite out-of-scope refusal without handoff, and natural tracking/delivery labels.
  Tool schema keys remain unchanged; the prompt maps their `_token` fields to customer
  labels while preserving literal placeholders and local identity/reveal rules.
  General documented geographical categories should be applied even if a city is absent.

F6 investigation changed the initial hypothesis: `retrieve` already accepts a chunk
above the similarity threshold without lexical coverage. The coordinator measured
similarities 0.564 (§5.1) and 0.551 (§5.2) for the Dourados question, above 0.30;
the model itself requested no_information after seeing sources. The coordinator
therefore explicitly requested **no retrieval code change**. Controlled fake vectors
now prove that missing location terms do not veto a strong match, while weak partial
lexical matches remain insufficient. Thresholds were not calibrated on real embeddings
in this task. The two existing no_answer evaluation cases must still hand off.

O-8 was a separate fixture problem: its random UUID appeared in customer free text,
not a typed identifier field. The fixture now uses a unique alphabetic nonce, retaining
the cache bypass and the original failure assertions. Digit-bearing free text stays
subject to the conservative privacy scanner. Schema-ID regressions cover the distinct
live transport-ID failure deterministically.

Final checks used `APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash
RUN_OPENAI_LIVE_TESTS=0` for pytest and evaluation. Added 53 regression cases; the
initial 700-test run passed before five further boundary regressions were added.
The two final runs below cover the complete 705-test suite.

| Command | Measured result |
|---|---|
| `uv run ruff check .` | all checks passed |
| `uv run ruff format --check .` | 162 files already formatted |
| `uv run mypy app` | no issues in 64 source files |
| `uv run pytest -q` — final run 1 | 705 passed, 124 subtests passed in 36.12s; no skips, warnings or xfails |
| `uv run pytest -q` — final run 2 | 705 passed, 124 subtests passed in 34.78s; no skips, warnings or xfails |
| `uv run python -m scripts.eval_rag` | all hard gates PASS: tenant leakage 0, privacy violations 0/23, harness errors 0 |
| Evaluation soft metrics | unchanged: source_hit 12/12, citation_coverage 12/12, no_answer_safety 2/2, groundedness 19/19, tool_use 4/4, handoff_reason 2/2; all 100% |
| `git diff --check` | clean |

Changed files: `app/services/privacy.py`, `app/agents/guardrails.py`,
`app/agents/instructions.py`, `app/services/chat.py` (guardrail argument only),
`tests/agent/test_live_model_fixes.py`, `tests/adversarial/test_adv_failures.py`,
`tests/rag/test_retrieval_sufficiency.py`, `docs/development-process.md`, `README.md`.
`app/services/model_gateway.py` needed no change: its existing scan call now uses the
schema-aware projection. Demo generation was not rerun because fake-model output
and the checked-in examples did not change.

Remaining limits: prompt changes need a live rerun; lexical guardrails are not general
semantic entailment; A-3/O-9 privacy gaps and O-10 promotion-negation false positives
remain open. No provider thresholds, retrieval SQL, fake-model output, evaluation cases
or example conversations were changed. The coordinator also observed imperfect source
ranking for the store-address query; that retrieval-quality issue remains uncalibrated.
No containers were stopped, no git staging/commit was performed, and the owner commits.


### 4.6 Second live-model round (R5, 2026-11-01)

The coordinator's second native gpt-5-mini run used R4 with the same 27 questions:
approximately 17 useful answers versus 8 previously; the case question worked in
three phrasings. These are coordinator observations, not a new live measurement by
this worker. Instrumented evidence identified the following remaining failures.

- **G1:** all-term catalog matching returned zero for `Yamaha C40 estoque` (“Yamaha C40 stock”) and
  `guitarra promoção` (“guitar promotion”), although their product-only queries returned 1 and 5.
  Search now ignores a small documented, accent-folded Portuguese commerce-intent
  vocabulary and tokenizes punctuation. Filtered all-term matches still win; if
  absent, partial matches require at least one meaningful term, rank by coverage
  then name hits, and retain category, effective-price, stock and tenant filters.
  Seed-catalog regressions cover the exact queries and a controlled fixture covers
  ranking and promotional-price filters. Partial results are alternatives, not proof
  that the exact requested model exists.
- **G2:** the checked-in seed already contains the full public street/number,
  district, city/UF and CEP. Existing `AgentSettings` previously received no updates
  when tenant setup reran; setup now merges missing public values without changing
  custom settings or other tenants. Reapply `scripts.create_tenant` for an existing
  live seed tenant. The scanner also refused an allowlisted CEP immediately before
  a full stop; a sentence-ending dot now qualifies as a boundary, while a domain
  suffix still cannot turn a private e-mail into an allowlisted one. Public address
  answers pass the real ChatService path; customer addresses/CEPs still fail.
- **G3:** persona spellings come from this tenant's configured assistant and store
  names, including `Assistente virtual da/do ...` (“Virtual assistant of …”); both provider wrappers and the
  final-answer scan receive them. Standalone assistant-role introductions are not
  personal names. Tests cover model intermediate messages and final answers, names
  after `sou o/a` or `me chamo` (“I am” / “my name is”), unknown-store personas, and adjacent private text.
  No generic NAME-pattern exemption or customer-data allowlist was added.
- **G4:** all five quoted guitar prices match the CSV seed: Telecaster 5400,
  PRS 6800, Ibanez 7500, Stratocaster 12500, Gibson 18900. The actual rejection was
  `PRS SE Custom 24 — R$ 6.800,00`: the `cust\w*` money prefix treated **Custom**
  as a price verb and extracted **24** as an additional amount. The prefix now
  accepts explicit Portuguese price-verb forms. The full live answer passes with
  newlines or slash separators; wrong prices, including another guitar's valid
  price attached to the wrong named product, remain blocked. Apostrophes and
  Brazilian thousands separators were not the cause; O-10 remains open generally.
- **G5:** `commerce-privacy-v4` explicitly applies the documented “outras cidades”
  (“other cities”) rule when a city is not listed as metropolitan, with carriers/prazos
  (lead times) and exact freight at checkout. For partially conflicting sources, answer the common fact
  (credit card, interest-free, up to 12 installments), cite both, identify only the
  disputed minimum-installment detail and offer human confirmation. ChatService
  already preserves a grounded answer plus `conflicting_sources`; a scripted
  integration regression proves both citations and the handoff survive, so no
  handoff-discard exception was needed. The prompt example was phrased to avoid
  triggering the existing eight-word prompt-copy guard on the agreed factual answer.

R4's uncommitted changes were preserved; R5 adds 47 regression cases. The first
full-suite run exposed two test assumptions (shared seed tenant and all-term-only
result counts), both corrected before the final runs. No real provider call, staging/commit or container stop was
performed. Remaining limits: prompt compliance still needs a live rerun; A-3/O-9,
O-10 and lexical grounding limitations remain; retrieval thresholds and real-vector
ranking were not changed. The broader catalog search exposed an existing fake-router
mistake: service inquiries mentioning an instrument went to `search_products`, and
empty all-term matches had hidden that error. The offline router now sends service,
maintenance and repair inquiries to knowledge, including when they mention a price.
This is generic routing, not an evaluation-case exception; real-model behavior remains
unproven. The existing catalog audit test now expects partial hits while still
asserting contact redaction, and the seed-update regression uses its own tenant slug.
The five demo conversations were regenerated because offline routing changed; their
contents are byte-for-byte unchanged (no diff under `examples/conversations`).

R5 changed files: `app/integrations/local_commerce.py`, `scripts/create_tenant.py`,
`app/services/privacy.py`, `app/services/chat.py`, `app/agents/guardrails.py`,
`app/agents/instructions.py`, `app/services/fake_model.py`,
`tests/agent/test_live_model_round_two.py`, `tests/tools/test_catalog_tools.py`,
`docs/development-process.md`, `README.md`. Other pending files belong to R4.

All pytest/evaluation/demo commands used `APP_ENV=test LLM_PROVIDER=fake
EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0`; databases were disposable and
existing containers were left running.

| Command | Measured result |
|---|---|
| `uv run ruff check .` | all checks passed |
| `uv run ruff format --check .` | 163 files already formatted |
| `uv run mypy app` | no issues in 64 source files |
| `uv run pytest -q` — final run 1 | 752 passed, 124 subtests passed in 35.26s; no skips, warnings or xfails |
| `uv run pytest -q` — final run 2 | 752 passed, 124 subtests passed in 35.47s; no skips, warnings or xfails |
| `uv run python -m scripts.eval_rag` | all hard gates PASS: tenant leakage 0, privacy violations 0/23, harness errors 0 |
| Evaluation soft metrics | unchanged from R4: source_hit 12/12, citation_coverage 12/12, no_answer_safety 2/2, groundedness 19/19, tool_use 4/4, handoff_reason 2/2; all 100% |
| `uv run python -m scripts.demo_conversations` | five generated successfully; no content changes |
| `git diff --check` | clean |


### 4.7 Third live-model round (R6, 2026-11-01)

The coordinator supplied two native-adapter gpt-5-mini runs of 27 questions after
R5: run A had 17 useful answers plus two correct handoffs, and run B had 20 plus
two. Installments now preserved the common answer with a `conflicting_sources`
handoff. Instrumented runs isolated remaining answer-side false positives:

- **H1:** the polite refusal to “Quem ganhou a copa de 2002?” (“Who won the 2002 World Cup?”) listed capabilities
  including “Buscar violões, teclados ou baterias disponíveis” (“Search available guitars, keyboards or drum kits”) and was rejected by
  the stock rule without any tool call. A shared bounded lexical classifier now
  recognizes lookup offers, action-list entries and assistant scope descriptions;
  the no-source check uses it too, so fixing stock does not merely move the rejection.
  Explicit assertions, quantities and numeric commercial values still require
  evidence, including “Temos 3 unidades do Yamaha C40 em estoque” (“We have 3 units of the Yamaha C40 in stock”) with no tools.
- **H2:** the correct contact request for order 2 contained `[[ORDER_1]]`; its
  numeric index triggered the ungrounded-claim fallback. Vault placeholders are
  removed before numeric claim detection. Contact requests, identity-confirmation
  requests and conditional promises to check after receiving contact are not order
  facts. The exact captured answer is retained in the scripted regression, including
  its historical `contact_token` wording; `commerce-privacy-v5` separately instructs
  the model never to expose `contact_token`, `order_token`, `tool` or tool narration,
  asking simply for the email or phone used for the purchase.
- **H3:** the no-source branch receives the tenant name, assistant name, business
  description and configured tagline as persona evidence. Contiguous configured
  phrases plus greeting/identity wording are permitted, including configured
  numbers in their original context. A founding year cannot authorize that number
  as a discount or delivery duration. The tagline is also included in the model
  instructions; persona coverage does not set `grounded=true` or bypass stock,
  price, privacy or citation checks. The captured Campo Grande-MS greeting passes,
  while “Olá! Hoje tudo com 20% de desconto.” (“Hi! Everything is 20% off today.”) still becomes `NO_INFORMATION`.

R4/R5 pending changes were preserved. R6 adds 47 regression cases in
`tests/agent/test_live_model_round_three.py`, including eight scripted ChatService
cases with no model tool calls. The focused run of R4/R5/R6 and stock/grounding tests
passed 278 tests. An initial focused run exposed the folded Portuguese conjunction
“estoque e políticas” (“stock and policies”) being mistaken for an assertion; the result pattern was
narrowed before final validation.
The initial full run also exposed a prompt-copy false positive in two existing
wrong-contact/demo tests: quoting the natural contact request created an eight-word
policy overlap. The instruction now uses the shorter “e-mail/telefone da compra”
(“purchase email/phone”) example; a regression exercises the longer natural customer wording against the
unchanged prompt-copy guard. The targeted follow-up passed all 50 tests.

R6 changed files: `app/agents/guardrails.py`, `app/agents/instructions.py`,
`app/services/chat.py`, `tests/agent/test_live_model_round_three.py`, `README.md`,
`docs/development-process.md`. Other pending changes belong to R4/R5.
No provider calls, staging/commit, stash/reset/checkout or container stop were used.
The fake model/router was not changed in this round. The existing demo tests
generate all five conversations in temporary directories, verifying their behavior
and repeatability; committed examples need no regeneration when contents are unchanged.

Limits: these are bounded Portuguese lexical checks, not semantic entailment.
The prompt prohibition on internal names requires live compliance validation;
the exact historical contact answer is intentionally accepted by the claim check.
The four additional stock rejections and one price rejection from the live runs
cannot be reproduced without their text. A-3/O-9, O-10, retrieval and real-vector ranking limitations
remain unchanged. The coordinator owns the next live rerun and README live counts.

All pytest/evaluation commands used `APP_ENV=test LLM_PROVIDER=fake
EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0`, with disposable test databases.

| Command | Measured result |
|---|---|
| `uv run ruff check .` | all checks passed |
| `uv run ruff format --check .` | 164 files already formatted |
| `uv run mypy app` | no issues in 64 source files |
| `uv run pytest -q` — final run 1 | 799 passed, 124 subtests passed in 38.50s; no skips, warnings or xfails |
| `uv run pytest -q` — final run 2 | 799 passed, 124 subtests passed in 36.29s; no skips, warnings or xfails |
| `uv run python -m scripts.eval_rag --json-out /tmp/r6-eval.json` | all hard gates PASS: tenant leakage 0, privacy violations 0/23, harness errors 0 |
| Evaluation soft metrics | unchanged from R5: source_hit 12/12, citation_coverage 12/12, no_answer_safety 2/2, groundedness 19/19, tool_use 4/4, handoff_reason 2/2; all 100% |
| Demo verification | all five test-generated conversations match committed examples after removing the generation date; no regeneration of committed files |
| `git diff --check` | clean |

### 4.8 Fourth live-model round (R7, 2026-11-01)

The coordinator supplied two native gpt-5-mini runs after R6: run A produced
23 useful answers plus one correct handoff out of 27, and run B produced 17 useful
answers. These are pre-R7 observations; the coordinator owns the next live rerun.

- **I1:** the seed query with category “violão” (“guitar”, singular) returned nothing while “violões”
  (plural) returned ten available products up to R$1,000. Accent-folded token variants now
  compare singular/plural forms in both categories and search terms (ões/ão,
  ães/ão, ais/al, eis/el, ns/m and s), preserving substring matching, tenant
  filtering, price/stock filters and exact-term preference. The exact live inputs
  return ten products, or three with the additional Yamaha search term; unknown
  categories remain empty.
- **I2:** repeated tool_failure outcomes at about 30.1 seconds reflected one model
  timeout enclosing the entire agent loop. MODEL_TIMEOUT_SECONDS (default 30)
  now bounds each model request in the shared privacy gateway, including injected
  and LangChain models; TURN_TIMEOUT_SECONDS (default 75) bounds Runner.run with
  tools. Both are finite, positive and capped (300/600 seconds respectively), in
  production too. MAX_AGENT_TURNS and tool deadlines remain in force. Scripted
  tests prove that multiple calls can exceed the individual budget in total,
  while an individual slow call or an exhausted turn still safely hands off.
- **I3:** the verbatim Copa refusal now passes both stock and no-source checks.
  With no catalog facts, stock predicates require an assertion verb, a quantity,
  a ready-delivery assertion or a terse product predicate such as “Yamaha C40
  disponível” (“Yamaha C40 available”). Topic nouns and modifiers inside offers do not assert stock.
  Existing unsupported quantities and availability assertions remain rejected,
  as do contradictions against returned stock facts. Offer/scope syntax is shared
  with the no-source classifier so a successful refusal does not merely move to
  another fallback.
- **I4 / prompt:** commerce-privacy-v6 prominently forbids out-of-scope handoffs,
  restricts conflicting_sources to contradictions about the customer's specific
  question, and reuses search_products price/availability instead of repeating
  get_price/check_stock. check_stock is reserved for explicit product availability
  or quantity questions, with another call after search only for missing quantity.

R4–R6 edits remain intact. R7 touches .env.example, README.md,
app/core/config.py, app/integrations/local_commerce.py, app/services/chat.py,
app/services/model_gateway.py, app/agents/guardrails.py, app/agents/instructions.py,
tests/agent/test_live_model_round_three.py (prompt version only),
tests/agent/test_live_model_round_four.py and this document.
No real provider calls, Git staging/commit/stash/reset/checkout or container stops
were used. The fake model is unchanged in R7; existing demo regression tests
verify all five generated conversations and their repeatability in temporary
directories. No committed demo regeneration was needed.

These are bounded Portuguese lexical rules, not general grammatical entailment.
Suffix variants can be ambiguous; the originals remain valid search forms.
Prompt compliance and real-model timing still require the coordinator's live rerun.

All pytest/evaluation commands used `APP_ENV=test LLM_PROVIDER=fake
EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0`, with disposable databases and
existing containers left running. R7 adds 51 offline regression cases.
The first focused run caught a short-word suffix omission (pães/pão) and an
incorrect expected Yamaha count in the new test; both were corrected before
the successful focused run (174 tests) and final validation below.

| Command | Measured result |
|---|---|
| `uv run ruff check .` | all checks passed |
| `uv run ruff format --check .` | 165 files already formatted |
| `uv run mypy app` | no issues in 64 source files |
| `uv run pytest -q` — final run 1 | 850 passed, 124 subtests passed in 39.89s; no skips, warnings or xfails |
| `uv run pytest -q` — final run 2 | 850 passed, 124 subtests passed in 39.51s; no skips, warnings or xfails |
| `uv run python -m scripts.eval_rag --json-out /tmp/r7-eval.json` | all hard gates PASS: tenant leakage 0, privacy violations 0/23, harness errors 0 |
| Evaluation soft metrics | unchanged from R6: source_hit 12/12, citation_coverage 12/12, no_answer_safety 2/2, groundedness 19/19, tool_use 4/4, handoff_reason 2/2; all 100% |
| Demo verification | all five test-generated conversations pass behavior and repeatability checks; no fake model edits or committed demo regeneration |
| `git diff --check` | clean |

### 4.9 LangChain client ownership and stock follow-ups (R8, 2026-11-01)

- **J1:** the first live LangChain run (`openai:gpt-5-mini`) answered the first
  question in 11 seconds, then every later question failed in 0.0–0.1 seconds.
  L1 / PR #1 closed OpenAI roots at the end of each turn, closing HTTP clients
  cached process-wide by langchain-openai. The factory now supplies dedicated
  synchronous/asynchronous clients with MODEL_TIMEOUT_SECONDS, using SDK default
  client classes for compatibility with OpenAI 3 / httpx2 and earlier httpx SDKs.
  Private factory model types explicitly mark ownership; borrowed models are
  never closed based merely on their provider name. Close is repeatable and
  attempts synchronous cleanup even if asynchronous cleanup fails.
- Installed ChatAnthropic also caches shared HTTP clients and exposes no public
  HTTP-client injection fields, so its adapter close is a no-op. ChatOllama
  constructs fresh clients per instance; only factory-owned instances are closed.
  OpenAIEmbeddings/OllamaEmbeddings were reviewed: neither the embedding adapter
  nor its callers close their HTTP clients, so no embedding lifecycle change was
  needed for this defect.
- **J2:** commerce-privacy-v7 explicitly requires check_stock or search_products
  in THIS turn when a customer asks availability of a previously mentioned
  product ("E tem em estoque?" — “And is it in stock?”). History identifies the product but cannot supply
  stock evidence; quantities require check_stock. R7 guidance reusing prices and
  availability already obtained within the same turn remains intact.

The two new real-ChatOpenAI regression cases were run before the production fix:
**2 failed, 37 deselected in 1.14s**, both on the second request with
`RuntimeError: Cannot send a request, as the client has been closed.` They cover
consecutive create/request/close cycles and two overlapping model instances.
After the fix, both succeed and each MockTransport receives exactly two requests;
tests also verify separate clients, timeout configuration, cleanup, borrowed
OpenAI clients, shared Anthropic clients, and independent Ollama cleanup.

The initial test-fixture attempt patched httpx while this installation uses
httpx2: two synthetic requests with the literal `offline-test` key reached the
provider and returned 401, before the fixture was corrected. This was reported
to and acknowledged by the coordinator; it is not lifecycle regression evidence
or successful live validation. The corrected fixture uses the SDK-compatible
MockTransport and blocks socket.connect/connect_ex, failing closed on networking.
No real key was supplied by these tests. All subsequent test/evaluation runs use
`APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0`.
The coordinator owns successful post-fix live OpenAI validation and its numbers;
Anthropic/Ollama remain unexecuted live. No Git staging/commit/stash/reset/checkout
or container stops were performed; all R4–R7 changes remain in place.

| Command | Measured result |
|---|---|
| `uv run ruff check .` | all checks passed |
| `uv run ruff format --check .` | 165 files already formatted |
| `uv run mypy app` | no issues in 64 source files |
| `uv run pytest -q tests/agent/test_langchain_model.py` | 42 passed in 1.98s |
| `uv run pytest -q` — final run 1 | 855 passed, 124 subtests passed in 39.11s; no skips, warnings or xfails |
| `uv run pytest -q` — final run 2 | 855 passed, 124 subtests passed in 38.30s; no skips, warnings or xfails |
| `uv run python -m scripts.eval_rag --json-out /tmp/r8-eval.json` | all hard gates PASS: tenant leakage 0, privacy violations 0/23, harness errors 0 |
| Evaluation soft metrics | source_hit 12/12, citation_coverage 12/12, no_answer_safety 2/2, groundedness 19/19, tool_use 4/4, handoff_reason 2/2; all 100% |
| `git diff --check` | clean |

### 4.10 Browser chat test interface (U1, 2026-11-01)

The vanilla static page at `/chat` uses the existing chat API and preserves its
conversation ID across turns. The composer, question chips, key and reset button
are disabled while a request is pending. A key change starts a new conversation;
the key is stored only in sessionStorage. Responses and source metadata use DOM
text nodes, with a strict CSP on the page and its two assets. The routes share the
`expose_api_docs` gate; production has none of them. `/docs` keeps its existing
headers and scripts. No frontend dependencies or build steps were introduced.

All Python tests ran with `APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash
RUN_OPENAI_LIVE_TESTS=0`. The initial full run found that the existing documentation
link checker treats a fragment as part of a filename; the new docs-index link was
changed to the README with the section named beside it. The final run below passed.

| Command / check | Measured result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 169 files already formatted |
| `uv run mypy app` | No issues in 65 source files |
| `APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0 uv run pytest -q` | 859 passed, 125 subtests passed in 38.94s; no skips, warnings or xfails |
| `node --check app/static/chat/app.js` | Exit 0 |
| Temporary dependency-free Node VM smoke with a simulated DOM | Passed session storage, missing-key handling, single-flight lock, literal markup rendering, source/handoff details, both latency paths, conversation continuity/reset and 401/429/503/network recovery |
| `docker build --target runtime -t emporio-platform:local .` | Exit 0 |
| `docker run --rm emporio-platform:local ls app/static/chat` | `app.js`, `index.html`, `style.css` |
| Curl against isolated fake/hash API on port 8047 | `/chat`: 200, text/html, CSP present; `/chat/app.js`: 200, text/javascript; `/chat/style.css`: 200, text/css |
| Curl POST using the isolated seed's chat-scoped key | Takamine GD20: R$ 2.199,00; grounded true; search_products; 91 ms; second turn also 200 and retained the same conversation ID |
| `git diff --check` | Clean |

The isolated `u1-chat-ui` Compose project ran migration, seed and API against its
own Postgres and Redis, then was removed with its volumes; the temporary credential
file was deleted. Existing containers were not stopped. No real model calls or
Git staging/commit/stash/reset/checkout were used.

Limitations: the browser automation host was unavailable, so no visual browser
inspection was completed; Node's simulated DOM is not a browser rendering test.
The deterministic fake returned a generic catalog list for the stock follow-up,
so that request proves conversation-ID continuity, not real-model context quality.
The interface is local evaluation tooling without end-user authentication, history
reload, streaming or cross-tab serialization. Reset clears the local view and ID,
not server history; the API does not expose the active provider mode.

## 5. How to reproduce the evidence

```bash
docker compose up -d db redis
uv run ruff check . && uv run mypy app
uv run pytest -q                               # unit + integration (throw-away DBs)
uv run python -m scripts.eval_rag             # deterministic evaluation, exit 1 on a hard-gate failure
```
