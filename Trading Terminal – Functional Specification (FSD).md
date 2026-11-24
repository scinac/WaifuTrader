# Trading Terminal – Functional Specification (FSD)

Nov 23, 2026 · @sean

## Purpose and goals

The system is a self-hosted trading terminal for a small group of users. Each user trades stocks, ETFs and crypto from their own broker or exchange account, manually or through bots, with model-based predictions as a decision aid.

**Goals**

- Manual trading on live markets through a web UI and a CLI.
- Bots that trade fully automatically on paper accounts and propose trades for confirmation on real-money accounts.
- Predictions (direction probability, volatility) shown to users and usable as bot signals.
- One codebase that works with several brokers through a common adapter interface.

**v1 is done when**

- Manual trading works live on a real broker account with small amounts.
- At least one prediction model passes a walk-forward test that includes fees and slippage.
- Friends are onboarded, each with their own login and their own broker account.

**Non-goals**

- Competing on latency with professional high-frequency firms.
- Managing or pooling other people's money.
- Guaranteeing profitable predictions.

## Scope

| Area | v1 | Later |
| --- | --- | --- |
| Markets | Stocks, ETFs, crypto spot | Futures, options, forex |
| Leverage | Margin info shown; leveraged orders opt-in per user | Wider leveraged products |
| Clients | Web UI (React), CLI | Mobile app |
| Manual trading | All order types in section 6 | — |
| Bots | Python strategies; auto on paper, confirm on real money | Visual no-code builder |
| Predictions | Direction probability, volatility, bot signals | More models and data sources |
| Notifications | Telegram via a pluggable channel interface | Email, Discord, push |
| Reporting | Austrian realized-gains export | Full tax report |
| Hosting | Raspberry Pi or home server | Cloud VPS |

## Architecture

&#91;embedded content: system architecture · 3 clients, 6 services, 2 brokers\]

Clients never talk to a broker directly: the web UI, CLI and Telegram go through the API or notifier to the engine, which runs the risk checks and sends orders through one adapter per broker. The mobile app will use the same API later.

## Data model

Every trading record belongs to one user and one broker account, so every query can be scoped to the logged-in user.

| Table | Key fields | Belongs to |
| --- | --- | --- |
| `users` | id, username, password\_hash, totp\_secret (encrypted), role, telegram\_chat\_id, leverage\_opt\_in, created\_at | — |
| `risk_limits` | user\_id, daily\_loss\_eur, max\_position\_pct, max\_orders\_per\_min, pending\_raise\_at | users |
| `broker_accounts` | id, user\_id, broker (ibkr / kraken), mode (paper / live), credentials (encrypted), status | users |
| `instruments` | id, broker, symbol, isin, exchange, currency, asset\_class, tick\_size, min\_qty | — |
| `orders` | id, client\_order\_id (unique), account\_id, instrument\_id, source (manual / bot), bot\_id, side, type, qty, limit\_price, stop\_price, trail, tif, parent\_order\_id, status, broker\_order\_id, created\_at | broker\_accounts, instruments, bots |
| `fills` | id, order\_id, qty, price, fee, fee\_currency, fx\_rate\_eur, filled\_at | orders |
| `positions` | account\_id, instrument\_id, qty, avg\_price\_eur, updated\_at | broker\_accounts, instruments |
| `bots` | id, user\_id, account\_id, strategy\_name, version, params (JSON), timeframe, state, created\_at | users, broker\_accounts |
| `proposals` | id, bot\_id, order\_payload (JSON), reason, prediction\_id, expires\_at, decision, decided\_by, decided\_via (web / telegram) | bots |
| `bars` | instrument\_id, interval, ts, open, high, low, close, volume | instruments (TimescaleDB hypertable) |
| `models` | id, instrument\_id, horizon, version, trained\_at, validation\_metrics (JSON), enabled | instruments |
| `predictions` | id, model\_id, ts, p\_up, vol\_forecast, outcome, outcome\_at | models |
| `tax_lots` | account\_id, instrument\_id, qty, avg\_cost\_eur, realized\_pnl\_eur, tax\_year | broker\_accounts, instruments |
| `audit_log` | id, user\_id, action, details (JSON), ip, created\_at | users |
| `notification_settings` | user\_id, channel, event\_type, enabled, quiet\_hours | users |

- Bracket and OCO orders link their legs through `parent_order_id`.
- `fills` stores the EUR rate at fill time, so the tax export never needs historical FX lookups.
- `positions` and `tax_lots` are rebuilt from `fills` and checked against the broker during reconciliation.

## Users and roles

The system serves the owner and a few friends. Every user trades only their own broker account with their own API keys; the system never pools money or places orders across accounts.

| Role | Can do |
| --- | --- |
| Admin (owner) | Everything a trader can, plus invite and disable users, view system health, set global limits, trigger the global kill switch |
| Trader | Connect own broker accounts, trade manually, run own bots, confirm bot proposals, view own data and exports |

- Users never see each other's positions, orders, keys or P&L.
- Each user's leverage opt-in, limits and bots are separate.

## Markets and broker integration

