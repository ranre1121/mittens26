import type { NextConfig } from "next";
import { initOpenNextCloudflareForDev } from "@opennextjs/cloudflare";

const nextConfig: NextConfig = {};

export default nextConfig;

// Exposes the D1/R2 bindings from wrangler.jsonc to `next dev`.
initOpenNextCloudflareForDev();
