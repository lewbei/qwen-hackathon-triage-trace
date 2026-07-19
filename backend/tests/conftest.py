import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.main import app
from backend.app.models import Base, MemoryRecord, get_db
from backend.app.qwen import qwen

TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/triagetrace"


def _make_session_maker():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    engine, maker = _make_session_maker()

    async def _override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(delete(MemoryRecord))
    yield
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session():
    engine, maker = _make_session_maker()
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def mock_qwen_embed(monkeypatch):
    """Prevent tests from calling the real Qwen embedding endpoint."""

    async def _fake_embed(texts: list[str], dimensions: int = 1536) -> list[list[float]]:
        return [[0.0] * dimensions for _ in texts]

    monkeypatch.setattr(qwen, "embed", _fake_embed)