v1 uses Interactive Brokers for stocks and ETFs and Kraken for crypto, both behind one broker adapter interface so more brokers can be added later. All users live in Austria and open their own accounts at both.

**Adapter interface (every adapter must implement)**

- Account: balances, buying power, margin info.
- Market data: live quote/trade stream, historical bars.
- Orders: place, modify, cancel, query; stream of order status and fills.
- Positions: list current positions.
- Capabilities: which order types are native at the broker (e.g. trailing stop, OCO), paper/testnet support, rate limits, trading hours.

**Candidate adapters**

| Adapter | Market | Library | Paper mode | Notes |
| --- | --- | --- | --- | --- |
| Interactive Brokers | Stocks, ETFs (US, Xetra, Vienna) | `ib_async` | IBKR paper account | One IB Gateway session per user on the Pi; daily auto-restart and re-login handled; each user subscribes to their own real-time data |
| Kraken | Crypto spot, margin (EUR pairs) | `ccxt` + Kraken WebSocket v2 | No public testnet: internal simulator on live Kraken data | API keys with trade permission only, withdrawals disabled |

**Product availability:** as EU retail clients, users cannot buy US-domiciled ETFs such as SPY or QQQ (no PRIIPs KID). The system uses UCITS equivalents instead, e.g. CSPX / SXR8 for the S&P 500 and EQQQ / SXRV for the Nasdaq-100. Prefer funds with Austrian reporting status (Meldefonds) for normal tax treatment.

**Rules**

- Orders the broker supports natively (stops, brackets, trailing stops) are placed at the broker, so they keep working if the server goes down.
- Orders the broker lacks are emulated by the server and marked "server-side" in the UI.
- Each adapter has a paper mode flag; paper and live use the same code path.

## Market data

A single market data service streams prices from the adapters over WebSocket and feeds the UI, bots and models from the same source.

- **Real-time:** streamed (WebSocket), never polled. Crypto from exchange public feeds (free). Stocks via each user's own IBKR market data subscription (exchange rules require per-user subscriptions); without one, data is delayed and labelled "delayed" in the UI, and bots on delayed data are blocked from proposing real-money trades.
- **History:** OHLCV bars at 1 s (crypto, where available), 1 min, 5 min, 1 h and 1 day, stored locally for charts, backtests and model training.
- **Order book:** top-of-book for all symbols; depth (level 2) for crypto, used as a model feature.
- **Data quality:** detect gaps, duplicates and stale feeds; on reconnect, backfill missing bars; mark a symbol "stale" if no update arrives within a configurable time.
- **Charts:** candlesticks with volume, indicator overlays, prediction overlays, and markers for the user's own fills.

**Reference data provider:** fundamentals, news, index levels and the instrument list for search come from free API tiers (e.g. Finnhub, Financial Modeling Prep) behind a `ReferenceDataProvider` interface, so providers can be swapped. Responses are cached (fundamentals 24 h, news 10 min, instrument list 7 days) and calls are rate-limited to stay inside the free quotas. Top movers and the screener use the system's own stored bars for the symbols it tracks, not a full-market feed.

## Manual trading

Users place, modify and cancel orders from the web order ticket or the CLI; both go through the same order manager as bots.

| Order type | Required fields | Notes |
| --- | --- | --- |
| Market | Symbol, side, quantity or € amount | Shows estimated cost incl. fees and spread before sending |
| Limit | + limit price, time in force | Day, GTC, IOC |
| Stop / stop-limit | + stop price (+ limit price) | Native at broker where possible |
| Take-profit | + target price |  |
| Bracket / OCO | Entry + stop-loss + take-profit | One leg filling cancels the other |
| Trailing stop | + trail amount or % | Server-side if the broker lacks it |

**Order ticket**

- Quantity by shares/units or by € amount; fractional where the broker allows.
- Preview: estimated fill price, fees, resulting position, margin use.
- Leveraged orders only appear if the user has opted in; margin and buying power are always shown.
- Confirmation step for orders above a user-set size.

**Positions and P&L**

- Open positions with average price, market value, unrealized and realized P&L, in EUR and the instrument currency.
- Open orders with status; order history with fills and fees.
- One-click "close position" and "cancel all orders for symbol".

**CLI**

- Same functions as the web ticket, e.g. `trade buy BTC/EUR 50eur --limit 61000`, `trade positions`, `trade cancel --all`.
- Talks to the backend API with the user's token; never holds broker keys itself.

## Order management and risk controls

Every order, manual or from a bot, passes through the order manager, which runs the risk checks before anything reaches the broker.

**Order lifecycle:** created → risk-checked → sent → accepted → partially filled → filled, or rejected / cancelled / expired. Each transition is logged with a timestamp.

**Required limits (v1)**

- **Kill switch:** per user and global (admin). Cancels all open orders and stops all bots; an optional second step closes all positions. Reachable from web, CLI and Telegram.
- **Max orders per minute:** per user and per bot; exceeding it rejects the order and pauses the bot.

**Built-in safety (always on)**

