import { boundedFetch, MarketLabRequestError } from "../../lib/tank";
import type { Tenant } from "../../lib/tenant";
import type { Brief, Curve, Load } from "./h7-model";

/** Same authenticated ML3 code reads used by /chats; no new endpoint or model. */
export async function readH7<T>(tenant: Pick<Tenant, "tenantId" | "userId">, operation: "briefs" | "calibration.curve", reportId?: string): Promise<T[]> {
  const url = new URL(`/v1/market-lab/reads/${operation}`, process.env.TANK_URL ?? "http://tank:8000");
  if (reportId) url.searchParams.set("report_id", reportId);
  url.searchParams.set("limit", "100");
  const rows: T[] = [];
  for (let page = 0; page < 100; page++) {
    if (page) url.searchParams.set("page", String(page));
    const payload = await boundedFetch(url, { method: "GET", cache: "no-store", headers: {
      "x-tank-key": process.env.TANK_API_KEY ?? "", "x-tervory-user": tenant.userId,
      "x-tervory-tenant": tenant.tenantId, "x-tervory-admin": tenant.userId,
    } }, 35000, async response => {
      if (!response.ok) throw new MarketLabRequestError(response.status);
      const payload = await response.json() as { records?: unknown; total?: unknown };
      if (!Array.isArray(payload.records)) throw new MarketLabRequestError(502);
      return { records: payload.records as T[], total: payload.total };
    });
    rows.push(...payload.records);
    if (payload.total === undefined) return rows;
    if (typeof payload.total !== "number" || !Number.isSafeInteger(payload.total) || payload.total < 0) throw new MarketLabRequestError(502);
    if (rows.length >= payload.total) return rows;
    if (!payload.records.length) throw new MarketLabRequestError(502);
  }
  // Refuse an incomplete view instead of silently dropping later cohorts.
  throw new MarketLabRequestError(502);
}

export async function loadBrief(tenant: Pick<Tenant, "tenantId" | "userId">, id: string): Promise<Load<Brief>> {
  try {
    const rows = await readH7<Brief>(tenant, "briefs", id);
    return rows.length ? { state: "ready", data: rows[0] } : { state: "empty" };
  } catch (error) {
    return { state: error instanceof MarketLabRequestError && error.status === 404 ? "empty" : "error" };
  }
}
export async function loadCalibration(tenant: Pick<Tenant, "tenantId" | "userId">): Promise<Load<Curve[]>> {
  try {
    const rows = await readH7<Curve>(tenant, "calibration.curve");
    return rows.length ? { state: "ready", data: rows } : { state: "empty" };
  } catch { return { state: "error" }; }
}
