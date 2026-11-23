import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { api, type Account } from "../lib/api";
import { dateTime, direction, money } from "../lib/format";
import { Blotter } from "./Blotter";

export function PortfolioView({ account, onSelect }: { account?: Account; onSelect: (id: string) => void }) {
  const unrealized = account ? String(Number(account.total_pnl) - Number(account.realized_pnl)) : null;
  const stats: [string, string | null | undefined, boolean][] = [
    ["Equity", account?.equity, false],
    ["Cash", account?.cash, false],
    ["Available", account?.buying_power, false],
    ["Today", account?.daily_pnl, true],
    ["Total P&L", account?.total_pnl, true],
    ["Realized", account?.realized_pnl, true],
    ["Unrealized", unrealized, true],
  ];
  return (
    <main className="page">
      <h1>Portfolio <span className="paper-tag">Paper account</span></h1>
      <dl className="stat-strip">
        {stats.map(([label, value, signed]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd className={`num ${signed ? direction(value) : ""}`}>{money(value, signed)}</dd>
          </div>
        ))}
      </dl>
      <p className="dim small">Started with {money(account?.starting_cash)} of simulated money. Fills use live Kraken prices with a 0.40% fee.</p>
      <Blotter onSelect={onSelect} />
    </main>
  );
}

export function RiskView({ account }: { account?: Account }) {
  const client = useQueryClient();
  const limits = useQuery({ queryKey: ["risk"], queryFn: api.riskLimits });
  const [loss, setLoss] = useState("");
  const [position, setPosition] = useState("");
  const [rate, setRate] = useState("");
  const [code, setCode] = useState("");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!limits.data) return;
    setLoss(String(Number(limits.data.daily_loss_eur)));
    setPosition(String(Number(limits.data.max_position_pct)));
    setRate(String(limits.data.max_orders_per_min));
  }, [limits.data]);

  const current = limits.data;
  const raising = Boolean(current) && (
    Number(loss) > Number(current!.daily_loss_eur) ||
    Number(position) > Number(current!.max_position_pct) ||
    Number(rate) > current!.max_orders_per_min
  );
  const save = useMutation({
    mutationFn: () => api.saveRiskLimits(
      { daily_loss_eur: loss, max_position_pct: position, max_orders_per_min: Number(rate) },
      raising ? code : undefined,
    ),
    onSuccess: (saved) => {
      setCode("");
      const waiting = Object.keys(saved.pending).length > 0;
      setNote({ ok: true, text: waiting ? "Saved. Raised limits apply from midnight Vienna time." : "Limits saved." });
      client.setQueryData(["risk"], saved);
      void client.invalidateQueries({ queryKey: ["account"] });
    },
    onError: (e: Error) => setNote({ ok: false, text: e.message }),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setNote(null);
    save.mutate();
  };

  const lossToday = account ? Math.max(0, -Number(account.daily_pnl)) : 0;
  const lossLimit = Number(account?.daily_loss_limit ?? current?.daily_loss_eur ?? 0);
  const used = lossLimit ? Math.min(100, (lossToday / lossLimit) * 100) : 0;
  const pending = current?.pending ?? {};
  const pendingLabel: Record<string, string> = {
    daily_loss_eur: "Daily loss", max_position_pct: "Position size", max_orders_per_min: "Orders per minute",
  };

  return (
    <main className="page narrow">
      <h1>Risk limits</h1>
      <div className="limit-bar">
        <div className="limit-head">
          <span>Daily loss used</span>
          <span className="num">{money(String(lossToday))} of {money(String(lossLimit))}</span>
        </div>
        <div className="meter" role="meter" aria-label="Daily loss used" aria-valuenow={Math.round(used)} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${used}%` }} className={used >= 80 ? "hot" : ""} />
        </div>
      </div>
      <form className="settings" onSubmit={submit}>
        <label>Daily loss limit (EUR)<input inputMode="decimal" value={loss} onChange={(e) => setLoss(e.target.value)} required /></label>
        <label>Max position size (% of equity)<input inputMode="decimal" value={position} onChange={(e) => setPosition(e.target.value)} required /></label>
        <label>Max orders per minute<input inputMode="numeric" value={rate} onChange={(e) => setRate(e.target.value.replace(/\D/g, ""))} required /></label>
        {raising && (
          <label>
            2FA code, required to raise a limit
            <input className="code" inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
          </label>
        )}
        <p className="dim small">Lowering a limit applies now. Raising one needs 2FA and applies from midnight Vienna time.</p>
        {note && <p className={note.ok ? "form-note" : "form-error"} role="status">{note.text}</p>}
        <button className="button primary" disabled={save.isPending || !current}>{save.isPending ? "Saving…" : "Save limits"}</button>
      </form>
      {Object.keys(pending).length > 0 && (
        <section className="health-symbol">
          <h3>Waiting to apply{current?.pending_raise_at ? ` on ${dateTime(current.pending_raise_at)}` : ""}</h3>
          <dl className="rows">
            {Object.entries(pending).map(([key, value]) => (
              <div key={key}><dt>{pendingLabel[key] ?? key}</dt><dd>{Number(value)}</dd></div>
            ))}
          </dl>
        </section>
      )}
    </main>
  );
}
