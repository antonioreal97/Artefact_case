# ADR 006 LangChain as the provider-neutral model layer

- Status: Accepted for the `test-rag` branch
- Date: 2026-11-01
- Owner decision: LangChain only at the chat/embedding adapter boundary (L1 / D-22)

## Context

[ADR 001](001-hybrid-provider-neutral-architecture.md) proposed provider independence.
The platform in [ADR 005](005-multi-tenant-platform-branch.md) implemented native OpenAI
and deterministic fake/hash providers. Provider independence should not require replacing
the agent loop, commerce tools, guardrails, vault or hybrid retrieval.
[SECURITY.md](../../SECURITY.md) and [ADR 002](002-no-pii-external-egress.md) remain normative;
existing pseudonymization and rule-based detection limitations remain unresolved.

## Decision

Use `langchain-core` and the OpenAI, Anthropic and Ollama integration packages only as
model adapters. `LangChainModel` implements the Agents SDK `Model` interface and translates
messages, local FunctionTool schemas, tool results, assistant text, call IDs and usage.
The Agents SDK retains the loop; `PrivacyGuardedModel` wraps every selected model and
checks each outbound payload before invocation. Exceptions and `MODEL_TIMEOUT_SECONDS`
continue through ChatService's existing safe fallback/handoff path. No automatic provider
rerouting is introduced. No hosted tools, hosted state or raw customer records are allowed.

The initial proposal used `langchain.init_chat_model` / `init_embeddings`. Dependency
inspection showed that the top-level `langchain` package pulls in LangGraph. The coordinator
explicitly revised that requirement: small factories parse `provider:model`, mapping chat
to `ChatOpenAI`, `ChatAnthropic`, `ChatOllama`, and embeddings to `OpenAIEmbeddings` or
`OllamaEmbeddings`. Unknown providers and Anthropic embeddings raise configuration errors.
There is no top-level `langchain`, LangGraph or `langchain-postgres` dependency.
`langsmith` is an unavoidable, accepted transitive dependency of `langchain-core`; the
application does not import it directly or attach a tracer/callback. Startup rejects
truthy `LANGSMITH_TRACING` or `LANGCHAIN_TRACING_V2` in every environment, including `.env`.

OpenAI through LangChain uses Chat Completions, `store=False`, and no retries at the model
adapter layer. Provider HTTP timeouts are bounded. Native `openai` and deterministic
`fake` / `hash` paths retain their existing behavior. The global `LANGCHAIN_CHAT_MODEL`
selects the LangChain model; native per-tenant `chat_model` overrides do not apply there.
Temperature, maximum tokens and supported tool choices are translated; unsupported choices
fail explicitly. Parallel tool responses are rejected when the SDK disables them.
Streaming is explicitly unsupported because ChatService uses non-streaming `Runner.run`.
SDK structured output and SDK handoff definitions are outside this adapter's supported
surface; the existing local human-handoff tool continues unchanged.

Embeddings use async document/query methods, public-payload inspection, document batching,
timeouts and vector validation. The first wrong dimension raises a clear error, with no
padding/truncation. PostgreSQL's vector column remains fixed at **1536 dimensions**; a model
with other dimensions requires a new migration and reindexing, outside this task. Changing
embedding models also requires reindexing even when the dimensions agree. Query caching,
hybrid pgvector + Portuguese full-text search, RRF and tenant filtering stay unchanged.

## Alternatives rejected

- Full LangGraph rewrite: changes orchestration and privacy-sensitive behavior without
  advancing the limited adapter requirement.
- `langchain-postgres` retrieval: would replace the established hybrid SQL/RRF and tenant
  isolation contracts unnecessarily.
- LiteLLM: another viable provider abstraction, but the owner selected LangChain's model
  interfaces and integrations; introducing a second abstraction adds no benefit here.
- Top-level `langchain` factory package: convenient factories do not justify the unwanted
  LangGraph dependency; explicit factories keep the supported provider list reviewable.

## Consequences and validation limits

Provider choice no longer changes the privacy rules. It does not prove the scanner finds
all PII or resolve A-3/O-9. Retention, training terms, regions and contractual controls must
be checked separately for every selected provider; disabling storage/tracing is not ZDR.
New adapter dependencies increase runtime size; exact locked versions and measured image
sizes are recorded in [development process](../development-process.md#44-langchain-adapter-validation-l1-2026-11-01).

Offline tests cover translation, a complete ChatService tool turn, blocked PII with zero
model calls, absent callbacks, tracing configuration, failures/timeouts and embedding
mismatches. **No live OpenAI, Anthropic or Ollama validation was executed.** No comparative
quality, cost, latency or real embedding-quality claim follows from deterministic tests.
