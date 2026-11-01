# Architecture review: test-rag platform

## 1. Scope, snapshot and method

Snapshot: `407d846` (`git rev-parse --short HEAD`), reviewed on 2026-11-01. The working tree was clean at the start. This review changes only this report. It evaluates the implemented `test-rag` branch against the supplied AI Engineer case; ADR 001-004 describe an alternative design, not a second completed implementation benchmarked here.

Files read: the three-page case PDF; all pages of `data/políticas_da_loja.pdf`; the six CSV exports; `docs/README.md`, `docs/development-process.md`, ADR 001-005, `docs/platform/contracts.md`, `SECURITY.md`, the security/adversarial/evaluation reports; application API, services, agents, tools, integrations, models and RAG modules; `src/emporio_agent/privacy.py`; seed, import and evaluation scripts; migration and container configuration; test fixtures and representative agent, API, RAG, infrastructure, evaluation and adversarial tests. PDF text was extracted with `pypdf`; CSV schemas, counts and contents were inspected locally.

Commands run and results:

| Command | Observed result |
|---|---|
| `git rev-parse --short HEAD`; `git status --short`; `git log -8 --oneline` | `407d846`; initially clean; incremental implementation, review, fixes and documentation commits |
| `rg --files`, `rg -n`, `cat`, `sed`, `nl -ba`, `wc -l` | Source and evidence inspection; `chat.py` 559 lines, `guardrails.py` 373 lines |
| `cat README.md`; `git ls-files '*README*' '*example*' '*conversation*' '*.png' '*.jpg' '.github/*'` | Root README absent; only `docs/README.md` is a README; no delivered conversation-example files found |
| `APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider` | **572 passed, 114 subtests passed in 28.65s**, no skips or xfails reported |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/mypy app --cache-dir=/dev/null` | **Success: no issues found in 63 source files** |
| `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m scripts.eval_rag --json-out /dev/null` | All 23 cases completed; leakage 0, privacy violations 0, harness errors 0; all six reported soft metrics 100% |
| Inline Python probes of guardrails, redaction, fake routing and vault snapshots | Results reproduced in A-2 through A-5 |

The installed `.venv` executables avoided dependency/lockfile changes. Tests and evaluation use disposable databases. No paid API was called, no live provider behavior was measured, and no deployment or load test was performed.

## 2. Executive summary

- **Verdict: a substantial, well-tested platform experiment, but not yet a complete or convincingly validated case submission.** The required root README and 3-5 interaction examples are missing.
- The right core distinction is implemented: exact commerce facts come from typed local tools, policies from retrieval, and identity/restricted-value assembly stay in trusted code. One agent is appropriate for seven tools and one business domain.
- The platform expansion is owner-authorized in ADR 005, but the case itself supplies only 191 CSV rows and an eight-page manual. There is no measured retrieval or concurrency need that establishes superiority over the smaller ADR 003/004 design.
- Passing tests provide strong evidence for the exercised isolation, failure and privacy paths. They do not establish general language understanding, actual model routing, policy entailment or production embedding quality. The documented absence of a live run is material.
- `grounded=true` is stronger than its implementation: a known citation label or a returned product name can validate an answer containing unsupported facts. Deterministic text guards are useful checks, but are not a general factual verifier.
- The vault/reveal boundary is thoughtfully designed, yet heuristic discovery of personal data cannot support the stated universal no-PII guarantee. Concurrent turns also lack a coherent state/transaction boundary.
- Keep the tools, privacy gateway, versioned source references and independent regression tests. Prioritize submission clarity and measured agent behavior before broader ingestion, channel or scaling features.

Scores assess this snapshot for the case, not a production certification: 1 = substantial gap; 3 = credible prototype with material limits; 5 = strong fit with appropriate evidence.

| Dimension | Score / 5 | Reason |
|---|---:|---|
| Requirement fit | 3 | Core paths exist, but explicit deliverables and real-model demonstration are incomplete. |
| Proportionality | 2 | Most platform infrastructure serves an expanded product hypothesis rather than the case's demonstrated needs. |
| Layering | 3 | Good typed integration boundaries; orchestration, persistence and validation remain tightly coupled. |
| Agent design | 3 | Sensible single-agent tool loop and safe fallbacks; free-text grounding and semantic guards are weak. |
| Privacy architecture | 3 | Strong local resolution and guarded egress mechanics, bounded by heuristic detection and policy inconsistencies. |
| RAG | 3 | Useful hybrid retrieval and source provenance; uncalibrated sufficiency and incomplete processing provenance. |
| Multi-tenancy / operations | 2 | Good tested tenant filters; concurrency, delivery durability and scale claims remain underdeveloped. |
| Quality strategy | 4 | Extensive real-DB and adversarial regressions with honest fake-mode limits; no live behavioral evidence. |
| Documentation | 2 | Detailed decision history, but no submission entry point and conflicting historical/current descriptions. |

## 3. Requirement-fit matrix

The case's explicit evaluation criteria are **logic, clarity and initiative**, with emphasis on reasoning and decisions rather than perfection (case PDF p.1). Python is its only mandatory technical choice (p.2). Neither a vector database, a specific agent framework, multi-tenancy, WhatsApp nor production deployment is required.

| Requirement / criterion | Status | Evidence and assessment |
|---|---|---|
| Python as principal language | Met | `pyproject.toml` specifies Python 3.12-3.13; API, tools, ingestion and agent are Python. |
| Text-message customer-service agent | Partially met / implemented | `app/api/routes/chat.py:43`, `app/services/chat.py:203`; offline end-to-end tests pass, external-model path exists but has not been exercised live. |
| Persona aligned with Empório da Música | Partially met | Seed persona/tone in `scripts/create_tenant.py:127`; versioned prompt in `app/agents/instructions.py:5`. Fake replies do not exercise persona instructions; naturalness is unmeasured. |
| Receive and answer using provided context | Partially met | Real CSV seed and policy ingestion; 23 deterministic eval cases pass. Claim-level support and ambiguous source handling are incomplete (A-2, A-8). |
| Know when to query operational data versus policies | Partially met | Seven typed SDK tool adapters in `app/agents/customer_service.py:38`; production model chooses tools. Tests establish tool behavior and scripted sequences, not reliable live selection (A-4). |
| Appropriate handling of out-of-store questions | Partially met | No-information handoff, sensitive-topic and injection checks; `noans-luthier`, `noans-parking`, accessories case. Broad off-topic/paraphrase behavior is not demonstrated by these few cases. |
| Use supplied data as useful; all data optional | Met | Seed validates 9 categories, 65 products, 25 promotions, 50 customers, 20 orders, 22 items. Tools query a local imported snapshot, not an actual store ERP. Order items need not be exposed to satisfy the case. |
| Justify framework, model/provider, interface, retrieval, prompt and data treatment in README | Partially met | ADRs and development log explain architecture and process; `.env.example` explains configuration. No root README; current OpenAI choice lacks comparative behavioral evidence. |
| Complete run instructions, including environment/provider/commands | Missing as required deliverable | Commands are scattered across module docstrings, `.env.example` and internal reports. `cat README.md` fails. |
| Known limitations and what to do with more time | Partially met | Development log §4 and prior reports contain limits, but not in the requested README. |
| Disclose coding assistant(s) and workflow | Partially met | Development log §2 describes orchestration, independent review and fixes. It does not clearly name the assistant/product used as required by case p.2. |
| 3-5 saved conversations, varied scenarios, at least one nontrivial | Missing | Tests/eval questions and isolated docstring examples are not 3-5 delivered conversations in `.md`, `.txt` or images. |
| Public GitHub repository with real progress history | Partially verified | Local incremental history exists. Public accessibility was not checked; local Git cannot prove visibility. |
| Submit repository link by email; freeze after deadline | Not verifiable here | Deadline/email not present in reviewed inputs; no external submission attempted. |
| Logic and justified assumptions | Good, with gaps | D-01 through D-18 show cause-and-effect decisions, including regressions; privacy and platform scope need clearer qualification. |
| Clarity | Partially met | Strong internal contracts and evidence, weakened by stale entry documentation and absent runnable walkthrough. |
| Initiative | Demonstrated | Independent review/adversarial regressions, fail-closed paths and reproducible evaluation exceed the minimum. Volume of infrastructure alone does not establish better agent quality. |

The manual further informs expected behavior: tool-verified prices/stock/deadlines (§7.1), polite accessory redirection, accurate promotion status (§7.3), empathetic complaint escalation, and management consultation on ambiguous policy (§10). It contains conflicting published contacts (cover/§1.2 versus §7) and general/specific installment wording (§3/§3.1). Preserving these sources is appropriate; silently resolving ambiguity is not. `scripts/create_tenant.py:35` explicitly preserves both contact variants, but the evaluation does not establish how the live agent explains the conflict. The case's suggested budgeted-guitar query is not itself a mandatory exact utterance, but is a useful missing demonstration.

Scope beyond the case: tenant/key management, generic PDF/DOCX/Markdown upload APIs, document-version administration, Redis-backed sessions and abuse controls, arq ingestion/retention jobs, WhatsApp transport, multi-process deployment machinery, and provider-comparison design documents. Some support a reusable platform; they should be presented as an optional experiment, not as implied requirements.

## 4. Findings, ordered by impact

`VERIFIED` means the mechanism or gap is established by the cited code/command; it does not mean an unrun production scenario was observed. `OPINION` identifies architectural judgment. Effort: S = localized change/documentation; M = several components plus validation; L = broader design and validation.

### A-1 — Required submission artifacts are absent

**High · VERIFIED · Effort S**

**Evidence:** Case p.2 requires a README with execution/setup, technical rationale, limitations and assistant disclosure, plus 3-5 interaction examples. `cat README.md` returned `No such file or directory`; tracked-file and Markdown/text inventories found no delivered conversation set. `docs/README.md:62` says only the privacy boundary is implemented.

**Why it matters:** This is a direct deliverable gap, not a preference for more documentation. A reviewer cannot reliably start from the repository landing page and see the agent solve the case. A large internal design pack does not replace the requested artifacts.

**Recommendation:** Add a short root README with one tested offline path, one explicitly optional real-model path, exact key/seed commands, a clear `test-rag` scope note, named AI-assistant disclosure, limits, and 3-5 saved conversations. Label fake conversations as deterministic; include budgeted catalog search, policy conditions, verified order lookup and out-of-scope handling.

### A-2 — Grounding validates evidence presence, not factual consistency

**High · VERIFIED · Effort M**

**Evidence:** `app/agents/guardrails.py:291` checks money, selected commercial expressions, citation-label membership and success/prompt wording. `answer_uses_tool_facts` at `:336` accepts a returned product name as grounding; it does not compare stock values. `app/services/chat.py:441` sets `grounded` from any selected citation or tool grounding. Inline probes returned:

```text
Tool result: check_stock(name='Yamaha C40', stock_quantity=0, available=False)
Answer: Yamaha C40: disponível em estoque, com 99 unidades.
output_violation -> None; answer_uses_tool_facts -> True

