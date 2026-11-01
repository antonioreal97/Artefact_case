# Task B verification report

Implemented the async Alembic environment and reviewed static initial migration for all
19 ORM tables, including the vector extension, 1536-dimensional vectors, HNSW cosine
index, generated Portuguese search vector and GIN index. The migration does not import
live models; future model changes require another revision.

Seed commands are `uv run python -m scripts.create_tenant` and
`uv run python -m scripts.seed`; both accept `--api-key-from-env SEED_API_KEY`.
Copy `.env.example` to the git-ignored `.env` before Compose runtime commands, set a
private pepper and generate a seed key using the command documented in that example.
The Compose seed is opt-in: `docker compose --profile seed run --rm seed`.
Environment-supplied keys are never printed; newly generated keys are printed once
immediately after the credential transaction commits, before fallible PDF ingestion.
Existing keys are not silently rotated or revived.

## Files changed

- `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`,
  `alembic/versions/0001_platform.py`
- `scripts/__init__.py`, `scripts/create_tenant.py`, `scripts/commerce.py`, `scripts/seed.py`
- `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`
- `tests/infra/test_migrations.py`, `tests/infra/test_seed.py`, `tests/infra/REPORT.md`

## Commands and observed results

| Command | Result |
|---|---|
| `uv run ruff check scripts alembic tests/infra` | Exit 0, all checks passed |
| `uv run ruff check alembic/versions/0001_platform.py` | Exit 0, all checks passed (explicit because project excludes migration versions during traversal) |
| `uv run ruff format scripts alembic tests/infra` | Exit 0, 7 files unchanged after formatting |
| `uv run ruff format alembic/versions/0001_platform.py` | Exit 0, 1 file unchanged |
| `uv run mypy scripts alembic tests/infra` | Exit 0, no issues in 8 source files, strict project settings |
| `uv run pytest tests/infra -q` | Exit 0, 26 passed in 2.75s, no skips |
| `docker compose build` | Exit 0; repeated successfully after implementation changes |
| `docker compose up -d migrate` | Exit 1 before starting services: `.env` does not exist |

The migration integration test runs the actual `python -m alembic upgrade head` and
`python -m alembic check` CLI in a fresh random database, asserting exit 0 and
`No new upgrade operations detected`. It additionally checks the complete table set,
HNSW operator class, GIN index and generated Portuguese expression. Only disposable
test databases are dropped; the Compose database and volume were not dropped.

The seed integration test runs twice with the real six CSVs and policy PDF and hash
embeddings, checking stable row IDs and exact counts: 9 categories, 65 products,
25 promotions, 50 customers, 20 orders, 22 order items. There remains exactly one tenant
configuration, credential, WhatsApp mapping, document and document version, and the
version is completed with persisted chunks. Further tests cover updates scoped to one
of two tenants, supplied-key hashing/reuse/rotation refusal, six duplicate-key cases,
orphan references, enums, JSON objects and nonfinite JSON numbers, exact Decimal money,
positive quantities and promotion boolean 0/1 validation.

Additional successful command (exit 0):

```sh
docker run --rm --network none emporio-platform:local python -c 'import os, tiktoken, app.models, scripts.seed; assert os.getuid() == 10001; assert tiktoken.get_encoding("cl100k_base").encode("teste"); assert tiktoken.get_encoding("o200k_base").encode("teste"); print("Non-root runtime, application imports, offline tokenizers: OK")'
```

A Settings-field coverage check confirmed `.env.example` documents all 29 fields.
No test called OpenAI. The real Task C ingestion and Task F security helpers were
available and exercised; there are no local placeholder implementations.

## Contract resolution and remaining limits

The original order-item model and CSV have no `external_id`. In response to the worker's
question, the coordinator added the unique constraint
`(tenant_id, order_external_id, product_external_id)` to the frozen model; the migration
includes it and order-item upserts use it. The other five CSV tables use
`(tenant_id, external_id)`. No contract questions remain open.

The public-contact allowlist preserves both conflicting published phones/emails, address,
CEP, CNPJ and the PDF's example tracking code, as directed by the coordinator.

The optional Compose migration run requires a populated `.env`; creating that file is
outside this worker's ownership, and the missing file was reported to the coordinator.
The API and worker process startup were not validated as part of this task; their entry
points are configured and their implementation is owned by other workers. The built
image reflects the shared worktree at build time and should be rebuilt after their final
changes. The seed preserves existing tenant settings and credentials on reruns, upserts
commerce rows without deleting records absent from the export, and retries incomplete
policy ingestion on the next run.