- Duplicate protection: each order carries a client order ID; a resent request with the same ID is ignored.
- Reconciliation: on startup and every few minutes, positions and open orders are compared with the broker; mismatches pause bots and alert the user.
- Fat-finger check: warn when a limit price is far from the market or an order is far larger than usual.
- Disconnect handling: on lost connection, bots pause; broker-side stops stay active.

**User-configurable limits**

Each user sets their own values; new users start with the defaults below.

| Limit | Default | Behaviour when hit |
| --- | --- | --- |
| Daily loss limit | 50 € realized + unrealized loss per day | New entries blocked until 00:00 Europe/Vienna; exits and stop orders still allowed |
| Max position size | 20% of account equity per instrument | Order rejected with the reason shown |
| Max orders per minute | 10 per user, 5 per bot | Order rejected, bot paused |
| Max leverage | Broker maximum once opted in | Broker rejects anything above |

- Raising a limit requires the user's 2FA code and takes effect the next trading day; lowering takes effect immediately.
- Leverage opt-in requires 2FA and a one-time risk acknowledgement; margin use and liquidation distance are always shown on the order ticket and portfolio.

## Strategy engine and bots

Bots run fully automatically on paper accounts and only propose trades on real-money accounts, where the user must confirm each one.

**Writing strategies**

- v1: Python plugins implementing a strategy interface (`on_start`, `on_bar`, `on_tick`, `on_fill`, `on_stop`) that emit signals, never broker calls.
- Later: a visual rule builder that compiles to the same interface.
- Each strategy declares symbols, timeframe (scalping, intraday or swing) and parameters.

**Bot lifecycle**

- States: created → backtested → paper running → live (confirm mode) → paused / stopped.
- The user decides when a bot moves from paper to live; there is no enforced minimum, but the switch shows the bot's paper stats (days run, trades, return, drawdown) and requires 2FA.
- Start, pause and stop from web, CLI and Telegram.

**Real-money confirmation**

- A proposal shows symbol, side, size, order type, reason (signal and prediction values) and expiry.
- The user approves or rejects in the web UI or Telegram; an unanswered proposal expires after its timeframe's default (scalping 15 s, intraday 2 min, swing 1 h).
- Scalping proposals expire within seconds, so confirm mode makes real-money scalping largely impractical; this is accepted for v1.

**Automatic real-money mode (later phase)**

- Enabled per bot only, with 2FA, after the bot has run in confirm mode.
- All hard limits still apply; the daily loss limit also pauses the bot.
- Not part of v1.

## Predictions

Models produce a direction probability and a volatility forecast per symbol and horizon; both are shown in the UI and available to bots as signals.

| Output | Example | Used for |
| --- | --- | --- |
| Direction probability | 54% up in next 15 min | Bot entry signals, chart overlay |
| Volatility forecast | Expected ±1.2% move in next hour | Position sizing, stop distance, risk display |

**Coverage**

| Symbol group | Examples | Horizons | Data source |
| --- | --- | --- | --- |
| Crypto | BTC/EUR, ETH/EUR | 5 min, 1 h, 1 day, 1 week | Kraken trades + order book (free, real-time) |
| US index UCITS ETFs | CSPX, EQQQ | 5 min, 1 h, 1 day, 1 week | IBKR; underlying US index data as a feature |
| European ETFs, ATX stocks | e.g. VWCE, Erste, OMV | 1 h, 1 day, 1 week | IBKR (Xetra, Wiener Börse); needs paid real-time data |
| User watchlist | Any supported symbol | Chosen by user | Trained on demand, queued |

- 5 min models for less liquid European stocks are skipped by default: spreads usually exceed the predictable move.
- Training jobs are queued and limited so the Raspberry Pi stays responsive for trading; one model per symbol and horizon.

**Pipeline**

1. Features from stored data: returns, volume, volatility, indicators, order-book imbalance (crypto), time of day.
2. Training with scikit-learn / LightGBM, run on a schedule (Raspberry Pi CPU limits model size; heavy training may run on a PC).
3. Walk-forward validation with fees and slippage, compared against buy-and-hold and a naive baseline.
4. A model is only enabled for bots if it beats the baselines after costs.
5. Live monitoring: track hit rate and calibration; disable a model automatically when it drifts below a threshold.

**Display rules**

- Every prediction shows its horizon, model version and recent live accuracy.
- The UI never presents predictions as certain.

## Backtesting and paper trading

The same strategy code runs in backtest, paper and live mode; only the execution target changes.

- **Backtest:** replay stored bars or ticks through the strategy with a simulated broker that models fees, spread, slippage and partial fills.
- **Results:** equity curve, total return, max drawdown, win rate, number of trades, fees paid, comparison with buy-and-hold. Full results screen is a later phase; v1 exports results and a basic chart.
- **Paper trading:** uses the broker's paper account or testnet where available, otherwise an internal simulator on live data.
- **Paper vs. live comparison:** for bots in confirm mode, log what the paper run would have done to measure slippage and missed fills.

## User interface

The web app is a dark, dense, pro-terminal UI built around one stock/coin detail screen where users see the market, the predictions and trade. Charts use KLineChart (open source, built-in indicators and drawing tools).

