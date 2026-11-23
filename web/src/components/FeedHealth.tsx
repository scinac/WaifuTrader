import { useQuery } from "@tanstack/react-query";
import { api, type Instrument, type User } from "../lib/api";
import { age, clock, dateTime } from "../lib/format";
import { useLive, type FeedAlert } from "../lib/live";
import type { FeedStatus } from "./Terminal";

const STATUS_LABEL: Record<FeedStatus, string> = {
  live: "Live",
  stale: "Stale",
  offline: "No connection",
  waiting: "Waiting",
};

export function StatusBadge({ status }: { status: FeedStatus }) {
  return <span className={`status status-${status}`}>{STATUS_LABEL[status]}</span>;
}

function describeAlert(alert: FeedAlert) {
  const symbol = alert.instrument_id?.split(":").at(-1);
  switch (alert.type) {
    case "feed_stale":
      return `${symbol} stopped updating`;
    case "feed_recovered":
      return `${symbol} is updating again`;
    case "broker_disconnected":
      return `Kraken disconnected${alert.reason ? `: ${alert.reason}` : ""}`;
    case "backfill_failed":
      return `${symbol} history backfill failed${alert.reason ? `: ${alert.reason}` : ""}`;
    default:
      return alert.type.replaceAll("_", " ");
  }
}

interface Props {
  user: User;
  instruments: Instrument[];
  statuses: Record<string, FeedStatus>;
  now: number;
}

export function FeedHealth({ user, instruments, statuses, now }: Props) {
  const socket = useLive((s) => s.socket);
  const quoteAt = useLive((s) => s.quoteAt);
  const alerts = useLive((s) => s.alerts);
  const isAdmin = user.role === "admin";
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    enabled: isAdmin,
    refetchInterval: 5000,
  });
  const feed = health.data?.marketdata.enabled ? health.data.marketdata : null;

  return (
    <aside className="health" aria-label="Feed health">
      <h2>Feed health</h2>

      <dl className="rows">
        <div>
          <dt>Browser stream</dt>
          <dd>
            <StatusBadge status={socket === "live" ? "live" : socket === "connecting" ? "waiting" : "offline"} />
          </dd>
        </div>
        {isAdmin && (
          <div>
            <dt>Kraken</dt>
            <dd>
              {feed ? (
                <StatusBadge status={feed.connected ? "live" : "offline"} />
              ) : health.isError ? (
                <span className="dim">Unavailable</span>
              ) : (
                <span className="dim">Checking…</span>
              )}
            </dd>
          </div>
        )}
      </dl>

      {instruments.map((instrument) => {
        const received = quoteAt[instrument.id];
        const stored = health.data?.bars_1m[instrument.id];
        return (
          <section key={instrument.id} className="health-symbol">
            <h3>
              {instrument.symbol}
              <StatusBadge status={statuses[instrument.id] ?? "waiting"} />
            </h3>
            <dl className="rows">
              <div>
                <dt>Last update</dt>
                <dd>{age(received ? Math.max(0, (now - received) / 1000) : null)}</dd>
              </div>
              {stored && (
                <>
                  <div>
                    <dt>1m bars stored</dt>
                    <dd>{stored.count.toLocaleString("en-GB")}</dd>
                  </div>
                  <div>
                    <dt>Latest bar</dt>
                    <dd>{stored.latest ? dateTime(stored.latest) : "none"}</dd>
                  </div>
                </>
              )}
            </dl>
          </section>
        );
      })}

      {feed && (
        <section className="health-symbol">
          <h3>Since server start</h3>
          <dl className="rows">
            <div>
              <dt>Reconnects</dt>
              <dd>{feed.reconnects}</dd>
            </div>
            <div>
              <dt>Bars written</dt>
              <dd>{feed.bars_written.toLocaleString("en-GB")}</dd>
            </div>
            <div>
              <dt>Bars backfilled</dt>
              <dd>{feed.backfilled_bars.toLocaleString("en-GB")}</dd>
            </div>
          </dl>
          {feed.last_disconnect_reason && <p className="dim small">Last drop: {feed.last_disconnect_reason}</p>}
        </section>
      )}

      <section className="health-symbol">
        <h3>Alerts</h3>
        {alerts.length === 0 ? (
          <p className="dim small">Nothing since you opened this page. Feed drops and recoveries appear here.</p>
        ) : (
          <ol className="alerts" aria-live="polite">
            {alerts.map((alert) => (
              <li key={alert.key}>
                <time>{clock(new Date(alert.at))}</time>
                <span>{describeAlert(alert)}</span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </aside>
  );
}
