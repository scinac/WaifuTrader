import QRCode from "qrcode";
import { useEffect, useState, type FormEvent } from "react";
import { api, ApiFailure, type TotpSetup, type User } from "../lib/api";
import { dateTime } from "../lib/format";

// login: sign in. create: new account (the first one is the admin, later ones are traders).
// setup: show the new account's 2FA key and confirm a code. confirm: 2FA for an existing account.
type Mode = "login" | "create" | "setup" | "confirm";

const MIN_PASSWORD = 12;

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
  if (error.code === "USERNAME_TAKEN") return "That username is taken. Pick another.";
  if (error.code === "VALIDATION_ERROR") {
    const errors = (error.details.errors ?? []) as { loc?: unknown[] }[];
    const field = errors.map((e) => e.loc?.[e.loc.length - 1]).find((f) => typeof f === "string");
    if (field === "username") return "Usernames need 3 to 64 letters, digits, dots, dashes or underscores.";
    if (field) return `The server rejected the ${String(field).replace(/_/g, " ")} field.`;
    return "The server rejected the form. Check every field.";
  }
  return error.message;
}

/** The base32 key in groups of four, which is how authenticator apps ask for it. */
function grouped(secret: string) {
  return secret.match(/.{1,4}/g)?.join(" ") ?? secret;
}

/** QR code for the otpauth link, drawn locally so the secret never leaves the browser. */
function TotpQr({ uri }: { uri: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    // Dark on white with a quiet zone: authenticator cameras need the contrast.
    QRCode.toDataURL(uri, { margin: 2, width: 200, errorCorrectionLevel: "M", color: { dark: "#000000", light: "#ffffff" } })
      .then((url) => { if (live) setSrc(url); })
      .catch(() => { if (live) setSrc(null); });
    return () => { live = false; };
  }, [uri]);
  if (!src) return null;
  return <img className="totp-qr" src={src} width={200} height={200} alt="QR code to add this account to your authenticator app" />;
}

function CodeField({ code, setCode, autoFocus }: { code: string; setCode: (v: string) => void; autoFocus?: boolean }) {
  return (
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
        autoFocus={autoFocus}
        required
      />
    </label>
  );
}

