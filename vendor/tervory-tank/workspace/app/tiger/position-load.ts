// Server-only: every read uses the signed-in tenant through the Tank library.
import { listTigerMatches, listTigerPosition, listTigerWatches } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { loadTigerPlugins } from "./vault-server";
import { positionRows, siteRows, siteHost, unavailableComparison, type ComparisonData } from "./position-model";

export async function loadTigerComparison(): Promise<ComparisonData> {
  const result = unavailableComparison();
  try {
    const actor = await currentTenant();
    const [position, matches, watches, plugins] = await Promise.allSettled([
      listTigerPosition(actor), listTigerMatches(actor), listTigerWatches(actor.tenantId), loadTigerPlugins(),
    ]);
    const links = plugins.status === "fulfilled" && plugins.value.enabled ? plugins.value.links : [];
    const now = new Date();
    if (plugins.status === "fulfilled") result.plugins = plugins.value;
    const review = matches.status === "fulfilled" && matches.value.tenant_id === actor.tenantId ? matches.value : null;
    if (plugins.status === "fulfilled" && plugins.value.enabled) result.sites = siteRows(links, now, review?.site_checks);
    if (review && Array.isArray(review.matches)) result.matches = review.matches.map(row => ({ ...row, site_label: links.find(link => siteHost(link.url) === siteHost(row.url))?.label }));
    if (position.status === "fulfilled" && position.value.tenant_id === actor.tenantId && Array.isArray(position.value.products) && watches.status === "fulfilled" && Array.isArray(watches.value.watches)) {
      result.positions = positionRows(position.value.products, watches.value.watches, links, now);
    }
  } catch { /* Unframed failure: preserve unavailable rather than inventing empty data. */ }
  return result;
}
