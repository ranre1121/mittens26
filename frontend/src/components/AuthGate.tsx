"use client";

import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { AUTH_REQUIRED_EVENT, fetchSession, login, logout, type Session } from "@/lib/session";

type AuthContextValue = { session: Session; signOut: () => Promise<void> };

const AuthContext = createContext<AuthContextValue>({ session: { status: "checking" }, signOut: async () => {} });

export function useAuth() {
  return useContext(AuthContext);
}

/** Holds the login state for the whole page (header and content). */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>({ status: "checking" });

  useEffect(() => {
    let cancelled = false;
    fetchSession().then((next) => !cancelled && setSession(next));
    // Any /api call that gets 401 (e.g. the 12-hour session ran out) returns here.
    const onAuthRequired = () => setSession((current) => (current.status === "open" ? current : { status: "login" }));
    window.addEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
    return () => {
      cancelled = true;
      window.removeEventListener(AUTH_REQUIRED_EVENT, onAuthRequired);
    };
  }, []);

  const signOut = useCallback(async () => {
    await logout();
    setSession({ status: "login" });
  }, []);

  return <AuthContext.Provider value={{ session, signOut }}>{children}</AuthContext.Provider>;
}

/** Shows the page only after login (or when the server has no login at all). */
export function AuthGate({ children }: { children: ReactNode }) {
  const { session } = useAuth();

  if (session.status === "open" || session.status === "signed-in") return <>{children}</>;
  if (session.status === "checking") {
    return <p className="auth-checking" role="status">Проверка доступа…</p>;
  }
  if (session.status === "error") {
    return (
      <section className="auth-card" aria-labelledby="auth-title">
        <h1 id="auth-title">Сервис недоступен</h1>
        <p className="auth-hint">{session.message}</p>
        <button type="button" className="auth-submit" onClick={() => window.location.reload()}>
          Повторить
        </button>
      </section>
    );
  }
  return <LoginForm />;
}

function LoginForm() {
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!key.trim() || busy) return;
    setBusy(true);
    const message = await login(key.trim());
    setBusy(false);
    if (message) {
      setError(message);
      return;
    }
    // Reload so every page section starts with the new session cookie.
    window.location.reload();
  }

  return (
    <section className="auth-card" aria-labelledby="auth-title">
      <h1 id="auth-title">Вход</h1>
      <p className="auth-hint">Введите ключ доступа к сервису. Вход действует 12 часов.</p>
      <form onSubmit={submit} className="auth-form">
        <label htmlFor="auth-key">Ключ доступа</label>
        <input
          id="auth-key"
          type="password"
          autoComplete="current-password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "auth-error" : undefined}
          autoFocus
        />
        {error && (
          <p id="auth-error" className="auth-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="auth-submit" disabled={busy || !key.trim()}>
          {busy ? "Проверка…" : "Войти"}
        </button>
      </form>
    </section>
  );
}
