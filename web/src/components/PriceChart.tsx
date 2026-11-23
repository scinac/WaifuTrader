import { dispose, init, type Chart, type DataLoader, type KLineData } from "klinecharts";
import { useEffect, useRef, useState } from "react";
import { api, type Interval } from "../lib/api";
import { INTERVALS, LiveBar, PERIODS, toBar } from "../lib/bars";
import { priceDecimals } from "../lib/format";
import { onCandle, useLive } from "../lib/live";

const PAGE_SIZE = 500;
const VIENNA = "Europe/Vienna";

type LoadState = "loading" | "ready" | "empty" | "error";

function chartStyles() {
  const css = getComputedStyle(document.documentElement);
  const token = (name: string) => css.getPropertyValue(`--${name}`).trim();
  const up = token("up");
  const down = token("down");
  const flat = token("muted");
  const line = token("line");
  const muted = token("muted");
  const text = token("text");
  const raised = token("raised");
  const family = token("font");
  const axis = { axisLine: { color: line }, tickLine: { color: line }, tickText: { color: muted, size: 11, family } };
  const crosshairLine = { color: muted, size: 1, style: "dashed" as const, dashedValue: [3, 3] };
  const crosshairText = { color: text, backgroundColor: raised, borderColor: line, size: 11, family };

  return {
    grid: {
      horizontal: { color: token("grid") },
      vertical: { color: token("grid") },
    },
    candle: {
      bar: {
        upColor: up, downColor: down, noChangeColor: flat,
        upBorderColor: up, downBorderColor: down, noChangeBorderColor: flat,
        upWickColor: up, downWickColor: down, noChangeWickColor: flat,
      },
      priceMark: {
        high: { color: muted, textFamily: family },
        low: { color: muted, textFamily: family },
        last: {
          upColor: up, downColor: down, noChangeColor: flat,
          text: { color: token("ground"), family },
        },
      },
      tooltip: {
        title: { show: false },
        legend: { color: muted, size: 12, family },
      },
    },
    indicator: {
      bars: [{ upColor: `${up}aa`, downColor: `${down}aa`, noChangeColor: `${flat}aa` }],
      lastValueMark: { show: false },
      tooltip: {
        title: { color: muted, size: 12, family },
        legend: { color: muted, size: 12, family },
      },
    },
    xAxis: axis,
    yAxis: axis,
    separator: { color: line, activeBackgroundColor: raised },
    crosshair: {
      horizontal: { line: crosshairLine, text: crosshairText },
      vertical: { line: crosshairLine, text: crosshairText },
    },
  };
}

const intervalOf = (period: { type: string; span: number }) =>
  INTERVALS.find((i) => PERIODS[i].type === period.type && PERIODS[i].span === period.span) ?? "1m";

export function PriceChart({ instrumentId, interval }: { instrumentId: string; interval: Interval }) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<Chart | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const epoch = useLive((s) => s.epoch);

  useEffect(() => {
    const element = host.current!;
    const instance = init(element, { timezone: VIENNA, locale: "en-US", styles: chartStyles() });
    if (!instance) return;
    chart.current = instance;
    instance.createIndicator({ name: "VOL", calcParams: [] });

    let loadToken = 0;
    let live: LiveBar | null = null;
    let stopLive: (() => void) | null = null;

    const loader: DataLoader = {
      getBars: async ({ type, timestamp, symbol, period, callback }) => {
        const id = symbol.ticker;
        const barInterval = intervalOf(period);
        if (type === "init") {
          const token = ++loadToken;
          live = null;
          setState("loading");
          try {
            const candles = await api.bars(id, barInterval, { limit: PAGE_SIZE });
            const bars = candles.map(toBar);
            const last = bars.at(-1) ?? null;
            let minutes: KLineData[] = [];
            if (last && barInterval !== "1m") {
              const seed = await api.bars(id, "1m", { from: new Date(last.timestamp), limit: 5000 });
              minutes = seed.map(toBar);
            }
            if (token !== loadToken) return;
            live = new LiveBar(barInterval, last, minutes);
            callback(bars, { forward: bars.length > 0, backward: false });
            setState(bars.length ? "ready" : "empty");
          } catch {
            if (token !== loadToken) return;
            callback([], false);
            setState("error");
          }
        } else if (type === "forward" && timestamp != null) {
          const token = loadToken;
          try {
            const candles = await api.bars(id, barInterval, { to: new Date(timestamp), limit: PAGE_SIZE });
            if (token !== loadToken) return;
            callback(candles.map(toBar), { forward: candles.length > 0, backward: false });
          } catch {
            if (token === loadToken) callback([], { forward: false, backward: false });
          }
        } else {
          callback([], false);
        }
      },
      subscribeBar: ({ symbol, callback }) => {
        stopLive?.();
        stopLive = onCandle((candle) => {
          if (candle.instrument_id !== symbol.ticker || !live) return;
          const bar = live.apply(candle);
          if (bar) {
            callback(bar);
            setState("ready");
          }
        });
      },
      unsubscribeBar: () => {
        stopLive?.();
        stopLive = null;
      },
    };
    instance.setDataLoader(loader);

    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(element);

    return () => {
      loadToken++;
      resize.disconnect();
      stopLive?.();
      dispose(element);
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    // Each setter reloads the chart, so only call the one that changed.
    const period = instance.getPeriod();
    if (!period || intervalOf(period) !== interval) instance.setPeriod(PERIODS[interval]);
    if (instance.getSymbol()?.ticker !== instrumentId) {
      instance.setSymbol({
        ticker: instrumentId,
        pricePrecision: priceDecimals(instrumentId),
        volumePrecision: 4,
      });
    }
  }, [instrumentId, interval]);

  // After a dropped connection, reload so missed candles appear.
  useEffect(() => {
    if (epoch > 0) chart.current?.resetData();
  }, [epoch]);

  return (
    <div className="chart-frame" aria-busy={state === "loading"}>
      <div ref={host} className="chart" role="img" aria-label={`${instrumentId} candlestick chart, ${interval} interval`} />
      {state === "empty" && (
        <p className="chart-message">No bars stored for this instrument yet. They appear as trades arrive.</p>
      )}
      {state === "error" && (
        <div className="chart-message" role="alert">
          <p>Couldn't load the price history.</p>
          <button className="button" onClick={() => chart.current?.resetData()}>Reload chart</button>
        </div>
      )}
    </div>
  );
}
