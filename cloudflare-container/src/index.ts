import { Container, getContainer } from "@cloudflare/containers";

interface Env {
  APP: DurableObjectNamespace<App>;
  // Worker secret (wrangler secret put API_KEYS); passed into the container.
  API_KEYS: string;
}

/**
 * The whole service (ARCHITECTURE.md's "single deployment host"): FastAPI and
 * the analysis worker run inside this one container, sharing its SQLite file
 * and media directory. The Worker is only the HTTPS entrypoint.
 */
export class App extends Container<Env> {
  defaultPort = 8000;
  pingEndpoint = "localhost/api/health";
  // The container disk is wiped when the instance sleeps, which loses jobs and
  // results, so keep it awake well past any judging session.
  sleepAfter = "24h";
  // Processing must not depend on the internet (case §7, ARCHITECTURE.md).
  enableInternet = false;

  constructor(...args: ConstructorParameters<typeof Container<Env>>) {
    super(...args);
    const env = args[1];
    this.envVars = {
      API_KEYS: env.API_KEYS ?? "",
      STUB_MODE: "none",
      ALLOW_UNVERIFIED_RULES: "false",
      MAX_UPLOAD_MB: "95", // Workers reject request bodies over 100 MB.
    };
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // Always the same instance: the service keeps state on its local disk.
    return getContainer(env.APP, "main").fetch(request);
  },
} satisfies ExportedHandler<Env>;
