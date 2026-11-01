import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def test_schema_has_vector_and_fts(session):
    cols = await session.execute(
        text(
            "select column_name, data_type from information_schema.columns "
            "where table_name='chunks' and column_name in ('embedding','search_vector')"
        )
    )
    assert {r[0] for r in cols} == {"embedding", "search_vector"}


async def test_two_tenants_fixture(two_tenants):
    a, b = two_tenants
    assert a != b
