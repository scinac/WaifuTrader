export interface User {
  id: string;
  username: string;
  role: string;
}

export interface Instrument {
  id: string;
  broker: string;
  symbol: string;
  name: string;
  currency: string;
  asset_class: string;
}

export interface Quote {
  instrument_id: string;
  ts: string;
  bid: string | null;
  ask: string | null;
  last: string | null;
  volume_24h: string | null;
  change_pct_24h: string | null;
  high_24h: string | null;
  low_24h: string | null;
  delayed: boolean;
}

export interface InstrumentDetail extends Instrument {
  quote: Quote | null;
  stale: boolean | null;
}

export type Interval = "1m" | "5m" | "15m" | "1h" | "4h" | "1d" | "1w";

export interface Candle {
  instrument_id: string;
  interval: Interval;
  ts: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
}

export interface FeedSymbolHealth {
  stale: boolean;
  seconds_since_update: number | null;
  late_trades: number;
}

export interface Health {
  server_time: string;
  marketdata:
    | { enabled: false }
    | {
        enabled: true;
        broker: string;
        connected: boolean;
        reconnects: number;
        last_disconnect_reason: string;
        bars_written: number;
        backfilled_bars: number;
        symbols: Record<string, FeedSymbolHealth>;
      };
  bars_1m: Record<string, { count: number; latest: string | null }>;
}

export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api/v1${path}`, {
      credentials: "same-origin",
      ...init,
      headers: init?.body ? { "content-type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiFailure(0, "NETWORK", "Can't reach the server. Check that the backend is running.");
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const error = body?.error;
    throw new ApiFailure(
      res.status,
      error?.code ?? "SERVER_ERROR",
      error?.message ?? `The server answered with an error (${res.status}).`,
      error?.details,
    );
  }
  return body as T;
}

const post = (path: string, body?: unknown) =>
  request<unknown>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

// Instrument ids keep their slash: the backend routes them as path parameters.
const instrumentPath = (id: string) => `/instruments/${encodeURI(id)}`;

export const api = {
  me: () => request<User>("/me"),
  login: async (username: string, password: string, totp: string) =>
    ((await post("/auth/login", { username, password, totp, client: "web" })) as { user: User }).user,
  confirmTotp: (username: string, password: string, code: string) =>
    post("/auth/totp/setup", { username, password, code }),
  logout: () => post("/auth/logout"),
  instruments: () => request<Instrument[]>("/instruments/search?q=kraken"),
  instrument: (id: string) => request<InstrumentDetail>(instrumentPath(id)),
  bars: async (id: string, interval: Interval, opts: { from?: Date; to?: Date; limit: number }) => {
    const query = new URLSearchParams({ interval, limit: String(opts.limit) });
    if (opts.from) query.set("from", opts.from.toISOString());
    if (opts.to) query.set("to", opts.to.toISOString());
    return (await request<{ bars: Candle[] }>(`${instrumentPath(id)}/bars?${query}`)).bars;
  },
  health: () => request<Health>("/admin/health"),
};
