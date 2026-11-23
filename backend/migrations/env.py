import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from tt.db.models import Base

target_metadata = Base.metadata
url = os.environ.get("TT_DATABASE_URL", "sqlite+aiosqlite:///./tt.db")


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(_run)
        await conn.commit()
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())
