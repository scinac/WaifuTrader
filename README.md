# Trading Terminal

Self-hosted trading terminal for a few users: stocks/ETFs via Interactive Brokers, crypto via
Kraken, manual trading, bots and predictions. The full specification is the FSD:
https://claude.ai/artifact/STBGAtAvkaLwmuTyTFszqY

## Status: phase 1 (Foundation)

| Built | Where |
| --- | --- |
| Broker adapter interface (market data + trading methods for later phases) | `backend/tt/adapters/base.py` |
| Kraken adapter: WebSocket v2 quotes + trades, REST OHLC backfill, reconnect with backoff 1/2/5/10/30 s | `backend/tt/adapters/kraken.py` |
| Paper simulator (market + limit, fees, cash/position reservation, duplicate protection) | `backend/tt/adapters/paper_sim.py` |
| Market data service: 1m candles from trades, backfill on start and reconnect, stale detection, alerts | `backend/tt/marketdata/` |
| Auth: invites, Argon2 passwords, mandatory TOTP 2FA, replay protection, 5-failure lockout, web cookie + CLI token, audit log | `backend/tt/api/auth.py` |
| REST: `/auth/*`, `/me`, `/instruments/search`, `/instruments/{id}`, `/instruments/{id}/bars` (1m to 1w), `/admin/invites`, `/admin/users`, `/admin/health` | `backend/tt/api/` |
| WebSocket `/ws`: `quotes`, `candles:1m`, `alerts`, heartbeat every 15 s | `backend/tt/api/market.py` |
| PostgreSQL + TimescaleDB (bars hypertable), Alembic migration | `backend/migrations/` |

**Phase 1 gate (FSD):** live prices streaming and stored for 7 days without gaps. Check with
`GET /api/v1/admin/health` (bar counts, reconnects, stale flags).

Deviations from the FSD to review:
- Bars are stored with `ts` = bar **open** time (TimescaleDB convention). The strategy plugin
  `Bar.ts` in the FSD says close time; the engine will convert when strategies arrive (phase 4).
- Kraken REST backfill returns at most 720 bars, so an outage longer than 12 h leaves a gap in 1m bars.
- Paper fill model: market orders at the opposite side of the spread, limit orders at the limit
  price once crossed, 0.40 % taker fee (configurable; check Kraken's current schedule).

## Run locally (dev, SQLite)

```bash
cd backend
pip install -e ".[dev]"
export TT_MASTER_KEY=$(python -m tt.cli gen-master-key) TT_COOKIE_SECURE=false
alembic upgrade head
python -m tt.cli create-admin you          # prints a TOTP URI: add it to your authenticator
uvicorn tt.main:app --reload
# confirm 2FA once:
curl -X POST localhost:8000/api/v1/auth/totp/setup -H 'content-type: application/json' \
  -d '{"username":"you","password":"...","code":"123456"}'
# login (CLI token):
curl -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"username":"you","password":"...","totp":"123456","client":"cli"}'
```

API docs: http://localhost:8000/docs

## Run the web app (dev)

With the backend running on port 8000:

```bash
cd web
pnpm install
pnpm dev
```

Open http://127.0.0.1:5173 and sign in. The market view has live BTC/EUR and ETH/EUR quotes,
candlestick charts from 1m to 1W, and a feed health panel. Trading screens arrive with phase 2.
The Docker stack does not serve the web app yet.

## Run on the Raspberry Pi

```bash
cd deploy
cp .env.example .env        # fill POSTGRES_PASSWORD and TT_MASTER_KEY
docker compose --env-file .env up -d --build
docker compose exec backend python -m tt.cli create-admin you
sudo tailscale serve --bg 8000   # HTTPS inside your tailnet only
```

Invite friends: `POST /api/v1/admin/invites` → send them the token → they call
`POST /api/v1/auth/register`, add the returned TOTP URI to their app, then `POST /api/v1/auth/totp/setup`.

## Tests

```bash
cd backend && pytest
```

41 tests cover auth (2FA, replay, lockout, invites, isolation, encryption at rest), Kraken message
parsing, candle building and aggregation, the paper simulator, the market data service
(backfill, reconnect, stale detection, idempotent storage) and the REST/WebSocket API.

## Next: phase 2 (Manual trading, paper)

Order manager with risk checks and kill switch, all order types, the React web app (market
overview, symbol detail with KLineChart, search, watchlists, portfolio), reference data provider
and the `trade` CLI. See the FSD roadmap.

## Working with OpenSpec

The FSD is the full spec. To use OpenSpec on top of this repo:

```bash
npm install -g @fission-ai/openspec@latest
openspec init
```

Then put each FSD section that is live in the code into `openspec/specs/<capability>/spec.md`
(start with `auth`, `market-data`, `broker-adapters`), and create one change per roadmap phase
(`openspec/changes/phase-2-manual-trading/`) with its proposal, tasks and spec deltas.
