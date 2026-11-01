# Platform contracts and module ownership (orchestrator-owned)

These files are **frozen contracts**. Workers must not edit them; ask the orchestrator.

| Contract | File |
|---|---|
| Settings | `app/core/config.py` |
| DB engine/session/Base | `app/core/database.py` |
| ORM models (all tables) | `app/models/*.py` |
| Tool result envelope | `app/schemas/tools.py` |
| Commerce backend protocol + DTOs | `app/integrations/base.py` |
| RAG types/protocols | `app/rag/types.py` |
| Privacy vault | `app/services/privacy_vault.py` |
| Tool run context | `app/tools/context.py` |
| Dependencies | `pyproject.toml` (ask before adding deps) |
| Base test fixtures | `tests/conftest.py` |

## Function signatures each owner must provide

### Task C – RAG (`app/rag/*`, `app/services/ingestion_jobs.py`, `tests/rag/`)
- `app.rag.embeddings.get_embedding_provider(settings) -> EmbeddingProvider`
  (`OpenAIEmbeddingProvider`, deterministic `HashEmbeddingProvider` (dev/test only),
  optional Redis query-embedding cache wrapper).
- `app.rag.ingestion.create_document_version(session, *, tenant_id, title, data: bytes,
  filename: str | None, source_type: str, category: str | None = None,
  source_url: str | None = None, document_id: UUID | None = None) -> IngestionReceipt`
  — validates type/size, sha256 dedup, stores raw bytes as `pending`; commits.
- `app.rag.ingestion.process_document_version(session, *, tenant_id, version_id,
  embedder) -> None` — extract → normalize → chunk → embed → store chunks, set
  `completed`/`failed` (+ safe `error_message`), set `Document.current_version_id`.
- `app.rag.retrieval.retrieve(session, *, tenant_id, query, embedder,
  filters: RetrievalFilters | None = None, top_k: int | None = None,
  min_similarity: float | None = None) -> RetrievalResult`
- `app.rag.citations.build_citations(chunks: list[RetrievedChunk]) -> list[Citation]`
  and `format_sources_footer(citations) -> str`.
- `app.services.ingestion_jobs.enqueue_ingestion(tenant_id, version_id)` (arq on Redis,
  or inline when `INGESTION_MODE=inline`) + arq `WorkerSettings`.

### Task D – Commerce tools (`app/integrations/local_commerce.py`, `app/tools/{catalog,inventory,orders,handoff}.py`, `tests/tools/`)
- `LocalCommerceBackend(session_factory, tenant_id)` implementing `CommerceBackend`.
- Framework-free async tool functions taking `ctx: ToolContext` first, returning
  `ToolResult[...]`, appending a `ToolCallRecord` to `ctx.tool_calls`, enforcing
  `settings.tool_timeout_seconds`:
  `search_products`, `get_product_details`, `get_price`, `check_stock`,
  `lookup_order(ctx, order_token, contact_token)`, `request_human_handoff(ctx, reason, summary)`.

### Task E – Agent (`app/agents/*`, `app/services/{chat,privacy,model_gateway,vault_store}.py`, `tests/agent/`)
- `app.services.chat.ChatService.handle_message(session, *, tenant_id, conversation_id |
  None, text, channel="api", external_user_ref=None, correlation_id) -> ChatReply`
  (schema in `app/schemas/chat.py`, owned by E).

### Task F – API (`app/api/**`, `app/main.py`, `app/core/{security,logging,metrics}.py`, `app/schemas/api.py`, `tests/api/`)

### Task B – Infra (`alembic/**`, `alembic.ini`, `scripts/**`, `Dockerfile`, compose app/worker/migrate services, `.env.example`, `tests/infra/`)
