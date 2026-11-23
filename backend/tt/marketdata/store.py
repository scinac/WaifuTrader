"""Bar persistence: idempotent upsert on (instrument_id, interval, ts), and range queries."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tt.adapters.base import Candle
from tt.db.models import Bar


async def upsert_bars(session: AsyncSession, candles: list[Candle]) -> None:
    if not candles:
        return
    rows = [dict(instrument_id=c.instrument_id, interval=c.interval, ts=c.ts, open=c.open,
                 high=c.high, low=c.low, close=c.close, volume=c.volume) for c in candles]
    dialect = session.bind.dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(Bar).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "interval", "ts"],
        set_={k: stmt.excluded[k] for k in ("open", "high", "low", "close", "volume")},
    )
    await session.execute(stmt)


async def load_bars(session: AsyncSession, instrument_id: str, start: datetime, end: datetime,
                    limit: int) -> list[Candle]:
    rows = (await session.execute(
        select(Bar).where(Bar.instrument_id == instrument_id, Bar.interval == "1m",
                          Bar.ts >= start, Bar.ts < end)
        .order_by(Bar.ts.desc()).limit(limit)
    )).scalars().all()
    return [Candle(r.instrument_id, "1m", r.ts, r.open, r.high, r.low, r.close, r.volume)
            for r in reversed(rows)]
