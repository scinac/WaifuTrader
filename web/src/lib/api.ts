export interface User {
  id: string;
  username: string;
  role: string;
  settings?: { indicators?: string[] } & Record<string, unknown>;
}

export type Role = "trader" | "admin";

export interface AdminUser {
  id: string;
  username: string;
  role: Role;
  totp_enabled: boolean;
  disabled: boolean;
  locked_until: string | null;
  created_at: string;
}

export interface TotpSetup {
  username: string;
  totp_secret: string;
  totp_uri: string;
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

export interface Account {
  id: string;
  mode: string;
  currency: string;
  cash: string;
  equity: string;
  buying_power: string;
  starting_cash: string;
  total_pnl: string;
  realized_pnl: string;
  daily_pnl: string;
  daily_loss_limit: string;
  halted: boolean;
  open_orders: number;
}

export interface Position {
  instrument_id: string;
  qty: string;
  avg_price: string;
  mark: string | null;
  market_value: string | null;
  unrealized_pnl: string | null;
  realized_pnl: string;
}

export type OrderType = "market" | "limit" | "stop" | "stop_limit" | "take_profit" | "trailing_stop";

export interface OrderDraft {
  instrument_id: string;
  side: "buy" | "sell";
  type: OrderType;
  qty: string;
  limit_price?: string;
  stop_price?: string;
  take_profit?: string;
  trail_amount?: string;
  trail_pct?: string;
  tif?: "day" | "gtc" | "ioc";
}

export interface Order {
  id: string;
  instrument_id: string;
  side: "buy" | "sell";
  type: OrderType;
  qty: string;
  limit_price: string | null;
  stop_price: string | null;
  take_profit: string | null;
  trail_amount: string | null;
  trail_pct: string | null;
  status: string;
  reason: string;
  filled_qty: string;
  avg_fill_price: string | null;
  trail_anchor: string | null;
  triggered: boolean;
  created_at: string;
}

export interface FillRow {
  id: string;
  order_id: string;
  instrument_id: string;
  side: "buy" | "sell";
  qty: string;
  price: string;
  fee: string;
  filled_at: string;
}

export interface Preview {
  ok: boolean;
  code: string | null;
  message: string | null;
  estimated_price: string | null;
  notional: string | null;
  fee: string | null;
  total: string | null;
  resulting_qty: string | null;
  available_cash: string | null;
  warnings: string[];
}

export interface RiskLimits {
  daily_loss_eur: string;
  max_position_pct: string;
  max_orders_per_min: number;
  pending: Record<string, string>;
  pending_raise_at: string | null;
  halted: boolean;
}

export interface Watchlist {
  id: string;
  name: string;
  symbols: string[];
}

export interface SavedDrawing {
  name: string;
  points: { timestamp?: number; value?: number }[];
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
      headers: {
        ...(init?.body ? { "content-type": "application/json" } : {}),
        ...(init?.headers as Record<string, string> | undefined),
      },
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

const send = <T = unknown>(method: string, path: string, body?: unknown, headers?: Record<string, string>) =>
  request<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body), headers });

const post = (path: string, body?: unknown) => send("POST", path, body);
const totpHeader = (code?: string): Record<string, string> | undefined => (code ? { "X-TOTP": code } : undefined);

// Instrument ids keep their slash: the backend routes them as path parameters.
const instrumentPath = (id: string) => `/instruments/${encodeURI(id)}`;

export const api = {
  me: () => request<User>("/me"),
  login: async (username: string, password: string, totp: string) =>
    ((await post("/auth/login", { username, password, totp, client: "web" })) as { user: User }).user,
  confirmTotp: (username: string, password: string, code: string) =>
    post("/auth/totp/setup", { username, password, code }),
  bootstrapOpen: async () => (await request<{ open: boolean }>("/auth/bootstrap")).open,
  bootstrap: (username: string, password: string) =>
    send<TotpSetup>("POST", "/auth/bootstrap", { username, password }),
  register: (username: string, password: string) =>
    send<TotpSetup>("POST", "/auth/register", { username, password }),
  logout: () => post("/auth/logout"),
  saveSettings: (settings: Record<string, unknown>) => send<User>("PATCH", "/me/settings", { settings }),
  instruments: () => request<Instrument[]>("/instruments/search?q=kraken"),
  instrument: (id: string) => request<InstrumentDetail>(instrumentPath(id)),
  bars: async (id: string, interval: Interval, opts: { from?: Date; to?: Date; limit: number }) => {
    const query = new URLSearchParams({ interval, limit: String(opts.limit) });
    if (opts.from) query.set("from", opts.from.toISOString());
    if (opts.to) query.set("to", opts.to.toISOString());
    return (await request<{ bars: Candle[] }>(`${instrumentPath(id)}/bars?${query}`)).bars;
  },
  health: () => request<Health>("/admin/health"),
  users: () => request<AdminUser[]>("/admin/users"),
  setRole: (id: string, role: Role, code: string) =>
    send<AdminUser>("PATCH", `/admin/users/${id}`, { role }, totpHeader(code)),

  account: async () => (await request<Account[]>("/accounts"))[0],
  positions: () => request<Position[]>("/positions"),
  orders: (status?: string) => request<Order[]>(`/orders${status ? `?status=${status}` : ""}`),
  fills: () => request<FillRow[]>("/fills"),
  preview: (draft: OrderDraft) => send<Preview>("POST", "/orders/preview", draft),
  placeOrder: (draft: OrderDraft, key: string) => send<Order>("POST", "/orders", draft, { "Idempotency-Key": key }),
  cancelOrder: (id: string) => send<Order>("DELETE", `/orders/${id}`),
  closePosition: (id: string) => send<Order>("POST", `/positions/${encodeURI(id)}/close`),
  kill: (closePositions: boolean, code?: string) =>
    send<{ cancelled: number; closed: number }>("POST", "/killswitch", { close_positions: closePositions }, totpHeader(code)),
  release: (code: string) => send("DELETE", "/killswitch", undefined, totpHeader(code)),
  riskLimits: () => request<RiskLimits>("/risk-limits"),
  saveRiskLimits: (changes: Record<string, string | number>, code?: string) =>
    send<RiskLimits>("PUT", "/risk-limits", changes, totpHeader(code)),
  watchlists: () => request<Watchlist[]>("/watchlists"),
  createWatchlist: (name: string) => send<Watchlist>("POST", "/watchlists", { name, symbols: [] }),
  updateWatchlist: (id: string, patch: Partial<Pick<Watchlist, "name" | "symbols">>) =>
    send<Watchlist>("PATCH", `/watchlists/${id}`, patch),
  deleteWatchlist: (id: string) => send("DELETE", `/watchlists/${id}`),
  drawings: async (id: string) =>
    (await request<{ items: SavedDrawing[] }>(`/drawings/${encodeURI(id)}`)).items,
  saveDrawings: (id: string, items: SavedDrawing[]) => send("PUT", `/drawings/${encodeURI(id)}`, { items }),
};