| Screen | Priority | Contents |
| --- | --- | --- |
| Market overview | v1 core | Tiles for ATX, DAX, S&P 500, Nasdaq-100, BTC, ETH (price, % change, sparkline); top gainers and losers in the user's markets; prediction highlights (strongest current signals, with horizon and model accuracy) |
| Symbol detail / trading | v1 core | Chart, key data, fundamentals, news, order book and recent trades, prediction panel, order ticket, own position and orders for that symbol (wireframe below) |
| Search | v1 core | Global search bar (Ctrl+K) by ticker, name or ISIN across IBKR and Kraken instruments |
| Watchlists | v1 core | Multiple named lists per user, live prices, % change, prediction column; drag to reorder |
| Screener | v1 basic | Filters on stored data: price, % change (1 d, 1 w), volume, relative volume, market, asset class, p\_up per horizon, volatility forecast; results sortable, savable as a watchlist |
| Portfolio | v1 core | Positions, open orders, order history, realized/unrealized P&L, balances per account |
| Bot manager | v1 basic | Bots, start/pause/stop, pending trade proposals to confirm |
| Backtests | v1 basic | Run a backtest, export results |
| Settings | v1 basic | Broker connections, limits, leverage opt-in, notifications, 2FA |
| Admin | v1 basic | Users, invites, system health |

**Chart requirements**

- Candles and volume; timeframes 1m, 5m, 15m, 1h, 4h, 1D, 1W; history scroll-back loads older bars on demand.
- Indicators: SMA, EMA, RSI, MACD, Bollinger Bands, VWAP; settings saved per user.
- Drawing tools: trendlines, horizontal levels, rectangles, Fibonacci retracement; saved per user and symbol.
- Trade from chart: open orders, stop-loss and take-profit shown as lines; dragging a line modifies the order after a confirmation; own fills shown as markers.
- Real-time updates of the last candle from the WebSocket feed.

**Detail page panels**

- **Key data:** last price, change, volume, bid/ask, day range, 52-week range, market cap (stocks).
- **Fundamentals (stocks/ETFs):** P/E, EPS, dividend yield, next earnings date, sector; ETFs show TER and domicile. Shown as "not available" when the provider has no data.
- **News:** latest 20 headlines with source and time, link out; no full article text stored.
- **Order book and trades:** 10-level depth and last 50 trades for crypto; for stocks, bid/ask only unless the user has IBKR depth data.
- **Prediction panel:** for each horizon (5 min, 1 h, 1 day, 1 week): probability up, volatility forecast as an expected ±% range, model version, live hit rate over the last 30 days, and the time the prediction was made. Horizons without a validated model show "no model".

**Layout:** resizable, dockable panels; the layout is saved per user. Keyboard shortcuts: B buy, S sell, Esc cancel ticket, Ctrl+K search.

- Live updates via WebSocket from the backend.
- Responsive layout so the web app is usable on a phone until the mobile app exists.
- Clear labels for paper vs. live account, and delayed vs. real-time data, on every screen.

**Wireframe: symbol detail and trading screen**

&#91;embedded content: wireframe · symbol detail and trading screen\]

Panels are resizable and dockable; this is the default layout. Clicking a watchlist row loads that symbol into every panel.

## Alerts and notifications

Telegram is the v1 channel, built behind a notification interface so email, Discord and push can be added as plugins.

- **Events:** order filled/rejected, bot started/stopped/errored, trade proposal awaiting confirmation, kill switch triggered, reconciliation mismatch, data feed stale, server restarted.
- **Telegram actions:** approve/reject proposals, pause bots, trigger the kill switch. Each user links their own Telegram account; actions require the link to be verified.
- **Settings:** per user, per event type, per channel; quiet hours except for critical events.

## Data storage, logging and reporting

All data lives in a local PostgreSQL database (with TimescaleDB for price history) on the home server, with nightly encrypted backups off the device.

**Stored**

- Users, settings, encrypted broker credentials.
- Orders, fills, fees, positions snapshots, bot proposals and decisions.
- Price history, model versions, predictions and their outcomes.
- Audit log: every login, order, confirmation, setting change and kill-switch use.

**Austrian tax export**

- Yearly CSV of realized gains and losses per user, per instrument, in EUR, with buy date, sell date, quantity, cost basis, proceeds and fees.
- FX rates used for EUR conversion are stored with each trade.
- Cost basis uses the moving average price (gleitender Durchschnittspreis) per instrument, including purchase fees; crypto acquired after 28 Feb 2021 is treated as new assets under 27.5% KESt. Confirm both with a tax advisor before relying on the export.
- The export supports the user's own declaration (foreign brokers do not withhold KESt); it is not tax advice.

## Non-functional requirements

| Requirement | Target |
| --- | --- |
| Order path latency (risk check to broker send) | < 50 ms on the server |
| UI price update delay | < 500 ms from feed |
| Uptime | 24/7 for crypto; auto-restart within 60 s after a crash |
| Recovery | On restart, reconcile with all brokers before any bot resumes |
| Concurrent users | Up to 10 |
| Hardware | Raspberry Pi 5 (8 GB) or comparable mini PC, SSD (not SD card) |
| Power and network | UPS recommended; broker-side stops cover outages |

