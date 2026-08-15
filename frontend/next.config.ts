import type { NextConfig } from "next";

// Defense-in-depth HTTP security headers applied to every response.
// The CSP is intentionally permissive on inline styles/scripts (Next.js +
// Tailwind inject them and the theme bootstrap script is inline) but still
// blocks the high-value vectors: no third-party frames, no foreign script
// origins, no foreign style origins, and WebSockets are restricted to the
// same host. Tighten further (nonce-based script-src) in a later pass.
const securityHeaders = [
  // Clickjacking protection (also enforced via CSP frame-ancestors).
  { key: "X-Frame-Options", value: "DENY" },
  // Stop MIME sniffing (e.g. treating a text response as HTML).
  { key: "X-Content-Type-Options", value: "nosniff" },
  // Don't leak full URLs to downstream origins.
  {
    key: "Referrer-Policy",
    value: "strict-origin-when-cross-origin",
  },
  // Disable powerful, unused browser features.
  {
    key: "Permissions-Policy",
    value:
      "camera=(), microphone=(), geolocation=(), interest-cohort=(), " +
      "browsing-topics=(), payment=()",
  },
  // Legacy XSS auditor is deprecated; CSP is the real protection.
  { key: "X-XSS-Protection", value: "0" },
  // Force HTTPS for a year (ignored on plain-HTTP dev, safe to send).
  {
    key: "Strict-Transport-Security",
    value: "max-age=63072000; includeSubDomains",
  },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      // Bundled + the inline theme bootstrap script.
      "script-src 'self' 'unsafe-inline'",
      // Next.js/Tailwind inject <style>; data: for next/font fallbacks.
      "style-src 'self' 'unsafe-inline'",
      // Avatars, markdown images, and next/font.
      "img-src 'self' data: blob: https:",
      "font-src 'self' data:",
      // API is same-origin (proxied); chat uses ws:/wss: to the backend.
      "connect-src 'self' ws: wss:",
      // No nested browsing contexts from other origins.
      "frame-ancestors 'none'",
      // Only our pages may be framed/embedded.
      "base-uri 'self'",
      "form-action 'self'",
      "object-src 'none'",
      "frame-src 'none'",
    ].join("; "),
  },
];

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
      {
        // Same-origin WebSocket proxy so the browser can connect to
        // wss://<host>/ws/<id>?ticket=<ticket> without cross-origin cookies.
        source: "/ws/:path*",
        destination: `${upstream}/ws/:path*`,
      },
    ];
  },
  // Apply security headers to every route (including proxied /api responses).
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders,
      },
    ];
  },
};

export default nextConfig;
