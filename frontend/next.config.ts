import type { NextConfig } from "next";

const apiOrigin = (process.env.BANKEASE_API_ORIGIN ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const nextConfig: NextConfig = {
  // AI inference may take up to 120 seconds; Next's rewrite proxy defaults to 30 seconds.
  experimental: { proxyTimeout: 180_000 },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiOrigin}/api/:path*` },
      { source: "/documents/:path*", destination: `${apiOrigin}/documents/:path*` },
      { source: "/health", destination: `${apiOrigin}/health` },
    ];
  },
};

export default nextConfig;
