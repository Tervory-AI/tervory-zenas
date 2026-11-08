/**
 * Product-per-host map for *.tervory.com.
 * app.tervory.com stays the client workspace home (matters / chats / squad).
 * protect / autumbra land on the public Autumbra product page.
 * Founder console remains at /admin/autumbra.
 * zenas.tervory.com is the Zenas product host — Moot watch at /moot, full
 * Zenas panel at /zenas. Squad product hosts land on their workspace paths.
 * Rig shares its host: Traefik keeps widget/store paths on agent-rig; the
 * remaining paths reach Workspace. Separate mcp, site and coolify do not rewrite.
 */

export type ProductHost = {
  host: string;
  /** Absolute path the host root rewrites to inside the workspace app. */
  rootPath: string;
  /** Short product label for docs / ops (not user-facing Ghost/Wraith names). */
  product: string;
  /** When true, only founders reach the landing (Autumbra /admin/*). */
  founderOnly?: boolean;
};

/** Hosts served by the Coolify workspace service (same as app.tervory.com). */
export const WORKSPACE_PRODUCT_HOSTS: readonly ProductHost[] = [
  { host: "protect.tervory.com", rootPath: "/autumbra", product: "Autumbra" },
  { host: "autumbra.tervory.com", rootPath: "/autumbra", product: "Autumbra" },
  /** Moot is part of Zenas — public watch lands on /moot; /zenas stays available. */
  { host: "zenas.tervory.com", rootPath: "/moot", product: "Zenas" },
  { host: "base.tervory.com", rootPath: "/base", product: "Base" },
  { host: "trigger.tervory.com", rootPath: "/trigger", product: "Trigger" },
  { host: "tiger.tervory.com", rootPath: "/tiger", product: "Tiger" },
  { host: "market-lab.tervory.com", rootPath: "/market-lab", product: "Market Lab" },
  { host: "tisan.tervory.com", rootPath: "/tisan", product: "Tisan" },
  { host: "rig.tervory.com", rootPath: "/rig", product: "Rig" },
  { host: "radix.tervory.com", rootPath: "/radix", product: "Radix" },
  { host: "skala.tervory.com", rootPath: "/skala", product: "Skala", founderOnly: true },
  { host: "tank.tervory.com", rootPath: "/tank", product: "Tank", founderOnly: true },
] as const;

const BY_HOST = new Map(WORKSPACE_PRODUCT_HOSTS.map((row) => [row.host, row]));

/** Client workspace host — matters / chats / squad stay here; Moot does not. */
export const APP_WORKSPACE_HOST = "app.tervory.com";

/** Canonical Zenas product host (Moot + Zenas panel). */
export const ZENAS_PRODUCT_HOST = "zenas.tervory.com";

/** Strip port and lowercase; empty when missing. */
export function normalizeHost(raw: string | null | undefined): string {
  if (!raw) return "";
  const first = raw.split(",")[0]?.trim().toLowerCase() ?? "";
  return first.replace(/:\d+$/, "");
}

export function productHostFor(hostHeader: string | null | undefined): ProductHost | null {
  const host = normalizeHost(hostHeader);
  if (!host) return null;
  return BY_HOST.get(host) ?? null;
}

/**
 * Path rewrite for a product host.
 * - `/` (and empty) → that product's rootPath
 * - Rig /admin and /admin/* → /rig/admin and /rig/admin/*
 * - already on rootPath or under it → no rewrite
 * - public SigninDirectory / health / webhook paths → no rewrite
 * - everything else on that host stays as-is (deep links work)
 */
export function productHostRewritePath(
  hostHeader: string | null | undefined,
  pathname: string,
): string | null {
  const product = productHostFor(hostHeader);
  if (!product) return null;
  const path = pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname || "/";
  if (
    path.startsWith("/sign-in") ||
    path.startsWith("/sign-up") ||
    path === "/api/health" ||
    path.startsWith("/api/billing/webhook") ||
    path.startsWith("/_next")
  ) {
    return null;
  }
  // Rig's former room /admin becomes the authenticated Workspace administrator
  // page. Already-prefixed /rig/* links keep their path, as on other hosts.
  if (product.product === "Rig" && (path === "/admin" || path.startsWith("/admin/"))) {
    return `/rig${path}`;
  }
  if (path === "/" || path === "") return product.rootPath;
  return null;
}

export function isAutumbraHost(hostHeader: string | null | undefined): boolean {
  const product = productHostFor(hostHeader);
  return product?.product === "Autumbra";
}

/**
 * Canonical URL when Moot is hit on the client workspace host.
 * Moot belongs on zenas.tervory.com; app keeps matters / chats / squad.
 * Returns null when no redirect is needed.
 */
export function mootCanonicalRedirectUrl(
  hostHeader: string | null | undefined,
  pathname: string,
  search = "",
): string | null {
  const host = normalizeHost(hostHeader);
  if (host !== APP_WORKSPACE_HOST) return null;
  const path = pathname.length > 1 && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname || "/";
  if (path !== "/moot" && !path.startsWith("/moot/")) return null;
  const qs = search && search !== "?" ? search : "";
  return `https://${ZENAS_PRODUCT_HOST}${path}${qs}`;
}

/**
 * Where to send a signed-out visitor back after sign-in: the public address they asked for.
 * Behind Coolify, req.nextUrl carries the internal bind (https://0.0.0.0:3000), and after a
 * host rewrite its path is the internal one (/base), so the sign-in page was being given
 * https://0.0.0.0:3000/base (seen 28 Oct). Only Tervory hosts are used; anything else
 * falls back to the client workspace home.
 */
export function publicReturnUrl(
  hostHeader: string | null | undefined,
  pathname: string,
  search = "",
): string {
  const host = normalizeHost(hostHeader);
  const ours = host === "tervory.com" || host.endsWith(".tervory.com");
  const path = pathname && pathname.startsWith("/") ? pathname : "/";
  const qs = search && search !== "?" ? search : "";
  return ours ? `https://${host}${path}${qs}` : `https://${APP_WORKSPACE_HOST}/`;
}

/** OPLI's release alias shares the same immutable CLI files as the app host. */
export function opliReleasePath(host: string | null | undefined, path: string): string | null {
  if (normalizeHost(host) !== "opli.tervory.com") return null;
  if (path === "/install.sh") return "/cli/opli-install.sh";
  if (path === "/latest.json") return "/cli/latest.json";
  if (/^\/releases\/\d{1,4}\.\d{1,4}\.\d{1,4}\/(manifest\.json|SHA256SUMS|tervory-cli-[A-Za-z0-9.-]+\.tar\.gz)(\.sig)?$/.test(path)) return "/cli" + path;
  return null;
}
