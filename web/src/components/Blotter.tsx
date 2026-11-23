import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Order } from "../lib/api";
import { amount, dateTime, direction, money, price } from "../lib/format";

type Tab = "positions" | "open" | "history" | "fills";

const TABS: { id: Tab; label: string }[] = [
  { id: "positions", label: "Positions" },
  { id: "open", label: "Open orders" },
  { id: "history", label: "Order history" },
  { id: "fills", label: "Fills" },
];

const TYPE_LABEL: Record<string, string> = {
  market: "Market", limit: "Limit", stop: "Stop", stop_limit: "Stop limit",
  take_profit: "Take profit", trailing_stop: "Trailing",
};

const symbol = (id: string) => id.split(":").at(-1);

function orderPrice(o: Order) {
  const id = o.instrument_id;
  if (o.type === "stop_limit") return `${price(id, o.stop_price)} → ${price(id, o.limit_price)}`;
  if (o.type === "limit") return price(id, o.limit_price);
  if (o.type === "stop") return price(id, o.stop_price);
  if (o.type === "take_profit") return price(id, o.take_profit);
  if (o.type === "trailing_stop") return o.trail_pct ? `${amount(o.trail_pct)}%` : money(o.trail_amount);
  return "Market";
}

export function Blotter({ onSelect }: { onSelect?: (id: string) => void }) {
  const client = useQueryClient();
  const [tab, setTab] = useState<Tab>("positions");
  const [error, setError] = useState<string | null>(null);
  const positions = useQuery({ queryKey: ["positions"], queryFn: api.positions, refetchInterval: 3000 });
  const orders = useQuery({ queryKey: ["orders"], queryFn: () => api.orders() });
  const fills = useQuery({ queryKey: ["fills"], queryFn: api.fills, enabled: tab === "fills" });

  const refresh = () => {
    for (const name of ["account", "positions", "orders", "fills"]) void client.invalidateQueries({ queryKey: [name] });
  };
  const options = { onSuccess: () => setError(null), onError: (e: Error) => setError(e.message), onSettled: refresh };
  const cancel = useMutation({ mutationFn: api.cancelOrder, ...options });
  const close = useMutation({ mutationFn: api.closePosition, ...options });

  const open = (orders.data ?? []).filter((o) => o.status === "accepted" || o.status === "partially_filled");
  const history = (orders.data ?? []).filter((o) => !open.includes(o));
  const counts: Partial<Record<Tab, number>> = { positions: positions.data?.length, open: open.length };

  const symbolCell = (id: string) => (
    <td>{onSelect ? <button className="link" onClick={() => onSelect(id)}>{symbol(id)}</button> : symbol(id)}</td>
  );

  return (
    <section className="blotter" aria-label="Positions and orders">
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}>
            {t.label}{counts[t.id] ? <span className="count">{counts[t.id]}</span> : null}
          </button>
        ))}
        {error && <span className="form-error tabs-error" role="alert">{error}</span>}
      </div>
      <div className="table-wrap">
        {tab === "positions" && (
          positions.data?.length ? (
            <table>
              <thead><tr><th>Symbol</th><th>Qty</th><th>Avg cost</th><th>Mark</th><th>Value</th><th>Unrealized</th><th>Realized</th><th /></tr></thead>
              <tbody>
                {positions.data.map((p) => (
                  <tr key={p.instrument_id}>
                    {symbolCell(p.instrument_id)}
                    <td>{amount(p.qty)}</td>
                    <td>{price(p.instrument_id, p.avg_price)}</td>
                    <td>{price(p.instrument_id, p.mark)}</td>
                    <td>{money(p.market_value)}</td>
                    <td className={direction(p.unrealized_pnl)}>{money(p.unrealized_pnl, true)}</td>
                    <td className={direction(p.realized_pnl)}>{money(p.realized_pnl, true)}</td>
                    <td><button className="button quiet" disabled={close.isPending} onClick={() => close.mutate(p.instrument_id)}>Close</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="empty">No open positions. Place a paper order from the ticket to start.</p>
        )}
        {tab === "open" && (
          open.length ? (
            <table>
              <thead><tr><th>Symbol</th><th>Side</th><th>Type</th><th>Qty</th><th>Price</th><th>Placed</th><th /></tr></thead>
              <tbody>
                {open.map((o) => (
                  <tr key={o.id}>
                    {symbolCell(o.instrument_id)}
                    <td className={o.side === "buy" ? "up" : "down"}>{o.side === "buy" ? "Buy" : "Sell"}</td>
                    <td>{TYPE_LABEL[o.type]}{o.triggered ? " (triggered)" : ""}</td>
                    <td>{amount(o.qty)}</td>
                    <td>{orderPrice(o)}</td>
                    <td>{dateTime(o.created_at)}</td>
                    <td><button className="button quiet" disabled={cancel.isPending} onClick={() => cancel.mutate(o.id)}>Cancel</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="empty">No open orders. Limit, stop and trailing orders wait here until they fill.</p>
        )}
        {tab === "history" && (
          history.length ? (
            <table>
              <thead><tr><th>Symbol</th><th>Side</th><th>Type</th><th>Qty</th><th>Fill price</th><th>Status</th><th>Time</th></tr></thead>
              <tbody>
                {history.map((o) => (
                  <tr key={o.id}>
                    {symbolCell(o.instrument_id)}
                    <td className={o.side === "buy" ? "up" : "down"}>{o.side === "buy" ? "Buy" : "Sell"}</td>
                    <td>{TYPE_LABEL[o.type]}</td>
                    <td>{amount(o.qty)}</td>
                    <td>{o.avg_fill_price ? price(o.instrument_id, o.avg_fill_price) : "n/a"}</td>
                    <td title={o.reason || undefined}>{o.status === "rejected" && o.reason ? `Rejected: ${o.reason.replaceAll("_", " ").toLowerCase()}` : o.status[0].toUpperCase() + o.status.slice(1)}</td>
                    <td>{dateTime(o.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="empty">Filled, cancelled and rejected orders appear here.</p>
        )}
        {tab === "fills" && (
          fills.data?.length ? (
            <table>
              <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Price</th><th>Fee</th><th>Time</th></tr></thead>
              <tbody>
                {fills.data.map((f) => (
                  <tr key={f.id}>
                    {symbolCell(f.instrument_id)}
                    <td className={f.side === "buy" ? "up" : "down"}>{f.side === "buy" ? "Buy" : "Sell"}</td>
                    <td>{amount(f.qty)}</td>
                    <td>{price(f.instrument_id, f.price)}</td>
                    <td>{money(f.fee)}</td>
                    <td>{dateTime(f.filled_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="empty">Your executed trades appear here, with fees.</p>
        )}
      </div>
    </section>
  );
}
