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

## 2. How the work was organised

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
| D-19 | 2026-11-01 | Disclose Claude Code, OpenAI Codex and their workflow in the root README, with explicit owner-confirmation placeholders where the tool record is missing | The case requires naming coding assistants and explaining their use; owner authorship of commits does not describe who generated the code | Case p.2; README “Uso de assistentes de código” |
| D-20 | 2026-11-01 | Present multi-tenancy, generic uploads and WhatsApp as optional platform experiments; keep one seed-tenant/API walkthrough instead of rewriting before submission | R3 A-6 finds the breadth disproportionate to demonstrated case needs; clear scope and evidence have more value than a late rewrite | ADR 005; R3 §6; README |

## 4. Open items and known limitations

| ID | Item | Status |
|---|---|---|
| O-1 | "No answer" detection: retrieval signalled sufficiency as "returned any chunk", which together with D-07 meant an off-topic question (case `noans-parking`) got an answer instead of a handoff | fixed in E3 (D-18) |
| O-2 | Price guardrail rejected R$ amounts quoted from cited policy chunks (cases about Pix discount, minimum installment, free shipping, SEDEX time) | fixed in E2 (amounts must match this turn's tool prices or the cited chunks) |
| O-3 | "cobrado duas vezes" (double charge) was not classified as a sensitive topic | fixed in E2 (part of M-4) |
| O-4 | Live evaluation (real model and embeddings) not run yet: no API key in this environment | open |
| O-5 | Adversarial findings FINDING-1…6 (see 4.1) | all fixed: FINDING-3/6 in F3, FINDING-1/2/4/5 in E3; no strict xfail left |
| O-6 | Case `rag-mouthpiece`: the answer missed the expected hygiene term. Cause: the right chunk (section 4.4) had cosine similarity 0.096 with hash embeddings, below the lexical-hit floor | fixed in E3 (D-18) |
| O-7 | Older lint debt outside the fix scopes | fixed at integration: `ruff check` and `ruff format --check` clean on the whole repo; `ToolResult` moved to PEP 695 generic syntax |
| O-8 | Intermittent test `test_embedding_failure_is_safe`: about 4% of random UUID hex strings match the egress phone pattern, so the turn is blocked as `privacy_guard`. It fails safe, but it is a false positive of the egress scanner | open |
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

## 5. How to reproduce the evidence

```bash
docker compose up -d db redis
uv run ruff check . && uv run mypy app
uv run pytest -q                               # unit + integration (throw-away DBs)
uv run python -m scripts.eval_rag             # deterministic evaluation, exit 1 on a hard-gate failure
```