Home internet latency (tens to hundreds of ms to brokers) rules out competitive scalping; scalping strategies are supported but not expected to compete with professional firms.

## Security

Users log in with username, password and a TOTP second factor; broker keys are encrypted at rest and never leave the server.

- **Authentication:** passwords hashed with Argon2; TOTP 2FA mandatory for every user; sessions expire; login rate limiting and lockout.
- **Re-authentication:** 2FA code required for enabling leverage, adding broker keys, switching a bot to live, and closing all positions.
- **Broker keys:** encrypted with a server master key; trading-only permissions, withdrawals disabled at the broker; IP allowlisting where the broker supports it.
- **Isolation:** every API query is scoped to the logged-in user.
- **Network:** the Pi is not exposed to the internet. Users reach it only through a Tailscale network, with access limited to the web and API ports; login with 2FA still applies inside the VPN. HTTPS via Tailscale certificates.
- **CLI:** personal API tokens, revocable, scoped per user.

## Deployment and operations

The system runs as Docker Compose services on the home server.

| Service | Stack | Role |
| --- | --- | --- |
| api | Python, FastAPI | REST + WebSocket API for web, CLI, future mobile |
| engine | Python, asyncio | Order manager, risk checks, strategy runner |
| adapters | Python (`ib_async`, `ccxt`) | One process per broker connection |
| ib-gateway | IB Gateway container | One session per user, auto-restart and re-login |
| models | Python, scikit-learn / LightGBM | Scheduled training and live inference |
| notifier | Python | Telegram and future channels |
| db | PostgreSQL + TimescaleDB | All storage |
| web | React, served by reverse proxy | UI |

- Health checks with automatic restart; Telegram alert on any restart.
- Updates by pulling new images; bots pause during deploys and reconcile afterwards.
- Separate configs for paper and live; live config cannot be activated without the admin's 2FA.

## API contract

The backend exposes one versioned REST API under `/api/v1` and one WebSocket at `/ws`; the web app, CLI and future mobile app use only these. The OpenAPI schema generated by FastAPI is the source of truth once built.

**Conventions**

- JSON, snake\_case, timestamps as ISO 8601 UTC, money as decimal strings (never floats).
- Auth: session cookie for the web app, bearer token for the CLI. Every request is scoped to the logged-in user.
- Errors: `{"error": {"code": "RISK_DAILY_LOSS", "message": "...", "details": {}}}` with HTTP 4xx/5xx.
- Writes that place or change orders take an `Idempotency-Key` header (becomes the `client_order_id`).
- Actions marked 2FA need a fresh TOTP code in the `X-TOTP` header.

**REST endpoints**

| Method + path | Purpose |
| --- | --- |
| `POST /auth/login`, `POST /auth/logout`, `POST /auth/totp/setup` | Login with password + TOTP, logout, enrol 2FA |
| `GET /me`, `PATCH /me/settings` | Profile, UI layout, chart settings |
| `GET /accounts`, `POST /accounts` (2FA), `DELETE /accounts/{id}` | Broker accounts and credentials |
| `GET /accounts/{id}/summary` | Balances, buying power, margin, daily P&L |
| `GET /instruments/search?q=` | Search by ticker, name, ISIN |
| `GET /instruments/{id}` | Key data + fundamentals |
| `GET /instruments/{id}/bars?interval=&from=&to=` | Historical candles |
| `GET /instruments/{id}/news` | Headlines |
| `GET /instruments/{id}/orderbook` | Snapshot of depth |
| `GET /market/overview` | Index tiles, top movers, prediction highlights |
| `POST /screener/query` | Run screener filters |
| `GET`, `POST`, `PATCH`, `DELETE /watchlists/{id}` | Watchlists and their symbols |
| `GET`, `PUT /drawings/{instrument_id}` | Saved chart drawings |
| `POST /orders/preview` | Estimated cost, fees, risk-check result; no order sent |
| `POST /orders` | Place order (incl. bracket, OCO, trailing) |
| `PATCH /orders/{id}`, `DELETE /orders/{id}` | Modify, cancel |
| `GET /orders?status=&from=` | Open and historical orders |
| `GET /positions`, `POST /positions/{id}/close` | Positions, close one |
| `POST /killswitch` with `close_positions` flag | Cancel all, stop bots, optionally flatten (2FA when flattening) |
| `GET`, `PUT /risk-limits` | Read limits; raising needs 2FA |
| `GET /predictions?instrument_id=` | Latest prediction per horizon |
| `GET /models`, `GET /models/{id}` | Model versions and validation metrics |
| `GET`, `POST /bots`, `PATCH /bots/{id}` | Bot management; switching to live needs 2FA |
| `POST /bots/{id}/start`, `/pause`, `/stop` | Bot control |
| `GET /proposals`, `POST /proposals/{id}/approve`, `/reject` | Real-money trade confirmations |
| `POST /backtests`, `GET /backtests/{id}` | Run and read backtests |
| `GET /exports/tax?year=` | Realized gains CSV |
| `GET /admin/users`, `POST /admin/invites`, `GET /admin/health` | Admin only |

