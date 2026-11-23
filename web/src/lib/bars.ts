import type { KLineData, Period } from "klinecharts";
import type { Candle, Interval } from "./api";

const MINUTE = 60_000;
const STEP_MS: Record<Interval, number> = {
  "1m": MINUTE,
  "5m": 5 * MINUTE,
  "15m": 15 * MINUTE,
  "1h": 60 * MINUTE,
  "4h": 240 * MINUTE,
  "1d": 1440 * MINUTE,
  "1w": 10080 * MINUTE,
};
const FIRST_MONDAY = Date.UTC(1970, 0, 5); // matches the backend's weekly buckets

export const INTERVALS = Object.keys(STEP_MS) as Interval[];

export const PERIODS: Record<Interval, Period> = {
  "1m": { type: "minute", span: 1 },
  "5m": { type: "minute", span: 5 },
  "15m": { type: "minute", span: 15 },
  "1h": { type: "hour", span: 1 },
  "4h": { type: "hour", span: 4 },
  "1d": { type: "day", span: 1 },
  "1w": { type: "week", span: 1 },
};

export function bucketStart(timestamp: number, interval: Interval) {
  const anchor = interval === "1w" ? FIRST_MONDAY : 0;
  const step = STEP_MS[interval];
  return anchor + Math.floor((timestamp - anchor) / step) * step;
}

export const toBar = (candle: Candle): KLineData => ({
  timestamp: Date.parse(candle.ts),
  open: Number(candle.open),
  high: Number(candle.high),
  low: Number(candle.low),
  close: Number(candle.close),
  volume: Number(candle.volume),
});

/**
 * The feed only streams 1m candles, and each message repeats that minute's running volume.
 * This folds them into the visible interval without counting a minute's volume twice.
 */
export class LiveBar {
  private bar: KLineData | null;
  private baseVolume = 0;
  private minutes = new Map<number, number>();

  constructor(
    private readonly interval: Interval,
    last: KLineData | null,
    seedMinutes: KLineData[],
  ) {
    this.bar = last ? { ...last } : null;
    if (!last) return;
    for (const minute of seedMinutes) {
      if (bucketStart(minute.timestamp, interval) === last.timestamp) {
        this.minutes.set(minute.timestamp, minute.volume ?? 0);
      }
    }
    this.baseVolume = (last.volume ?? 0) - this.minuteVolume();
  }

  private minuteVolume() {
    let total = 0;
    for (const volume of this.minutes.values()) total += volume;
    return total;
  }

  apply(candle: Candle): KLineData | null {
    const minute = toBar(candle);
    const start = bucketStart(minute.timestamp, this.interval);
    if (this.bar && start < this.bar.timestamp) return null;

    if (!this.bar || start > this.bar.timestamp) {
      this.bar = { ...minute, timestamp: start };
      this.baseVolume = 0;
      this.minutes = new Map([[minute.timestamp, minute.volume ?? 0]]);
      return { ...this.bar };
    }

    this.minutes.set(minute.timestamp, minute.volume ?? 0);
    this.bar = {
      ...this.bar,
      high: Math.max(this.bar.high, minute.high),
      low: Math.min(this.bar.low, minute.low),
      close: minute.close,
      volume: this.baseVolume + this.minuteVolume(),
    };
    return { ...this.bar };
  }
}
