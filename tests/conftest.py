import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal

TEST_org_id = "test-tenant-00000000"


@pytest.fixture(scope="session")
def test_org_id() -> str:
    return TEST_org_id


@pytest.fixture
async def db_session(test_org_id: str) -> AsyncSession:
    """
    Test DB session fixture that replicates the production get_db pattern exactly.

    Issues SET LOCAL app.org_id inside a transaction, then rolls back after each
    test to keep tests isolated — no test data persists between tests.

    NOTE: This fixture requires a real PostgreSQL instance (not SQLite). RLS policies
    and current_setting() are PostgreSQL-specific. Tests that bypass this fixture and
    create sessions without SET LOCAL will silently bypass RLS, returning unfiltered
    data and masking real isolation bugs.

    TODO: Set TEST_DATABASE_URL in .env (or CI environment) and wire AsyncSessionLocal
    to it for test runs. Consider pytest-docker or a dedicated test schema.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SET LOCAL app.org_id = :tid"),
                {"tid": test_org_id},
            )
            yield session
            await session.rollback()
