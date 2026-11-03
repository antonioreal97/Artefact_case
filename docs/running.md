# Running the agent

Full run instructions for Docker, host Python/uv, ports, PowerShell, real-model
providers and in-container tests. For a short path, start with the root
[README quick start](../README.md#quick-start).

## Run with Docker (recommended)

### Prerequisites and quick start

Install **Docker with Compose v2 or later**, with the daemon running.
Python and `uv` are not required on the host. Run from the repository root with the
supplied data in `Desafio Técnico AI Engineer 2026/data/` (six CSVs and
`políticas_da_loja.pdf`). The first build needs internet access to download images,
dependencies and tokenizer encodings; default customer service uses fake/hash and
does not call OpenAI. Case data stays out of every image and is mounted read-only
only into the optional `seed` and `test` services.

On macOS, Linux or Windows with WSL/Bash:

```bash
./scripts/docker_up.sh --smoke
```

Without `--smoke`, the script performs the same initialization and checks `/health`
without sending a message. It creates `.env` from `.env.example` only when absent,
generates the administrative key inside the container if empty, protects the file
with mode 600 when possible, runs migrations and seed, and starts the API and worker.
It can be rerun: it preserves configuration and reuses the existing key.
The smoke check requires HTTP 200 and `R$` in the answer to the case question.

The API is available at `http://127.0.0.1:8000` by default, with documentation at `/docs`.
The script shows a curl example and the command to load the key without displaying it:

```bash
export SEED_API_KEY="$(grep '^SEED_API_KEY=' .env | tail -n 1 | cut -d= -f2-)"
curl --fail-with-body --silent --show-error http://127.0.0.1:8000/v1/chat/messages \
  -H "X-API-Key: $SEED_API_KEY" -H "Content-Type: application/json" \
  -d '{"message":"Quais opções de violões disponíveis custando até R$1000?"}'
```

The customer question means “Which acoustic guitars are available for up to R$1000?”
This key-loading command assumes the unquoted format written by the script.
`SEED_API_KEY` is administrative: keep it in a trusted terminal/server and out of Git.
If the API is not healthy within 90 seconds, check `docker compose logs api`.

### Manual equivalent

For a new environment in Bash/WSL, run the commands below. Preserve an existing
`.env`; if `SEED_API_KEY` already exists, skip key generation and reuse it.

```bash
cp .env.example .env
docker compose build api
SEED_API_KEY="$(docker compose run --rm --no-deps -T api python -c 'from app.core.security import generate_api_key; print(generate_api_key()[0])')"
printf '\nSEED_API_KEY=%s\n' "$SEED_API_KEY" >> .env
chmod 600 .env
unset SEED_API_KEY
docker compose up -d db redis
docker compose --profile seed run --rm seed
docker compose up -d api worker
```

On Windows without WSL, use PowerShell to copy the file and generate the key (also
only for a new environment), then run the same final three `docker compose` commands:

```powershell
Copy-Item .env.example .env
docker compose build api
$seedKey = docker compose run --rm --no-deps -T api python -c 'from app.core.security import generate_api_key; print(generate_api_key()[0])'
if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar a chave no container' }
Add-Content -Path .env -Encoding utf8 -Value "`nSEED_API_KEY=$seedKey"
Remove-Variable seedKey
```

The preserved PowerShell error message means “Failed to generate the key in the container.”
The `seed` service waits for migration through `depends_on`; installing or running
Alembic on the host is unnecessary. To check health without curl installed:

```bash
docker compose exec -T api python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5).status)'
```

### Ports, shutdown and reset

Default ports are API **8000**, Postgres **5434** and Redis **6380**, published only
on `127.0.0.1`. Set `API_PORT`, `DB_PORT` and `REDIS_PORT` in `.env` or export them
in the shell. For example, for an isolated stack:

```bash
export COMPOSE_PROJECT_NAME=emporiod1
export API_PORT=8010 DB_PORT=5444 REDIS_PORT=6390
./scripts/docker_up.sh --smoke
```

In PowerShell, use `$env:API_PORT = '8010'` (likewise for the other variables), or edit
`.env`. Inside the containers, services still use `db:5432` and `redis:6379`; the
host-Python alternative needs adjusted URLs if the published ports change.
Keep the same project name and variables in all subsequent commands.

```bash
docker compose down       # para os serviços; preserva o volume do Postgres
docker compose down -v    # apaga TODOS os dados do projeto selecionado
```

The first command stops services and preserves the Postgres volume; the second
deletes ALL data for the selected project, as the preserved comments state.
After a reset, rerun the script: **the same key in `.env` works**, because the seed
provisions it in the new database. A different key is rejected in an initialized
database; do not delete or regenerate the key on every run.

### Real model inside Docker

Configure providers in `.env` as described in [Real-model mode](#real-model-mode-optional):
`LLM_PROVIDER=openai` retains the native adapter; `LLM_PROVIDER=langchain` uses
`LANGCHAIN_CHAT_MODEL=provider:model`. For embeddings, choose `openai` or `langchain`
and a model with 1536 dimensions. Keep `OPENAI_TRACING_ENABLED=false`,
`LANGSMITH_TRACING=false` and `LANGCHAIN_TRACING_V2=false`.
For Ollama on the host, use `OLLAMA_BASE_URL=http://host.docker.internal:11434`
(`localhost` inside a container refers to that container).