**WebSocket**

Clients send `{"op": "subscribe", "channel": "quotes", "symbols": ["ibkr:CSPX", "kraken:BTC/EUR"]}` and receive `{"channel": ..., "data": ..., "ts": ...}`.

| Channel | Payload |
| --- | --- |
| `quotes` | Last, bid, ask, volume, change; delayed flag |
| `candles:{interval}` | Updated current candle |
| `orderbook` | Depth snapshot, then deltas with sequence numbers |
| `trades` | Public trades (crypto) |
| `orders` | Status changes of the user's orders |
| `fills` | The user's fills |
| `positions` | Position and P&L changes |
| `account` | Balance, margin, daily P&L, limit usage |
| `predictions` | New predictions for subscribed symbols |
| `proposals` | New, expiring and decided proposals |
| `alerts` | System events: feed stale, reconciliation mismatch, kill switch, broker disconnected |

The server sends a heartbeat every 15 s; clients resubscribe automatically after reconnecting and refetch state through REST.

## Strategy plugin interface

A strategy is a Python class that receives market events and returns signals; it never calls a broker, so the same class runs in backtest, paper and live mode.

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

@dataclass(frozen=True)
class Bar:
    instrument_id: str
    interval: str          # "1m", "5m", "1h", "1d" ...
    ts: str                # ISO 8601 UTC, bar close time
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

@dataclass(frozen=True)
class Prediction:
    instrument_id: str
    horizon: str           # "5m", "1h", "1d", "1w"
    p_up: float            # 0..1
    vol_forecast: float    # expected absolute move, fraction of price
    model_version: str

@dataclass(frozen=True)
class Signal:
    instrument_id: str
    side: Literal["buy", "sell"]
    size_pct: float        # share of the bot's allocated capital, 0..1
    order_type: Literal["market", "limit", "bracket"]
    limit_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    reason: str = ""       # shown to the user in proposals

class Context(Protocol):
    params: dict                        # validated against Strategy.params_schema
    def position(self, instrument_id: str) -> Decimal: ...
    def history(self, instrument_id: str, interval: str, n: int) -> list[Bar]: ...
    def prediction(self, instrument_id: str, horizon: str) -> Prediction | None: ...
    def log(self, message: str) -> None: ...
    state: dict                         # persisted between restarts

class Strategy:
    name: str
    version: str
    symbols: list[str]
    interval: str
    timeframe: Literal["scalping", "intraday", "swing"]
    params_schema: dict                 # JSON Schema for the UI form

    def on_start(self, ctx: Context) -> None: ...
    def on_bar(self, ctx: Context, bar: Bar) -> list[Signal]: return []
    def on_prediction(self, ctx: Context, p: Prediction) -> list[Signal]: return []
    def on_fill(self, ctx: Context, fill: dict) -> None: ...
    def on_stop(self, ctx: Context) -> None: ...
```

**Example strategy**

```python
class PredictionTrend(Strategy):
    name, version = "prediction_trend", "1.0"
    symbols, interval, timeframe = ["kraken:BTC/EUR"], "1h", "intraday"
    params_schema = {"type": "object", "properties": {
        "min_p_up": {"type": "number", "default": 0.58},
        "sma_len": {"type": "integer", "default": 50}}}

    def on_bar(self, ctx, bar):
        hist = ctx.history(bar.instrument_id, "1h", ctx.params["sma_len"])
        if len(hist) < ctx.params["sma_len"]:
            return []
        sma = sum(b.close for b in hist) / len(hist)
        p = ctx.prediction(bar.instrument_id, "1h")
        holding = ctx.position(bar.instrument_id) > 0
        if not holding and p and p.p_up >= ctx.params["min_p_up"] and bar.close > sma:
            stop = bar.close * Decimal(1 - 2 * p.vol_forecast)
            target = bar.close * Decimal(1 + 3 * p.vol_forecast)
            return [Signal(bar.instrument_id, "buy", 0.5, "bracket",
                           stop_loss=stop, take_profit=target,
                           reason=f"p_up {p.p_up:.2f}, close above SMA{len(hist)}")]
        if holding and bar.close < sma:
            return [Signal(bar.instrument_id, "sell", 1.0, "market", reason="close below SMA")]
        return []
