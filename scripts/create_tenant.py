"""Create the seed tenant and a credential: uv run python -m scripts.create_tenant.

Only a newly generated credential is printed, after commit, directly to stdout.
An environment-supplied credential is never echoed. Reruns keep the existing key.

The ``seed`` key is an ADMIN key (all scopes) for operators; never embed it in a web
widget. Set ``SEED_CHAT_API_KEY`` to also provision a ``widget`` key that holds only the
``chat`` scope (review L-11); it is never generated or printed by this script.
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

WELCOME_MESSAGE = (
    "Oi! Aqui é o Empório da Música. Atendimento de segunda a sexta, das 9h às 18h, "
    "e sábado das 9h às 13h (domingo e feriado, fechado). "
    "Posso te ajudar com instrumentos, preços, estoque, pedidos e nossas políticas."
)
HANDOFF_MESSAGE = "Vou te passar pra nossa equipe."

TENANT_SLUG = "emporio-da-musica"
SCOPES = ["chat", "knowledge:write", "knowledge:read", "handoffs", "conversations:read"]
CHAT_SCOPES = ["chat"]
CHAT_KEY_NAME = "widget"
CHAT_KEY_ENV = "SEED_CHAT_API_KEY"
_KEY_FORMAT = r"emp_[a-z0-9]{8}_[A-Za-z0-9_-]{32,128}"
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
    if re.fullmatch(_KEY_FORMAT, value) is None:
        raise ValueError("The API key must use emp_<8 lowercase alphanumeric>_<secret>")
    return value


def optional_api_key_from_env(variable: str) -> str | None:
    """Like ``api_key_from_env`` but an unset/empty variable means "not requested"."""
    return api_key_from_env(variable) if os.environ.get(variable) else None


async def _ensure_key(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    name: str,
    scopes: list[str],
    api_key: str | None,
    generate: bool,
) -> str | None:
    """Create the named key once; returns the plaintext only when it was generated here."""
    existing = await session.scalar(
        select(ApiKey).where(ApiKey.tenant_id == tenant_id, ApiKey.name == name)
    )
    if existing is not None:
        if api_key is not None and existing.key_hash != hash_api_key(api_key):
            raise ValueError(
                f"A different {name} key already exists; rotate credentials explicitly"
            )
        return None
    if api_key is None:
        if not generate:
            return None
        full_key, prefix = generate_api_key()
        plaintext: str | None = full_key
    else:
        if re.fullmatch(_KEY_FORMAT, api_key) is None:
            raise ValueError(f"Invalid {name} API-key format")
        full_key, prefix, plaintext = api_key, api_key.split("_", 2)[1], None
    session.add(
        ApiKey(
            tenant_id=tenant_id,
            name=name,
            key_prefix=prefix,
            key_hash=hash_api_key(full_key),
            scopes=scopes,
        )
    )
    return plaintext


async def create_tenant(
    session: AsyncSession,
    *,
    api_key: str | None = None,
    whatsapp_phone_number_id: str | None = None,
    chat_api_key: str | None = None,
    refresh_welcome_and_handoff: bool = False,
) -> tuple[uuid.UUID, str | None]:
    """Stage idempotent seed configuration. Caller commits before revealing a new key.

    Existing welcome/handoff text is preserved unless ``refresh_welcome_and_handoff``
    is True (explicit operator opt-in) or welcome is empty.
    """
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
                welcome_message=WELCOME_MESSAGE,
                handoff_message=HANDOFF_MESSAGE,
                public_contact_values=PUBLIC_CONTACT_VALUES,
                extra={"tagline": "Sua música começa aqui."},
            )
        )
    else:
        # Existing seed tenants need newly documented public contacts too. Preserve
        # operator-added values and persona/model settings; never touch other tenants.
        if refresh_welcome_and_handoff or not (config.welcome_message or "").strip():
            config.welcome_message = WELCOME_MESSAGE
        if refresh_welcome_and_handoff or not (config.handoff_message or "").strip():
            config.handoff_message = HANDOFF_MESSAGE
        config.public_contact_values = list(
            dict.fromkeys([*(config.public_contact_values or []), *PUBLIC_CONTACT_VALUES])
        )
    plaintext = await _ensure_key(
        session, tenant.id, name="seed", scopes=SCOPES, api_key=api_key, generate=True
    )
    await _ensure_key(
        session,
        tenant.id,
        name=CHAT_KEY_NAME,
        scopes=CHAT_SCOPES,
        api_key=chat_api_key,
        generate=False,
    )
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


async def run(api_key_variable: str | None, *, refresh_welcome_and_handoff: bool = False) -> None:
    key = api_key_from_env(api_key_variable)
    try:
        async with get_sessionmaker()() as session:
            _, plaintext = await create_tenant(
                session,
                api_key=key,
                whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID"),
                chat_api_key=optional_api_key_from_env(CHAT_KEY_ENV),
                refresh_welcome_and_handoff=refresh_welcome_and_handoff,
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
    parser.add_argument(
        "--refresh-welcome-and-handoff",
        action="store_true",
        help=(
            "Overwrite Empório welcome_message and handoff_message with the seed defaults. "
            "Without this flag, existing operator-set text is preserved."
        ),
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            run(
                args.api_key_from_env,
                refresh_welcome_and_handoff=args.refresh_welcome_and_handoff,
            )
        )
    except Exception as exc:
        # Database exceptions can include secrets and customer parameters.
        print(f"Tenant setup failed ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
