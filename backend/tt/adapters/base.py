"""Broker adapter interface (FSD: Markets and broker integration).

Every broker (Kraken, IBKR, ...) implements BrokerAdapter. The engine, market data
service and API only talk to this interface. All prices and quantities are Decimal.
Phase 1 implements market data; trading methods are defined for later phases.
"""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit", "take_profit", "bracket", "oco",
                    "trailing_stop"]
OrderStatus = Literal["created", "risk_checked", "sent", "accepted", "partially_filled", "filled",
                      "rejected", "cancelled", "expired"]


def instrument_id(broker: str, symbol: str) -> str:
    return f"{broker}:{symbol}"


def split_instrument_id(iid: str) -> tuple[str, str]:
    broker, _, symbol = iid.partition(":")
    if not broker or not symbol:
        raise ValueError(f"invalid instrument id: {iid!r}")
    return broker, symbol


# --- market data events ------------------------------------------------------
@dataclass(frozen=True)
class Quote:
    instrument_id: str
    ts: datetime
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    volume_24h: Decimal | None = None
    change_pct_24h: Decimal | None = None
    high_24h: Decimal | None = None
    low_24h: Decimal | None = None
    delayed: bool = False


@dataclass(frozen=True)
class Trade:
    instrument_id: str
    ts: datetime
    price: Decimal
    qty: Decimal
    side: Side
    trade_id: str | None = None


@dataclass(frozen=True)
class Candle:
    instrument_id: str
    interval: str  # "1m", "5m", "15m", "1h", "4h", "1d", "1w"
    ts: datetime  # bar OPEN time, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class ConnectionEvent:
    broker: str
    status: Literal["connected", "disconnected"]
    ts: datetime
    reason: str = ""


MarketEvent = Quote | Trade | ConnectionEvent


# --- trading (phase 2+) ------------------------------------------------------
@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    instrument_id: str
    side: Side
    type: OrderType
    qty: Decimal
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    trail_amount: Decimal | None = None
    trail_pct: Decimal | None = None
    take_profit: Decimal | None = None
    stop_loss: Decimal | None = None
    tif: Literal["day", "gtc", "ioc"] = "gtc"


@dataclass(frozen=True)
class OrderUpdate:
    client_order_id: str
    status: OrderStatus
    ts: datetime
    broker_order_id: str | None = None
    filled_qty: Decimal = Decimal(0)
    fill_price: Decimal | None = None
    fee: Decimal = Decimal(0)
    fee_currency: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class Position:
    instrument_id: str
    qty: Decimal
    avg_price: Decimal


@dataclass(frozen=True)
class AccountSummary:
    currency: str
    cash: Decimal
    equity: Decimal
    buying_power: Decimal
    margin_used: Decimal = Decimal(0)


@dataclass(frozen=True)
class Capabilities:
    broker: str
    native_order_types: frozenset[str]
    paper_mode: Literal["broker", "simulator"]
    max_requests_per_second: float
    trading_hours: Literal["24/7", "exchange"]
    supports_margin: bool = False
    extra: dict = field(default_factory=dict)


class BrokerAdapter(abc.ABC):
    broker: str

    @abc.abstractmethod
    def capabilities(self) -> Capabilities: ...

    # market data (phase 1)
    @abc.abstractmethod
    def stream(self, symbols: list[str]) -> AsyncIterator[MarketEvent]:
        """Yield quotes, trades and connection events forever; reconnect internally."""

    @abc.abstractmethod
    async def fetch_bars(self, symbol: str, interval: str, since: datetime) -> list[Candle]:
        """Completed historical bars with ts >= since."""

    async def close(self) -> None:  # noqa: B027
        pass

    # trading (phase 2+)
    async def place_order(self, req: OrderRequest) -> OrderUpdate:
        raise NotImplementedError

    async def cancel_order(self, client_order_id: str) -> OrderUpdate:
        raise NotImplementedError

    async def get_positions(self) -> list[Position]:
        raise NotImplementedError

    async def get_account(self) -> AccountSummary:
        raise NotImplementedError
