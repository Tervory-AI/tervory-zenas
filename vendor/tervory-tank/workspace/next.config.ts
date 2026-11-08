import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Bound build workers under CI so trace collection cannot hang the check harness.
  ...(process.env.CI || process.env.NEXT_TELEMETRY_DISABLED
    ? { experimental: { cpus: 1 } }
    : {}),
  async headers() {
    // The CLI approval page grants a credential: never framed, never cached.
    return [{
      source: "/:path*",
      headers: [{ key: "X-Content-Type-Options", value: "nosniff" }, { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" }],
    }, {
      source: "/sw.js",
      headers: [{ key: "Cache-Control", value: "no-cache, no-store" }, { key: "Service-Worker-Allowed", value: "/" }, { key: "X-Content-Type-Options", value: "nosniff" }],
    }, {
      // Public OFL widget fonts must load from approved stores on other origins.
      source: "/trigger/:font(AtkinsonHyperlegible-Regular|Literata).ttf",
      headers: [{ key: "Access-Control-Allow-Origin", value: "*" }, { key: "X-Content-Type-Options", value: "nosniff" }],
    }, {
      // Self-hosted CLI: the pointer and installer must not be cached; a versioned
      // release never changes once published.
      source: "/cli/:file(latest.json|install.sh)",
      headers: [{ key: "Cache-Control", value: "no-cache" }, { key: "X-Content-Type-Options", value: "nosniff" }],
    }, {
      source: "/cli/releases/:path*",
      headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }, { key: "X-Content-Type-Options", value: "nosniff" }],
    }, {
      source: "/cli/device",
      headers: [
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
        { key: "Cache-Control", value: "no-store" },
        { key: "Referrer-Policy", value: "no-referrer" },
      ],
    }];
  },
};

export default nextConfig;
