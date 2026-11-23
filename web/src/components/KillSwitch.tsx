import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { api } from "../lib/api";

export function KillSwitch({ halted }: { halted: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const client = useQueryClient();
  const [flatten, setFlatten] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    dialog.current?.close();
    setCode("");
    setFlatten(false);
    setError(null);
  };
  const options = {
    onSuccess: close,
    onError: (e: Error) => setError(e.message),
    onSettled: () => {
      for (const name of ["account", "positions", "orders", "fills", "risk", "preview"]) {
        void client.invalidateQueries({ queryKey: [name] });
      }
    },
  };
  const kill = useMutation({ mutationFn: () => api.kill(flatten, flatten ? code : undefined), ...options });
  const release = useMutation({ mutationFn: () => api.release(code), ...options });
  const busy = kill.isPending || release.isPending;
  const needsCode = halted || flatten;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (halted) release.mutate();
    else kill.mutate();
  };

  return (
    <>
      <button className={`button kill ${halted ? "is-on" : ""}`} onClick={() => dialog.current?.showModal()}>
        {halted ? "Trading halted" : "Kill switch"}
      </button>
      <dialog ref={dialog} className="confirm" aria-label="Kill switch" onClose={close}>
        <form onSubmit={submit}>
          <h2>{halted ? "Resume trading?" : "Stop all trading?"}</h2>
          <p>
            {halted
              ? "New entries are blocked while the kill switch is on. Exits still work."
              : "This cancels all your open orders and blocks new entries until you turn it off."}
          </p>
          {!halted && (
            <label className="check">
              <input type="checkbox" checked={flatten} onChange={(e) => setFlatten(e.target.checked)} />
              Also close all positions at market
            </label>
          )}
          {needsCode && (
            <label>
              2FA code
              <input className="code" value={code} inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} autoFocus />
            </label>
          )}
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="confirm-actions">
            <button type="button" className="button" onClick={close}>Keep as is</button>
            <button className={`button ${halted ? "primary" : "danger"}`} disabled={busy}>
              {busy ? "Working…" : halted ? "Turn off kill switch" : flatten ? "Stop and close all" : "Stop trading"}
            </button>
          </div>
        </form>
      </dialog>
    </>
  );
}
