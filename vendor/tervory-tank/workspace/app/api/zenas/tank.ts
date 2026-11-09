/** Server-side calls to Tank. The key never reaches the browser. */

const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

export type Region = "us-east" | "za";

export function homeRegion(): Region {
  return process.env.TANK_REGION === "za" ? "za" : "us-east";
}

/** Every Zenas call names its destination's home. Explicit homes still face Tank's checks. */
function withHome(path: string, init: RequestInit, home: Region = homeRegion(), force = false): { path: string; init: RequestInit } {
  const split = path.indexOf("?");
  const pathname = split === -1 ? path : path.slice(0, split);
  const params = new URLSearchParams(split === -1 ? "" : path.slice(split + 1));
  if (force || !params.get("home_region")) params.set("home_region", home);
  let body = init.body;
  if (typeof body === "string" && body) {
    try {
      const parsed = JSON.parse(body) as unknown;
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const record = parsed as Record<string, unknown>;
        if (force || typeof record.home_region !== "string" || !record.home_region.trim()) {
          record.home_region = home;
          body = JSON.stringify(record);
        }
      }
    } catch {
      // The query string still carries the home.
    }
  }
  const query = params.toString();
  return { path: query ? `${pathname}?${query}` : pathname, init: { ...init, body } };
}

export async function zenas(path: string, init: RequestInit = {}): Promise<Response> {
  const missing = [!TANK_API_KEY ? "TANK_API_KEY" : "", !process.env.TANK_REGION ? "TANK_REGION" : ""].filter(Boolean);
  if (missing.length) return Response.json({ needs_settings: missing }, { status: 503 });
  const {tenantDestination, requestTenant, isRedirect, RedirectError, boundedFetch} = await import("@/lib/tank");
  const tenant = requestTenant("/v1/zenas/" + path, init);
  const target = tenant ? await tenantDestination(tenant) : {url: TANK_URL, key: TANK_API_KEY, home: null};
  const call = withHome(path, init, target.home ?? homeRegion());
  const headers = new Headers(call.init.headers);
  if (!headers.has("content-type")) headers.set("content-type", "application/json");
  headers.set("x-tank-key", target.key);
  if (tenant) headers.set("x-zenas-tenant", tenant);
  const response = await boundedFetch(new URL("/v1/zenas/" + call.path, target.url), {
    ...call.init, headers, cache: "no-store", redirect: "manual",
  }, 120_000, async response => new Response(await response.arrayBuffer(), { status: response.status, headers: response.headers }));
  if (isRedirect(response)) {
    await response.body?.cancel().catch(() => undefined);
    throw new RedirectError(response.status);
  }
  return response;
}

/**
 * The Tank that owns a region. This server's own Tank for its own region.
 * The other region's Tank only through its public HTTPS address and its own
 * restricted homes/feeds key (TANK_URL_ZA + TANK_API_KEY_ZA, or TANK_URL_US + TANK_API_KEY_US).
 * Private content instead uses the separate full content keys in lib/tank.ts.
 * Null when that region is not connected here.
 */
export function regionTank(region: Region): { url: string; key: string } | null {
  if (region === homeRegion()) return { url: TANK_URL, key: TANK_API_KEY };
  const url = ((region === "za" ? process.env.TANK_URL_ZA : process.env.TANK_URL_US) ?? "").trim().replace(/\/+$/, "");
  const key = ((region === "za" ? process.env.TANK_API_KEY_ZA : process.env.TANK_API_KEY_US) ?? "").trim();
  if (!url || !key) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) return null;
  return { url, key };
}

/**
 * Call /v1/zenas/* on the Tank that owns `region`. The home on the call is
 * always that region, so the other Tank's own region check passes only for
 * its own tenants. Used for founder-only calls that carry no personal data
 * (a tenant id and a region code). Matter data never goes through here.
 */
export async function zenasIn(region: Region, path: string, init: RequestInit = {}): Promise<Response | null> {
  const tank = regionTank(region);
  if (!tank) return null;
  if (!tank.key) return Response.json({ needs_settings: ["TANK_API_KEY"] }, { status: 503 });
  const call = withHome(path, init, region, true);
  const { boundedFetch } = await import("@/lib/tank");
  return boundedFetch(`${tank.url}/v1/zenas/${call.path}`, {
    ...call.init,
    headers: {
      "content-type": "application/json",
      "x-tank-key": tank.key,
      ...(call.init.headers ?? {}),
    },
    cache: "no-store", redirect: "manual",
  }, 120_000, async response => new Response(await response.arrayBuffer(), { status: response.status, headers: response.headers }));
}