```

**Rules for the engine**

- Strategies live in `strategies/` and are discovered by class; a syntax or import error marks the bot "errored" without affecting other bots.
- Signals become orders only after sizing (`size_pct` × allocated capital) and the risk checks; on live accounts they become proposals.
- Each bot runs in its own asyncio task with a time limit per callback (default 2 s); exceeding it pauses the bot.
- Strategy code runs inside the server and could read other users' data, so in v1 only the admin installs strategy files; friends pick installed strategies and set their parameters in the UI. Sandboxed user-uploaded code is a later phase.

## Error handling and edge cases

When in doubt the system stops opening new risk, keeps broker-side protective orders alive, and tells the user.

| Situation | System behaviour | User sees |
| --- | --- | --- |
| Broker connection lost | Retry with backoff (1, 2, 5, 10, 30 s); bots on that broker pause; new orders rejected with `BROKER_DISCONNECTED` | Red broker status badge, Telegram alert after 60 s |
| IB Gateway session expired (needs phone 2FA) | IBKR trading paused for that user; Kraken unaffected | Banner + Telegram: "Confirm IBKR login in IBKR Mobile" |
| Market closed | Market orders rejected; limit orders accepted as GTC or queued for open if the broker supports it | Ticket shows market hours and next open |
| Order rejected by broker | Status "rejected" with the broker's reason stored; bot gets `on_fill` not called and logs the reason | Toast with the broker's reason |
| Partial fill | Position and P&L updated per fill; bracket legs sized to filled quantity | Order shows filled / total |
| Unknown order status after timeout | Query broker by client order ID before any retry; never resend blindly | "Pending confirmation" status |
| Reconciliation mismatch | Broker state wins; local state corrected; bots for that account paused until a user resumes | Alert with the differences |
| Market data stale (no update for 30 s in market hours, 10 s crypto) | Symbol flagged stale; bots on it pause; prediction panel greys out | "Stale" badge on price |
| Reference data provider quota hit or down | Serve cached data with its age; retry later | "Data as of 14:05" |
| Model missing or disabled | No prediction published for that horizon; bots depending on it skip signals | "No model" in prediction panel |
| Risk limit hit | Order rejected with the limit name and current usage | Toast + limit bar in portfolio |
| Proposal expired | Marked expired, no order | Proposal greyed out |
| Insufficient funds or margin | Rejected in pre-check using account summary; broker rejection as fallback | Toast with available buying power |
| Server restart | On boot: load state, reconcile all accounts, resume only bots that were running, in paused-then-resume order | Telegram "server restarted" |
| Database unavailable | Engine refuses new orders; broker-side orders stay active | Error page |
| Clock drift > 1 s | Warning logged; NTP sync required on the Pi | Admin health page |

## Acceptance criteria

A feature is done only when its criteria pass on paper accounts (IBKR paper, Kraken simulator) and in automated tests.

| Feature | Passes when |
| --- | --- |
| Login + 2FA | Login fails without a valid TOTP code; 5 failed attempts lock the account for 15 min; user A's token returns 404 for user B's orders |
| Market data | BTC/EUR quote reaches the UI < 500 ms after Kraken sends it; after a 60 s network cut, the gap is backfilled with no missing 1m bars |
| Search | Typing "CSPX", the ISIN IE00B5BMR087, or "Erste" returns the right instrument in < 300 ms |
| Market overview | All index and crypto tiles update live; top movers match a manual calculation from stored bars |
| Chart | All 7 timeframes load; indicators match a reference calculation (e.g. pandas-ta) to 4 decimals; drawings survive a reload |
| Trade from chart | Dragging a stop line asks for confirmation, then the broker shows the modified stop price |
| Market + limit orders | Order appears at the broker, fills update position and P&L, fees match the broker's report |
| Bracket / OCO | Creates 3 linked orders; filling the target cancels the stop and vice versa, also after a server restart |
| Trailing stop | Native at IBKR; server-side on Kraken, where the stop moves up only and fires within 2 s of the trigger |
| Duplicate protection | Sending the same `Idempotency-Key` twice creates one order |
| Risk limits | An order breaching position size or daily loss is rejected with the limit name; raising a limit without 2FA fails |
| Kill switch | From web, CLI and Telegram: all open orders cancelled and all bots stopped within 5 s; with flatten, positions are closed |
| Order rate limit | The 11th order within a minute is rejected and the sending bot paused |
| Bot paper mode | Example strategy runs 7 days on paper with no unhandled errors and every signal logged |
| Bot live confirm | A live signal becomes a proposal; approve in Telegram places the order; no answer within the expiry leaves no order |
| Predictions | Each model shows walk-forward metrics; a model is only enabled when it beats both baselines after costs; panel shows all 4 horizons or "no model" |
| Screener | Filtering by % change and p\_up returns the same set as a direct SQL query; results can be saved as a watchlist |
| Reconciliation | Manually placing an order in the broker's own app is detected within 5 min and bots pause |
| Tax export | For a test set of trades, gains with moving-average cost basis match a hand-calculated spreadsheet to the cent |
| Restart | Killing the engine container mid-session: back within 60 s, reconciled, no duplicate orders |
| Multi-user | Two users trade at once; neither can see or affect the other's data |

## Build guide

The implementer (human or AI) builds one roadmap phase at a time in a single monorepo, and a phase is merged only when its acceptance criteria and gate pass.

**Stack (latest stable at build time, pinned in lockfiles)**

| Layer | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, asyncio |
| Brokers | `ib_async` (IBKR), `ccxt` + Kraken WebSocket v2 |
| ML | pandas, scikit-learn, LightGBM |
| Database | PostgreSQL 16 + TimescaleDB |
| Frontend | React + TypeScript + Vite, TanStack Query, Zustand, KLineChart, a dockable panel library (e.g. FlexLayout) |
| CLI | Python, Typer, calls the REST API |
| Runtime | Docker Compose on Raspberry Pi 5 (arm64 images) |

**Repo layout**

```
trading-terminal/
  backend/
    api/          # FastAPI routers, auth, WebSocket hub
    engine/       # order manager, risk, bot runner, reconciliation
    adapters/     # base.py, ibkr.py, kraken.py, paper_sim.py
    marketdata/   # feeds, bar builder, gap backfill
    refdata/      # ReferenceDataProvider + Finnhub/FMP implementations
    models/       # features, training, walk-forward, inference
    notify/       # channel interface, telegram.py
    tax/          # moving-average lots, CSV export
    db/           # SQLAlchemy models, Alembic migrations
    tests/
  strategies/     # admin-installed strategy plugins
  web/            # React app
  cli/
  deploy/         # docker-compose.yml, env templates, backup scripts
  docs/           # this FSD, ADRs