export function Login({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [mode, setMode] = useState<Mode>("login");
  const [firstAccount, setFirstAccount] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  // A fresh install has no accounts: open straight on "create the admin account".
  useEffect(() => {
    let live = true;
    api.bootstrapOpen()
      .then((open) => {
        if (!live) return;
        setFirstAccount(open);
        if (open) setMode("create");
      })
      .catch(() => undefined);  // Sign-in still works if this check fails.
    return () => { live = false; };
  }, []);

  const go = (next: Mode) => {
    setMode(next);
    setCode("");
    setError(null);
    setNote(null);
  };

  const finishSetup = (message: string) => {
    setMode("login");
    setCode("");
    setRepeat("");
    setSetup(null);
    setFirstAccount(false);
    setNote(message);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (mode === "create") {
      if (password.length < MIN_PASSWORD) return setError(`Use at least ${MIN_PASSWORD} characters for the password.`);
      if (password !== repeat) return setError("The passwords don't match.");
    }
    setBusy(true);
    try {
      if (mode === "create") {
        const created = firstAccount
          ? await api.bootstrap(username.trim(), password)
          : await api.register(username.trim(), password);
        setUsername(created.username);
        setSetup(created);
        setCopied(false);
        go("setup");
      } else if (mode === "setup" || mode === "confirm") {
        await api.confirmTotp(username, password, code);
        finishSetup("2FA is on. Wait for the next code, because each code works only once.");
      } else {
        onSignedIn(await api.login(username, password, code));
      }
    } catch (failure) {
      if (failure instanceof ApiFailure && failure.code === "TOTP_SETUP_REQUIRED") {
        // Right password, 2FA never confirmed: the server sends the key again, so show the QR.
        const key = failure.details as Partial<TotpSetup>;
        if (key.totp_uri && key.totp_secret && key.username) {
          setSetup({ username: key.username, totp_secret: key.totp_secret, totp_uri: key.totp_uri });
          setCopied(false);
          go("setup");
        } else {
          go("confirm");
        }
      } else if (failure instanceof ApiFailure && failure.code === "TOTP_ALREADY_ENABLED") {
        finishSetup("2FA is already on. Sign in with a new code.");
      } else if (failure instanceof ApiFailure && failure.code === "BOOTSTRAP_CLOSED") {
        setFirstAccount(false);
        setError("The admin account exists now. Submit again to create a trader account.");
      } else {
        setError(explain(failure));
      }
    } finally {
      setBusy(false);
    }
  };

  const copyKey = async () => {
    if (!setup) return;
    try {
      await navigator.clipboard.writeText(setup.totp_secret);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  const heading = {
    login: "Trading Terminal",
    create: firstAccount ? "Create the admin account" : "Create your account",
    setup: "Set up 2FA",
    confirm: "Finish 2FA setup",
  }[mode];

  const lead = {
    login: "Sign in with your password and authenticator code.",
    create: firstAccount
      ? "No accounts exist yet. The first account becomes the admin."
      : "New accounts start as traders. An admin can make you an admin later.",
    setup: "Scan this QR code with your authenticator app, then enter the code it shows.",
    confirm: "Enter the current code from the authenticator app you added when your account was created.",
  }[mode];

  const action = {
    login: busy ? "Checking…" : "Sign in",
    create: busy ? "Creating…" : "Create account",
    setup: busy ? "Checking…" : "Turn on 2FA",
    confirm: busy ? "Checking…" : "Turn on 2FA",
  }[mode];

  return (
    <main className="center">
      <form className="login" onSubmit={submit}>
        <h1>{heading}</h1>
        <p className="login-lead">{lead}</p>

        {(mode === "login" || mode === "create") && (
          <>
            <label>
              Username
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                minLength={mode === "create" ? 3 : undefined}
                maxLength={64}
                pattern={mode === "create" ? "[A-Za-z0-9_.\\-]+" : undefined}
                title={mode === "create" ? "Letters, digits, dots, dashes and underscores" : undefined}
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
                autoComplete={mode === "create" ? "new-password" : "current-password"}
                required
              />
            </label>
          </>
        )}

        {mode === "create" && (
          <label>
            Repeat password
            <input
              type="password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              autoComplete="new-password"
              required
            />
            <span className="field-hint">At least {MIN_PASSWORD} characters.</span>
          </label>
        )}

        {mode === "setup" && setup && (
          <div className="totp-key">
            <TotpQr uri={setup.totp_uri} />
            <span className="field-label">Can't scan? Setup key for {setup.username}</span>
            <code className="num">{grouped(setup.totp_secret)}</code>
            <div className="totp-actions">
              <button type="button" className="button quiet" onClick={copyKey}>
                {copied ? "Copied" : "Copy key"}
              </button>
              <a className="button quiet" href={setup.totp_uri}>Open in authenticator</a>
            </div>
            <span className="field-hint">Scan the code with your authenticator app, or enter the key and pick time based. It's shown only now.</span>
          </div>
        )}

        {mode !== "create" && (
          <CodeField code={code} setCode={setCode} autoFocus={mode === "setup" || mode === "confirm"} />
        )}

        {note && <p className="form-note" role="status">{note}</p>}
        {error && <p className="form-error" role="alert">{error}</p>}

        <button className="button primary" disabled={busy}>{action}</button>

        {mode === "login" && (
          <p className="login-switch">
            New here? <button type="button" className="link" onClick={() => go("create")}>Create an account</button>
          </p>
        )}
        {mode === "create" && !firstAccount && (
          <p className="login-switch">
            Have an account? <button type="button" className="link" onClick={() => go("login")}>Sign in</button>
          </p>
        )}
      </form>
    </main>
  );
}