**Before switching from hash to real embeddings, run `docker compose down -v`**
on the demonstration stack and run `./scripts/docker_up.sh` to migrate and index
a new database; this deletes existing data, and the new seed may make billable calls.
Rerunning the seed over old vectors does not guarantee reindexing. Real OpenAI,
Anthropic and Ollama providers were not run during the Docker run-path validation
reported below; subsequent OpenAI runs are documented under
[live-model validation](../evals/live/2026-11-01-gpt-5-mini.md).

### Tests and evaluation inside Docker

The `test` target installs development dependencies and includes the tests;
the default runtime image contains neither those files nor those dependencies.
The commands below use fake/hash with tracing and live tests disabled, even when
`.env` is configured for a real model:

```bash
docker compose --profile test build test
docker compose --profile test run --rm test
docker compose --profile test run --rm test python -m scripts.eval_rag
```

Tests and evaluation create and remove disposable databases using the local
`emporio` user, which has `CREATE DATABASE` permission. Evaluation JSON stays in
the disposable container; to preserve it on the host, mount a writable directory
at `/app/evals/results`. Check skips and the three mandatory gates before interpreting
results, as described in the root [Tests and evaluation](../README.md#tests-and-evaluation)
section.

Validation on 2026-11-01: macOS/arm64, Bash 3.2.57, Docker Engine 29.7.2,
Compose v5.5.1 and Python 3.12.16 inside the Linux/arm64 image. The isolated
`emporiod1` stack (ports 8010/5444/6390) passed the smoke script twice, preserving
`.env` and the key; the container suite passed with **603 tests and 123 subtests,
no skips**, and evaluation passed all three mandatory gates (leakage 0, privacy
0/23, errors 0). All six deterministic metrics reached 100%; this does not measure
real-LLM quality. The runtime image was inspected: no `tests/`, case data or pytest.
Details are in the [development process](development-process.md#43-docker-run-path-validation-2026-11-01).
Windows/WSL, host Linux and a `linux/amd64` build were not exercised; ShellCheck was
not installed. The manual PowerShell path is provided without local validation.
Current host suite counts are recorded in the root README (they have grown since this
Docker checkpoint).

## Run offline (Python/uv on the host)

An alternative using **Python/uv on the host**. To run everything in containers,
follow [Run with Docker](#run-with-docker-recommended).

### Prerequisites

Python **3.12**, `uv`, Docker with Compose, and `curl`. Run commands from the
repository root; keep the supplied data in `Desafio Técnico AI Engineer 2026/data/`.
`uv run` prepares the environment according to `pyproject.toml` and `uv.lock`;
the first run may need internet access to download dependencies.

```bash
uv --version
uv run python --version
docker compose version
```

Validated on 2026-11-01 with Python 3.12.13, uv 0.11.18 and Docker Compose v5.5.1.
Full configuration is in [.env.example](../.env.example). This walkthrough uses
environment variables without modifying that file or an existing `.env`.
The values below are for local development only.

### Database, configuration and migrations

```bash
export APP_ENV=dev
export LLM_PROVIDER=fake
export EMBEDDING_PROVIDER=hash
export INGESTION_MODE=inline
export DATABASE_URL=postgresql+asyncpg://emporio:emporio@localhost:5434/emporio
export REDIS_URL=redis://localhost:6380/0
export API_KEY_PEPPER=dev-only-pepper-change-me
export USER_REF_KEY=dev-only-user-ref-key-change-me
export OPENAI_TRACING_ENABLED=false
export RUN_OPENAI_LIVE_TESTS=0

docker compose up -d db redis
uv run alembic upgrade head
```

Postgres/pgvector uses port **5434** and Redis uses **6380**, published only on
localhost. On first startup, wait for healthy services before migration.
An absent `APP_ENV` means `prod`, which rejects fake/hash and development secrets.
`inline` removes the need to start a worker for this walkthrough.

### Seed and local key

For a new database, generate a key and keep it in this session:

```bash
export SEED_API_KEY="$(uv run python -c 'from app.core.security import generate_api_key; print(generate_api_key()[0])')"
uv run python -m scripts.seed --api-key-from-env SEED_API_KEY
```

The seed validates all six CSVs before importing them and ingests the policy PDF:
9 categories, 65 products, 25 promotions, 50 customers, 20 orders and 22 items.
It is idempotent for the same key; it does not delete records absent from the CSVs.
For an initialized database, reuse the original key: a different key is rejected,
and its value cannot be recovered from the persisted hash.
Store it outside Git. Do not rerun the generation line when reusing the seed.

`SEED_API_KEY` is an **administrative** key with all scopes; use it only in a trusted
terminal or server. Optional `SEED_CHAT_API_KEY` provisions a second key with the
`chat` scope, but does not resolve the API's lack of end-user authentication.
Do not distribute the administrative key in a widget.

### API and first message

Start the API in the same terminal:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another terminal, make the **same** `SEED_API_KEY` generated above available
without printing it to logs. The development API exposes its specification at
`http://127.0.0.1:8000/docs`; documentation is disabled in production.

To test in a browser, open `http://127.0.0.1:8000/chat` and follow the
[browser test interface](platform/extensions.md#browser-test-interface).

```bash
curl --fail-with-body --silent --show-error http://127.0.0.1:8000/health

curl --fail-with-body --silent --show-error \
  http://127.0.0.1:8000/v1/chat/messages \
  -H "X-API-Key: $SEED_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"message":"Quanto custa o Takamine GD20?"}'
```

The customer question means “How much does the Takamine GD20 cost?”
Verified response, abbreviated:

```json
{
  "reply": "Takamine GD20 Dreadnought Natural: R$ 2.199,00.",
  "sources": [],
  "handoff": null,
  "grounded": true,
  "tools_used": ["search_products"]
}
```

The JSON also contains `conversation_id`: send it with `message` in subsequent
messages to continue the conversation. Send **one turn at a time per conversation**;
concurrent turns have neither serialization nor idempotency.
`grounded` indicates evidence accepted by the heuristics, rather than verification
of every fact. `handoff` represents a persisted request; it does not prove human
notification or a human response.

### Evaluation output and host walkthrough validation

`--json-out /dev/null` discards the evaluation JSON; without `--json-out`, results are
saved to the Git-ignored `evals/results/latest.json`.

Host walkthrough validation on 2026-11-01: infrastructure, migration, key generation,
seed (twice), server and curls all ran successfully. The database was isolated to
preserve the existing environment; no repository configuration changed. Integrated state
after X1 (2026-11-01): clean `ruff` and `ruff format`, clean mypy on 63 files, 603 tests and
123 subtests passing in two consecutive runs, and offline evaluation passing every mandatory
gate and reaching 100% on all six metrics. The conversation generator ran successfully and
produced the five files in `examples/conversations/`. Dependency installation on an empty
machine was not exercised.

## Real-model mode (optional)

Chat has three options: `fake` (deterministic), `openai` (the existing native adapter),
and `langchain` (OpenAI, Anthropic or Ollama adapters under the same Agents SDK loop).
Embeddings support `hash`, `openai` and `langchain` (OpenAI or Ollama; Anthropic has no
embeddings in this adapter). This choice does not change the [privacy rules](../SECURITY.md):
`PrivacyGuardedModel` remains mandatory, without hosted tools, remote state or tracing.
See the decision and limitations in [ADR 006](adr/006-langchain-model-layer.md).

LangChain with OpenAI, using environment variables or a Git-ignored `.env`:

```dotenv
LLM_PROVIDER=langchain
LANGCHAIN_CHAT_MODEL=openai:gpt-5-mini
EMBEDDING_PROVIDER=langchain
LANGCHAIN_EMBEDDING_MODEL=openai:text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
# OPENAI_API_KEY=<your key>
OPENAI_TRACING_ENABLED=false
LANGSMITH_TRACING=false
LANGCHAIN_TRACING_V2=false
```

For Anthropic, change `LANGCHAIN_CHAT_MODEL=anthropic:<model-available-in-your-account>` and set `ANTHROPIC_API_KEY`; retain a separate
embedding provider, such as OpenAI with its key. For local Ollama, use
`LANGCHAIN_CHAT_MODEL=ollama:<model-with-tools>` and
`OLLAMA_BASE_URL=http://localhost:11434`; in Docker, use
`http://host.docker.internal:11434`. Install the model in Ollama beforehand.
`LANGCHAIN_EMBEDDING_MODEL=ollama:<1536-dimension-model>`
requires vectors of that dimension; other dimensions require a new migration and
reindexing, outside this scope. Names inside `<...>` are placeholders: actual model
compatibility has not been tested.

The native path remains available with `LLM_PROVIDER=openai`, `EMBEDDING_PROVIDER=openai`,
`OPENAI_CHAT_MODEL=gpt-5-mini` and `OPENAI_EMBEDDING_MODEL=text-embedding-3-small`.
Defaults are implementation choices without a quality or cost benchmark.
`OPENAI_REASONING_EFFORT=low` is the one measured default: on the 27-turn battery it cut
median latency from 11.8s to 9.3s and p90 from 17.7s to 12.0s versus the provider default,
with 23 vs 24 useful answers (within run-to-run variance). `minimal` reached a 4.0s median
but dropped to 21/27 and gave wrong refusals, so it is not the default (D-37). Accepted
values are `minimal`, `low`, `medium` and `high`; an empty value sends no reasoning setting
(provider default). It applies to the native OpenAI adapter only; the LangChain and fake
adapters ignore it.
The LangChain adapter uses global `LANGCHAIN_CHAT_MODEL`; the tenant's native
`chat_model` field does not override it. LangChain streaming is not implemented:
the current API uses `Runner.run` without streaming. Failures and timeouts retain
the local fallback/human handoff behavior.

`MODEL_TIMEOUT_SECONDS=30` limits each model call, including LangChain adapters;
`TURN_TIMEOUT_SECONDS=75` limits the complete agent loop, including tools.
Multiple short calls can therefore complete a turn lasting over 30s.
Both must be finite and positive: at most 300s per call and 600s per turn, also
validated in production. `MAX_AGENT_TURNS=8` still bounds iterations;
`TOOL_TIMEOUT_SECONDS=5` remains the individual tool limit.
Either model/turn timeout triggers the fallback with reason `tool_failure`.

**Changing variables on a database populated with hash embeddings is not enough.**
Vectors may have the same dimension but incompatible spaces. For the API, use a new
database with migrations and seed configured for the selected provider, or reprocess
all documents before serving queries. Rerunning only the seed may skip completed
documents; there is no vector-generation fingerprint. The existing `--live` harness
validates the native OpenAI path, creates a disposable database and makes billable
calls; it is not a comparative evaluation of LangChain adapters.

The first live LangChain run with `openai:gpt-5-mini` revealed that closing shared
HTTP clients broke every turn after the first (D-27 / R8). The fix creates owned
OpenAI clients per instance and closes only owned clients; shared Anthropic clients
remain open. After the fix, the manual LangChain battery with `openai:gpt-5-mini`
had 22 useful answers and 1 correct handoff in 27 turns
([live-model validation](../evals/live/2026-11-01-gpt-5-mini.md)).
**Anthropic and Ollama still have no live execution.** Regression tests use simulated
transports and fake/hash; they do not measure quality, threshold calibration or retention.
Before real use, verify each provider's terms, retention and controls; `store=False`
does not replace that verification. Any enabled LangSmith tracing flag blocks startup
in every environment.
