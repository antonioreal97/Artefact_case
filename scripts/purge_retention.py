"""Run the retention purge once: uv run python -m scripts.purge_retention.

Deletes conversations (with their messages, sources, tool executions and handoffs) and audit
events older than RETENTION_DAYS, and empties the stored bytes of superseded document
versions. The arq worker runs the same purge daily; this is for manual or cron use.
Only aggregate counts are printed.
"""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from app.core.config import get_settings
from app.core.database import get_engine, get_sessionmaker
from app.services.retention import purge_expired


async def run() -> None:
    try:
        report = await purge_expired(get_sessionmaker(), get_settings())
    finally:
        await get_engine().dispose()
    print(
        f"Retention purge complete (cutoff {report.cutoff.isoformat()}): "
        f"tenants={report.tenants}, conversations={report.conversations}, "
        f"tool_executions={report.tool_executions}, audit_events={report.audit_events}, "
        f"raw_contents_cleared={report.raw_contents_cleared}"
    )


def main() -> None:
    load_dotenv()
    try:
        asyncio.run(run())
    except Exception as exc:
        # Database exceptions can include connection secrets or row values.
        print(f"Retention purge failed ({type(exc).__name__}).", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
