"""Review M-7 / L-11: no customer data in the runtime image, data mounted read-only into the
seed/test services only, chat-only widget key, and the new settings documented."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import generate_api_key, hash_api_key
from app.models import ApiKey, Tenant
from scripts.create_tenant import CHAT_KEY_NAME, CHAT_SCOPES, SCOPES, create_tenant

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR_NAME = "Desafio Técnico AI Engineer 2026"


def test_dockerfile_does_not_copy_customer_data() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lines = dockerfile.splitlines()
    copies = [ln for ln in lines if ln.strip().upper().startswith(("COPY", "ADD"))]
    assert copies and not any(DATA_DIR_NAME in ln or "data" in ln.split()[1:] for ln in copies)
    assert not any(re.search(r"\bCOPY\s+\.\s", ln) for ln in copies)  # no whole-context copy


def test_dockerignore_excludes_data_dir() -> None:
    patterns = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert DATA_DIR_NAME in [p.strip() for p in patterns]


def test_only_seed_and_test_services_mount_data_read_only() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    mounts = {
        name: [v for v in svc.get("volumes", []) if DATA_DIR_NAME in str(v)]
        for name, svc in services.items()
    }
    assert {name for name, found in mounts.items() if found} == {"seed", "test"}
    for name in ("seed", "test"):
        assert services[name]["profiles"] == [name]
        (volume,) = mounts[name]
        assert volume["type"] == "bind" and volume["read_only"] is True
        assert volume["target"] == f"/app/{DATA_DIR_NAME}/data"  # scripts.commerce.DATA_DIR


def test_env_example_documents_new_settings() -> None:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, flags=re.MULTILINE))
    for name in (
        "USER_REF_KEY",
        "EXPOSE_API_DOCS",
        "DB_STATEMENT_TIMEOUT_MS",
        "ORDER_LOOKUP_MAX_FAILURES",
        "ORDER_LOOKUP_LOCKOUT_SECONDS",
        "CHAT_RATE_LIMIT_PER_MINUTE",
        "AUTH_FAILURE_LIMIT_PER_MINUTE",
        "RETENTION_DAYS",
        "SEED_CHAT_API_KEY",
    ):
        assert name in documented, name
        if name != "SEED_CHAT_API_KEY":
            assert name.lower() in Settings.model_fields, name


@pytest.mark.integration
async def test_seed_can_provision_chat_only_widget_key(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("scripts.create_tenant.TENANT_SLUG", f"seed-widget-{uuid.uuid4().hex[:8]}")
    admin, _ = generate_api_key()
    widget, _ = generate_api_key()
    tenant_id, plaintext = await create_tenant(session, api_key=admin, chat_api_key=widget)
    await session.commit()
    assert plaintext is None
    rows = await session.scalars(select(ApiKey).where(ApiKey.tenant_id == tenant_id))
    keys = {k.name: k for k in rows}
    assert keys["seed"].scopes == SCOPES
    assert keys[CHAT_KEY_NAME].scopes == CHAT_SCOPES == ["chat"]
    assert keys[CHAT_KEY_NAME].key_hash == hash_api_key(widget)
    # Idempotent; a different widget key is refused rather than silently rotated.
    assert await create_tenant(session, api_key=admin, chat_api_key=widget) == (tenant_id, None)
    other, _ = generate_api_key()
    with pytest.raises(ValueError, match="different widget key"):
        await create_tenant(session, api_key=admin, chat_api_key=other)
    await session.rollback()
    assert widget not in capsys.readouterr().out
    assert await session.scalar(select(Tenant.id).where(Tenant.id == tenant_id)) == tenant_id
