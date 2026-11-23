"""Tables for auth, market data and paper trading. Later phases add bots, proposals,
models, predictions, tax_lots and notification_settings (see FSD data model)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator):
    """Stores timezone-aware UTC datetimes; SQLite drops tzinfo, so restore it on load."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use UTC-aware datetimes")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


Money = Numeric(28, 10, asdecimal=True)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="trader")  # admin | trader
    totp_secret_enc: Mapped[str] = mapped_column(String(512))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    totp_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    leverage_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(8))  # web | cli
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default="trader")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class BrokerAccount(Base):
    __tablename__ = "broker_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    broker: Mapped[str] = mapped_column(String(16))  # ibkr | kraken
    mode: Mapped[str] = mapped_column(String(8))  # paper | live
    credentials_enc: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="disconnected")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # broker:symbol
    broker: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128), default="")
    isin: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    currency: Mapped[str] = mapped_column(String(8))
    asset_class: Mapped[str] = mapped_column(String(16))  # crypto | stock | etf
    tick_size: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    min_qty: Mapped[Decimal | None] = mapped_column(Money, nullable=True)


class Bar(Base):
    """OHLCV bars. `ts` = bar OPEN time (UTC). TimescaleDB hypertable on Postgres."""

    __tablename__ = "bars"
    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    interval: Mapped[str] = mapped_column(String(4), primary_key=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    open: Mapped[Decimal] = mapped_column(Money)
    high: Mapped[Decimal] = mapped_column(Money)
    low: Mapped[Decimal] = mapped_column(Money)
    close: Mapped[Decimal] = mapped_column(Money)
    volume: Mapped[Decimal] = mapped_column(Money)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PaperBalance(Base):
    """Simulated cash for a paper account, plus the equity the current Vienna day started at."""

    __tablename__ = "paper_balances"
    account_id: Mapped[str] = mapped_column(ForeignKey("broker_accounts.id", ondelete="CASCADE"),
                                            primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    starting_cash: Mapped[Decimal] = mapped_column(Money)
    cash: Mapped[Decimal] = mapped_column(Money)
    day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    day_start_equity: Mapped[Decimal | None] = mapped_column(Money, nullable=True)


class RiskLimit(Base):
    __tablename__ = "risk_limits"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    daily_loss_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal("50"))
    max_position_pct: Mapped[Decimal] = mapped_column(Money, default=Decimal("20"))
    max_orders_per_min: Mapped[int] = mapped_column(Integer, default=10)
    pending: Mapped[dict] = mapped_column(JSON, default=dict)  # raises waiting for the next day
    pending_raise_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    halted: Mapped[bool] = mapped_column(Boolean, default=False)  # kill switch


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("account_id", "client_order_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    client_order_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[str] = mapped_column(ForeignKey("broker_accounts.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), index=True)
    source: Mapped[str] = mapped_column(String(8), default="manual")
    side: Mapped[str] = mapped_column(String(4))
    type: Mapped[str] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(Money)
    limit_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    trail_amount: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    trail_pct: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    tif: Mapped[str] = mapped_column(String(4), default="gtc")
    status: Mapped[str] = mapped_column(String(20), default="created", index=True)
    reason: Mapped[str] = mapped_column(String(64), default="")
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    filled_qty: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    trail_anchor: Mapped[Decimal | None] = mapped_column(Money, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Fill(Base):
    __tablename__ = "fills"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    qty: Mapped[Decimal] = mapped_column(Money)
    price: Mapped[Decimal] = mapped_column(Money)
    fee: Mapped[Decimal] = mapped_column(Money)
    fee_currency: Mapped[str] = mapped_column(String(8))
    fx_rate_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal(1))
    filled_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PositionRow(Base):
    __tablename__ = "positions"
    account_id: Mapped[str] = mapped_column(ForeignKey("broker_accounts.id", ondelete="CASCADE"),
                                            primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    qty: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    avg_price_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    realized_pnl_eur: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Drawing(Base):
    __tablename__ = "drawings"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    items: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
