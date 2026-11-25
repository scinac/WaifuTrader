import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, type Role, type User } from "../lib/api";
import { dateTime } from "../lib/format";

const ROLE_LABEL: Record<Role, string> = { admin: "Admin", trader: "Trader" };

/** Admin only: see every account and decide who is an admin. */
export function UsersView({ user }: { user: User }) {
  const client = useQueryClient();
  const users = useQuery({ queryKey: ["admin-users"], queryFn: api.users });

  const [pending, setPending] = useState<{ id: string; username: string; role: Role } | null>(null);
  const [code, setCode] = useState("");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const setRole = useMutation({
    mutationFn: () => api.setRole(pending!.id, pending!.role, code),
    onSuccess: (saved) => {
      setNote({ ok: true, text: `${saved.username} is now ${ROLE_LABEL[saved.role].toLowerCase()}.` });
      setPending(null);
      setCode("");
      void client.invalidateQueries({ queryKey: ["admin-users"] });
    },
    onError: (e: Error) => setNote({ ok: false, text: e.message }),
  });

  const ask = (id: string, username: string, role: Role) => {
    setPending({ id, username, role });
    setCode("");
    setNote(null);
  };

  const confirm = (event: FormEvent) => {
    event.preventDefault();
    setNote(null);
    setRole.mutate();
  };

  return (
    <main className="page narrow">
      <h1>Users</h1>

      <section>
        <h3>Accounts</h3>
        {users.isError ? (
          <p className="form-error" role="alert">Couldn't load users. {users.error.message}</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="left">Username</th>
                  <th className="left">Role</th>
                  <th className="left">2FA</th>
                  <th className="left">Joined</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(users.data ?? []).map((u) => {
                  const self = u.id === user.id;
                  const next: Role = u.role === "admin" ? "trader" : "admin";
                  return (
                    <tr key={u.id}>
                      <td className="left">{u.username}{self && <span className="dim"> (you)</span>}</td>
                      <td className="left">{ROLE_LABEL[u.role]}</td>
                      <td className="left">{u.totp_enabled ? "On" : <span className="warn">Not set up</span>}</td>
                      <td className="left num">{dateTime(u.created_at)}</td>
                      <td>
                        {!self && (
                          <button className="button quiet" onClick={() => ask(u.id, u.username, next)}>
                            {next === "admin" ? "Make admin" : "Make trader"}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {pending && (
        <form className="settings" onSubmit={confirm}>
          <label>
            2FA code to make {pending.username} {ROLE_LABEL[pending.role].toLowerCase()}
            <input
              className="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              required
              autoFocus
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            />
          </label>
          {pending.role === "admin" && (
            <p className="dim small">Admins can change roles and see system health.</p>
          )}
          <div className="invite-row">
            <button className="button primary" disabled={setRole.isPending}>{setRole.isPending ? "Saving…" : "Confirm"}</button>
            <button type="button" className="button quiet" onClick={() => { setPending(null); setNote(null); }}>Cancel</button>
          </div>
        </form>
      )}
      {note && <p className={note.ok ? "form-note" : "form-error"} role="status">{note.text}</p>}
    </main>
  );
}