```

**Conventions**

- All money and quantities use `Decimal` end to end; floats only inside ML code.
- All times stored in UTC; displayed in Europe/Vienna.
- Instrument IDs are `broker:symbol` (e.g. `ibkr:CSPX`, `kraken:BTC/EUR`).
- Secrets only from environment variables or the encrypted credentials table; never in code or logs.
- Every adapter passes the same shared adapter test suite against a fake broker.

**Testing rules**

- Unit tests for risk checks, order state machine, tax calculation and indicators: 90%+ line coverage in `engine/` and `tax/`.
- Integration tests against IBKR paper and the Kraken simulator, run manually before each phase gate.
- A fake broker adapter with scripted scenarios (partial fills, rejects, disconnects) for every row in the error-handling table.
- No live-money code path ships without a test that proves the kill switch cancels its orders.

**What the implementer must not decide alone**

- Anything that changes risk limits, default values, or what needs 2FA.
- Adding a broker, data provider or paid service.
- Enabling automatic real-money trading.

## Roadmap

Each phase ends with a gate that must pass before the next starts.

1. **Foundation:** adapter interface, Kraken adapter with the internal paper simulator, market data service, database, auth with 2FA. *Gate: live prices streaming and stored for 7 days without gaps.*
2. **Manual trading (paper):** order manager, kill switch, order rate limit, all order types, market overview, symbol detail with charts and drawing tools, search, watchlists, portfolio, reference data provider, CLI. *Gate: every order type tested on paper, reconciliation clean.*
3. **Manual trading (live):** IBKR adapter and IB Gateway, live accounts with small amounts, Telegram alerts. *Gate: manual trading works live (v1 criterion).*
4. **Bots:** strategy plugin interface, paper auto mode, live confirm mode via web and Telegram. *Gate: one bot stable on paper for several weeks.*
5. **Predictions:** feature pipeline, models, walk-forward validation with costs, prediction panel, prediction highlights, screener, bot signals. *Gate: a model beats baselines after costs (v1 criterion).*
6. **Friends and reporting:** invites, per-user isolation, tax export. *Gate: friends onboarded with own accounts (v1 criterion).*
7. **Later:** visual strategy builder, full backtest screen, more channels, mobile app, VPS option.

## Decisions, open questions and assumptions

**Decisions made (23 Nov 2026)**

| Question | Decision |
| --- | --- |
| Stock broker | Interactive Brokers |
| Crypto exchange | Kraken |
| Where users live | All in Austria |
| Real-time stock data | Each user pays for their own IBKR subscription |
| Position size and daily loss limits | Added as hard limits, user-configurable; defaults 20% per position, 50 € loss per day |
| On daily loss limit | Block new entries until midnight; exits allowed |
| Max leverage | Broker maximum after opt-in |
| Remote access | Tailscale VPN plus login with 2FA; Pi not exposed |
| Paper period before live | User decides; no enforced minimum |
| Proposal expiry | Scalping 15 s, intraday 2 min, swing 1 h |
| Automatic real-money bots | Later phase, per bot, with 2FA |
| Prediction symbols | BTC/EUR, ETH/EUR, UCITS index ETFs, European ETFs and ATX stocks, user watchlist |
| Prediction horizons | 5 min, 1 h, 1 day, 1 week |
| Tax cost basis | Moving average price, to be confirmed by a tax advisor |

**Still open**

- [ ] IB Gateway logins need periodic 2FA confirmation on the user's phone (IBKR Mobile). Is a weekly manual re-login acceptable, or should IBKR stocks pause when a session lapses?
- [ ] Where do nightly encrypted backups go: a USB drive, a friend's machine, or cloud storage?
- [ ] One shared Telegram bot for all users, or one bot each? Should approving a real-money trade in Telegram also need a PIN?
- [ ] Which slippage and fee model does the internal Kraken paper simulator use (e.g. fill at the opposite side of the spread plus Kraken's taker fee)?
- [ ] Does any user want leveraged crypto on Kraken? Margin there has rollover fees and liquidation rules that the UI must show.
- [ ] Is a 50 € daily loss default sensible for every friend, or should it scale with account size once accounts grow?

**Assumptions**

- Each user is responsible for their own trading decisions, broker account and taxes.
- The software is for private use and is not offered as a financial service.
- Losses are possible and expected while learning; v1 runs with small amounts.
