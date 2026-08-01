import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_API_URL:
      process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  },
  // Proxy /api/* to the FastAPI backend so the standalone Next.js server
  // (port 3001) works without Nginx in front. Nginx also proxies /api/* to
  // the backend directly, so both entry points behave identically.
  // Set API_PROXY_URL to the backend upstream (e.g. http://api:8000 inside
  // Docker Compose, or http://localhost:8000 for local `npm run dev`).
  async rewrites() {
    const upstream = process.env.API_PROXY_URL || "http://localhost:8000";
    return [
      {
        source: "/api/:path*",
        destination: `${upstream}/:path*`,
      },
    ];
  },
};

export default nextConfig;
