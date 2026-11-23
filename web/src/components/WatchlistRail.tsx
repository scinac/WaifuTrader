import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { api, type Instrument, type Quote, type Watchlist } from "../lib/api";
import { direction, percent } from "../lib/format";
import { StatusBadge } from "./FeedHealth";
import { LivePrice } from "./LivePrice";
import type { FeedStatus } from "./Terminal";

interface Props {
  instruments: Instrument[];
  selected: string;
  onSelect: (id: string) => void;
  onSearch: () => void;
  quotes: Record<string, Quote | undefined>;
  statuses: Record<string, FeedStatus>;
  activeList: string | null;
  onActiveList: (id: string) => void;
}

export function useWatchlists() {
  return useQuery({ queryKey: ["watchlists"], queryFn: api.watchlists });
}

export function WatchlistRail({ instruments, selected, onSelect, onSearch, quotes, statuses, activeList, onActiveList }: Props) {
  const client = useQueryClient();
  const lists = useWatchlists();
  const list: Watchlist | undefined = lists.data?.find((w) => w.id === activeList) ?? lists.data?.[0];
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const dragged = useRef<string | null>(null);

  const refresh = () => client.invalidateQueries({ queryKey: ["watchlists"] });
  const onError = (e: Error) => setError(e.message);
  const update = useMutation({
    mutationFn: (symbols: string[]) => api.updateWatchlist(list!.id, { symbols }),
    onMutate: (symbols) => {
      client.setQueryData<Watchlist[]>(["watchlists"], (rows) => rows?.map((w) => (w.id === list!.id ? { ...w, symbols } : w)));
    },
    onError,
    onSettled: refresh,
  });
  const create = useMutation({
    mutationFn: api.createWatchlist,
    onSuccess: (row) => {
      setNaming(false);
      setName("");
      onActiveList(row.id);
    },
    onError,
    onSettled: refresh,
  });
  const remove = useMutation({ mutationFn: api.deleteWatchlist, onError, onSettled: refresh });

  const move = (id: string, target: string) => {
    if (!list || id === target) return;
    const next = list.symbols.filter((s) => s !== id);
    next.splice(next.indexOf(target) + (list.symbols.indexOf(id) < list.symbols.indexOf(target) ? 1 : 0), 0, id);
    update.mutate(next);
  };

  const submitName = (event: FormEvent) => {
    event.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  };

  const byId = new Map(instruments.map((i) => [i.id, i]));
  const rows = (list?.symbols ?? []).map((id) => byId.get(id)).filter((i): i is Instrument => Boolean(i));

  return (
    <nav className="rail" aria-label="Watchlist">
      <button className="search-trigger" onClick={onSearch}>
        <span>Search instruments</span><kbd>Ctrl K</kbd>
      </button>
      <div className="rail-lists">
        <select aria-label="Watchlist" value={list?.id ?? ""} onChange={(e) => onActiveList(e.target.value)}>
          {lists.data?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
        <button className="button quiet" onClick={() => setNaming((v) => !v)} aria-expanded={naming}>New</button>
        {(lists.data?.length ?? 0) > 1 && list && (
          <button className="button quiet" onClick={() => { remove.mutate(list.id); onActiveList(lists.data!.find((w) => w.id !== list.id)!.id); }}>Delete</button>
        )}
      </div>
      {naming && (
        <form className="rail-name" onSubmit={submitName}>
          <input aria-label="New watchlist name" value={name} maxLength={64} onChange={(e) => setName(e.target.value)} placeholder="List name" autoFocus />
          <button className="button" disabled={!name.trim() || create.isPending}>Add</button>
        </form>
      )}
      {error && <p className="form-error rail-error" role="alert">{error}</p>}

      <div className="rail-items">
        {rows.map((instrument) => {
          const quote = quotes[instrument.id];
          const status = statuses[instrument.id];
          return (
            <div
              key={instrument.id}
              className="rail-row"
              draggable
              onDragStart={() => { dragged.current = instrument.id; }}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => { if (dragged.current) move(dragged.current, instrument.id); dragged.current = null; }}
            >
              <button className="rail-item" aria-current={instrument.id === selected ? "true" : undefined} onClick={() => onSelect(instrument.id)}>
                <span className="rail-symbol">{instrument.symbol}</span>
                <LivePrice id={instrument.id} quote={quote} className="rail-price num" />
                <span className="rail-name">{status === "live" ? instrument.name : <StatusBadge status={status ?? "waiting"} />}</span>
                <span className={`rail-change num ${direction(quote?.change_pct_24h)}`}>{percent(quote?.change_pct_24h)}</span>
              </button>
              <button className="rail-remove" aria-label={`Remove ${instrument.symbol} from this watchlist`} title="Remove" onClick={() => update.mutate(list!.symbols.filter((s) => s !== instrument.id))}>
                <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
              </button>
            </div>
          );
        })}
        {lists.data && rows.length === 0 && <p className="empty">This list is empty. Use search to add instruments.</p>}
      </div>
    </nav>
  );
}
