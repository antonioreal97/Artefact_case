"""Create the seed tenant and a credential: uv run python -m scripts.create_tenant.

Only a newly generated credential is printed, after commit, directly to stdout.
An environment-supplied credential is never echoed. Reruns keep the existing key.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_engine, get_sessionmaker
from app.core.security import generate_api_key, hash_api_key
from app.models import AgentSettings, ApiKey, Tenant, TenantChannel

TENANT_SLUG = "emporio-da-musica"
SCOPES = ["chat", "knowledge:write", "knowledge:read", "handoffs", "conversations:read"]
# Both conflicting published phones/emails are retained, not silently reconciled.
# Source: políticas_da_loja.pdf cover, §1.2 and §7. Include extraction variants.
PUBLIC_CONTACT_VALUES = [
    "(67) 3341-4444",
    "(67) 3341 - 4444",
    "(67) 3321-4500",
    "contato@emporiodamusica.com.br",
    "contato@empóriodamusica.com.br",
    "Rua 14 de Maio, 3200",
    "Rua 14 de Maio, 3200 — Centro",
    "Rua 14 de Maio, 3200 — Centro, Campo Grande - MS, 79202-333",
    "79202-333",
    "12.345.678/0001-90",
    "BR4K7M2X9P1BR",  # Published tracking-format example, not a customer order.
]


def api_key_from_env(variable: str | None) -> str | None:
    if variable is None:
        return None
    value = os.environ.get(variable)
    if not value:
        raise ValueError("The requested API-key environment variable is empty or unset")
    if re.fullmatch(r"emp_[a-z0-9]{8}_[A-Za-z0-9_-]{32,128}", value) is None:
        raise ValueError("The API key must use emp_<8 lowercase alphanumeric>_<secret>")
    return value


async def create_tenant(
    session: AsyncSession,
    *,
    api_key: str | None = None,
    whatsapp_phone_number_id: str | None = None,
) -> tuple[uuid.UUID, str | None]:
    """Stage idempotent seed configuration. Caller commits before revealing a new key."""
    await session.execute(
        insert(Tenant)
        .values(slug=TENANT_SLUG, name="Empório da Música", status="active")
        .on_conflict_do_nothing(index_elements=["slug"])
    )
    tenant = await session.scalar(
        select(Tenant).where(Tenant.slug == TENANT_SLUG).with_for_update()
    )
    assert tenant is not None
    config = await session.scalar(select(AgentSettings).where(AgentSettings.tenant_id == tenant.id))
    if config is None:
        session.add(
            AgentSettings(
                tenant_id=tenant.id,
                assistant_name="Assistente Empório da Música",
                business_description=(
                    "Empório da Música, em Campo Grande-MS. Especializada exclusivamente em "
                    "instrumentos musicais; não comercializa acessórios. Sua música começa aqui."
                ),
                tone_of_voice="Informal, profissional e acolhedor, em português brasileiro.",
                default_language="pt-BR",
                timezone="America/Campo_Grande",
                handoff_message="Vou encaminhar você para nossa equipe. Sua música começa aqui.",
                public_contact_values=PUBLIC_CONTACT_VALUES,
                extra={"tagline": "Sua música começa aqui."},
            )
        )
    existing_key = await session.scalar(
        select(ApiKey).where(ApiKey.tenant_id == tenant.id, ApiKey.name == "seed")
    )
    plaintext: str | None = None
    if existing_key is None:
        if api_key is None:
            full_key, prefix = generate_api_key()
            plaintext = full_key
        else:
            if re.fullmatch(r"emp_[a-z0-9]{8}_[A-Za-z0-9_-]{32,128}", api_key) is None:
                raise ValueError("Invalid seed API-key format")
            full_key, prefix = api_key, api_key.split("_", 2)[1]
        session.add(
            ApiKey(
                tenant_id=tenant.id,
                name="seed",
                key_prefix=prefix,
                key_hash=hash_api_key(full_key),
                scopes=SCOPES,
            )
        )
    elif api_key is not None and existing_key.key_hash != hash_api_key(api_key):
        raise ValueError("A different seed key already exists; rotate credentials explicitly")
    if whatsapp_phone_number_id:
        channel = await session.scalar(
            select(TenantChannel).where(
                TenantChannel.tenant_id == tenant.id,
                TenantChannel.channel == "whatsapp",
                TenantChannel.external_id == whatsapp_phone_number_id,
            )
        )
        if channel is None:
            session.add(
                TenantChannel(
                    tenant_id=tenant.id,
                    channel="whatsapp",
                    external_id=whatsapp_phone_number_id,
                )
            )
    await session.flush()
    return tenant.id, plaintext


async def run(api_key_variable: str | None) -> None:
    key = api_key_from_env(api_key_variable)
    try:
        async with get_sessionmaker()() as session:
            _, plaintext = await create_tenant(
                session,
                api_key=key,
                whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID"),
            )
            await session.commit()
        if plaintext:
            print(plaintext)
        print("Tenant configuration ready.", file=sys.stderr)
    finally:
        await get_engine().dispose()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key-from-env", metavar="VARIABLE")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.api_key_from_env))
    except Exception as exc:
        # Database exceptions can include secrets and customer parameters.
        print(f"Tenant setup failed ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
