import type { KeyFlags, KeyName } from "./catalog";
import type { OnCard, PageModel } from "./model";

/** Popup title. The word Sources is reserved for answer references. */
export const POPUP_TITLE = "Plugins";
export const REFERENCE_LABEL = "Sources";

export const VAULT_OFF_MESSAGE = "Plugins are not available yet. Ask your workspace administrator to enable them.";
export const NEED_KEY = "Add a key first. Nothing was stored.";
export const NEED_FILE = "Choose a file first. Nothing was saved.";
export const NEED_LINK = "Add a link first. Nothing was saved.";
export const NEED_MCP = "Add a name and a server address first. Nothing was saved.";

export const PLUGIN_TABS = ["Access keys", "Files", "Source links", "Connections"] as const;
export type PluginTab = (typeof PLUGIN_TABS)[number];

export type PublicKeyRow = {
  name: KeyName;
  title: string;
  summary: string;
  connected: boolean;
};

const KEY_ROWS: { name: KeyName; title: string; summary: string }[] = [
  {
    name: "FRED_API_KEY",
    title: "FRED",
    summary: "Price and freight figures published by the Federal Reserve Bank of St. Louis. These pages are linked only; their figures are not used in saved-feed answers.",
  },
  { name: "CENSUS_API_KEY", title: "Census", summary: "Census population and Census trade." },
  { name: "EIA_API_KEY", title: "U.S. Energy Information Administration", summary: "Weekly diesel prices." },
  { name: "NOAA_TOKEN", title: "National Oceanic and Atmospheric Administration", summary: "Monthly climate. Weather alerts need no key." },
  { name: "LEGISCAN_API_KEY", title: "LegiScan", summary: "Status changes on state bills about firearms and ammunition." },
];

/** Pages a person can open. Taken from the feed list, not invented. */
const FEED_HREF: Record<string, string> = {
  listing_counts: "https://www.atf.gov/firearms/listing-federal-firearms-licensees",
  check_counts: "https://www.fbi.gov/file-repository/cjis/nics_firearm_checks_-_month_year_by_state.pdf",
  census_population: "https://api.census.gov/data/2023/acs/acs5",
  legiscan: "https://legiscan.com/legiscan",
  federal_register: "https://www.federalregister.gov/api/v1/documents.json",
  ecfr: "https://www.ecfr.gov/current/title-49",
  census_trade: "https://api.census.gov/data/timeseries/intltrade/imports/hs",
  eia_diesel: "https://api.eia.gov/v2/petroleum/pri/gnd/data/",
  world_bank_commodities: "https://api.worldbank.org/v2/country/WLD/indicator/PCOPPUSDM?format=json",
  noaa_alerts: "https://api.weather.gov/alerts/active",
  noaa_climate: "https://www.ncei.noaa.gov/cdo-web/webservices/v2",
  port_los_angeles: "https://www.portoflosangeles.org/business/statistics/container-statistics",
  bts_freight: "https://www.bts.gov/",
  licence_holders: "https://www.fws.gov/program/wildlife-and-sport-fish-restoration/apportionments-and-licenses-data",
  fred: "https://fred.stlouisfed.org/",
};

/** What the browser is allowed to know: which keys are set, never the key itself. */
export function publicKeyRows(flags: KeyFlags): PublicKeyRow[] {
  return KEY_ROWS.map((row) => ({ ...row, connected: Boolean(flags[row.name]) }));
}

export type SecretMeta = { name: string; purpose: string; mask: string; savedAt: string | null };
export type FileMeta = {
  id: string;
  name: string;
  type: string;
  size: number;
  rows: number | null;
  pages: number | null;
  uploadedAt: string;
  reason: string;
  status: string;
};
export type LinkMeta = {
  id: string;
  url: string;
  label: string;
  frequency: string;
  lastChecked: string | null;
  status: string;
};
export type ConnectionMeta = {
  id: string;
  name: string;
  serverUrl: string;
  authType: string;
  status: string;
  lastUsed: string | null;
  hasToken: boolean;
};
export type PluginPanel = {
  enabled: boolean;
  message: string | null;
  secrets: SecretMeta[];
  files: FileMeta[];
  links: LinkMeta[];
  connections: ConnectionMeta[];
};

export function offPanel(message: string = VAULT_OFF_MESSAGE): PluginPanel {
  return { enabled: false, message, secrets: [], files: [], links: [], connections: [] };
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    learned: "Learned", reading: "Reading", saved: "Saved", ready: "Saved", pending: "Waiting", processing: "Processing",
    failed: "Could not complete", error: "Could not complete", connected: "Connected",
    disconnected: "Not connected", needs_auth: "Sign-in needed", needs_sign_in: "Sign-in needed",
    not_checked: "Not checked yet", "not checked": "Not checked yet", checked: "Checked",
  };
  return labels[value.toLowerCase()] ?? "Status unavailable";
}

/** Feed flags. An off vault adds nothing, even if names were sent with it. */
export function flagsWithVault(flags: KeyFlags, vault: { enabled: boolean; names: readonly string[] }): KeyFlags {
  const next = { ...flags };
  if (!vault.enabled) return next;
  for (const name of vault.names) {
    if (name in next) next[name as KeyName] = true;
  }
  return next;
}

