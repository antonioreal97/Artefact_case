# Empório da Música — customer-service agent

A Python customer-service agent for the fictional Empório da Música store in Campo Grande/MS, built with FastAPI and the OpenAI Agents SDK.
It answers catalog, price and stock questions through typed tools, retrieves store policies with RAG citations, and checks order status after local contact verification.
It requests human handoff when needed and is instructed to reply in the customer's language, with Portuguese as the default.
Its configured tone is informal, professional and welcoming: “Sua música começa aqui” (“Your music starts here”).

## Reviewer's guide (5 minutes)

1. **Run it (no API key, deterministic):** `./scripts/docker_up.sh --smoke` starts the
   stack with a scripted fake model and hash embeddings and checks one answer to the case
   question. Details: [Run with Docker](#run-with-docker-recommended).
2. **Run it with the real model:** in `.env` set `LLM_PROVIDER=openai`,
   `EMBEDDING_PROVIDER=openai` and `OPENAI_API_KEY`, then run `docker compose down -v` and
   `./scripts/docker_up.sh` to re-index with real embeddings (this deletes the demo data and
   makes billable calls). Details: [Real model inside Docker](#real-model-inside-docker).
   Then open `http://127.0.0.1:8000/chat` or use the curl example below.
3. **Read, in this order:** the [five interaction examples](examples/conversations/),
   the [customer-service flows](docs/customer-service-flows.md) and the
   [technical decisions](#technical-decisions).
4. **Scope:** the core of the case is one agent with seven typed tools, RAG over the
   store manual and a privacy boundary (`app/agents/`, `app/tools/`, `app/rag/`,
   `app/services/chat.py`). The multi-tenant platform around it is a deliberate,
   optional extension; see [why](#platform-extensions-optional).

## How the agent works

1. Authenticate the request, identify the store and load or create the conversation.
2. Classify the message locally, tokenize recognized personal data, and apply input guardrails; restricted requests and human-service requests can be handled before the model runs.
3. Resolve configured triage choices locally; unmatched messages and specific follow-ups run one bounded agent loop: typed commerce tools supply catalog, price, stock and verified order facts; document retrieval supplies policy excerpts and citations.
4. Check the answer for unsupported prices, stock, commercial claims and citations, and scan it for restricted data.
5. Reveal only authorized tokens locally; use a safe fallback or persist a human handoff request when the evidence or safety checks require it.
6. Return and persist the reply, sources and tool evidence; include the configured welcome message on first contact when appropriate.

See the [customer-service flows](docs/customer-service-flows.md) for intent diagrams, guardrails and handoff reasons.
The demonstrated path is one store, one API and deterministic providers. The backend queries a snapshot imported from CSVs, rather than a live ERP.
Tests verify integration and the controls exercised, rather than LLM naturalness or quality. A manual battery with `gpt-5-mini` and `text-embedding-3-small` ran on 2026-11-01 (see [live-model validation](#live-model-validation)); it is not a statistical evaluation.

## Triage menu

Empório greeting-only first contact includes a numbered menu. A first-contact question gets
the welcome without the menu, the normal answer, and a short hint to type `menu`. Digits are
menu choices only while the previous assistant turn left the menu (or an expecting sub-step)
pending; any agent reply clears that state so `4` after a product list reaches the agent.
Clear purchase intent (`quero comprar`, `vou levar`, …) hands off to sales immediately
(`triage:purchase`) because the agent cannot complete a sale. Customers can also use shortcuts
such as `horário` or `endereço`, or write normally; `menu` and `0` reopen the full menu.
Python answers hours/contact and the short online-return policy, guides verified order
lookup, and creates human handoffs without model calls. Product details, prices, stock,
and specific policy questions go to the agent.

Triage is opt-in through `AgentSettings.extra.triage`; new Empório seeds enable it.
Existing tenants keep their configuration. To explicitly enable/reset the Empório defaults,
run `uv run python -m scripts.create_tenant --refresh-triage`. Pending steps persist in
assistant-message metadata; no migration is needed. See the [routing table](docs/customer-service-flows.md#deterministic-triage)
and [contract](docs/platform/contracts.md#t1--deterministic-triage).

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
| Justify framework/approach, model/provider, interface, history and data treatment | [Technical decisions](#technical-decisions), [real-model mode](#real-model-mode-optional) and [ADR 005](docs/adr/005-multi-tenant-platform-branch.md) explain the choices and their scope |
| Complete run instructions and configuration | [Docker quick start](#run-with-docker-recommended), [host setup](#run-offline), [.env.example](.env.example) and [model configuration](#real-model-mode-optional) |
| Known limitations, next steps and documented assumptions | [Limitations and next steps](#limitations-and-next-steps) and [Assumptions](#assumptions) |
| Explain coding-assistant choices and workflow | [Use of coding assistants](#use-of-coding-assistants) |
| 3–5 varied saved conversations, including a nontrivial data lookup or policy application | [Five offline examples](#interaction-examples), including verified order status and return-policy application; runtime queries use the local snapshot |
| Public GitHub repository with a genuine commit history; submit its link by email and stop changes at the deadline | [Repository](https://github.com/antonioreal97/Artefact_case) and its commit history are the delivery artifacts; email submission, public visibility and deadline compliance require owner verification |

- [Run with Docker (recommended)](#run-with-docker-recommended)
- [Run offline with Python/uv on the host](#run-offline)
- [Real model](#real-model-mode-optional)
- [Platform extensions (optional)](#platform-extensions-optional)
- [Technical decisions](#technical-decisions)
- [Examples](#interaction-examples)
- [Tests and evaluation](#tests-and-evaluation)
- [Limitations](#limitations-and-next-steps)
- [Use of coding assistants](#use-of-coding-assistants)

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

Configure providers in `.env` as described in [real-model mode](#real-model-mode-optional):
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
reported below; subsequent OpenAI runs are documented under [live-model validation](#live-model-validation).

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
results, as described in [Tests and evaluation](#tests-and-evaluation).

Validation on 2026-11-01: macOS/arm64, Bash 3.2.57, Docker Engine 29.7.2,
Compose v5.5.1 and Python 3.12.16 inside the Linux/arm64 image. The isolated
`emporiod1` stack (ports 8010/5444/6390) passed the smoke script twice, preserving
`.env` and the key; the container suite passed with **603 tests and 123 subtests,
no skips**, and evaluation passed all three mandatory gates (leakage 0, privacy
0/23, errors 0). All six deterministic metrics reached 100%; this does not measure
real-LLM quality. The runtime image was inspected: no `tests/`, case data or pytest.
Details are in the [development process](docs/development-process.md#43-docker-run-path-validation-2026-11-01).
Windows/WSL, host Linux and a `linux/amd64` build were not exercised; ShellCheck was
not installed. The manual PowerShell path is provided without local validation.

## Run offline

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
Full configuration is in [.env.example](.env.example). This walkthrough uses
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

To test in a browser, open `http://127.0.0.1:8000/chat` and follow
[Browser test interface](#browser-test-interface).

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

## Real-model mode (optional)

Chat has three options: `fake` (deterministic), `openai` (the existing native adapter),
and `langchain` (OpenAI, Anthropic or Ollama adapters under the same Agents SDK loop).
Embeddings support `hash`, `openai` and `langchain` (OpenAI or Ollama; Anthropic has no
embeddings in this adapter). This choice does not change the [privacy rules](SECURITY.md):
`PrivacyGuardedModel` remains mandatory, without hosted tools, remote state or tracing.
See the decision and limitations in [ADR 006](docs/adr/006-langchain-model-layer.md).

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
had 22 useful answers and 1 correct handoff in 27 turns ([live-model validation](#live-model-validation)).
**Anthropic and Ollama still have no live execution.** Regression tests use simulated
transports and fake/hash; they do not measure quality, threshold calibration or retention.
Before real use, verify each provider's terms, retention and controls; `store=False`
does not replace that verification. Any enabled LangSmith tracing flag blocks startup
in every environment.

## Platform extensions (optional)

The delivered solution on this repository's `main` branch extends the case into a
**multi-tenant platform**. The case asks for a messaging agent, rationale, instructions
and examples, solved as simply as possible; multi-tenancy, generic uploads and WhatsApp are
not case requirements. I chose to build them on purpose: to show how I would build an
agent meant to scale and keep running for long periods with little maintenance, where new
stores, channels and policy documents are configuration and operator actions rather than
code changes. A minimal script would answer the case, but usually needs small edits every
time the data, documents or channels change. This is a design intent, not a measured
result: no load or long-running operation tests were run, and the limitations below list
what still needs work before production use. [ADR 005](docs/adr/005-multi-tenant-platform-branch.md)
records that decision and its heavier runtime compared with the original smaller design.

Tenant isolation, the WhatsApp webhook, document uploads and ingestion jobs, Redis
rate limits, and the `/chat` test UI extend the core agent demonstration. They let the
same service exercise store isolation, additional channels and operator document workflows;
they do not establish a measured advantage over a simpler solution for this case.
Redis also supports the implemented privacy vault and order-lookup lockout; optional
product scope does not mean every infrastructure component can be removed from the
current runtime without changes. The supported walkthrough remains one seeded store
over HTTP; limitations of the extra features are listed below.

### Browser test interface

With the API and seed ready, open `http://127.0.0.1:<API_PORT>/chat`
(default `http://127.0.0.1:8000/chat`) and paste the key into the access field.
The quick-start `export SEED_API_KEY=...` command loads the key from `.env`;
prefer `SEED_CHAT_API_KEY`, if provisioned by the seed, loading it the same way.
Use the administrative key only on the local machine. The interface keeps the key
only in `sessionStorage`, within the tab session; clear the field to remove it.

Send questions, continue the conversation, or click **Nova conversa** (“New conversation”).
Each response's details show tools, evidence (`grounded`), sources, latency and any
handoff. One turn is sent at a time. Reloading starts a new conversation;
**Nova conversa** clears the screen without deleting server-side history.

This is a development and demonstration interface: it follows `EXPOSE_API_DOCS`
and returns 404 in production, including its assets. It is not a production widget
and provides no end-user authentication. It uses the provider configured in the API;
the page does not detect whether that provider is fake/hash or a real model.

## Technical decisions

| Choice | Rationale and scope |
|---|---|
| One agent with the OpenAI Agents SDK | Seven tools cover one domain; the bounded loop avoids unnecessary specialist agents. `PrivacyGuardedModel` inspects every payload before the provider, enforces `store=False` and blocks hosted state. |
| FastAPI interface | HTTP makes conversations reproducible with curl and exercises authentication, errors and isolation; a visual interface is unnecessary to evaluate the agent. |
| Native OpenAI, LangChain or fake through configuration | LangChain adapts OpenAI/Anthropic/Ollama chat and OpenAI/Ollama embeddings; the loop stays in the Agents SDK and hybrid retrieval is unchanged. Explicit factories avoid installing LangGraph; LangSmith is only a transitive dependency, with tracing prohibited. Offline regressions; the first live LangChain run revealed D-27; after the fix, 22 of 27 useful answers with `openai:gpt-5-mini`. Anthropic/Ollama have not been run live. |
| Tools for operational facts | Price, stock, promotion, order and customer data come from typed `CommerceBackend` queries; the model neither generates SQL nor uses RAG to infer these data. |
| Document-only RAG | Policies, FAQ and manuals require excerpts and references; the seed includes the store PDF. Embeddings do not replace identity checks. |
| Hybrid retrieval + RRF | pgvector/cosine captures similarity and Portuguese full-text search captures exact terms; Reciprocal Rank Fusion combines ranks without adding incompatible scales. Superiority over lexical search has not been measured. |
| Sufficiency before answering | Similarity thresholds and lexical coverage withhold insufficient sources; SQL queries filter tenant, active document and current version. This is a relevance heuristic, not proof of a complete answer. |
| Versioned prompt | `commerce-privacy-v11` combines a configurable persona with mandatory rules plus an ESTILO block (WhatsApp-like Empório tone: answer first, short, no greeting loops, light citations; prompt-only, so live replies still sometimes use em dashes, long policy answers or a templated closing question); documents arrive in `<untrusted_document>` as untrusted data. Guardrails and authorization remain in code. Per-turn welcome notes are validated against the same instruction string the model sees (truthful “welcome already sent”, no assumed hours). Unbacked handoff claims are rejected in code. Offering photos or a return start/freight quote without a tool is forbidden (prompt-only; no image tool exists; specs stay on `get_product_details`). Money validation is unchanged from `main`: amounts are read from digits only, and a customer budget is exempt only when written in digits. |
| Validated CSVs + vault | Types, references and uniqueness are validated; import uses per-tenant upsert. Restricted data stays local; recognized spans become reversible tokens resolved by local tools. This is pseudonymization, not anonymization. |
| Persisted history | Postgres stores conversations, original and redacted messages, sources and tool executions; only the redacted form goes to the model. Redis stores the vault with a sliding 24-hour TTL. |

Turn flow (the original diagram is preserved verbatim):

```text
API autenticada → tenant → classificação/tokenização local → agente limitado
   ├─ catálogo, estoque, pedidos → ferramentas → banco local
   └─ políticas → busca híbrida → trechos não confiáveis + citações
→ validação de saída → revelação local autorizada → resposta + persistência
```

In English: authenticated API → tenant → local classification/tokenization → bounded agent;
catalog, stock and orders use tools and the local database; policies use hybrid retrieval,
untrusted excerpts and citations; output validation → authorized local reveal → reply and persistence.

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
Conversation artifacts must not publish customer identifiers. The generator rejects unknown
English translations; tests regenerate all five files twice and compare them except for the date.
Examples 04 and 05 include deterministic order-status and public-contact triage.
Regenerate the examples with Postgres available:

```bash
uv run python -m scripts.demo_conversations
```

[Live English conversations](evals/live/english-conversations/) were re-recorded against
`gpt-5-mini` under `commerce-privacy-v9` (catalog/price/stock: attempt 2; return/order/scope:
attempt 3; earlier attempts archived). Attempts 1–2 of the order conversation were refused
because the deliberate wrong-contact turns of earlier recordings had locked order 1 (three
failed verifications in one hour), which is the lockout control working as designed. They are nondeterministic observations—not automated
quality gates—and remain separate from the reproducible offline demonstrations. See
[live conversation notes](evals/live/english-conversations/README.md) for what worked, what
failed and remaining multilingual limits.

## Tests and evaluation

The commands below use Python/uv on the host. The
[Docker testing walkthrough](#tests-and-evaluation-inside-docker) requires neither installation.

Use offline providers and keep live tests disabled:

```bash
APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash RUN_OPENAI_LIVE_TESTS=0 uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy app
APP_ENV=test LLM_PROVIDER=fake EMBEDDING_PROVIDER=hash uv run python -m scripts.eval_rag --json-out /dev/null
```

Integration tests use disposable Postgres databases; they may be skipped if Postgres
is unavailable. Check skips before interpreting a successful run.
Evaluation creates and removes its own database, with Empório and a synthetic tenant
used only to test isolation. `/dev/null` discards the JSON; without `--json-out`,
results are saved to Git-ignored `evals/results/latest.json`.

There are 23 cases and three mandatory gates: cross-tenant leakage = 0, privacy
violations = 0 and harness errors = 0. Exit code 1 indicates a gate failure;
2 indicates an environment problem. Quality metrics are informative: even 100% with
fake/hash does not measure understanding, tone, paraphrase robustness or a real LLM.

The [integration status](docs/development-process.md#42-state-at-integration-2026-11-01)
separates accepted results from this round's checks. The [initial evaluation](evals/REPORT.md),
[R1 security review](docs/reviews/security-review.md) and
[adversarial tests](tests/adversarial/FINDINGS.md) preserve historical snapshots.
The [R3 architecture review](docs/reviews/architecture-review.md) documents limitations
that remain open even after tests pass.

Walkthrough validation on 2026-11-01: infrastructure, migration, key generation,
seed (twice), server and curls all ran successfully. The database was isolated to
preserve the existing environment; no repository configuration changed.
Integrated state after X1 (2026-11-01): clean `ruff` and `ruff format`, clean mypy on
63 files, 603 tests and 123 subtests passing in two consecutive runs, and offline
evaluation passing every mandatory gate and reaching 100% on all six metrics.
The conversation generator ran successfully and produces the five files in
`examples/conversations/`. Dependency installation on an empty machine was not exercised.

### Live-model validation

On 2026-11-01, the Docker stack ran with `gpt-5-mini` and `text-embedding-3-small`
against a manual battery of 27 turns (24 standalone questions and one 3-turn conversation).
Each fix round (R4–R8) was followed by another full run; R5–R7 ran twice because the
model is nondeterministic.

| Run | Useful answers (out of 27) |
|---|---|
| Before fixes | 8 |
| R4 | 15 |
| R5 (A / B) | 17 / 20 |
| R6 (A / B) | 23 / 17 |
| R7 (A / B) | 22 / 21 |
| R8, native adapter | 19 |
| R8, LangChain (`openai:gpt-5-mini`) | 22 |
| R9, native adapter (`commerce-privacy-v9`) | 24 |
| R10, native adapter (`commerce-privacy-v10`, humanized tone) | 24 |
| R11, native adapter, `OPENAI_REASONING_EFFORT=low` (shipped default) | 23 |
| R11, native adapter, `OPENAI_REASONING_EFFORT=minimal` (rejected) | 21 |

In every run, “quero falar com um atendente” (“I want to speak to a representative”)
was correctly handed off. The first run showed that the privacy filter itself blocked
legitimate answers: opaque provider-generated IDs looked like postal codes. It also
revealed price and stock rules that were too strict for real-LLM phrasing, catalog
search sensitive to extra words and singular/plural forms, a short timeout and, in
the LangChain path, a shared HTTP client closed after the first turn. Causes and fixes
are in the [development log](docs/development-process.md) §4.5–4.9; the per-question
table is in [evals/live](evals/live/2026-11-01-gpt-5-mini.md).

Behavior still varies between runs: the case question falls back to “no information”
in some attempts; an order without contact details is sometimes handed off instead
of prompting for an email; and installment-payment questions always request human
confirmation because the manual has conflicting minimum-installment rules.
This is a small set used to find defects, not a quality measurement;
Anthropic and Ollama were not run.

## Limitations and next steps

- **Quality and grounding (A-2/A-4, O-4):** the Portuguese live battery reached 24/27
  useful answers on R9 native after the English-customer fixes and again on R10 native
  after the humanized-tone changes (23/27 on R11 with `OPENAI_REASONING_EFFORT=low`),
  with earlier rounds
  between 19 and 23; evaluation with held-out questions, more multi-turn conversations
  and conflicting sources is still missing. Output rules are lexical: they reduce
  fabrication but also reject correct answers. A citation or product name does not prove
  stock, lead-time or condition consistency. I would render critical facts locally from
  typed evidence and add contradiction tests.
- **English / multilingual customers:** live English re-recordings under v9 can answer a
  natural return-policy question with citations and refuse out-of-scope questions in
  English, and the budget catalog no longer fails solely on `R$1,000` formatting. Remaining
  limits: the tenant welcome stays Portuguese; replies can still mix languages (labels of
  locally revealed order fields and closing questions came back in Portuguese); a wrong
  contact ends in a `no_information` handoff instead of asking the customer to check it;
  three wrong contacts lock the order for an hour for everyone, including the real customer,
  so anyone who knows an order number can trigger it (next step: key the lockout to the
  actor and alert instead of blocking the order); catalog breadth and
  retrieval still vary by phrasing; offers of tool-less actions (starting a return, quoting
  freight) are prompt-only, enforced only by the success-claim check when the model claims
  the action happened. Broader multilingual evaluation is still open.
- **Latency:** a reply takes several seconds because each turn makes 2–3 sequential
  `gpt-5-mini` calls (choose a tool, read its result, answer); catalog tools take
  milliseconds and knowledge search about 2s. On the 27-turn battery (2026-11-03),
  `OPENAI_REASONING_EFFORT=low` measured median 9.3s, p90 12.0s, max 27.6s, mean 9.4s
  (provider default: 11.8s / 17.7s / 44.2s / 11.9s). There is no streaming, so the
  customer waits for the whole answer. `minimal` (median 4.0s) cost answer quality. Next
  steps: stream the final answer, and answer simple policy questions with one model call.
- **Money written in words:** price validation reads digits only (as on `main`). A budget
  written in words ("até mil reais") is not exempt, so an answer that repeats it as
  "até R$1.000" falls back to a human; a price written only in words ("custa mil reais")
  is not checked at all, and "mil e 500 reais" in a document is read as R$500. These cases
  are `xfail` tests in `tests/agent/test_humanized_tone_review_fixes.py`. I tried a
  number-word parser on this branch, but each review round found a new false-price bypass,
  so I reverted to `main`'s behavior and kept the limitation documented.
- **Privacy (A-3, O-9):** unknown lowercase names and contextual identifiers may
  escape the rules. Tokens enable linkage within a conversation. I would restrict
  the external boundary to structured public/abstract inputs and keep ambiguous text
  local; I claim neither anonymization nor universal coverage.
- **Concurrency (A-5):** vault GET/SET and turn commits are not atomic; simultaneous
  requests or retries may lose state or duplicate actions. I would implement
  per-conversation serialization and idempotency before concurrent use.
- **Scope (A-6):** the platform exceeds the need demonstrated by the case. I would
  keep extra features optional, without a rewrite just before delivery.
- **RAG (A-7/A-8):** immutable processing generations, atomic index swaps, calibration
  with real embeddings and condition/conflict evaluation are missing. Scanned PDFs
  have no OCR; tables and complex layouts have limited support. The manual has
  ambiguous contacts and installment rules: I do not reconcile them by assumption.
- **Code and scale (A-9/A-10):** the chat service concentrates responsibilities;
  several requests load a tenant's entire customer/product sets. I would refactor
  evidence/validation/rendering and measure memory, cost and p95 before scaling
  beyond the seed's 50 customers and 65 products.
- **Authorization and operations (A-11/A-12):** tenant filters are a tested convention,
  without RLS; end users are not bound to HTTP sessions. Queues and WhatsApp lack
  durable delivery guarantees. I would prioritize identity, inbox/outbox, retries
  and per-tenant credentials; Redis in Compose has no durable volume.
- **Retention and observability (A-13/A-14):** purge exists, but conversation activity
  and document lifecycles are incomplete. Usage/cost and effective-configuration
  metrics are missing; current metrics are per process.
- **False positives (O-10):** correct denials of a promotion can be blocked by the
  lexical rule; R5 fixed the specific case where “Custom 24” was read as a price,
  without resolving every promotion denial. Partial search can return alternatives
  sharing terms, without guaranteeing equivalence to the requested product.
  Existing seed tenants need to reapply public configuration with
  `scripts.create_tenant` to incorporate missing contacts/address. Opaque transport
  IDs and UUIDs in dedicated fields received specific handling in R4; numbers in
  free text remain subject to the scanner.

## Welcome message

Configure `agent_settings.welcome_message` per tenant in the database (optional text,
up to 1,000 characters). The Empório seed uses the manual's hours: Monday–Friday,
9 am–6 pm; Saturday, 9 am–1 pm; closed Sundays and holidays. Rerunning the seed fills
only empty values and preserves operator-defined text.

On first contact in a conversation, a greeting alone receives only that text, without
a model call. If the message already includes a question, the customer receives the
welcome and normal answer without repeating the question. In the latter case, the
API returns `welcome_message` before `reply`; WhatsApp and `/chat` display separate
messages. The welcome stays in history and is not repeated. Tenants without configured
text retain their previous behavior. Refusals, failures and handoffs receive no welcome;
safety and privacy controls stay active. Use only public store text, without personal
data or customer placeholders.

## Use of coding assistants

I used **Claude Code (Anthropic)**, **Cursor**, **Hermes** and **OpenAI Codex** in agent
sessions, with parallel terminals managed by **Orca** and local Python environments
to run code, tests and evaluation. I, `antonio-real`, defined the objective and
constraints, chose the platform direction (ADR 005), decided what to accept and
approved every commit. All commits name the owner as author; that authorship does
not mean all code was written manually.

Claude Code coordinated the work: it divided workstreams with frozen contracts and
exclusive file ownership, wrote task specifications, answered workers' questions,
reran every check before accepting each delivery and proposed commits.
The tools identified per workstream are:

| Workstream | Tool |
|---|---|
| B — infrastructure | Orca, Hermes and Cursor |
| C — RAG | Claude Code and Cursor; the embedding-model choice in PostgreSQL/pgvector is still open |
| D — commerce tools | Claude Code and Cursor; planned channel: WhatsApp |
| E — initial agent implementation | OpenAI Codex |
| F — API (flow validation) | OpenAI Codex |
| R1 — security review | OpenAI Codex |
| E2, E3 — agent fixes | Claude Code |
| F2, F3 — platform fixes | Claude Code |
| R2 — adversarial tests | Claude Code |
| G1 — evaluation | Claude Code |
| R3 — architecture review | OpenAI Codex |
| X1 — example conversations, catalog filtering and stock validation | OpenAI Codex |
| X2 — this README and documentation alignment | OpenAI Codex |
| D1 — complete Docker run | OpenAI Codex |
| L1 — LangChain model layer | OpenAI Codex |
| U1 — browser test interface | OpenAI Codex |
| W1 — welcome message | OpenAI Codex (main implementation) and Cursor (final review, verification and completion) |
| F1 — customer-service flow documentation | Cursor (Composer / Orca worker) |
| R4–R8 — fixes after live-model runs | Claude Code ran the batteries and diagnosed causes; OpenAI Codex implemented the fixes |
| E1 — out-of-scope refusal without handoff, bilingual demo conversations, first English live recordings | OpenAI Codex |
| T — English translation of the documentation and README restructure | OpenAI Codex (README, docs index, service flows, context/evaluation/security docs) and Cursor (process, architecture, ADRs, reviews and test reports) |
| E2 — English-customer fixes (`commerce-privacy-v9`), re-recordings and R9 battery | Cursor |
| Integration of the English delivery and diagnosis of the R9 order failure (lockout from earlier recordings) | Claude Code |
| E3–E3f — humanized store tone (`commerce-privacy-v10`): style rules, warm fixed messages, lighter citations, fixes after each review round | Cursor |
| V3–V3f — independent read-only reviews of the tone work (six rounds; rejected attempts became regression tests; final approval) | OpenAI Codex |
| E4 — configurable reasoning effort and live latency/quality comparison (`low` shipped) | Cursor |
| T1 — deterministic triage menu before the agent | OpenAI Codex (implementation) and Cursor (completion after a usage limit, smoke test) |
| RT1 — independent read-only review of T1 and E4 | Cursor |
| T2 — triage UX fixes from the owner's browser test (no menu after a first-contact question, menu digits only right after the menu, purchase intent to sales) | Cursor (implementation); Claude Code (review and live re-check) |
| Coordination of the tone, latency and triage work, decisions after each review, and integration | Claude Code |

The workflow was: **contracts → parallel workstreams → independent review (R1) →
adversarial tests (R2) → evaluation with gates (G1) → fixes by owners (E2/F2, E3/F3) →
independent architecture review (R3) → this README**. Some rounds overlapped; the
history and [development log](docs/development-process.md) show the checkpoints.

Not everything worked on the first attempt. Usage limits interrupted workstreams,
which resumed from checkpoints. The E2 price fix introduced a regression: values
in documents started being accepted as product prices. The adversarial suite found
**FINDING-5**; E3 restricted those values and retained the case as a regression test.
Tests and independent reviews verified the assistants' deliveries; acceptance
remained a human decision.
