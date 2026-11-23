import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api, type Instrument, type Quote, type Watchlist } from "../lib/api";
import { direction, percent, price } from "../lib/format";

interface Props {
  open: boolean;
  onClose: () => void;
  instruments: Instrument[];
  quotes: Record<string, Quote | undefined>;
  watchlist?: Watchlist;
  onSelect: (id: string) => void;
}

export function SearchPalette({ open, onClose, instruments, quotes, watchlist, onSelect }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const client = useQueryClient();
  const [text, setText] = useState("");
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    const element = dialog.current!;
    if (open && !element.open) {
      setText("");
      setCursor(0);
      element.showModal();
    } else if (!open && element.open) {
      element.close();
    }
  }, [open]);

  const results = useMemo(() => {
    const q = text.trim().toLowerCase();
    return q ? instruments.filter((i) => `${i.symbol} ${i.name} ${i.id}`.toLowerCase().includes(q)) : instruments;
  }, [text, instruments]);

  const add = useMutation({
    mutationFn: (id: string) => api.updateWatchlist(watchlist!.id, { symbols: [...watchlist!.symbols, id] }),
    onSettled: () => client.invalidateQueries({ queryKey: ["watchlists"] }),
  });

  const choose = (id: string) => {
    onSelect(id);
    onClose();
  };

  const onKey = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((c) => Math.min(c + 1, results.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (event.key === "Enter" && results[cursor]) {
      event.preventDefault();
      choose(results[cursor].id);
    }
  };

  return (
    <dialog ref={dialog} className="palette" aria-label="Search instruments" onClose={onClose} onClick={(e) => { if (e.target === dialog.current) onClose(); }}>
      <input
        value={text}
        onChange={(e) => { setText(e.target.value); setCursor(0); }}
        onKeyDown={onKey}
        placeholder="Search by symbol or name"
        aria-label="Search by symbol or name"
        autoFocus
      />
      <ul>
        {results.map((instrument, index) => {
          const quote = quotes[instrument.id];
          const listed = watchlist?.symbols.includes(instrument.id);
          return (
            <li key={instrument.id} className={index === cursor ? "is-cursor" : undefined} onMouseEnter={() => setCursor(index)}>
              <button className="palette-pick" onClick={() => choose(instrument.id)}>
                <strong>{instrument.symbol}</strong>
                <span className="dim">{instrument.name}</span>
                <span className="num">{price(instrument.id, quote?.last)}</span>
                <span className={`num ${direction(quote?.change_pct_24h)}`}>{percent(quote?.change_pct_24h)}</span>
              </button>
              {watchlist && (
                <button className="button quiet" disabled={listed || add.isPending} onClick={() => add.mutate(instrument.id)}>
                  {listed ? "Listed" : "Add to list"}
                </button>
              )}
            </li>
          );
        })}
        {results.length === 0 && <li className="empty">No instrument matches "{text}".</li>}
      </ul>
    </dialog>
  );
}
