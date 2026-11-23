import { dispose, init, registerOverlay, type Chart, type DataLoader, type KLineData, type OverlayEvent } from "klinecharts";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type FillRow, type Interval, type Order, type SavedDrawing } from "../lib/api";
import { bucketStart, INTERVALS, LiveBar, PERIODS, toBar } from "../lib/bars";
import { midValue, priceDecimals } from "../lib/format";
import { onCandle, useLive } from "../lib/live";

export const INDICATORS = [
  { name: "MA", label: "SMA", main: true },
  { name: "EMA", label: "EMA", main: true },
  { name: "BOLL", label: "Bollinger", main: true },
  { name: "AVP", label: "Avg price", main: true },
  { name: "RSI", label: "RSI", main: false },
  { name: "MACD", label: "MACD", main: false },
];

export const DRAWING_TOOLS = [
  { name: "segment", label: "Trend" },
  { name: "horizontalStraightLine", label: "Level" },
  { name: "fibonacciLine", label: "Fib" },
];

// The live mid price: a line across the chart with a tag on the price axis.
registerOverlay({
  name: "midPriceLine",
  totalStep: 2,
  needDefaultPointFigure: false,
  needDefaultXAxisFigure: false,
  needDefaultYAxisFigure: false,
  createPointFigures: ({ coordinates, bounding, overlay }) => ({
    type: "line",
    ignoreEvent: true,
    attrs: { coordinates: [{ x: 0, y: coordinates[0].y }, { x: bounding.width, y: coordinates[0].y }] },
    styles: { style: "dashed", dashedValue: [3, 3], size: 1, color: (overlay.extendData as { color: string }).color },
  }),
  createYAxisFigures: ({ coordinates, bounding, overlay, yAxis }) => {
    const { color, ink, family, text } = overlay.extendData as { color: string; ink: string; family: string; text: string };
    const fromZero = yAxis?.isFromZero() ?? false;
    return {
      type: "text",
      ignoreEvent: true,
      attrs: { x: fromZero ? 0 : bounding.width, y: coordinates[0].y, text, align: fromZero ? "left" : "right", baseline: "middle" },
      styles: { style: "fill", color: ink, backgroundColor: color, size: 11, family, weight: 600, paddingLeft: 4, paddingRight: 4, paddingTop: 3, paddingBottom: 3, borderRadius: 2 },
    };
  },
});

const MID_LINE = "mid-line";
const CANDLE_PANE = "candle_pane";
const DRAWINGS = "drawings";
const TRADE_MARKS = "trade-marks";
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
        last: { show: false }, // the live mid line marks the current price
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

interface Props {
  instrumentId: string;
  interval: Interval;
  indicators: string[];
  tool: string | null;
  onToolDone: () => void;
  clearDrawings: number;
  orders: Order[];
  fills: FillRow[];
}

