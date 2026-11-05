import { APP_WORKSPACE_HOST, WORKSPACE_PRODUCT_HOSTS } from "./product-hosts";

const PUBLIC_HOSTS = new Set([APP_WORKSPACE_HOST, ...WORKSPACE_PRODUCT_HOSTS.map(row => row.host)]);

/** Coolify preserves Host; Next's URL may be the internal HTTP bind address.
 * Public Workspace hosts always use HTTPS. Forwarded headers cannot select an
 * origin, protocol or redirect destination. Unknown/malformed hosts fail closed.
 */
export function workspaceOrigin(request: Request): string | null {
  const host = request.headers.get("host")?.toLowerCase();
  if (!host) return null;
  if (PUBLIC_HOSTS.has(host)) return `https://${host}`;
  // Direct local development only: require Host to agree with the request URL.
  if (process.env.NODE_ENV !== "production" && /^(localhost|127\.0\.0\.1)(:\d+)?$/.test(host)) {
    const url = new URL(request.url);
    if (url.host === host && (url.protocol === "http:" || url.protocol === "https:")) return url.origin;
  }
  return null;
}

export function isWorkspaceOrigin(request: Request): boolean {
  const expected = workspaceOrigin(request);
  // An Origin header is an origin, not a URL with credentials, path or query.
  // Exact comparison also rejects missing, null and multiple-origin values.
  return expected !== null && request.headers.get("origin") === expected;
}
