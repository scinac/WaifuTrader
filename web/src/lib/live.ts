import { create } from "zustand";
import { api, ApiFailure, type Candle, type Order, type Quote } from "./api";

export type SocketState = "connecting" | "live" | "reconnecting";

export interface FeedAlert {
  key: number;
  type: string;
  instrument_id?: string;
  reason?: string;
  at: number;
}

interface LiveState {
  socket: SocketState;
  quotes: Record<string, Quote>;
  quoteAt: Record<string, number>;
  alerts: FeedAlert[];
  /** Increases on each reconnect, so views can refetch what they missed. */
  epoch: number;
}

const initial: LiveState = { socket: "connecting", quotes: {}, quoteAt: {}, alerts: [], epoch: 0 };

export const useLive = create<LiveState>(() => initial);

const candleListeners = new Set<(candle: Candle) => void>();

export function onCandle(listener: (candle: Candle) => void) {
  candleListeners.add(listener);
  return () => {
    candleListeners.delete(listener);
  };
}

const orderListeners = new Set<(order: Order) => void>();

export function onOrder(listener: (order: Order) => void) {
  orderListeners.add(listener);
  return () => {
    orderListeners.delete(listener);
  };
}

const BACKOFF_MS = [1000, 2000, 5000, 10000, 30000];
const SILENCE_LIMIT_MS = 40000; // the server sends a heartbeat every 15 s
const QUOTE_FLUSH_MS = 100;
const MAX_ALERTS = 30;

export function startLive(symbols: string[], onSignedOut: () => void) {
  let socket: WebSocket | null = null;
  let stopped = false;
  let attempt = 0;
  let everOpened = false;
  let lastMessage = 0;
  let retry: number | undefined;
  let flush: number | undefined;
  let alertKey = 0;
  let pending: Record<string, Quote> = {};

  useLive.setState(initial);

  const flushQuotes = () => {
    flush = undefined;
    const batch = pending;
    pending = {};
    const now = Date.now();
    useLive.setState((s) => ({
      quotes: { ...s.quotes, ...batch },
      quoteAt: { ...s.quoteAt, ...Object.fromEntries(Object.keys(batch).map((id) => [id, now])) },
    }));
  };

  const handle = (raw: string) => {
    lastMessage = Date.now();
    const message = JSON.parse(raw) as { channel: string; data: unknown };
    if (message.channel === "quotes") {
      const quote = message.data as Quote;
      pending[quote.instrument_id] = quote;
      flush ??= window.setTimeout(flushQuotes, QUOTE_FLUSH_MS);
    } else if (message.channel === "candles:1m") {
      candleListeners.forEach((listener) => listener(message.data as Candle));
    } else if (message.channel === "orders") {
      orderListeners.forEach((listener) => listener(message.data as Order));
    } else if (message.channel === "alerts") {
      const alert = message.data as Omit<FeedAlert, "key" | "at">;
      useLive.setState((s) => ({
        alerts: [{ ...alert, key: alertKey++, at: Date.now() }, ...s.alerts].slice(0, MAX_ALERTS),
      }));
    }
  };

  const connect = () => {
    if (stopped) return;
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${scheme}://${location.host}/ws`);
    socket = ws;
    let opened = false;

    ws.onopen = () => {
      opened = true;
      attempt = 0;
      lastMessage = Date.now();
      for (const channel of ["quotes", "candles:1m"]) {
        ws.send(JSON.stringify({ op: "subscribe", channel, symbols }));
      }
      useLive.setState((s) => ({ socket: "live", epoch: everOpened ? s.epoch + 1 : s.epoch }));
      everOpened = true;
    };
    ws.onmessage = (event) => handle(String(event.data));
    ws.onclose = () => {
      if (stopped) return;
      useLive.setState({ socket: "reconnecting" });
      // A rejected handshake has no readable close code, so ask the API why.
      if (!opened) {
        api.me().catch((error) => {
          if (error instanceof ApiFailure && error.status === 401) onSignedOut();
        });
      }
      retry = window.setTimeout(connect, BACKOFF_MS[Math.min(attempt++, BACKOFF_MS.length - 1)]);
    };
  };

  const watchdog = window.setInterval(() => {
    if (socket?.readyState === WebSocket.OPEN && Date.now() - lastMessage > SILENCE_LIMIT_MS) {
      socket.close();
    }
  }, 5000);

  connect();

  return () => {
    stopped = true;
    window.clearInterval(watchdog);
    window.clearTimeout(retry);
    window.clearTimeout(flush);
    socket?.close();
  };
}