Document: Sábado 09:00 às 13:00. Domingo fechado.
Answer: A loja abre domingo às 23h. [S1]
labels={'S1'}; output_violation -> None
```

The probes exercise the real pure guards, not a live model. With a selected citation, the chat path performs no additional entailment check. Prices have stronger special-purpose checks, but dates, quantities, negation and policy conditions do not share that assurance.

**Why it matters:** Correct prices, stock, status and policy answers are the core case. A cited hallucination undermines that objective even when all tenant/privacy gates pass. This is the structural limitation remaining after earlier price fixes, not a claim that those fixes failed their regressions.

**Recommendation:** Prefer structured answer facts tied to tool/source IDs; render critical commerce values deterministically. Separate `has_evidence` from a claim of factual validation. For public policy text, assess individual claims and conditions against cited passages, using a validated local or approved model judge as a supplementary quality check, never as authorization. Add negative scripted-model cases for stock, dates, unsupported policy claims and misattribution.

### A-3 — Heuristic PII discovery cannot establish the declared privacy guarantee

**High · VERIFIED · Effort M**

**Evidence:** `app/services/privacy.py:1` and development log O-9 acknowledge rule-based detection limits. The actual path tokenizes free text, tests residual cues, and sends remaining text/history to the guarded model (`app/services/chat.py:258`, `:289`, `:348`). A local probe of `redact_user_text`, `looks_restricted` and `scan_outbound` produced:

```text
input/output: 'ana paula silveira quer saber da garantia'
residual_restricted=False; egress_scan=PASS
```

The name is synthetic and absent from the supplied known-name set. The structural issue extends to contextual identifiers and identifying combinations without recognizable markers. Multiple scans with overlapping regex families are not independent classification guarantees. `SECURITY.md:87` requires local/deterministic processing for restricted turns; `:97` says random tokens; `:193` calls reversible pseudonym egress a high finding. ADR 002 rejects external reversible pseudonyms, while ADR 005 and `PrivacyVault.tokenize` deliberately use sequential, reversible conversation tokens externally.

**Why it matters:** The local vault is a sound minimization mechanism once a sensitive span is found. It cannot prove that everything left is public/non-identifying. The unchanged-invariant claim is stronger than the implementation and internally inconsistent with the approved token design. This finding concerns the privacy model, rather than reopening the fixed CPF/tracking/address regressions.

**Recommendation:** Reconcile ADR 002/005 and SECURITY explicitly; describe tokenization as pseudonymization with scoped linkability, not anonymization. For a strict no-raw-PII guarantee, construct allowlisted structured public/abstract inputs locally and keep ambiguous free text local or hand it off. A local entity detector can improve recall but does not prove absence; an external LLM must not inspect the raw text to decide whether sending it is safe. Keep per-conversation storage and current-turn/verified-lookup reveal restrictions.

### A-4 — Agent quality is unvalidated outside the deterministic test surrogate

**High · VERIFIED · Effort M**

**Evidence:** Development log O-4 records no live run. `app/services/fake_model.py:61` implements keyword routing and tool-result rendering, reusing `check_input` and declining suspicious passages itself. The actual prompt is not interpreted. Eval `run_case` starts a fresh conversation (`scripts/eval_rag.py:475`); source-hit accepts a matching page or section (`:342`), citation coverage needs only one label (`:497`), and groundedness checks expected substrings plus flags (`:505`). D-13 makes only privacy/leakage/harness errors hard gates, also in live mode. `tests/eval/test_eval_harness.py:33` asserts these gates, not the quality metrics.

A direct fake-router probe for `Quais opções de violões disponíveis custando até R$1000?` produced:

```json
{"tool":"search_products","arguments":{"query":"Quais opções violões disponíveis custando até R 1000"}}
```

It supplies neither `max_price` nor `in_stock_only`; the real backend requires all query terms to match. The offline demo therefore cannot demonstrate this representative request merely because the typed search tool supports the filters.

**Why it matters:** The harness is valuable pipeline evidence, accurately labeled. It does not validate the case's central model decisions, multilingual paraphrases, tone, multi-turn reference resolution or real embeddings. A fake designed to refuse injections also cannot measure an LLM's resistance. One hundred percent on these soft checks is not one hundred percent answer accuracy.

**Recommendation:** Keep fast deterministic tests and hard safety gates. Before claiming model quality, run a small approved real/local-model evaluation over frozen, separately held-out paraphrases, multi-turn flows and policy conflicts; record model/prompt versions, latency and usage. Add independent safety/correctness assertions for critical facts and enforce all relevant expectation fields (including absence of unwanted handoff). Do not call a paid API as part of this review. If live evidence remains unavailable, state that limitation prominently and present the system as an integration prototype.

### A-5 — Conversation state lacks serialization and retry identity

**High · VERIFIED · Effort M**

**Evidence:** `app/services/vault_store.py:42`/`:52` performs separate Redis GET and whole-state SET with no version/CAS. `app/services/privacy_vault.py:28` allocates counters from the loaded snapshot. `ChatService` reads history and saves the vault independently (`:253`, `:277`, `:304`, `:442`); there is no per-conversation turn lock or request-id deduplication. The request correlation ID is not an idempotency key. An inline probe using the documented equivalent in-memory store loaded two snapshots before either saved:

```text
snapshot A: tokenize EMAIL first@example.test -> [[EMAIL_1]]
snapshot B: tokenize EMAIL second@example.test -> [[EMAIL_1]]
save A; save B; reload.resolve('[[EMAIL_1]]') -> ('EMAIL', 'second@example.test')
```

The database also commits the user message before the model/tool loop (`chat.py:275`) and assistant/audits later (`:533`); independent tool sessions can commit a handoff in between. Existing single-turn handoff reuse does not make simultaneous turns idempotent.

**Why it matters:** Double submits, overlapping messages or retries can corrupt token/history meaning, duplicate actions or leave incomplete turns. This is a state-integrity failure relevant even at low traffic; the probe does not claim a demonstrated cross-tenant leak. Distributed runtime adds concurrency that a sequential CLI would avoid.

**Recommendation:** Serialize turns per conversation with a bounded lease/fencing or transactional sequence; use random unique token IDs plus versioned vault writes. Persist turn IDs, statuses and idempotency keys, and define recovery for interruption after user/tool commit. Test two concurrent turns and a retry after partial completion.

### A-6 — Platform breadth is disproportionate to the demonstrated case need

**Medium · OPINION · Effort S for scope/presentation; L for a rewrite**

**Evidence:** Case p.1-2 permits CLI/notebook/API/simple UI and makes persistence optional. ADR 003 identifies the 191-row/eight-page dataset and chooses SQLite/curated retrieval; ADR 004 chooses explicit orchestration. ADR 005 gives an owner request, rather than measured failures of those designs, as the platform rationale. Compose runs DB, Redis, migrations, API and worker plus optional seed; the migration defines 19 tables.

**Why it matters:** Initiative is rewarded, but explanation and agent correctness are the focus. Multi-tenancy, upload administration and a second external channel consume implementation/review effort while the submission walkthrough and live-agent evidence remain absent. The platform is justified as a separate product experiment, not shown necessary for this case.

**Recommendation:** Do not rewrite everything immediately before submission. Present one supported seed-tenant/API workflow and move generic uploads/WhatsApp/platform evolution behind an optional section. For a fresh minimal implementation, choose SQLite, versioned curated policy sections, one orchestrator, typed tools and local restricted handling; add FastAPI only for the preferred interface. Keep the Agents SDK if it demonstrably simplifies the bounded tool loop, but do not add specialists. Defer Redis/arq/pgvector until persistence, ingestion volume or retrieval measurements justify them. Compare lexical-only versus hybrid on held-out questions before calling hybrid necessary.

### A-7 — Document versions do not identify the embedding/processing generation

**Medium · VERIFIED · Effort M**

**Evidence:** `app/models/knowledge.py:46` and `:66` store raw-byte hash, chunks and vectors, but no embedding provider/model revision or extraction/chunking revision. Runtime provider is selected from settings (`app/rag/embeddings.py:189`); query retrieval does not filter embedding generation (`app/rag/retrieval.py:93`). Hash and OpenAI embeddings can both be 1536-dimensional. Seed skips completed identical documents (`scripts/seed.py:54`). Reprocessing deletes and commits existing chunks before embedding succeeds (`app/rag/ingestion.py:314`); citation chunk references use `ON DELETE SET NULL` (`app/models/conversation.py:50`).

**Why it matters:** Switching an offline installation to real embeddings can silently compare incompatible vector spaces unless everything is reprocessed. Reprocessing an active version creates an availability gap and can leave it without chunks on failure. A raw document version alone cannot reproduce the processed evidence behind an old answer, although the saved quote preserves partial evidence.

**Recommendation:** Persist an immutable processing-generation fingerprint (source version, extractor/chunker configuration, embedding model/dimensions). Reject mismatched query vectors. Build new chunks alongside active ones and atomically swap the serving generation after success; retain references required by saved citations. Document offline-to-live reindexing even before implementing richer lifecycle machinery.

### A-8 — RAG relevance is treated as answer sufficiency; generic-document support is limited

**Medium · VERIFIED · Effort M**

**Evidence:** D-18 and `app/rag/retrieval.py:160` declare sufficiency when one chunk clears cosine threshold or at least half the distinct query lexemes with a reduced cosine floor. `search_knowledge` then serves the returned set if that single boolean is true (`app/agents/customer_service.py:124`). D-17 filters regex-flagged chunks (`retrieval.py:102`; `ingestion.py:53`), but documents otherwise become active automatically. PDF extraction uses numbered-heading detection and a literal Empório header removal (`app/rag/extraction.py:87`, `:129`); image-only documents have no OCR path. Token windows preserve section/page provenance but not an explicit table/rule/exception relationship (`app/rag/chunking.py:28`).

**Why it matters:** The D-18 fix correctly avoids “any hit means enough,” but overlapping words do not establish that all parts of a question are answerable, that conditions were preserved, or that sources agree. Thresholds tuned against lexical hash embeddings are not calibrated for production embeddings. A benign-looking poisoned policy or contradictory merchant upload is not solved by suspicious-instruction regexes. The seed's clean numbered PDF is a favorable corpus.

**Recommendation:** Keep RRF as a reasonable ranking method and retain SQL tenant/current-version filters. Rename/calibrate the sufficiency signal as a retrieval-confidence heuristic, add compound/no-answer/contradiction cases and measure precision as well as hit rate. Require source approval/authority metadata for commercial policies; preserve parent sections or explicit rule/exception units where needed. Document unsupported scans and complex layouts rather than adding OCR speculatively. A public-text answerability/entailment model may help after retrieval, but must not replace deterministic authorization or tenant filters.

### A-9 — The application layer is a large coordinator with leaky lower-layer boundaries

**Medium · VERIFIED · Effort M**

**Evidence:** `ChatService` spans 559 lines and `handle_message` alone spans `app/services/chat.py:203-559`: CRM scanning, persistence, history, vault, model lifecycle, guard decisions, citations, handoffs, reveal, audit and reply construction. It instantiates `LocalCommerceBackend` directly at `:312`. The agent adapter embeds retrieval orchestration and audit logic (`app/agents/customer_service.py:103`). Tools use global `get_settings()` and `run_tool` lives in catalog specifically because of workstream ownership (`app/tools/catalog.py:1`). Guardrails recover typed facts from mutable `dict[str, object]` tool summaries (`app/agents/guardrails.py:188`).

**Why it matters:** Service-level orchestration belongs here, but persistence, policy and rendering changes now interact in one long conditional flow. The nominal API → service → agent → tools → integration → data direction is mostly respected; SDK types are kept out of commerce tools. However, ToolContext carries ORM session factories and mutable audit state, and policy validation depends on SDK adapters enriching audit dictionaries. Frozen contracts helped integration but also fossilized missing concerns: tenant-neutral embedding protocol/cache, document-level source format and independent commits.

**Recommendation:** Extract a small turn state/result model, typed evidence ledger, answer validator and local renderer. Inject commerce/embedding/configuration interfaces into the service and tools. Keep transaction ownership explicit. Move shared tool execution out of catalog. Amend contracts when runtime requirements demonstrate a missing boundary; exclusive file ownership is a development technique, not an architectural reason to retain misplaced code. Avoid introducing a general workflow framework merely to split this file.

### A-10 — Per-request work grows with complete tenant datasets

**Medium · VERIFIED · Effort M**

**Evidence:** Every chat loads all customer names, emails and phones (`app/services/chat.py:230`); explicit handoff redaction also loads all customer contacts and order IDs (`app/api/routes/handoffs.py:61`). `_known_pattern` caches 32 compiled value-set patterns, but `_known_spans` sorts/builds lookup inputs and scans current/history text (`app/services/privacy.py:164`, `:187`). Catalog search loads all tenant products/categories/active promotions and filters/sorts in Python (`app/integrations/local_commerce.py:132`). Ingestion also loads tenant customer/order values (`app/rag/ingestion.py:235`).

**Why it matters:** Fine for 50 customers and 65 products, but this is not yet a scalable tenant platform. The current combined regex is an improvement over the earlier per-name scan, so the old review's exact scan-count estimate should not be repeated as a current benchmark. No load test establishes the actual break point. Large tenants can consume shared memory/CPU despite SQL tenant isolation.

**Recommendation:** For submission, state seed-scale limits. Before larger tenants, measure query count, memory and p95 latency; push catalog filters/limits into SQL and cache an explicitly versioned tenant matcher or use bounded candidate lookup. Avoid collecting the entire CRM on every turn merely to make unrestricted model free text safe; the structured boundary in A-3 can remove much of this cost.

### A-11 — Tenant isolation is an application convention; end-user authorization is a separate gap

**Medium · VERIFIED · Effort M**

**Evidence:** `TenantScoped` requires SQL filters by convention (`app/models/base.py:23`); migration 0001 has tenant columns/indexes but no row-level security policies or composite parent/tenant foreign keys. Tests thoroughly cover forged tenant IDs and colliding external IDs. The HTTP chat route always passes `external_user_ref=None` (`app/api/routes/chat.py:85`), while conversation ownership checking is conditional on a supplied/stored ref (`app/services/chat.py:191`). WhatsApp supplies an HMAC user ref. API-key scopes authorize a tenant service, not an individual customer.

**Why it matters:** Current tested queries enforce tenant isolation well. This does not make future queries or inconsistent cross-tenant relationships safe by construction, nor bind shared widget users to their conversations. Current reveal checks mitigate prior-token disclosure, but same-tenant unauthorized conversation mutation remains structurally possible if an ID is obtained. This is not evidence of a new cross-tenant bypass.

**Recommendation:** Define whether API keys are trusted server credentials or browser-distributed credentials. Add signed end-user/session binding for the latter. For platform evolution, enforce tenant-consistent relationships and consider RLS with appropriately scoped DB roles; test with those roles rather than only the development owner. Keep application filters and negative cross-tenant tests even with database enforcement.

### A-12 — Queue and channel boundaries do not provide durable completion semantics

**Medium · VERIFIED · Effort M**

**Evidence:** Document creation commits before enqueue (`app/rag/ingestion.py:231`); enqueue failure is logged/swallowed and left for manual reprocess (`app/api/routes/knowledge.py:158`). `WorkerSettings` has retention cron but no pending-job reconciliation (`app/services/ingestion_jobs.py:65`). Compose Redis has no configured durable volume. WhatsApp is inline and acknowledges signed requests with 200 after per-message failures (`app/api/webhooks/whatsapp.py:174`). `MetaWhatsAppClient.send_text` returns `False` on delivery failure (`app/integrations/meta_whatsapp.py:35`), but `_process` still returns `True` and `_handle_message` marks dedupe success (`whatsapp.py:264`, `:313`). Credentials remain global, as the development log already acknowledges.

**Why it matters:** Redis plays four distinct roles: disposable embedding cache, rate limits, privacy vault and ingestion queue. Cache failure can safely degrade; vault/lookup failure must close; losing queued work or pretending transport delivery completed requires recovery. Logging a failed send is not delivery. The added channel is not required by the case and widens the operational claim significantly.

**Recommendation:** Keep WhatsApp explicitly experimental for submission. For production use, persist an inbox/turn/outbox with idempotency, acknowledge only durable acceptance, retry failed sends separately, and reconcile pending ingestion from the DB. Give queue durability and vault expiry separate policies; move channel credentials to tenant-scoped secret references. Do not assume removing a dedupe key causes a retry after a successful HTTP acknowledgement.

### A-13 — Retention policy and timestamps do not form a complete lifecycle

**Medium · VERIFIED · Effort M**

**Evidence:** Purge uses `Conversation.updated_at` (`app/services/retention.py:61`), whose ORM `onupdate` fires on conversation-row updates (`app/models/conversation.py:21`). Ordinary turns insert messages without touching that timestamp; only handoff status is changed in the chat path. Document raw bytes are cleared for superseded versions without a cutoff condition (`retention.py:78`), while current or never-superseded failed uploads are retained. Redis vault expiry is a sliding 24 hours on save (`app/services/vault_store.py:16`, `:57`). `tests/infra/test_retention.py:61` directly stamps timestamps, so does not establish activity-driven retention behavior.

**Why it matters:** A functioning scheduled purge is a strength, but a 90-day conversation inactivity policy can delete recently active conversations because activity is not recorded consistently. Raw-document and conversation lifetimes differ from the broad “bounded TTL” promise. Tenant/customer deletion and retention of backups are not addressed by the cron alone; these are evolution requirements, not case mandates.

**Recommendation:** Define message-age versus conversation-inactivity retention, update activity atomically, and test recent messages in old conversations. Specify current/failed/superseded document lifetimes and reprocessing behavior after raw bytes are cleared. Document Redis loss/expiry and deletion propagation rather than presenting one global retention setting as comprehensive coverage.

### A-14 — Current evidence, historical documents and operational observability are not aligned

**Low · VERIFIED · Effort S-M**

**Evidence:** `docs/README.md:3` promises provider-neutral behavior, and `:62` says orchestration/adapters remain planned; current settings allow OpenAI/fake only (`app/core/config.py:33`). `evals/REPORT.md` preserves the earlier 50% citation / 63% groundedness run, while development log §4.2 and this run show 100%; historical security findings describe previous code. The log partly reconciles this, but primary reports lack a clear current-status banner. `PROMPT_VERSION` is recorded in message metadata (`chat.py:482`), but effective tenant persona/model/embedding configuration and token usage are not. `MODEL_ERRORS` increments at the API for escaping exceptions, while ordinary model timeout/errors are converted inside ChatService into `tool_failure` handoffs (`chat.py:365`; `app/api/routes/chat.py:89`). Metrics are per-process by design (`app/core/metrics.py:8`).

**Why it matters:** Reviewers must reconstruct which statements apply to this branch. Operators can see response latency and handoffs but cannot reliably separate model failure/cost from tool problems, or reproduce a response from the prompt-version string alone. This limits diagnosis without requiring unsafe prompt logging.

**Recommendation:** Make the root README and branch-specific index authoritative, mark earlier reports with snapshot/status links, and describe original-design ADRs as scoped alternatives. Record privacy-safe model/embedding IDs, effective configuration hash, usage counts and distinct error categories. Keep metrics aggregated and payloads out of logs; document single-process metrics assumptions. Add deployment backup/restore and alerting guidance only if claiming operational readiness.

## 5. Strengths worth keeping

- **Typed facts instead of generated SQL.** `CommerceBackend` binds tenant at construction and distinguishes restricted `OrderRecord` from public DTOs (`app/integrations/base.py`). Decimal promotion calculation, active/stock status handling, schema validation and referential checks in `scripts/commerce.py:116` are appropriate for these CSVs. Upserts are idempotent; they do not pretend absent rows were deleted from the upstream system.
- **One bounded agent with thin commerce adapters.** `app/agents/customer_service.py:155` registers seven tools with parallel tool calls disabled; `chat.py:345` bounds the model run. No evidence calls for specialist agents. Tools are independently testable outside the SDK.
- **Explicit local restricted-data handling.** `app/tools/orders.py:206` resolves tokens and verifies contact locally, returning an abstract view; `chat.py:462` reveals only current-turn or verified-lookup tokens. Unknown/expired/forged tokens and unavailable vaults have regression tests. This is a stronger design than simply instructing a model not to leak records.
- **Non-optional model gateway.** `app/services/model_gateway.py:36` rejects hosted state/request overrides and forces `store=False`; both response and streaming methods are guarded. Chat wraps injected providers too. Tracing is off by default; exceptions are handled without sending raw failure strings to users.
- **Tenant scope is consistently exercised.** `tests/adversarial/test_adv_isolation.py`, `test_adv_retrieval.py` and `test_adv_orders.py` verify HTTP-level negative cases, identical documents, colliding IDs and token boundaries. Tools take tenant context from authentication, not model arguments.
- **RAG mechanics are coherent.** `retrieval.py:93` applies tenant, active-document and current-version restrictions in SQL before ranking; RRF avoids adding incomparable raw lexical/cosine scores. Section-aware token windows, per-page provenance, stable per-answer citation labels and saved quotes are useful foundations. New document versions only advance the serving pointer when processing succeeds and are protected against an older job superseding a newer version (`ingestion.py:382`).
- **Safe error semantics improved through review.** Recoverable invalid/missing/unauthorized tool outcomes are distinguished from outages (`chat.py:384`); handoff success is checked; no-information and privacy fallbacks are explicit. A handoff here is a persisted request for human handling, not proof that a human was notified or responded.
- **Validation goes beyond mocks.** The passing suite exercises local Postgres, tenant isolation and migration CLI checks; `tests/infra/test_migrations.py:19` runs Alembic upgrade and schema diff. Adversarial scripted models test uncooperative output, which is more useful for guard testing than only cooperative fake output.
- **Independent review artifacts and honest gates.** D-13 distinguishes pipeline checks from language quality; D-14 keeps evaluation cases fixed; D-15 used strict xfails until fixes. The current run has no remaining xfails. The E2→E3 history exposes a guardrail regression and its correction rather than hiding it.
- **Practical container and failure hardening.** Non-root image, frozen dependency install, no customer exports baked into the runtime image, localhost-only Compose ports, bounded uploads, parser validation, DB timeouts, safe logging and a scheduled purge are useful engineering work (`Dockerfile`, `docker-compose.yml`, `app/rag/extraction.py`, `app/core/database.py`). These are development deployment controls, not evidence of tested production availability.

## 6. Prioritized recommendations

### Before submitting the case

1. **Complete the explicit deliverables (A-1).** Root README, named coding-assistant disclosure, one verified setup path and 3-5 realistic interaction artifacts. Clearly distinguish the seed snapshot from a real ERP and fake outputs from LLM outputs. Confirm repository visibility and deadline separately.
2. **Correct the assurance language and demonstrate the real behavior (A-2, A-3, A-4).** Do not equate a citation/returned name with factual correctness or tokenization with anonymous data. Prefer deterministic rendering of critical facts, add contradiction regressions, and disclose the privacy boundary's actual coverage. Run an approved live/local model evaluation if available; otherwise state the absence plainly.
3. **Choose a bounded submission scope (A-6).** Freeze feature expansion. Lead with the store agent and reasoning; mark reusable platform and WhatsApp as optional/experimental. A late full rewrite is lower value than a working walkthrough and honest evidence.
4. **Protect demo state and offline-to-live transitions (A-5, A-7).** Serialize concurrent conversation turns or explicitly restrict the demo to one active turn, and document reindexing when switching embedding providers. Avoid claiming parallel production readiness before those controls are verified.
5. **Align current documentation/evidence (A-14).** Keep historical review reports, but add snapshot/status navigation, current command results, unresolved issues and the platform-specific architecture. Explain the one-agent decision and how exact tools and policy retrieval differ.

### Later evolution, only if the platform is retained

1. Implement durable turn/ingestion/channel state and retry semantics, signed end-user identity, and tenant-consistent database relationships (A-5, A-11, A-12).
2. Introduce immutable processing generations, calibrated retrieval/no-answer evaluation, source approval and condition-preserving document representation (A-7, A-8). Add layout/OCR support only for an actual corpus need.
3. Refactor turn orchestration around typed evidence and explicit transaction ownership; remove whole-tenant scans after measuring scale (A-9, A-10).
4. Complete retention/deletion semantics and operational metrics, then test restore, dependency outages, queue recovery, concurrency and cost under realistic load (A-12 through A-14).

No recommendation requires more agents or a larger framework. The most valuable next evidence is that a reviewer can run the agent, ask realistic follow-ups, and see accurate, appropriately scoped answers with clearly stated limits.
