import "server-only";
import { getEnv } from "@/lib/env";

/** Constant-time string compare, so key checks don't leak timing. */
function safeEqual(a: string, b: string) {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  }
  return diff === 0;
}

function unauthorized(message: string) {
  return Response.json({ error: message }, { status: 401 });
}

/**
 * Jury API: `X-API-Key` must match one of the comma-separated keys in the
 * API_KEYS secret. Returns a 401 response to send back, or null when allowed.
 */
export function checkApiKey(request: Request): Response | null {
  const given = request.headers.get("x-api-key") ?? "";
  const keys = (getEnv().API_KEYS ?? "")
    .split(",")
    .map((k) => k.trim())
    .filter(Boolean);
  if (!given || !keys.some((k) => safeEqual(given, k))) {
    return unauthorized("Missing or invalid X-API-Key");
  }
  return null;
}

/** Analyzer API: `Authorization: Bearer <ANALYZER_TOKEN>`. */
export function checkAnalyzerToken(request: Request): Response | null {
  const token = getEnv().ANALYZER_TOKEN ?? "";
  const header = request.headers.get("authorization") ?? "";
  const given = header.startsWith("Bearer ") ? header.slice(7) : "";
  if (!token || !given || !safeEqual(given, token)) {
    return unauthorized("Missing or invalid analyzer token");
  }
  return null;
}
