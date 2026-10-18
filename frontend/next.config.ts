import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

// FastAPI serves the built site from `out/`, so production is a static
// export. Static exports can't use rewrites, so only `next dev` proxies
// /api to the FastAPI process.
const API_ORIGIN = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

export default function config(phase: string): NextConfig {
  const base: NextConfig = {
    // /jobs/ -> out/jobs/index.html, which FastAPI's StaticFiles serves as-is.
    trailingSlash: true,
    images: { unoptimized: true },
  };

  if (phase === PHASE_DEVELOPMENT_SERVER) {
    return {
      ...base,
      // Otherwise trailingSlash redirects /api/jobs to /api/jobs/ before the rewrite.
      skipTrailingSlashRedirect: true,
      // The dev proxy buffers request bodies (10 MB by default); videos are larger.
      experimental: { proxyClientMaxBodySize: "600mb" },
      async rewrites() {
        return [{ source: "/api/:path*", destination: `${API_ORIGIN}/api/:path*` }];
      },
    };
  }
  return { ...base, output: "export" };
}
