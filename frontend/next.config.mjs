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
  async redirects() {
    // The old demo storefront's URLs lead to the Shopify surfaces that replaced them.
    return [
      { source: "/merchant", destination: "/app", permanent: false },
      { source: "/merchant/:path*", destination: "/app/:path*", permanent: false },
      { source: "/assistant", destination: "/embed", permanent: false },
      { source: "/orders/:path*", destination: "/embed/orders/:path*", permanent: false },
    ];
  },
  async headers() {
    return [
      // The merchant console renders inside Shopify admin only (plus direct visits).
      { source: "/app/:path*", headers: [{ key: "Content-Security-Policy", value: "frame-ancestors https://admin.shopify.com https://*.myshopify.com;" }] },
      { source: "/app", headers: [{ key: "Content-Security-Policy", value: "frame-ancestors https://admin.shopify.com https://*.myshopify.com;" }] },
      // The shopper assistant is framed by the store's theme, which may be on a custom domain.
      { source: "/embed", headers: [{ key: "Content-Security-Policy", value: "frame-ancestors *;" }] },
      { source: "/embed/:path*", headers: [{ key: "Content-Security-Policy", value: "frame-ancestors *;" }] },
    ];
  },
};

export default nextConfig;
