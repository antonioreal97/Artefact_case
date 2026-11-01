"""Seed commerce and ingest policy knowledge: uv run python -m scripts.seed.

Use --api-key-from-env SEED_API_KEY for a known local development credential (admin key,
all scopes). If SEED_CHAT_API_KEY is set, a chat-only ``widget`` key is provisioned too.
The six CSVs are validated together before writes. Only aggregate counts are output.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_engine, get_sessionmaker
from app.models import Document, DocumentVersion
from app.rag.embeddings import get_embedding_provider
from app.rag.ingestion import create_document_version, process_document_version
from scripts.commerce import DATA_DIR, import_commerce, load_commerce
from scripts.create_tenant import (
    CHAT_KEY_ENV,
    api_key_from_env,
    create_tenant,
    optional_api_key_from_env,
)


async def ingest_policy(
    session: AsyncSession, *, tenant_id: uuid.UUID, data: bytes, filename: str
) -> None:
    document = await session.scalar(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.title == "Políticas da Loja",
            Document.category == "politicas",
        )
    )
    receipt = await create_document_version(
        session,
        tenant_id=tenant_id,
        title="Políticas da Loja",
        data=data,
        filename=filename,
        source_type="pdf",
        category="politicas",
        document_id=document.id if document else None,
    )
    if receipt.status != "completed":
        await process_document_version(
            session,
            tenant_id=tenant_id,
            version_id=receipt.version_id,
            embedder=get_embedding_provider(get_settings()),
        )
    status = await session.scalar(
        select(DocumentVersion.ingestion_status).where(
            DocumentVersion.tenant_id == tenant_id, DocumentVersion.id == receipt.version_id
        )
    )
    if status != "completed":
        raise RuntimeError("Policy ingestion did not complete; retry the seed after recovery")


async def seed(
    session: AsyncSession,
    *,
    data_dir: Path = DATA_DIR,
    api_key: str | None = None,
    whatsapp_phone_number_id: str | None = None,
    chat_api_key: str | None = None,
) -> tuple[uuid.UUID, str | None, dict[str, int]]:
    # File IO runs off the event loop and every CSV is validated before any write.
    exports = await asyncio.to_thread(load_commerce, data_dir)
    policy = data_dir / "políticas_da_loja.pdf"
    data = await asyncio.to_thread(policy.read_bytes)
    tenant_id, plaintext = await create_tenant(
        session,
        api_key=api_key,
        whatsapp_phone_number_id=whatsapp_phone_number_id,
        chat_api_key=chat_api_key,
    )
    counts = await import_commerce(session, tenant_id=tenant_id, exports=exports)
    await session.commit()
    # A key must be revealed before ingestion, which can fail after the key is committed.
    if plaintext:
        print(plaintext, flush=True)
    await ingest_policy(session, tenant_id=tenant_id, data=data, filename=policy.name)
    return tenant_id, plaintext, counts


async def run(data_dir: Path, api_key_variable: str | None) -> None:
    key = api_key_from_env(api_key_variable)
    chat_key = optional_api_key_from_env(CHAT_KEY_ENV)
    try:
        async with get_sessionmaker()() as session:
            _, _, counts = await seed(
                session,
                data_dir=data_dir,
                api_key=key,
                whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID"),
                chat_api_key=chat_key,
            )
        print("Seed complete: " + ", ".join(f"{name}={n}" for name, n in counts.items()))
    finally:
        await get_engine().dispose()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--api-key-from-env", metavar="VARIABLE")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.data_dir, args.api_key_from_env))
    except Exception as exc:
        print(f"Seed failed ({type(exc).__name__}); no customer data logged.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