type WireSecret = { name?: string; purpose?: string; mask?: string; saved_at?: string; value?: string };
type WireFile = {
  id?: string;
  name?: string;
  type?: string;
  size?: number;
  rows?: number | null;
  pages?: number | null;
  uploaded_at?: string;
  reason?: string;
  status?: string;
  content?: string;
};
type WireLink = {
  id?: string;
  url?: string;
  label?: string;
  frequency?: string;
  last_checked?: string | null;
  status?: string;
};
type WireConnection = {
  id?: string;
  name?: string;
  server_url?: string;
  auth_type?: string;
  status?: string;
  last_used?: string | null;
  has_token?: boolean;
  token?: string;
};

/** Copy the fields the browser may see. Secret values and file bytes are dropped. */
export function mapVaultPayload(raw: {
  enabled?: boolean;
  message?: string | null;
  secrets?: WireSecret[];
  files?: WireFile[];
  links?: WireLink[];
  connections?: WireConnection[];
}): PluginPanel {
  if (!raw.enabled) return offPanel();
  return {
    enabled: true,
    message: null,
    secrets: (raw.secrets ?? []).flatMap((row) =>
      row.name ? [{ name: row.name, purpose: row.purpose ?? "", mask: row.mask || "••••••••", savedAt: row.saved_at ?? null }] : [],
    ),
    files: (raw.files ?? []).flatMap((row) =>
      row.id && row.name
        ? [{
            id: row.id,
            name: row.name,
            type: row.type ?? "",
            size: row.size ?? 0,
            rows: row.rows ?? null,
            pages: row.pages ?? null,
            uploadedAt: row.uploaded_at ?? "",
            reason: row.reason ?? "",
            status: statusLabel(row.status ?? "ready"),
          }]
        : [],
    ),
    links: (raw.links ?? []).flatMap((row) =>
      row.id && row.url
        ? [{
            id: row.id,
            url: row.url,
            label: row.label ?? row.url,
            frequency: row.frequency ?? "weekly",
            lastChecked: row.last_checked ?? null,
            status: statusLabel(row.status ?? "not checked"),
          }]
        : [],
    ),
    connections: (raw.connections ?? []).flatMap((row) =>
      row.id && row.name
        ? [{
            id: row.id,
            name: row.name,
            serverUrl: row.server_url ?? "",
            authType: row.auth_type ?? "provider",
            status: statusLabel(row.status ?? "saved"),
            lastUsed: row.last_used ?? null,
            hasToken: Boolean(row.has_token),
          }]
        : [],
    ),
  };
}

export function feedKeyCatalog(): { name: KeyName; title: string; summary: string }[] {
  return KEY_ROWS;
}

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function clientPluginState(flags: KeyFlags) {
  return {
    title: POPUP_TITLE,
    keys: publicKeyRows(flags),
    files: [] as [],
    links: [] as [],
    connections: [] as [],
  };
}

export type AnswerSource = { name: string; href: string | null; date: string | null };

function hasReading(card: OnCard): boolean {
  return Boolean(card.headline || card.detail || card.lines.length || card.notices.length);
}

function blob(parts: (string | null | undefined)[]): string {
  return parts.filter((part): part is string => Boolean(part && part.trim())).join(" ");
}

function matches(text: string, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = text.toLowerCase();
  if (hay.includes(q)) return true;
  const tokens = q.split(/[^a-z0-9]+/).filter((token) => token.length > 2);
  return tokens.length > 0 && tokens.every((token) => hay.includes(token));
}

function onCards(model: PageModel): OnCard[] {
  return [...model.featured, ...model.cards];
}

export function searchMarketLab(model: PageModel, query: string): { onIds: string[]; offIds: string[]; linkIds: string[] } {
  const on = onCards(model);
  const links = model.groups.flatMap((group) =>
    group.pages.map((page) => ({ ...page, group: group.title, lead: group.lead })),
  );
  if (!query.trim()) {
    return { onIds: on.map((card) => card.id), offIds: model.off.map((row) => row.id), linkIds: links.map((page) => page.id) };
  }
  return {
    onIds: on
      .filter((card) => matches(blob([card.title, card.summary, card.sourceLabel, card.headline, card.detail]), query))
      .map((card) => card.id),
    offIds: model.off.filter((row) => matches(blob([row.title, row.summary, row.keyName]), query)).map((row) => row.id),
    linkIds: links
      .filter((page) => matches(blob([page.label, page.href, page.note, page.group, page.lead]), query))
      .map((page) => page.id),
  };
}

function sourceName(card: OnCard): string {
  if (!card.sourceLabel || card.title.includes(card.sourceLabel)) return card.title;
  return `${card.title} (${card.sourceLabel})`;
}

/** References actually attached to an answer. Unread and unmatched feeds are left out. */
export function groundQuestion(model: PageModel, question: string): { sources: AnswerSource[]; context: string } {
  const readable = onCards(model).filter(hasReading);
  const matched = readable.filter((card) =>
    matches(blob([card.title, card.summary, card.sourceLabel, card.headline, card.detail, ...card.lines.flatMap((line) => [line.label, line.value])]), question),
  );
  const picked = matched.slice(0, 4);
  const sources: AnswerSource[] = picked.map((card) => ({
    name: sourceName(card),
    href: FEED_HREF[card.id] ?? null,
    date: card.asOf,
  }));
  const context = picked
    .map((card) => {
      const bits = blob([
        card.headline,
        card.detail,
        ...card.lines.map((line) => `${line.label}: ${line.value}`),
        ...card.notices.map((notice) => notice.title),
        card.problem,
      ]);
      return `${sourceName(card)}${card.asOf ? ` (${card.asOf})` : ""}: ${bits}`;
    })
    .join("\n");
  return { sources, context };
}
