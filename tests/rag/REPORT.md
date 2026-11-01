# Task C RAG verification

Implemented six RAG modules and the ingestion job service, with four test modules.
Frozen contracts and other owners' files were not edited; no dependencies were added.

## Validation

- `uv run pytest tests/rag -q`: **31 passed**, including live local PostgreSQL/pgvector integration tests; no skips and no OpenAI network calls.
- `uv run ruff check app/rag/{extraction,chunking,embeddings,ingestion,retrieval,citations}.py app/services/ingestion_jobs.py tests/rag`: **all checks passed**.
- `uv run ruff format app/rag/{extraction,chunking,embeddings,ingestion,retrieval,citations}.py app/services/ingestion_jobs.py tests/rag`: **12 files unchanged** on final run.
- `uv run mypy app/rag/{extraction,chunking,embeddings,ingestion,retrieval,citations}.py app/services/ingestion_jobs.py`: **no issues in 7 source files**.

Coverage includes PDF/DOCX fixture generation and real PDF pages, Markdown/TXT/raw text,
malformed formats/magic/zip bombs/encrypted PDFs/size limits, Unicode and token windows,
embedding retries/cache failure, version deduplication and reprocessing, safe failure status,
PII redaction before embedding, injection flags, tenant isolation including deliberately
inconsistent tenant/document rows, filters/status/current version selection, FTS half-floor,
near-identical content deduplication, citation labels and inline/queue job dispatch.

## Real policy PDF retrieval sanity

Source: `Desafio Técnico AI Engineer 2026/data/políticas_da_loja.pdf` (8 pages).
Provider: deterministic HashEmbeddingProvider, 1536 dimensions; default relevance floor 0.30.
The integration test ingests the real PDF into its throw-away PostgreSQL database and runs
these questions through the production hybrid retrieval function.

| PT-BR question | Top section | Page start | Cosine similarity |
|---|---|---:|---:|
| Quais são as formas de pagamento? | 3. Formas de Pagamento | 3 | 0.442 |
| Quais são as regras de parcelamento? | 3.1 Regras de Parcelamento | 3 | 0.456 |
| Como funciona a política de trocas e devoluções? | 4. Política de Trocas e Devoluções | 4 | 0.504 |
| Qual é o prazo de entrega? | 5.2 Entregas para Outras Cidades | 4 | 0.234 |
| Como funciona a garantia dos instrumentos? | 8. Garantia | 7 | 0.455 |

All five returned `sufficient=True`. The delivery hit qualifies via Portuguese FTS with
cosine >= half the default threshold (0.15). Its section starts on page 4 and continues
onto page 5; citations intentionally report `page_start`.

## Decisions and limits

- Coordinator clarified citation ordering: preserve first occurrence of each chunk ID in
  caller order; never re-sort accumulated results across multiple searches. Implemented
  and regression-tested. No outstanding contract questions.
- Public merchant contacts use case/whitespace-insensitive allowlist matching and remain
  in stored/retrieved chunks. Embedding input replaces them with `[PUBLIC_CONTACT]` so
  its generic final egress guard does not need a tenant-specific bypass.
- Customer data is redacted before splitting and scanned again before embedding. Local
  tenant customer names/phones/emails and order identifiers/tracking/notes supplement
  the shared scanner; contextual name/address/CPF/delivery fields are also removed.
  Detection remains deterministic and heuristic: arbitrary unknown names without any
  contextual label cannot be reliably identified without a separate entity detector.
- Error logs contain the exception category only, never raw exception text or traceback.
- Hash vectors are a dev/test lexical baseline; production refuses them. Live OpenAI
  behavior was mocked, including batching/retry/dimensions/privacy rejection.
- FTS uses built-in Portuguese stemming; no unaccent extension was added.
- Versions retain a document's original format because source type/filename live on the
  frozen Document model. Same-document uploads with a different format are rejected.
- Cancellation marks ingestion failed and allows retry. Abrupt process termination can
  leave a processing status requiring operator recovery; there is no durable lease
  timestamp in the frozen version contract.
