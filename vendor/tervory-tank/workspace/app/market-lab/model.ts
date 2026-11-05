import {
  CADENCE_LABEL,
  CATALOG,
  FEATURED_IDS,
  LINK_GROUPS,
  type CatalogItem,
  type KeyFlags,
  type KeyName,
} from "./catalog";
import { formatDay, formatRefreshed } from "./format";
import type { Notice, PriceLine } from "./parse";

/** One feed's latest reading. Off feeds are ignored even if a reading is passed in. */
export type FeedReading = {
  rows?: number;
  documentId?: string | null;
  note?: string;
  headline?: string | null;
  detail?: string | null;
  lines?: PriceLine[];
  notices?: Notice[];
  failed?: boolean;
  checkedAt?: string | null;
  goodAt?: string | null;
};

export type LinkCheck = {
  note?: string;
  events: number;
  latest?: string | null;
};

export type OnCard = {
  sourceHref?: string;
  id: string;
  title: string;
  summary: string;
  sourceLabel: string;
  cadence: string;
  headline: string | null;
  detail: string | null;
  lines: PriceLine[];
  notices: Notice[];
  problem: string | null;
  asOf: string | null;
};

export type OffRow = {
  reason?: string;
  id: string;
  title: string;
  summary: string;
  keyName: string;
};

export type LinkPage = {
  id: string;
  label: string;
  href: string;
  note: string;
};

export type LinkGroupView = {
  id: string;
  title: string;
  cadence: string;
  lead: string;
  pages: LinkPage[];
};

export type PageModel = {
  refreshStatus?: string | null;
  refreshedLabel: string;
  onCount: number;
  offCount: number;
  linkedCount: number;
  onEmpty: boolean;
  loadError: boolean;
  featured: OnCard[];
  cards: OnCard[];
  off: OffRow[];
  groups: LinkGroupView[];
};

export function isOn(item: CatalogItem, flags: KeyFlags): boolean {
  if (!item.keyName) return true;
  return Boolean(flags[item.keyName as KeyName]);
}

/** Copy feeds and FRED series whose key is set. Linked pages are not called. */
export function feedsToCall(flags: KeyFlags): string[] {
  return CATALOG.filter((item) => item.kind !== "linked" && isOn(item, flags)).map((item) => item.id);
}

function clean(value: string | null | undefined): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text ? text : null;
}

/** A failed check with no earlier success contributes no number. */
function shownReading(reading: FeedReading | undefined): FeedReading {
  const source = reading ?? {};
  if (source.failed && !clean(source.goodAt)) {
    return { failed: true, checkedAt: source.checkedAt ?? null };
  }
  return source;
}

function problemFor(reading: FeedReading): string | null {
  if (!reading.failed) return null;
  const checked = clean(reading.checkedAt);
  const good = clean(reading.goodAt);
  if (checked && good) {
    return `The last check on ${formatDay(checked)} did not work. The source did not answer in time. Showing the last good data, from ${formatDay(good)}.`;
  }
  if (checked) {
    return `The last check on ${formatDay(checked)} did not work. The source did not answer in time.`;
  }
  return "The last check did not work. The source did not answer in time.";
}

function dated(reading: FeedReading | undefined): string | null {
  const shown = shownReading(reading);
  const raw = clean(shown.goodAt) || clean(shown.checkedAt);
  return raw ? formatDay(raw) : null;
}

function cardFor(item: CatalogItem, reading: FeedReading | undefined): OnCard {
  const shown = shownReading(reading);
  return {
    sourceHref: reading?.documentId ? `/market-lab/sources/${reading.documentId}` : undefined,
    id: item.id,
    title: item.title,
    summary: item.summary,
    sourceLabel: item.sourceLabel,
    cadence: CADENCE_LABEL[item.cadence],
    headline: clean(shown.headline),
    detail: clean(shown.detail),
    lines: shown.lines ?? [],
    notices: shown.notices ?? [],
    problem: problemFor(shown),
    asOf: dated(reading),
  };
}

function hasContent(card: OnCard): boolean {
  return Boolean(card.headline || card.detail || card.lines.length || card.notices.length || card.problem);
}

function linkNote(item: CatalogItem, check: LinkCheck | undefined): string {
  if (item.linkNote) return item.linkNote;
  if (check?.note?.startsWith("Off,")) return check.note.replace("Off,", "Not checked:");
  const latest = clean(check?.latest);
  if (!latest || !check?.events) return "Not checked yet";
  return `Checked ${formatDay(latest)}`;
}

export function buildPage(
  flags: KeyFlags,
  readings: Record<string, FeedReading>,
  linkChecks: Record<string, LinkCheck>,
  options: { error?: boolean; refreshedAt?: string | null; sources?: CatalogItem[] } = {},
): PageModel {
  const copies = [...CATALOG, ...(options.sources ?? []).filter((item) => !CATALOG.some((known) => known.id === item.id))].filter((item) => item.kind === "copy");
  const hasRows = (id: string) => Boolean(readings[id]?.documentId && (readings[id]?.rows ?? 0) > 0 && !readings[id]?.failed);
  const linked = CATALOG.filter((item) => item.kind === "linked");
  const featured = copies.filter((item) => (FEATURED_IDS as readonly string[]).includes(item.id) && hasRows(item.id)).map((item) => cardFor(item, readings[item.id]));
  const cards = copies.filter((item) => !(FEATURED_IDS as readonly string[]).includes(item.id) && hasRows(item.id)).map((item) => cardFor(item, readings[item.id]));
  const onCards = [...featured, ...cards];
  const loadError = Boolean(options.error);
  const off = copies.filter((item) => !hasRows(item.id)).map((item) => ({
    id: item.id, title: item.title, summary: item.summary, keyName: item.keyName,
    reason: readings[item.id]?.note || "Off, not fetched yet",
  }));
  const groups: LinkGroupView[] = LINK_GROUPS.map((group) => ({
    id: group.id,
    title: group.title,
    cadence: group.cadence,
    lead: group.lead,
    pages: linked
      .filter((item) => item.group === group.id)
      .map((item) => ({
        id: item.id,
        label: item.linkLabel || item.title,
        href: item.href || "",
        note: linkNote(item, linkChecks[item.id]),
      })),
  }));

  const restricted = CATALOG.filter((item) => item.kind === "fred");
  groups.push({
    id: "economic", title: "Prices and freight on other websites", cadence: "Monthly",
    lead: "These sources do not permit a stored copy. Their numbers are not used in briefs.",
    pages: restricted.map((item) => ({ id: item.id, label: item.title, href: item.href || "", note: "Opens the publisher’s website" })),
  });
  const refreshedAt = options.refreshedAt ?? "";
  return {
    refreshedLabel: refreshedAt ? formatRefreshed(refreshedAt) : "Not refreshed yet",
    onCount: onCards.length,
    offCount: off.length,
    linkedCount: linked.length + restricted.length,
    onEmpty: !loadError && onCards.every((card) => !hasContent(card)),
    loadError,
    featured,
    cards,
    off,
    groups,
  };
}
