/**
 * Browser login for the interface. The user types the API key once; the server
 * answers with an HttpOnly session cookie (12 hours) that the browser then sends
 * with every /api request, including <video> and <img>.
 *
 * A server without login (the Cloudflare Worker deployment) answers
 * GET /api/session with 404, and the interface stays open.
 */

export const AUTH_REQUIRED_EVENT = "kassa:auth-required";

/** Cookie-authenticated writes must carry this header (request-forgery guard). */
export const CSRF_HEADER: [string, string] = ["X-Requested-With", "kassa-control"];

export type Session =
  | { status: "checking" }
  | { status: "open" }
  | { status: "login" }
  | { status: "error"; message: string }
  | { status: "signed-in"; method: "cookie" | "header" | "local"; expiresAt?: number };

export function notifyAuthRequired() {
  window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT));
}

/** fetch for /api calls: sends the session cookie and reports an expired login. */
export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const res = await fetch(input, { credentials: "same-origin", ...init });
  if (res.status === 401) notifyAuthRequired();
  return res;
}

export async function fetchSession(): Promise<Session> {
  try {
    const res = await fetch("/api/session", { cache: "no-store", credentials: "same-origin" });
    if (res.status === 404) return { status: "open" };
    if (res.status === 401) return { status: "login" };
    if (!res.ok) return { status: "error", message: `Сервер недоступен (${res.status})` };
    const body = (await res.json()) as { method: "cookie" | "header" | "local"; expires_at?: number };
    return { status: "signed-in", method: body.method, expiresAt: body.expires_at };
  } catch {
    return { status: "error", message: "Нет соединения с сервером" };
  }
}

/** Returns null on success, or a message to show under the key field. */
export async function login(key: string): Promise<string | null> {
  let res: Response;
  try {
    res = await fetch("/api/session", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
  } catch {
    return "Нет соединения с сервером";
  }
  if (res.ok) return null;
  if (res.status === 401) return "Неверный ключ";
  if (res.status === 429) return "Слишком много попыток. Подождите несколько минут.";
  return `Не удалось войти (${res.status})`;
}

export async function logout(): Promise<void> {
  await fetch("/api/session", { method: "DELETE", credentials: "same-origin" }).catch(() => {});
}
