import { marketLabRequest, type MarketOverview } from "../../lib/tank";
import { KEY_NAMES, emptyFlags, type KeyFlags, type CatalogItem } from "./catalog";
import { buildPage, type FeedReading, type LinkCheck, type PageModel } from "./model";

export function readFlags(env: NodeJS.ProcessEnv = process.env): KeyFlags {
  const flags = emptyFlags();
  for (const name of KEY_NAMES) flags[name] = Boolean((env[name] ?? "").trim());
  return flags;
}

/** The room's tenant-scoped Postgres records are the only source of readings. */
export async function loadMarketLab(tenantId?: string): Promise<PageModel> {
  try {
    if (!tenantId) {
      const { activeTenant } = await import("../../lib/tenant");
      tenantId = (await activeTenant())?.tenantId;
    }
    if (!tenantId) return buildPage(emptyFlags(), {}, {}, { error: true });
    const data = await marketLabRequest<MarketOverview>(tenantId, "overview");
    const readings: Record<string, FeedReading> = {};
    const linkChecks: Record<string, LinkCheck> = {};
    for (const feed of data.feeds) {
      if (feed.kind === "linked") linkChecks[feed.id] = { events: feed.rows, latest: feed.checked_at, note: feed.note };
      readings[feed.id] = {
        rows: feed.rows, note: feed.note, checkedAt: feed.checked_at,
        documentId: feed.document_id,
        headline: feed.rows > 0 ? `${feed.rows.toLocaleString("en-US")} stored ${feed.rows === 1 ? "observation" : "observations"}` : null,
      };
    }
    const refreshedAt = data.feeds.map((feed) => feed.checked_at).filter((date): date is string => Boolean(date)).sort().at(-1);
    const sources: CatalogItem[] = data.feeds.flatMap((feed) => feed.kind === "copy" && feed.url && feed.cadence ? [{
      id: feed.id, title: feed.name, summary: "", sourceLabel: new URL(feed.url).hostname,
      cadence: feed.cadence, keyName: "", kind: "copy" as const, href: feed.url,
    }] : []);
    return { ...buildPage(emptyFlags(), readings, linkChecks, { refreshedAt, sources }), refreshStatus: data.refresh_status };
  } catch {
    return buildPage(emptyFlags(), {}, {}, { error: true });
  }
}

export function scrubSecrets<T>(value: T, secrets: string[]): T {
  const usable = secrets.filter((secret) => secret.length >= 8);
  const walk = (input: unknown): unknown => {
    if (typeof input === "string") {
      let text = input;
      for (const secret of usable) {
        if (text.includes(secret)) text = text.split(secret).join("");
      }
      return text.replace(/(api_key|apikey|token)=([^&\s]+)/gi, "$1=");
    }
    if (Array.isArray(input)) return input.map(walk);
    if (input && typeof input === "object") {
      const out: Record<string, unknown> = {};
      for (const [key, item] of Object.entries(input)) out[key] = walk(item);
      return out;
    }
    return input;
  };
  return walk(value) as T;
}
