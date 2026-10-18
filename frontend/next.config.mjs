import path from "node:path";
import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const backend = process.env.PERKPILOT_API_URL || "http://127.0.0.1:8000";

const nextConfig = {
  // Pin the workspace root to this folder so a stray lockfile higher up (e.g. ~/package-lock.json) isn't picked up.
  outputFileTracingRoot: path.dirname(fileURLToPath(import.meta.url)),
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
