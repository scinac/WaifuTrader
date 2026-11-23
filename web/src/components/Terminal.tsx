import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Account, type Instrument, type Interval, type Quote, type User } from "../lib/api";
import { INTERVALS } from "../lib/bars";
import { clock, direction, money, percent, price, priceDecimals, quantity } from "../lib/format";
import { onOrder, startLive, useLive } from "../lib/live";
import { Blotter } from "./Blotter";
import { FeedHealth, StatusBadge } from "./FeedHealth";
import { KillSwitch } from "./KillSwitch";
import { LivePrice } from "./LivePrice";
import { OrderTicket, type TicketHandle } from "./OrderTicket";
import { PortfolioView, RiskView } from "./PortfolioView";
import { DRAWING_TOOLS, INDICATORS, PriceChart } from "./PriceChart";
import { SearchPalette } from "./SearchPalette";
import { useWatchlists, WatchlistRail } from "./WatchlistRail";

export type FeedStatus = "live" | "stale" | "offline" | "waiting";
type View = "trade" | "portfolio" | "risk" | "feed";

const VIEWS: { id: View; label: string }[] = [
  { id: "trade", label: "Trade" },
  { id: "portfolio", label: "Portfolio" },
  { id: "risk", label: "Risk" },
  { id: "feed", label: "Feed" },
];

const INTERVAL_LABEL: Record<Interval, string> = {
  "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W",
};

function remembered<T extends string>(key: string, fallback: T, allowed?: readonly T[]): T {
  try {
    const value = localStorage.getItem(key) as T | null;
    return value && (!allowed || allowed.includes(value)) ? value : fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Preference only: the terminal works without storage.
  }
}

function useNow() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function QuoteHeader({ instrument, quote, status }: { instrument: Instrument; quote?: Quote; status: FeedStatus }) {
  const id = instrument.id;
  const spread = quote?.bid != null && quote.ask != null
    ? (Number(quote.ask) - Number(quote.bid)).toFixed(priceDecimals(id))
    : null;
  return (
    <header className="quote">
      <div className="quote-main">
        <h1>{instrument.symbol}</h1>
        <LivePrice id={id} quote={quote} className={`quote-last num ${status === "stale" ? "is-stale" : ""}`} />
        <span className="quote-currency">{instrument.currency} mid</span>
        <span className={`quote-change num ${direction(quote?.change_pct_24h)}`}>{percent(quote?.change_pct_24h)}</span>
        <StatusBadge status={status} />
      </div>
      <dl className="quote-stats">
        <div><dt>Last trade</dt><dd className="num">{price(id, quote?.last)}</dd></div>
        <div><dt>Bid</dt><dd className="num">{price(id, quote?.bid)}</dd></div>
        <div><dt>Ask</dt><dd className="num">{price(id, quote?.ask)}</dd></div>
        <div><dt>Spread</dt><dd className="num">{spread ?? "n/a"}</dd></div>
        <div><dt>24h high</dt><dd className="num">{price(id, quote?.high_24h)}</dd></div>
        <div><dt>24h low</dt><dd className="num">{price(id, quote?.low_24h)}</dd></div>
        <div><dt>24h volume</dt><dd className="num">{quantity(quote?.volume_24h)}</dd></div>
      </dl>
    </header>
  );
}

