# Emporio da Musica Agent Technology Stack

## Purpose and status

This document records the stack selected before implementation and explains why each
component fits the prototype. It is a design commitment, not evidence that every package
is already installed. Exact versions will be resolved, locked and recorded when the Python
project is scaffolded.

The central stack decision is to use a small explicit Python application with official
provider SDKs behind internal interfaces. The project will not use a general-purpose agent
framework or vector database unless evaluation evidence shows a concrete need.

## Selected stack

| Layer | Selection | Status | Reason |
|---|---|---|---|
| Language | Python 3.12 or newer compatible release | Decided | Required by the case; strong typing, SDK and testing ecosystem |
| Project management | `uv`, `pyproject.toml`, `uv.lock` | Decided | Reproducible environments and one cross-platform lockfile |
| CLI | Typer with Rich output | Proposed | Clear commands and readable demo output with little UI code |
| Domain validation | Pydantic v2 | Decided | Typed runtime validation and JSON Schema generation |
| Operational data | Python `csv` loader into SQLite | Decided | Small relational dataset, zero server dependency and exact queries |
| Policy knowledge | Curated JSON/Markdown sections plus deterministic index | Decided | Eight-page manual does not justify a vector database |
| Orchestration | Explicit Python state machine | Decided | Auditable routing, privacy and retry behavior |
| Paid model access | Official OpenAI, Anthropic and Google SDK adapters | Proposed candidates | Keeps provider behavior isolated and makes comparison possible |
| Local inference | vLLM OpenAI-compatible server | Proposed production local runtime | Compatible HTTP surface, structured outputs and tool calling |
| Local development | Ollama as an optional developer convenience | Optional | Simple laptop experimentation, not the production contract |
| Configuration | Environment variables plus TOML capability registry | Decided | Secrets stay outside files; TOML is readable through the standard library |
| Logging | Python logging with JSON formatter and mandatory redaction | Decided | Small footprint and control over fields; no prompt logging |
| Tests | pytest running existing unittest-compatible tests | Decided | Parametrized contracts, fixtures, markers and readable reports |
| HTTP mocking | respx or provider adapter fakes | Proposed | Prevents paid/network calls in deterministic tests |
| Quality | Ruff and mypy | Decided | Fast lint/format checks and static contract validation |
| Packaging | Docker only after local serving or deployment requires it | Deferred | Avoids container complexity in the CLI MVP |

## Dependency groups

The planned `pyproject.toml` groups dependencies by responsibility:

```text
core
  pydantic
  pydantic-settings
  typer
  rich

providers
  openai
  anthropic
  google-genai

development
  pytest
  pytest-asyncio
  respx
  ruff
  mypy
```

Local inference runtime is deployed separately. GPU-heavy vLLM/PyTorch dependencies must
not be forced onto a normal CLI or paid-only installation.

## Data technology decision

SQLite is preferred over pandas for the agent's operational layer because the data is
relational, joins and filters must be exact, and the complete dataset has only 191 rows.
Python's built-in `sqlite3` module provides a serverless DB-API interface suitable for
prototyping and later migration if needed:
<https://docs.python.org/3/library/sqlite3.html>.

CSV files remain immutable inputs. On startup, a deterministic loader validates schemas
and builds an in-memory or temporary SQLite database. No model generates SQL; repositories
own parameterized queries.

## Retrieval technology decision

The policy manual has stable numbered sections and limited content. The first release uses
a curated `policies.v1.json` file with identifiers, titles, canonical text, keywords and
manual version. Retrieval combines intent mapping and deterministic keyword matching.

Embeddings, FAISS, Chroma, Elasticsearch and a reranker remain out of scope until a frozen
retrieval test set shows that deterministic retrieval misses the required recall target.

## Model integration decision

Each provider gets a small adapter implementing the same `ModelGateway` interface. Official
SDK types stop at the adapter boundary. The domain layer only handles internal request and
result models.

vLLM is the preferred local serving experiment because its official documentation exposes
OpenAI-compatible APIs and structured tool calling. Schema-constrained output guarantees
parseability, not semantic correctness, so model/tool quality still requires evaluation:
<https://docs.vllm.ai/en/latest/serving/openai_compatible_server/> and
<https://docs.vllm.ai/en/stable/features/tool_calling/>.

## Dependency management decision

`uv` will manage the environment and lockfile. CI and release checks use locked mode so an
out-of-date lockfile fails instead of silently changing dependencies. The official uv
documentation describes lock and sync behavior at
<https://docs.astral.sh/uv/concepts/projects/sync/>.

## Explicit non-selections

| Technology | Decision | Reason |
|---|---|---|
| LangChain or a large agent framework | Not in MVP | Hides control flow and adds abstractions not required by six tools |
| LLM-generated SQL | Rejected | Unnecessary correctness and security risk for fixed schemas |
| Vector database | Deferred | Policy corpus is too small and structured |
| Redis | Deferred | Session persistence and distributed locks are not MVP requirements |
| PostgreSQL | Deferred | SQLite is sufficient for the provided workload |
| Hosted conversation or vector state | Rejected for restricted data | Conflicts with privacy and retention boundaries |
| Provider voting on every request | Rejected | Multiplies cost without guaranteeing factual correctness |
| Fine-tuning | Rejected for MVP | Retrieval, prompts and tools must be evaluated first |

## Versioning policy

- Pin exact direct and transitive dependencies in `uv.lock`.
- Record the Python version in `.python-version`.
- Record exact model IDs or snapshots in every evaluation report.
- Treat changes to prompts, tool schemas, policy corpus, router or model as versioned
  behavior changes requiring regression evaluation.
- Review provider data controls and local-model licenses before every production release.

