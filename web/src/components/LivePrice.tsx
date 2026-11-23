import { useRef } from "react";
import type { Quote } from "../lib/api";
import { midPrice, midValue } from "../lib/format";

/** The live mid price, with a brief color tick in the direction it moved. */
export function LivePrice({ id, quote, className }: { id: string; quote?: Quote; className: string }) {
  const value = midValue(quote) ?? quote?.last ?? null;
  const previous = useRef(value);
  const tick = useRef<"up" | "down" | "">("");
  if (value !== previous.current) {
    if (value != null && previous.current != null) {
      tick.current = Number(value) > Number(previous.current) ? "up" : "down";
    }
    previous.current = value;
  }
  return (
    <span key={value ?? "none"} className={`${className} ${tick.current ? `tick-${tick.current}` : ""}`}>
      {midPrice(id, quote)}
    </span>
  );
}
