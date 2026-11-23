"""FastAPI application. Run: uvicorn tt.main:app"""
from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from sqlalchemy import select

from tt.adapters.base import BrokerAdapter, split_instrument_id
from tt.api import admin, auth, market
from tt.api import trading as trading_api
from tt.config import Settings, get_settings
from tt.core.errors import install_error_handlers
from tt.core.security import Vault
from tt.db.models import Instrument
from tt.db.session import Database
from tt.marketdata.hub import Hub
from tt.marketdata.service import MarketDataService
from tt.trading.engine import TradingEngine

log = logging.getLogger("tt")

# Phase 1 instrument catalogue. Phase 2 loads the full lists from Kraken and IBKR.
KNOWN_INSTRUMENTS = {
    "kraken:BTC/EUR": dict(name="Bitcoin / Euro", currency="EUR", asset_class="crypto"),
    "kraken:ETH/EUR": dict(name="Ethereum / Euro", currency="EUR", asset_class="crypto"),
}


def default_adapter_factory(broker: str) -> BrokerAdapter:
    if broker == "kraken":
        from tt.adapters.kraken import KrakenAdapter
        return KrakenAdapter()
    raise ValueError(f"no market data adapter for {broker} in phase 1")


async def seed_instruments(db: Database, ids: list[str]) -> None:
    async with db.sessionmaker() as s:
        existing = set((await s.execute(select(Instrument.id))).scalars())
        for iid in ids:
            if iid in existing:
                continue
            broker, symbol = split_instrument_id(iid)
            meta = KNOWN_INSTRUMENTS.get(iid, dict(name=symbol, currency=symbol.split("/")[-1],
                                                   asset_class="crypto"))
            s.add(Instrument(id=iid, broker=broker, symbol=symbol, **meta))
        await s.commit()


def create_app(settings: Settings | None = None,
               adapter_factory: Callable[[str], BrokerAdapter] = default_adapter_factory) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings.database_url)
        app.state.db = db
        if settings.auto_create_schema:
            await db.create_all()
        await seed_instruments(db, settings.tracked_symbols)
        trading = TradingEngine(db.sessionmaker, app.state.hub, settings.paper_starting_cash,
                                settings.paper_fee_rate, settings.stale_after_seconds_crypto)
        await trading.start()
        app.state.trading = trading
        app.state.marketdata = None
        adapters: list[BrokerAdapter] = []
        if settings.marketdata_enabled:
            brokers = {split_instrument_id(i)[0] for i in settings.tracked_symbols}
            if brokers != {"kraken"}:
                raise RuntimeError("phase 1 streams Kraken symbols only")
            adapter = adapter_factory("kraken")
            adapters.append(adapter)
            md = MarketDataService(
                adapter, db.sessionmaker, app.state.hub, settings.tracked_symbols,
                stale_after_s=settings.stale_after_seconds_crypto,
                candle_publish_interval_ms=settings.candle_publish_interval_ms,
                backfill_minutes=settings.backfill_on_start_minutes,
            )
            md.quote_listeners.append(trading.on_quote)
            app.state.marketdata = md
            await md.start()
        try:
            yield
        finally:
            if app.state.marketdata:
                await app.state.marketdata.stop()
            for a in adapters:
                await a.close()
            await db.dispose()

    app = FastAPI(title="Trading Terminal", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.vault = Vault(settings.master_key)
    app.state.hub = Hub()
    install_error_handlers(app)

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth.router)
    api.include_router(auth.me_router)
    api.include_router(admin.router)
    api.include_router(market.router)
    api.include_router(trading_api.router)
    app.include_router(api)
    app.include_router(market.ws_router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz():
        return {"ok": True}

    return app


def __getattr__(name: str):
    # `uvicorn tt.main:app` builds the app lazily so importing this module needs no env vars.
    if name == "app":
        logging.basicConfig(level=logging.INFO)
        return create_app()
    raise AttributeError(name)
