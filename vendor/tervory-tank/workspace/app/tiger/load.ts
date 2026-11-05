import { listTigerAlerts, listTigerObservations, listTigerWatches } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { emptyData, fromTiger, type TigerData } from "./model";

export type TigerLoad = { data: TigerData; notice: string | null };

export async function loadTiger(): Promise<TigerLoad> {
  try {
    const tenant = await currentTenant();
    const [listed, alerted] = await Promise.all([
      listTigerWatches(tenant.tenantId),
      listTigerAlerts(tenant.tenantId),
    ]);
    const watches = Array.isArray(listed.watches) ? listed.watches : [];
    const alerts = Array.isArray(alerted.alerts) ? alerted.alerts : [];
    if (watches.length === 0 && alerts.length === 0) return { data: emptyData(), notice: null };
    const unavailableUrls = new Set<string>();
    const urls = [...new Set([...watches.map((watch) => watch.url), ...alerts.map((alert) => alert.url)])];
    const batches = await Promise.all(urls.map(async (url) => {
      try {
        const data = await listTigerObservations(tenant.tenantId, url);
        if (!Array.isArray(data.observations)) throw new Error("Missing check history");
        return data.observations;
      } catch {
        unavailableUrls.add(url);
        return [];
      }
    }));
    return { data: fromTiger(watches, batches.flat(), alerts, new Date(), unavailableUrls), notice: null };
  } catch (err) {
    const text = err instanceof Error ? err.message : "";
    if (text.includes("not signed in")) return { data: emptyData(), notice: null };
    return { data: emptyData(), notice: "Tiger is not reachable right now. No products are shown." };
  }
}
