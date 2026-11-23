const PRICE_DECIMALS: Record<string, number> = {
  "kraken:BTC/EUR": 1,
  "kraken:ETH/EUR": 2,
  "kraken:SOL/EUR": 2,
  "kraken:XRP/EUR": 5,
  "kraken:LINK/EUR": 3,
};

export const priceDecimals = (id: string) => PRICE_DECIMALS[id] ?? 2;

// Intl formats decimal strings exactly, so prices never pass through a float.
const formatDecimal = (value: string, options: Intl.NumberFormatOptions) =>
  new Intl.NumberFormat("en-GB", options).format(value as unknown as number);

export function price(id: string, value: string | null | undefined) {
  if (value == null) return "n/a";
  const digits = priceDecimals(id);
  return formatDecimal(value, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function quantity(value: string | null | undefined, digits = 2) {
  if (value == null) return "n/a";
  return formatDecimal(value, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function percent(value: string | null | undefined) {
  if (value == null) return "n/a";
  return `${formatDecimal(value, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: "exceptZero",
  })}%`;
}

export function money(value: string | null | undefined, signed = false) {
  if (value == null) return "n/a";
  return `${formatDecimal(value, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: signed ? "exceptZero" : "auto",
  })} €`;
}

export function amount(value: string | null | undefined) {
  if (value == null) return "n/a";
  return formatDecimal(value, { maximumFractionDigits: 8 });
}

export function direction(value: string | null | undefined): "up" | "down" | "flat" {
  if (value == null) return "flat";
  const n = Number(value);
  return n > 0 ? "up" : n < 0 ? "down" : "flat";
}

const viennaTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/Vienna",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const viennaDateTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Europe/Vienna",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export const clock = (date: Date) => viennaTime.format(date);
export const dateTime = (value: string) => viennaDateTime.format(new Date(value));

export function age(seconds: number | null | undefined) {
  if (seconds == null) return "no data";
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}
