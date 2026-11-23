import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { Login } from "./components/Login";
import { Terminal } from "./components/Terminal";
import { api, ApiFailure, type User } from "./lib/api";

export function App() {
  const client = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });

  const signedIn = useCallback((user: User) => client.setQueryData(["me"], user), [client]);
  const signedOut = useCallback(() => {
    client.clear();
    void client.fetchQuery({ queryKey: ["me"], queryFn: api.me }).catch(() => undefined);
  }, [client]);

  if (me.data) return <Terminal user={me.data} onSignedOut={signedOut} />;
  if (me.isPending) return <main className="center" aria-busy="true" />;
  if (me.error instanceof ApiFailure && me.error.status === 401) return <Login onSignedIn={signedIn} />;

  return (
    <main className="center">
      <div className="notice" role="alert">
        <h1>Can't reach the server</h1>
        <p>Check that the backend is running on port 8000, then try again.</p>
        <button className="button" onClick={() => void me.refetch()} disabled={me.isFetching}>
          {me.isFetching ? "Trying…" : "Try again"}
        </button>
      </div>
    </main>
  );
}
