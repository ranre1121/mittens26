import "server-only";
import { getCloudflareContext } from "@opennextjs/cloudflare";
import { drizzle } from "drizzle-orm/d1";
import * as schema from "@/db/schema";

/** Bindings and secrets for the current request. Call inside a handler. */
export function getEnv() {
  return getCloudflareContext().env;
}

export function getDb() {
  return drizzle(getEnv().DB, { schema });
}
