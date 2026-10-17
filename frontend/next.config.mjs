/** @type {import('next').NextConfig} */
const backend = process.env.PERKPILOT_API_URL || "http://127.0.0.1:8000";

const nextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
