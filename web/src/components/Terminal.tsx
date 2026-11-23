import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Instrument, type Interval, type Quote, type User } from "../lib/api";
import { INTERVALS } from "../lib/bars";
import { clock, direction, percent, price, quantity } from "../lib/format";
import { startLive, useLive } from "../lib/live";
import { FeedHealth, StatusBadge } from "./FeedHealth";
import { PriceChart } from "./PriceChart";

export type FeedStatus = "live" | "stale" | "offline" | "waiting";

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

function LastPrice({ id, value, className }: { id: string; value: string | null | undefined; className: string }) {
  const previous = useRef(value);
  const tick = useRef<"up" | "down" | "">("");
  if (value !== previous.current) {
    if (value != null && previous.current != null) {
      tick.current = Number(value) > Number(previous.current) ? "up" : "down";
    }
    previous.current = value;
  }
  return (
    <span key={value ?? "none"} className={`${className} ${tick.current ? `tick-${tick.current}` : ""}`}>
      {price(id, value)}
    </span>
  );
}

function InstrumentRail({ instruments, selected, onSelect, quotes, statuses }: {
  instruments: Instrument[];
  selected: string;
  onSelect: (id: string) => void;
  quotes: Record<string, Quote | undefined>;
  statuses: Record<string, FeedStatus>;
}) {
  return (
    <nav className="rail" aria-label="Instruments">
      {instruments.map((instrument) => {
        const quote = quotes[instrument.id];
        const status = statuses[instrument.id];
        return (
          <button
            key={instrument.id}
            className="rail-item"
            aria-current={instrument.id === selected ? "true" : undefined}
            onClick={() => onSelect(instrument.id)}
          >
            <span className="rail-symbol">{instrument.symbol}</span>
            <LastPrice id={instrument.id} value={quote?.last} className="rail-price num" />
            <span className="rail-name">{status === "live" ? instrument.name : <StatusBadge status={status ?? "waiting"} />}</span>
            <span className={`rail-change num ${direction(quote?.change_pct_24h)}`}>{percent(quote?.change_pct_24h)}</span>
          </button>
        );
      })}
    </nav>
  );
}

function QuoteHeader({ instrument, quote, status }: { instrument: Instrument; quote?: Quote; status: FeedStatus }) {
  const id = instrument.id;
  const spread = quote?.bid != null && quote.ask != null ? (Number(quote.ask) - Number(quote.bid)).toFixed(2) : null;
  return (
    <header className="quote">
      <div className="quote-main">
        <h1>{instrument.symbol}</h1>
        <LastPrice id={id} value={quote?.last} className={`quote-last num ${status === "stale" ? "is-stale" : ""}`} />
        <span className="quote-currency">{instrument.currency}</span>
        <span className={`quote-change num ${direction(quote?.change_pct_24h)}`}>{percent(quote?.change_pct_24h)}</span>
        <StatusBadge status={status} />
      </div>
      <dl className="quote-stats">
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
  const now = useNow();
  const instrumentsQuery = useQuery({ queryKey: ["instruments"], queryFn: api.instruments });
  const instruments = instrumentsQuery.data ?? [];
  const ids = useMemo(() => instruments.map((i) => i.id), [instruments]);
  const idsKey = ids.join(",");

  const [chosen, setChosen] = useState<string>(() => remembered<string>("tt.instrument", ""));
  const [interval, setIntervalChoice] = useState<Interval>(() => remembered<Interval>("tt.interval", "15m", INTERVALS));
  const selected = ids.includes(chosen) ? chosen : ids[0];

  const socket = useLive((s) => s.socket);
  const liveQuotes = useLive((s) => s.quotes);

  useEffect(() => {
    if (!idsKey) return;
    return startLive(idsKey.split(","), onSignedOut);
  }, [idsKey, onSignedOut]);

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
  const instrument = instruments.find((i) => i.id === selected);

  const choose = (id: string) => {
    setChosen(id);
    remember("tt.instrument", id);
  };
  const chooseInterval = (value: Interval) => {
    setIntervalChoice(value);
    remember("tt.interval", value);
  };

  return (
    <div className="shell">
      <header className="topbar">
        <strong className="brand">Trading Terminal</strong>
        <span className="mode">Market data only</span>
        <span className="topbar-end">
          <time className="num" title="Europe/Vienna">{clock(new Date(now))}</time>
          <span className="user">{user.username}</span>
          <button className="button quiet" onClick={() => logout.mutate()} disabled={logout.isPending}>
            {logout.isPending ? "Signing out…" : "Sign out"}
          </button>
        </span>
      </header>

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
      ) : (
        <div className="workspace">
          <InstrumentRail instruments={instruments} selected={instrument.id} onSelect={choose} quotes={quotes} statuses={statuses} />
          <main className="market">
            <QuoteHeader instrument={instrument} quote={quotes[instrument.id]} status={statuses[instrument.id]} />
            <div className="intervals" role="group" aria-label="Chart interval">
              {INTERVALS.map((value) => (
                <button key={value} aria-pressed={value === interval} onClick={() => chooseInterval(value)}>
                  {INTERVAL_LABEL[value]}
                </button>
              ))}
            </div>
            <PriceChart instrumentId={instrument.id} interval={interval} />
          </main>
          <FeedHealth user={user} instruments={instruments} statuses={statuses} now={now} />
        </div>
      )}
    </div>
  );
}
