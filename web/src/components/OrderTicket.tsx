import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useImperativeHandle, useMemo, useRef, useState, type FormEvent, type Ref } from "react";
import { api, ApiFailure, type Instrument, type OrderDraft, type OrderType, type Quote } from "../lib/api";
import { amount, money, price } from "../lib/format";

const TYPES: { value: OrderType; label: string }[] = [
  { value: "market", label: "Market" },
  { value: "limit", label: "Limit" },
  { value: "stop", label: "Stop" },
  { value: "stop_limit", label: "Stop limit" },
  { value: "take_profit", label: "Take profit" },
  { value: "trailing_stop", label: "Trailing stop" },
];

export interface TicketHandle {
  start: (side: "buy" | "sell") => void;
  reset: () => void;
}

const newKey = () => crypto.randomUUID();
const isPositive = (value: string) => /^\d*\.?\d+$/.test(value) && Number(value) > 0;

function useDebounced<T>(value: T, ms: number) {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(timer);
  }, [value, ms]);
  return settled;
}

interface Props {
  instrument: Instrument;
  quote?: Quote;
  handle: Ref<TicketHandle>;
}

export function OrderTicket({ instrument, quote, handle }: Props) {
  const client = useQueryClient();
  const id = instrument.id;
  const base = instrument.symbol.split("/")[0];
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState<OrderType>("market");
  const [unit, setUnit] = useState<"base" | "eur">("base");
  const [size, setSize] = useState("");
  const [limit, setLimit] = useState("");
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  const [trail, setTrail] = useState("");
  const [trailUnit, setTrailUnit] = useState<"pct" | "eur">("pct");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const key = useRef(newKey());
  const sizeInput = useRef<HTMLInputElement>(null);

  const reset = () => {
    setSize("");
    setLimit("");
    setStop("");
    setTarget("");
    setTrail("");
    setResult(null);
    key.current = newKey();
  };

  useImperativeHandle(handle, () => ({
    start: (next) => {
      setSide(next);
      setResult(null);
      sizeInput.current?.focus();
    },
    reset,
  }));

  useEffect(reset, [id]);

  const reference = side === "buy" ? quote?.ask : quote?.bid;
  const qty = useMemo(() => {
    if (!isPositive(size)) return "";
    if (unit === "base") return size;
    const basis = type === "limit" || type === "stop_limit" ? limit : type === "stop" ? stop : type === "take_profit" ? target : reference;
    if (!basis || !isPositive(basis)) return "";
    const units = Math.floor((Number(size) / Number(basis)) * 1e8) / 1e8;
    return units > 0 ? units.toFixed(8) : "";
  }, [size, unit, type, limit, stop, target, reference]);

  const draft = useMemo<OrderDraft | null>(() => {
    if (!qty) return null;
    const next: OrderDraft = { instrument_id: id, side, type, qty, tif: "gtc" };
    if (type === "limit" || type === "stop_limit") {
      if (!isPositive(limit)) return null;
      next.limit_price = limit;
    }
    if (type === "stop" || type === "stop_limit") {
      if (!isPositive(stop)) return null;
      next.stop_price = stop;
    }
    if (type === "take_profit") {
      if (!isPositive(target)) return null;
      next.take_profit = target;
    }
    if (type === "trailing_stop") {
      if (!isPositive(trail)) return null;
      if (trailUnit === "pct") next.trail_pct = trail;
      else next.trail_amount = trail;
    }
    return next;
  }, [id, side, type, qty, limit, stop, target, trail, trailUnit]);

  // A market order's quantity in euros moves with the price, so don't re-key on every tick.
  const stableDraft = useDebounced(draft, 250);
  const preview = useQuery({
    queryKey: ["preview", stableDraft],
    queryFn: () => api.preview(stableDraft!),
    enabled: stableDraft !== null,
    staleTime: 1000,
  });

  const place = useMutation({
    mutationFn: (order: OrderDraft) => api.placeOrder(order, key.current),
    onSuccess: (order) => {
      key.current = newKey();
      setSize("");
      setResult({
        ok: true,
        text: order.status === "filled"
          ? `Filled ${amount(order.qty)} ${base} at ${price(id, order.avg_fill_price)}`
          : `Order placed for ${amount(order.qty)} ${base}`,
      });
    },
    onError: (error) => {
      // A dropped connection keeps the key, so retrying can't create a second order.
      if (!(error instanceof ApiFailure && error.status === 0)) key.current = newKey();
      setResult({ ok: false, text: error instanceof Error ? error.message : "The order failed." });
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["preview"] });
      for (const name of ["account", "positions", "orders", "fills"]) {
        void client.invalidateQueries({ queryKey: [name] });
      }
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (draft) place.mutate(draft);
  };

  const current = draft && stableDraft === draft ? preview.data : undefined;
  const blocked = current?.ok === false;

  return (
    <form className="ticket" onSubmit={submit} aria-label={`Order ticket for ${instrument.symbol}`}>
      <div className="ticket-head">
        <h2>{instrument.symbol}</h2>
        <span className="paper-tag">Paper</span>
      </div>

      <div className="side-toggle" role="group" aria-label="Side">
        <button type="button" className="buy" aria-pressed={side === "buy"} onClick={() => setSide("buy")}>Buy</button>
        <button type="button" className="sell" aria-pressed={side === "sell"} onClick={() => setSide("sell")}>Sell</button>
      </div>

      <label>
        Order type
        <select value={type} onChange={(e) => setType(e.target.value as OrderType)}>
          {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      </label>

      <label>
        Size
        <span className="field-group">
          <input ref={sizeInput} inputMode="decimal" value={size} onChange={(e) => setSize(e.target.value)} placeholder="0.00" autoComplete="off" />
          <select aria-label="Size unit" value={unit} onChange={(e) => { setUnit(e.target.value as "base" | "eur"); setSize(""); }}>
            <option value="base">{base}</option>
            <option value="eur">EUR</option>
          </select>
        </span>
      </label>

      {(type === "stop" || type === "stop_limit") && (
        <label>Stop price<input inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)} autoComplete="off" /></label>
      )}
      {(type === "limit" || type === "stop_limit") && (
        <label>Limit price<input inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} autoComplete="off" /></label>
      )}
      {type === "take_profit" && (
        <label>Target price<input inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} autoComplete="off" /></label>
      )}
      {type === "trailing_stop" && (
        <label>
          Trail
          <span className="field-group">
            <input inputMode="decimal" value={trail} onChange={(e) => setTrail(e.target.value)} autoComplete="off" />
            <select aria-label="Trail unit" value={trailUnit} onChange={(e) => setTrailUnit(e.target.value as "pct" | "eur")}>
              <option value="pct">%</option>
              <option value="eur">EUR</option>
            </select>
          </span>
        </label>
      )}
      <dl className="rows ticket-preview" aria-live="polite">
        <div><dt>Est. price</dt><dd>{price(id, current?.estimated_price ?? reference)}</dd></div>
        <div><dt>Fee (0.40%)</dt><dd>{current?.fee ? money(current.fee) : "n/a"}</dd></div>
        <div><dt>{side === "buy" ? "Total cost" : "You receive"}</dt><dd>{current?.total ? money(current.total) : "n/a"}</dd></div>
        <div><dt>Position after</dt><dd>{current?.resulting_qty ? `${amount(current.resulting_qty)} ${base}` : "n/a"}</dd></div>
        <div><dt>Available</dt><dd>{current?.available_cash ? money(current.available_cash) : "n/a"}</dd></div>
      </dl>

      {current?.warnings.map((warning) => <p key={warning} className="form-warn">{warning}</p>)}
      {blocked && <p className="form-error" role="alert">{current.message}</p>}
      {result && <p className={result.ok ? "form-note" : "form-error"} role="status">{result.text}</p>}

      <button className={`button submit ${side}`} disabled={!draft || blocked || place.isPending}>
        {place.isPending ? "Sending…" : `${side === "buy" ? "Buy" : "Sell"} ${base}`}
      </button>
    </form>
  );
}