export function Terminal({ user, onSignedOut }: { user: User; onSignedOut: () => void }) {
  const client = useQueryClient();
  const now = useNow();
  const instrumentsQuery = useQuery({ queryKey: ["instruments"], queryFn: api.instruments });
  const instruments = instrumentsQuery.data ?? [];
  const ids = useMemo(() => instruments.map((i) => i.id), [instruments]);
  const idsKey = ids.join(",");

  const [view, setView] = useState<View>(() => remembered<View>("tt.view", "trade", ["trade", "portfolio", "risk", "feed"]));
  const [chosen, setChosen] = useState<string>(() => remembered<string>("tt.instrument", ""));
  const [interval, setIntervalChoice] = useState<Interval>(() => remembered<Interval>("tt.interval", "15m", INTERVALS));
  const [activeList, setActiveList] = useState<string | null>(() => remembered<string>("tt.watchlist", "") || null);
  const [searching, setSearching] = useState(false);
  const [indicators, setIndicators] = useState<string[]>(() => user.settings?.indicators ?? []);
  const [tool, setTool] = useState<string | null>(null);
  const [clearDrawings, setClearDrawings] = useState(0);
  const ticket = useRef<TicketHandle>(null);
  const selected = ids.includes(chosen) ? chosen : ids[0];

  const socket = useLive((s) => s.socket);
  const liveQuotes = useLive((s) => s.quotes);
  const epoch = useLive((s) => s.epoch);
  const account = useQuery({ queryKey: ["account"], queryFn: api.account, refetchInterval: 3000 });
  const orders = useQuery({ queryKey: ["orders"], queryFn: () => api.orders() });
  const fills = useQuery({ queryKey: ["fills"], queryFn: api.fills });
  const positions = useQuery({ queryKey: ["positions"], queryFn: api.positions, refetchInterval: 3000 });
  const lists = useWatchlists();
  const currentList = lists.data?.find((w) => w.id === activeList) ?? lists.data?.[0];

  useEffect(() => {
    if (!idsKey) return;
    return startLive(idsKey.split(","), onSignedOut);
  }, [idsKey, onSignedOut]);

  useEffect(() => onOrder(() => {
    for (const name of ["account", "positions", "orders", "fills"]) void client.invalidateQueries({ queryKey: [name] });
  }), [client]);

  useEffect(() => {
    if (epoch === 0) return;
    for (const name of ["account", "positions", "orders", "fills"]) void client.invalidateQueries({ queryKey: [name] });
  }, [epoch, client]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearching(true);
        return;
      }
      const target = event.target as HTMLElement;
      if (target.closest("input, select, textarea, dialog") || event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "b" || key === "s") {
        event.preventDefault();
        setView("trade");
        ticket.current?.start(key === "b" ? "buy" : "sell");
      } else if (event.key === "Escape") {
        ticket.current?.reset();
        setTool(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const details = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["instrument", id],
      queryFn: () => api.instrument(id),
      refetchInterval: 5000,
    })),
  });

  const quotes: Record<string, Quote | undefined> = {};
  const statuses: Record<string, FeedStatus> = {};
  ids.forEach((id, index) => {
    const detail = details[index]?.data;
    quotes[id] = liveQuotes[id] ?? detail?.quote ?? undefined;
    statuses[id] = socket === "reconnecting"
      ? "offline"
      : detail?.stale
        ? "stale"
        : quotes[id] && detail
          ? "live"
          : "waiting";
  });

  const logout = useMutation({ mutationFn: api.logout, onSettled: onSignedOut });
  const saveIndicators = useMutation({ mutationFn: (next: string[]) => api.saveSettings({ indicators: next }) });
  const instrument = instruments.find((i) => i.id === selected);

  const choose = (id: string) => {
    setChosen(id);
    remember("tt.instrument", id);
    setView("trade");
  };
  const chooseView = (next: View) => {
    setView(next);
    remember("tt.view", next);
  };
  const chooseList = (id: string) => {
    setActiveList(id);
    remember("tt.watchlist", id);
  };
  const chooseInterval = (value: Interval) => {
    setIntervalChoice(value);
    remember("tt.interval", value);
  };
  const toggleIndicator = (name: string) => {
    const next = indicators.includes(name) ? indicators.filter((i) => i !== name) : [...indicators, name];
    setIndicators(next);
    saveIndicators.mutate(next);
  };

  // Equity and P&L move with every quote; the server remains the source for cash and fills.
  const liveAccount = useMemo<Account | undefined>(() => {
    const base = account.data;
    if (!base) return undefined;
    let equity = Number(base.cash);
    for (const p of positions.data ?? []) {
      equity += Number(p.qty) * Number(liveQuotes[p.instrument_id]?.bid ?? p.mark ?? p.avg_price);
    }
    const dayStart = Number(base.equity) - Number(base.daily_pnl);
    return {
      ...base,
      equity: equity.toFixed(10),
      daily_pnl: (equity - dayStart).toFixed(10),
      total_pnl: (equity - Number(base.starting_cash)).toFixed(10),
    };
  }, [account.data, positions.data, liveQuotes]);

  const halted = account.data?.halted ?? false;

  return (
    <div className="shell">
      <header className="topbar">
        <strong className="brand">Trading Terminal</strong>
        <nav className="views" aria-label="Main">
          {VIEWS.map((v) => (
            <button key={v.id} aria-current={view === v.id ? "page" : undefined} onClick={() => chooseView(v.id)}>{v.label}</button>
          ))}
        </nav>
        <span className="paper-tag">Paper</span>
        <span className="topbar-end">
          <span className="topstat"><span>Equity</span> <b className="num">{money(liveAccount?.equity)}</b></span>
          <span className="topstat"><span>Today</span> <b className={`num ${direction(liveAccount?.daily_pnl)}`}>{money(liveAccount?.daily_pnl, true)}</b></span>
          <time className="num topclock" title="Europe/Vienna">{clock(new Date(now))}</time>
          <KillSwitch halted={halted} />
          <span className="user">{user.username}</span>
          <button className="button quiet" onClick={() => logout.mutate()} disabled={logout.isPending}>
            {logout.isPending ? "Signing out…" : "Sign out"}
          </button>
        </span>
      </header>

      {halted && <p className="banner" role="alert">Kill switch is on. New entries are blocked, and exits still work.</p>}

      {instrumentsQuery.isError ? (
        <main className="center">
          <div className="notice" role="alert">
            <h1>Couldn't load instruments</h1>
            <button className="button" onClick={() => void instrumentsQuery.refetch()}>Try again</button>
          </div>
        </main>
      ) : !instrument ? (
        <main className="center" aria-busy={instrumentsQuery.isPending}>
          {!instrumentsQuery.isPending && <p className="dim">No instruments are being tracked.</p>}
        </main>
      ) : view === "portfolio" ? (
        <PortfolioView account={liveAccount} onSelect={choose} />
      ) : view === "risk" ? (
        <RiskView account={liveAccount} />
      ) : view === "feed" ? (
        <main className="page narrow"><FeedHealth user={user} instruments={instruments} statuses={statuses} now={now} /></main>
      ) : (
        <div className="workspace">
          <WatchlistRail
            instruments={instruments}
            selected={instrument.id}
            onSelect={choose}
            onSearch={() => setSearching(true)}
            quotes={quotes}
            statuses={statuses}
            activeList={currentList?.id ?? null}
            onActiveList={chooseList}
          />
          <main className="market">
            <QuoteHeader instrument={instrument} quote={quotes[instrument.id]} status={statuses[instrument.id]} />
            <div className="chart-tools">
              <div className="intervals" role="group" aria-label="Chart interval">
                {INTERVALS.map((value) => (
                  <button key={value} aria-pressed={value === interval} onClick={() => chooseInterval(value)}>
                    {INTERVAL_LABEL[value]}
                  </button>
                ))}
              </div>
              <div className="intervals" role="group" aria-label="Indicators">
                {INDICATORS.map((item) => (
                  <button key={item.name} aria-pressed={indicators.includes(item.name)} onClick={() => toggleIndicator(item.name)}>{item.label}</button>
                ))}
              </div>
              <div className="intervals" role="group" aria-label="Drawing tools">
                {DRAWING_TOOLS.map((item) => (
                  <button key={item.name} aria-pressed={tool === item.name} onClick={() => setTool((t) => (t === item.name ? null : item.name))}>{item.label}</button>
                ))}
                <button onClick={() => setClearDrawings((n) => n + 1)}>Clear</button>
              </div>
            </div>
            <PriceChart
              instrumentId={instrument.id}
              interval={interval}
              indicators={indicators}
              tool={tool}
              onToolDone={() => setTool(null)}
              clearDrawings={clearDrawings}
              orders={orders.data ?? []}
              fills={fills.data ?? []}
            />
            <Blotter onSelect={choose} />
          </main>
          <OrderTicket key={instrument.id} instrument={instrument} quote={quotes[instrument.id]} handle={ticket} />
        </div>
      )}

      <SearchPalette
        open={searching}
        onClose={() => setSearching(false)}
        instruments={instruments}
        quotes={quotes}
        watchlist={currentList}
        onSelect={choose}
      />
    </div>
  );
}
