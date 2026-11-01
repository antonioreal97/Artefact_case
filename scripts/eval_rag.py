"""RAG/agent evaluation harness: uv run python -m scripts.eval_rag [--live] [--json-out PATH].

Creates a throw-away database on the compose Postgres, seeds the "emporio" tenant (reusing
scripts.create_tenant / scripts.commerce / scripts.seed) and a test-only "synthetic" tenant,
then runs every case in evals/rag_eval_cases.jsonl through (a) app.rag.retrieval.retrieve and
(b) one full ChatService.handle_message turn. Every payload handed to the model provider is
captured and checked for raw personal data. The database is always dropped at the end.

Default mode uses the deterministic providers (rule-based fake LLM + hash embeddings), so the
numbers measure the pipeline (retrieval, tools, guardrails, citations, privacy), NOT language
quality. --live uses the OpenAI providers and requires OPENAI_API_KEY.

Exit codes: 0 all hard gates pass, 1 a hard gate failed, 2 environment/setup problem.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents import Model, ModelProvider, ModelSettings
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import ModelTracing
from agents.tool import Tool
from openai.types.responses.response_prompt_param import ResponsePromptParam
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "rag_eval_cases.jsonl"
SYNTHETIC_DOC = ROOT / "evals" / "synthetic_tenant" / "politicas_loja_sintetica.md"
SYNTHETIC_TITLE = "Loja Sintética — Políticas"
DEFAULT_JSON_OUT = ROOT / "evals" / "results" / "latest.json"
ADMIN_URL_DEFAULT = "postgresql+asyncpg://emporio:emporio@localhost:5434/postgres"
DISCLAIMER = (
    "Deterministic mode: the fake LLM is rule-based; quality numbers measure the pipeline "
    "(retrieval, tools, guardrails, citations, privacy), NOT language quality."
)
LIVE_NOTE = "Live mode: OpenAI chat + embedding providers (results are non-deterministic)."
# Phrases that count as an explicit "I could not find it" answer.
NO_ANSWER_MARKERS = ("não encontrei", "nao encontrei", "não consegui confirmar")
# Independent egress patterns (the gateway has its own; the eval must not grade itself).
EGRESS_PATTERNS = {
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "tracking": re.compile(r"(?i)\bBR[A-Z0-9]{9}BR\b"),
    "phone": re.compile(r"(?<![\w.])(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[-\s]?\d{4}(?![\w.])"),
    "order_id": re.compile(r"(?i)\b(?:pedido|order)\s*(?:n[ºo°.]*\s*)?#?\s*\d+\b"),
}
TOKEN_RE = re.compile(r"\[\[[A-Z]+_\d+\]\]")


class SetupError(RuntimeError):
    """Environment problem (database unreachable, missing key); message is user-safe."""


# --------------------------------------------------------------------------------------
# Payload capture: sits inside ChatService's privacy guard, so it sees exactly what egresses.
# --------------------------------------------------------------------------------------
def _jsonable(value: object) -> Any:
    def default(obj: object) -> object:
        dump = getattr(obj, "model_dump", None)
        return dump(mode="json") if callable(dump) else str(obj)

    return json.loads(json.dumps(value, default=default, ensure_ascii=False))


class RecordingModel(Model):
    def __init__(self, inner: Model, sink: list[dict[str, Any]]) -> None:
        self.inner = inner
        self.sink = sink

    def _record(
        self, system_instructions: str | None, input: str | list[TResponseInputItem]
    ) -> None:
        self.sink.append({"instructions": system_instructions, "input": _jsonable(input)})

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        self._record(system_instructions, input)
        return await self.inner.get_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        self._record(system_instructions, input)
        async for event in self.inner.stream_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        ):
            yield event

    async def close(self) -> None:
        await self.inner.close()


class RecordingProvider(ModelProvider):
    def __init__(self, inner: ModelProvider, sink: list[dict[str, Any]]) -> None:
        self.inner = inner
        self.sink = sink

    def get_model(self, model_name: str | None) -> Model:
        return RecordingModel(self.inner.get_model(model_name), self.sink)

    async def aclose(self) -> None:
        await self.inner.aclose()


# --------------------------------------------------------------------------------------
# Environment, database lifecycle and seeding
# --------------------------------------------------------------------------------------
@contextmanager
def mode_environment(live: bool) -> Iterator[None]:
    """Select providers via env (Settings is env-driven), restoring everything afterwards."""
    from app.core.config import get_settings

    wanted = {
        "APP_ENV": "dev" if live else "test",
        "LLM_PROVIDER": "openai" if live else "fake",
        "EMBEDDING_PROVIDER": "openai" if live else "hash",
        "INGESTION_MODE": "inline",
    }
    saved = {key: os.environ.get(key) for key in wanted}
    os.environ.update(wanted)
    get_settings.cache_clear()
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


async def _create_database(admin_url: str, name: str) -> str:
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:
        raise SetupError(
            f"Postgres not reachable at the admin URL ({type(exc).__name__}). "
            "Run `docker compose up -d db` or set TEST_DATABASE_ADMIN_URL."
        ) from None
    finally:
        await admin.dispose()
    return admin_url.rsplit("/", 1)[0] + f"/{name}"


async def _drop_database(admin_url: str, name: str) -> None:
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        await admin.dispose()


@dataclass
class TenantInfo:
    key: str
    tenant_id: uuid.UUID
    document_ids: set[uuid.UUID]
    titles: set[str]
    public_contact_values: list[str]


async def _tenant_info(session: AsyncSession, key: str, tenant_id: uuid.UUID) -> TenantInfo:
    from app.models import AgentSettings, Document

    rows = (
        await session.execute(
            select(Document.id, Document.title).where(Document.tenant_id == tenant_id)
        )
    ).all()
    contacts = await session.scalar(
        select(AgentSettings.public_contact_values).where(AgentSettings.tenant_id == tenant_id)
    )
    return TenantInfo(
        key=key,
        tenant_id=tenant_id,
        document_ids={row.id for row in rows},
        titles={row.title for row in rows},
        public_contact_values=list(contacts or []),
    )


async def seed_tenants(factory: async_sessionmaker[AsyncSession]) -> dict[str, TenantInfo]:
    from app.core.config import get_settings
    from app.models import AgentSettings, DocumentVersion, Tenant
    from app.rag.embeddings import get_embedding_provider
    from app.rag.ingestion import create_document_version, process_document_version
    from scripts.commerce import DATA_DIR, import_commerce, load_commerce
    from scripts.create_tenant import create_tenant
    from scripts.seed import ingest_policy

    exports = await asyncio.to_thread(load_commerce, DATA_DIR)
    policy = DATA_DIR / "políticas_da_loja.pdf"
    policy_bytes = await asyncio.to_thread(policy.read_bytes)
    async with factory() as session:
        # The generated seed API key is discarded: it lives only in the throw-away DB.
        emporio_id, _ = await create_tenant(session)
        await import_commerce(session, tenant_id=emporio_id, exports=exports)
        await session.commit()
        await ingest_policy(session, tenant_id=emporio_id, data=policy_bytes, filename=policy.name)

    async with factory() as session:
        synthetic = Tenant(slug="synthetic-eval", name="Loja Sintética de Testes")
        session.add(synthetic)
        await session.flush()
        session.add(
            AgentSettings(
                tenant_id=synthetic.id,
                assistant_name="Assistente Sintético",
                business_description="Loja fictícia usada apenas na avaliação de isolamento.",
                public_contact_values=[],
            )
        )
        await session.commit()
        receipt = await create_document_version(
            session,
            tenant_id=synthetic.id,
            title=SYNTHETIC_TITLE,
            data=await asyncio.to_thread(SYNTHETIC_DOC.read_bytes),
            filename=SYNTHETIC_DOC.name,
            source_type="md",
            category="politicas",
        )
        await process_document_version(
            session,
            tenant_id=synthetic.id,
            version_id=receipt.version_id,
            embedder=get_embedding_provider(get_settings()),
        )
        status = await session.scalar(
            select(DocumentVersion.ingestion_status).where(
                DocumentVersion.tenant_id == synthetic.id,
                DocumentVersion.id == receipt.version_id,
            )
        )
        if status != "completed":
            raise SetupError("Synthetic tenant document ingestion did not complete")
        return {
            "emporio": await _tenant_info(session, "emporio", emporio_id),
            "synthetic": await _tenant_info(session, "synthetic", synthetic.id),
        }


# --------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------
@dataclass
class CaseResult:
    id: str
    kind: str
    tenant: str
    # Metric value: True pass / False fail / None not applicable to this case kind.
    source_hit: bool | None = None
    tenant_leakage: int = 0
    citation_coverage: bool | None = None
    no_answer_safety: bool | None = None
    groundedness: bool | None = None
    tool_use: bool | None = None
    handoff_reason: bool | None = None
    privacy_violations: list[str] = field(default_factory=list)
    # Observations (redacted) for diagnosis.
    retrieved_sections: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    observed_handoff: str | None = None
    grounded: bool | None = None
    sources: list[str] = field(default_factory=list)
    model_payloads: int = 0
    reply_excerpt: str = ""
    failures: list[str] = field(default_factory=list)
    error: str | None = None


def _section_matches(section: str | None, expected: str) -> bool:
    """'3' matches '3. Pagamento' and '3.1 Parcelamento' but not '30' or '13'."""
    if not section:
        return False
    label = section.lstrip("§ ").strip()
    return label.startswith(expected) and not label[len(expected) : len(expected) + 1].isdigit()


def _source_hit(chunks: list[Any], case: dict[str, Any]) -> bool:
    sections = case.get("expected_sections") or []
    pages = set(case.get("expected_pages") or [])
    for chunk in chunks:
        if any(_section_matches(chunk.section_title, expected) for expected in sections):
            return True
        start, end = chunk.page_start, chunk.page_end or chunk.page_start
        if start is not None and any(start <= page <= end for page in pages):
            return True
    return False


def _strip_public(payload: str, public_values: Iterable[str]) -> str:
    result = TOKEN_RE.sub("", payload)
    for value in sorted({v for v in public_values if v.strip()}, key=len, reverse=True):
        pattern = r"\s*".join(re.escape(c) for c in value if not c.isspace())
        result = re.sub(pattern, "", result, flags=re.IGNORECASE)
    return result


def privacy_violations(
    payloads: list[dict[str, Any]],
    restricted_values: Iterable[str],
    public_values: Iterable[str],
) -> list[str]:
    """Kinds of raw restricted data found in model payloads (never the values themselves)."""
    from app.services.privacy import PrivacyBoundaryError, scan_outbound

    found: list[str] = []
    public = list(public_values)
    for index, payload in enumerate(payloads):
        serialized = json.dumps(payload, ensure_ascii=False)
        # JSON-encoded tool outputs inside the payload are double-escaped; decode once more.
        flat = serialized.encode().decode("unicode_escape", errors="ignore")
        for value in restricted_values:
            if value.casefold() in serialized.casefold() or value.casefold() in flat.casefold():
                found.append(f"payload{index}:restricted_value")
        cleaned = _strip_public(serialized, public)
        for kind, pattern in EGRESS_PATTERNS.items():
            if pattern.search(cleaned):
                found.append(f"payload{index}:{kind}")
        try:
            scan_outbound(payload, allowlist=public)
        except PrivacyBoundaryError:
            found.append(f"payload{index}:gateway_scanner")
    return sorted(set(found))


def _contains(haystack: str, needle: str) -> bool:
    return needle.casefold() in haystack.casefold()


def _redact(value: str, restricted: Iterable[str]) -> str:
    from emporio_agent.privacy import redact_for_log

    redacted = str(redact_for_log(value, known_restricted_values=restricted))
    return " ".join(redacted.split())[:160]


# --------------------------------------------------------------------------------------
# Case execution
# --------------------------------------------------------------------------------------
RETRIEVAL_KINDS = {"rag", "no_answer", "isolation", "injection"}
FAILURE_HANDOFFS = {"no_information", "low_confidence", "tool_failure", "privacy_guard"}


@contextmanager
def capture_chat_retrievals(sink: list[Any]) -> Iterator[None]:
    """Record chunks the agent's search_knowledge tool retrieves (module attribute hook)."""
    from app.rag import retrieval

    original = retrieval.retrieve

    async def recording(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        sink.extend(result.chunks)
        return result

    retrieval.retrieve = recording  # type: ignore[assignment]
    try:
        yield
    finally:
        retrieval.retrieve = original


async def run_case(
    case: dict[str, Any],
    tenants: dict[str, TenantInfo],
    factory: async_sessionmaker[AsyncSession],
    embedder: Any,
) -> CaseResult:
    from app.core.config import get_settings
    from app.rag.retrieval import retrieve
    from app.services.chat import ChatService
    from app.services.model_gateway import build_model_provider
    from app.services.vault_store import InMemoryVaultStore

    settings = get_settings()
    kind = case["kind"]
    tenant = tenants[case["tenant"]]
    result = CaseResult(id=case["id"], kind=kind, tenant=case["tenant"])
    forbidden_keys = case.get("forbidden_tenant_sources") or [
        key for key in tenants if key != tenant.key
    ]
    forbidden = [tenants[key] for key in forbidden_keys]
    forbidden_docs = set().union(*(t.document_ids for t in forbidden))
    restricted = list(case.get("restricted_values") or [])

    # (a) retrieval layer, exactly as the agent tool calls it.
    if kind in RETRIEVAL_KINDS:
        async with factory() as session:
            found = await retrieve(
                session, tenant_id=tenant.tenant_id, query=case["question"], embedder=embedder
            )
        result.retrieved_sections = [
            f"{c.section_title or '-'}@p{c.page_start}" for c in found.chunks
        ]
        result.tenant_leakage += sum(c.document_id in forbidden_docs for c in found.chunks)
        if kind == "rag":
            result.source_hit = _source_hit(found.chunks, case)

    # (b) one full chat turn on a fresh conversation and vault.
    payloads: list[dict[str, Any]] = []
    chat_chunks: list[Any] = []

    def provider_factory(s: Any, allowlist: Iterable[str]) -> ModelProvider:
        return RecordingProvider(build_model_provider(s, allowlist), payloads)

    service = ChatService(factory, settings, InMemoryVaultStore(), provider_factory)
    with capture_chat_retrievals(chat_chunks):
        async with factory() as session:
            reply = await service.handle_message(
                session,
                tenant_id=tenant.tenant_id,
                conversation_id=None,
                text=case["question"],
                correlation_id=f"eval-{case['id']}",
                channel="api",
            )
    answer = reply.reply
    result.tools_used = reply.tools_used
    result.observed_handoff = reply.handoff.reason if reply.handoff else None
    result.grounded = reply.grounded
    result.sources = [f"{s.label}:{s.section or '-'}@p{s.page}" for s in reply.sources]
    result.model_payloads = len(payloads)
    result.reply_excerpt = _redact(answer, restricted)

    # tenant_leakage: forbidden chunks retrieved in-chat, forbidden sources, forbidden titles.
    result.tenant_leakage += sum(c.document_id in forbidden_docs for c in chat_chunks)
    result.tenant_leakage += sum(s.document_id in forbidden_docs for s in reply.sources)
    forbidden_titles = set().union(*(t.titles for t in forbidden)) - tenant.titles
    result.tenant_leakage += sum(_contains(answer, title) for title in forbidden_titles)
    if result.tenant_leakage:
        result.failures.append(f"tenant_leakage={result.tenant_leakage}")

    if kind == "rag":
        result.citation_coverage = bool(reply.sources) and any(
            f"[{s.label}]" in answer for s in reply.sources
        )
    if kind == "no_answer":
        explicit = any(marker in answer.casefold() for marker in NO_ANSWER_MARKERS)
        result.no_answer_safety = (reply.handoff is not None or explicit) and not reply.sources

    # groundedness: content expectations + grounded flag consistency.
    must = case.get("answer_must_include") or []
    must_not = case.get("answer_must_not_include") or []
    missing = [m for m in must if not _contains(answer, m)]
    present = [m for m in must_not if _contains(answer, m)]
    consistent = True
    if reply.sources and not reply.grounded:
        consistent = False
    if result.observed_handoff in FAILURE_HANDOFFS and reply.grounded:
        consistent = False
    if kind == "rag" and not reply.grounded:
        consistent = False
    if must or must_not or kind == "rag":
        result.groundedness = not missing and not present and consistent
    for item in missing:
        result.failures.append(f"missing {item!r}")
    for item in present:
        result.failures.append(f"forbidden text present {item!r}")
    if not consistent:
        result.failures.append(f"grounded={reply.grounded} inconsistent")

    if kind == "tool":
        expected_tools = set(case.get("expected_tools") or [])
        result.tool_use = expected_tools <= set(reply.tools_used) and not reply.sources
        if not result.tool_use:
            result.failures.append(f"tools={reply.tools_used} sources={len(reply.sources)}")
    if kind == "handoff":
        result.handoff_reason = result.observed_handoff == case.get("expected_handoff_reason")
        if not result.handoff_reason:
            result.failures.append(
                f"handoff={result.observed_handoff} expected={case.get('expected_handoff_reason')}"
            )

    public = tenant.public_contact_values
    result.privacy_violations = privacy_violations(payloads, restricted, public)
    if result.privacy_violations:
        result.failures.append("privacy:" + ",".join(result.privacy_violations))
    for label, ok in [
        ("source_hit", result.source_hit),
        ("citation", result.citation_coverage),
        ("no_answer", result.no_answer_safety),
    ]:
        if ok is False:
            result.failures.append(f"{label} failed")
    return result


# --------------------------------------------------------------------------------------
# Aggregation and reporting
# --------------------------------------------------------------------------------------
METRICS = [
    ("source_hit", "rag: expected section prefix or page among top-k retrieved chunks"),
    ("citation_coverage", "rag: >=1 SourceRef whose [label] appears in the reply"),
    ("no_answer_safety", "no_answer: handoff or explicit 'não encontrei' and zero sources"),
    (
        "groundedness",
        "must_include all present, must_not_include absent, grounded flag consistent",
    ),
    ("tool_use", "tool: expected_tools ⊆ tools_used and no RAG source in the reply"),
    ("handoff_reason", "handoff: ChatReply.handoff.reason == expected_handoff_reason"),
]


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name, rule in METRICS:
        values = [getattr(r, name) for r in results if getattr(r, name) is not None]
        passed = sum(bool(v) for v in values)
        summary[name] = {
            "passed": passed,
            "applicable": len(values),
            "rate": round(passed / len(values), 3) if values else None,
            "rule": rule,
        }
    leakage = sum(r.tenant_leakage for r in results)
    violations = sum(len(r.privacy_violations) for r in results)
    errors = [r.id for r in results if r.error]
    summary["tenant_leakage"] = {"total": leakage, "rule": "must be 0 (hard gate)"}
    summary["privacy"] = {
        "violations": violations,
        "cases_checked": sum(r.error is None for r in results),
        "rule": "no restricted value / raw email|phone|order-id|tracking in any model payload "
        "(hard gate)",
    }
    summary["gates"] = {
        "tenant_leakage": leakage == 0,
        "privacy": violations == 0,
        # A crashed case could not be verified, so it fails closed.
        "harness_errors": not errors,
    }
    summary["errored_cases"] = errors
    return summary


def _mark(value: bool | None) -> str:
    return "–" if value is None else ("✓" if value else "✗")


def render_table(report: dict[str, Any]) -> str:
    header = (
        f"{'case':<28} {'kind':<9} {'hit@k':^5} {'leak':^4} {'cite':^4} {'noans':^5} "
        f"{'grnd':^4} {'tools':^5} {'hoff':^4} {'priv':^4}  notes"
    )
    lines = [
        f"RAG/agent evaluation — mode: {report['mode']} — k={report['k']}",
        report["note"],
        "",
        header,
        "-" * len(header),
    ]
    for r in report["cases"]:
        notes = r["error"] or "; ".join(r["failures"])
        if not notes:
            notes = f"handoff={r['observed_handoff']}" if r["observed_handoff"] else "ok"
        lines.append(
            f"{r['id']:<28} {r['kind']:<9} {_mark(r['source_hit']):^5} "
            f"{r['tenant_leakage']:^4} {_mark(r['citation_coverage']):^4} "
            f"{_mark(r['no_answer_safety']):^5} {_mark(r['groundedness']):^4} "
            f"{_mark(r['tool_use']):^5} {_mark(r['handoff_reason']):^4} "
            f"{_mark(None if r['error'] else not r['privacy_violations']):^4}  {notes}"
        )
    agg = report["aggregate"]
    lines += ["", "Aggregate (soft metrics):"]
    for name, rule in METRICS:
        m = agg[name]
        rate = "n/a" if m["rate"] is None else f"{m['rate'] * 100:.0f}%"
        lines.append(f"  {name:<18} {m['passed']:>2}/{m['applicable']:<2} {rate:>5}  ({rule})")
    lines += [
        "",
        "Hard gates:",
        f"  tenant_leakage   total={agg['tenant_leakage']['total']}  "
        + ("PASS" if agg["gates"]["tenant_leakage"] else "FAIL"),
        f"  privacy          violations={agg['privacy']['violations']} over "
        f"{agg['privacy']['cases_checked']} cases  "
        + ("PASS" if agg["gates"]["privacy"] else "FAIL"),
        f"  harness_errors   {len(agg['errored_cases'])}  "
        + ("PASS" if agg["gates"]["harness_errors"] else "FAIL"),
        "RESULT: " + ("ALL HARD GATES PASS" if report["passed"] else "HARD GATE FAILURE"),
    ]
    return "\n".join(lines)


def load_cases(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


async def run_eval(
    *,
    live: bool = False,
    case_ids: set[str] | None = None,
    admin_url: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the harness and return the report dict. Raises SetupError for environment issues."""
    import app.models  # noqa: F401  (register all tables)
    from app.core.config import get_settings
    from app.core.database import Base
    from app.rag.embeddings import get_embedding_provider

    cases = [c for c in load_cases() if case_ids is None or c["id"] in case_ids]
    admin = admin_url or os.environ.get("TEST_DATABASE_ADMIN_URL", ADMIN_URL_DEFAULT)
    with mode_environment(live):
        settings = get_settings()
        if live and not (
            settings.openai_api_key and settings.openai_api_key.get_secret_value().strip()
        ):
            raise SetupError("--live requires OPENAI_API_KEY (environment or .env).")
        name = f"eval_{uuid.uuid4().hex[:10]}"
        url = await _create_database(admin, name)
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            tenants = await seed_tenants(factory)
            embedder = get_embedding_provider(settings)
            results: list[CaseResult] = []
            for case in cases:
                try:
                    results.append(await run_case(case, tenants, factory, embedder))
                except Exception as exc:
                    # Exception text can carry DB parameters; report only the type.
                    results.append(
                        CaseResult(
                            id=case["id"],
                            kind=case["kind"],
                            tenant=case["tenant"],
                            error=f"error: {type(exc).__name__}",
                        )
                    )
                if progress:
                    progress(case["id"])
            k = settings.retrieval_top_k
        finally:
            await engine.dispose()
            await _drop_database(admin, name)
    agg = aggregate(results)
    return {
        "mode": "live (openai)" if live else "deterministic (fake LLM + hash embeddings)",
        "note": LIVE_NOTE if live else DISCLAIMER,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "k": k,
        "cases": [asdict(r) for r in results],
        "aggregate": agg,
        "passed": all(agg["gates"].values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--live", action="store_true", help="use OpenAI providers")
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--case", action="append", dest="cases", help="run only this case id")
    args = parser.parse_args(argv)
    if args.live:
        from dotenv import load_dotenv

        load_dotenv()
    from app.core.config import Settings
    from app.core.logging import configure_logging

    # Keep stdout for the report; app logs (already redacted) go to stderr, warnings only.
    configure_logging(Settings(app_env="test", log_level="WARNING"), stream=sys.stderr)
    try:
        report = asyncio.run(
            run_eval(live=args.live, case_ids=set(args.cases) if args.cases else None)
        )
    except SetupError as exc:
        print(f"Evaluation not run: {exc}", file=sys.stderr)
        return 2
    print(render_table(report))
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON report: {args.json_out}")
    return 0 if report["passed"] else 1


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
