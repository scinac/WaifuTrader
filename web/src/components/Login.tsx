import { useState, type FormEvent } from "react";
import { api, ApiFailure, type User } from "../lib/api";
import { dateTime } from "../lib/format";

type Mode = "login" | "confirm";

function explain(error: unknown) {
  if (!(error instanceof ApiFailure)) return "Something went wrong. Try again.";
  if (error.code === "ACCOUNT_LOCKED") {
    const until = error.details.locked_until;
    return typeof until === "string"
      ? `Too many failed sign-ins. This account is locked until ${dateTime(until)}.`
      : "Too many failed sign-ins. This account is locked for 15 minutes.";
  }
  if (error.code === "INVALID_CREDENTIALS") return "That username, password or 2FA code is wrong.";
  if (error.code === "TOTP_INVALID") return "That code doesn't match. Check your authenticator and try the current one.";
  if (error.code === "VALIDATION_ERROR") return "Fill in every field.";
  return error.message;
}

export function Login({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "confirm") {
        await api.confirmTotp(username, password, code);
        setMode("login");
        setCode("");
        setNote("2FA is on. Wait for the next code, because each code works only once.");
      } else {
        onSignedIn(await api.login(username, password, code));
      }
    } catch (failure) {
      if (failure instanceof ApiFailure && failure.code === "TOTP_SETUP_REQUIRED") {
        setMode("confirm");
        setCode("");
        setNote(null);
        setError(null);
      } else if (failure instanceof ApiFailure && failure.code === "TOTP_ALREADY_ENABLED") {
        setMode("login");
        setCode("");
        setNote("2FA is already on. Sign in with a new code.");
      } else {
        setError(explain(failure));
      }
    } finally {
      setBusy(false);
    }
  };

  const confirming = mode === "confirm";

  return (
    <main className="center">
      <form className="login" onSubmit={submit}>
        <h1>{confirming ? "Finish 2FA setup" : "Trading Terminal"}</h1>
        <p className="login-lead">
          {confirming
            ? "Enter the current code from the authenticator app you added when your account was created."
            : "Sign in with your password and authenticator code."}
        </p>

        {!confirming && (
          <>
            <label>
              Username
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                autoFocus
                required
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>
          </>
        )}

        <label>
          2FA code
          <input
            className="code"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            title="Six digits from your authenticator app"
            autoFocus={confirming}
            required
          />
        </label>

        {note && <p className="form-note" role="status">{note}</p>}
        {error && <p className="form-error" role="alert">{error}</p>}

        <button className="button primary" disabled={busy}>
          {busy ? "Checking…" : confirming ? "Turn on 2FA" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