export function PriceChart({ instrumentId, interval, indicators, tool, onToolDone, clearDrawings, orders, fills }: Props) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<Chart | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const epoch = useLive((s) => s.epoch);
  const activeIndicators = useRef<Map<string, string>>(new Map());
  const drawingsFor = useRef<string | null>(null);
  const midLine = useRef<string | null>(null);
  const quote = useLive((s) => s.quotes[instrumentId]);
  const mid = midValue(quote);
  const toolDone = useRef(onToolDone);
  toolDone.current = onToolDone;

  const saveDrawings = useCallback(() => {
    const instance = chart.current;
    const id = drawingsFor.current;
    if (!instance || !id) return;
    const items: SavedDrawing[] = instance.getOverlays({ groupId: DRAWINGS }).map((overlay) => ({
      name: overlay.name,
      points: overlay.points.map((p) => ({ timestamp: p.timestamp, value: p.value })),
    }));
    void api.saveDrawings(id, items).catch(() => undefined);
  }, []);

  const drawingEvents = useCallback(() => ({
    groupId: DRAWINGS,
    onDrawEnd: () => {
      saveDrawings();
      toolDone.current();
      return false;
    },
    onPressedMoveEnd: () => {
      saveDrawings();
      return false;
    },
    onRightClick: (event: OverlayEvent<unknown>) => {
      chart.current?.removeOverlay({ id: event.overlay.id });
      saveDrawings();
      return true;
    },
  }), [saveDrawings]);

  useEffect(() => {
    const element = host.current!;
    const instance = init(element, {
      timezone: VIENNA,
      locale: "en-US",
      styles: chartStyles(),
      decimalFold: { threshold: 32 }, // show 0.0001, not 0.0{3}1
    });
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
      activeIndicators.current.clear();
      midLine.current = null;
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

  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    const active = activeIndicators.current;
    for (const name of [...active.keys()]) {
      if (!indicators.includes(name)) {
        instance.removeIndicator({ id: active.get(name) });
        active.delete(name);
      }
    }
    for (const item of INDICATORS) {
      if (!indicators.includes(item.name) || active.has(item.name)) continue;
      const id = item.main
        ? instance.createIndicator({ name: item.name, paneId: CANDLE_PANE }, true)
        : instance.createIndicator(item.name);
      if (id) active.set(item.name, id);
    }
  }, [indicators]);

  // Saved drawings belong to one instrument and are stored by time and price.
  useEffect(() => {
    let cancelled = false;
    drawingsFor.current = null;
    chart.current?.removeOverlay({ groupId: DRAWINGS });
    if (state !== "ready") return;
    api.drawings(instrumentId).then((items) => {
      if (cancelled || !chart.current) return;
      drawingsFor.current = instrumentId;
      for (const item of items) {
        chart.current.createOverlay({ name: item.name, points: item.points, ...drawingEvents() });
      }
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [instrumentId, state === "ready", drawingEvents]); // eslint-disable-line

  useEffect(() => {
    if (!tool || !chart.current) return;
    drawingsFor.current ??= instrumentId;
    const id = chart.current.createOverlay({ name: tool, ...drawingEvents() }) as string | null;
    return () => {
      const overlay = id ? chart.current?.getOverlays({ id })[0] : undefined;
      if (overlay && overlay.currentStep !== -1) chart.current?.removeOverlay({ id: id! });
    };
  }, [tool, instrumentId, drawingEvents]);

  useEffect(() => {
    if (clearDrawings === 0 || !chart.current) return;
    chart.current.removeOverlay({ groupId: DRAWINGS });
    saveDrawings();
  }, [clearDrawings, saveDrawings]);

  // Moves between trades too, because candles only change when something actually trades.
  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    const anchor = instance.getDataList().at(-1)?.timestamp;
    if (state !== "ready" || mid == null || anchor === undefined) {
      instance.removeOverlay({ groupId: MID_LINE });
      midLine.current = null;
      return;
    }
    const css = getComputedStyle(document.documentElement);
    const digits = priceDecimals(instrumentId) + 1;
    const extendData = {
      color: css.getPropertyValue("--accent").trim(),
      ink: css.getPropertyValue("--ground").trim(),
      family: css.getPropertyValue("--font").trim(),
      text: new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(mid as unknown as number),
    };
    const points = [{ timestamp: anchor, value: Number(mid) }];
    if (midLine.current) {
      instance.overrideOverlay({ id: midLine.current, points, extendData });
    } else {
      midLine.current = instance.createOverlay({
        name: "midPriceLine", groupId: MID_LINE, lock: true, zLevel: 10, points, extendData,
      }) as string | null;
    }
  }, [mid, state, instrumentId]);

  // Open orders are horizontal lines; fills are labelled marks on their candle.
  useEffect(() => {
    const instance = chart.current;
    if (!instance) return;
    instance.removeOverlay({ groupId: TRADE_MARKS });
    if (state !== "ready") return;
    const css = getComputedStyle(document.documentElement);
    const up = css.getPropertyValue("--up").trim();
    const down = css.getPropertyValue("--down").trim();
    const anchor = instance.getDataList().at(-1)?.timestamp;
    if (anchor === undefined) return;
    for (const order of orders) {
      if (order.instrument_id !== instrumentId || order.status !== "accepted") continue;
      for (const value of [order.limit_price, order.stop_price, order.take_profit]) {
        if (value == null) continue;
        instance.createOverlay({
          name: "horizontalStraightLine",
          groupId: TRADE_MARKS,
          lock: true,
          points: [{ timestamp: anchor, value: Number(value) }],
          styles: { line: { color: order.side === "buy" ? up : down, style: "dashed", dashedValue: [6, 4] } },
        });
      }
    }
    for (const fill of fills) {
      if (fill.instrument_id !== instrumentId) continue;
      instance.createOverlay({
        name: "simpleAnnotation",
        groupId: TRADE_MARKS,
        lock: true,
        extendData: fill.side === "buy" ? "B" : "S",
        points: [{ timestamp: bucketStart(Date.parse(fill.filled_at), interval), value: Number(fill.price) }],
        styles: {
          line: { color: fill.side === "buy" ? up : down },
          polygon: { color: fill.side === "buy" ? up : down },
          text: { color: css.getPropertyValue("--ground").trim(), backgroundColor: fill.side === "buy" ? up : down },
        },
      });
    }
  }, [orders, fills, instrumentId, interval, state]);

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
