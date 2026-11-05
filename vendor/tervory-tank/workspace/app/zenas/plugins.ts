/** Plugins for zenas.vault. The browser never receives a secret value. */

export const POPUP_TITLE = "Plugins";
export const REFERENCE_LABEL = "Sources";
export const VAULT_NAME = "zenas.vault";
export const VAULT_OFF_MESSAGE = "This vault is off. Turn it on in Tank.";
export const VAULT_OFF_DETAIL =
  "Nothing here can be added or changed while zenas.vault is off. Tank returns no items and refuses writes.";
export const VAULT_NOTE =
  "Calls go through Tank, server-side. This page uses zenas.vault only and never reads another vault. Saved values are never sent back to the page.";
export const NEED_KEY = "Add a key first. Nothing was stored.";
export const NEED_FILE = "Choose a file first. Nothing was saved.";
export const NEED_LINK = "Add a link first. Nothing was saved.";
export const NEED_MCP = "Add a name and a server URL first. Nothing was saved.";
export const SEARCH_PLACEHOLDER = "Search matters, drafts, documents and citations";

export const PLUGIN_TABS = ["API keys", "Files", "Source links", "MCP connections"] as const;
export type PluginTab = (typeof PLUGIN_TABS)[number];

export type SecretMeta = { name: string; purpose: string; mask: string; savedAt: string | null };
export type FileMeta = {
  id: string;
  name: string;
  type: string;
  size: number;
  rows: number | null;
  pages: number | null;
  uploadedAt: string;
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

type WireSecret = { name?: string; purpose?: string; mask?: string; saved_at?: string; value?: string };
type WireFile = {
  id?: string;
  name?: string;
  type?: string;
  size?: number;
  rows?: number | null;
  pages?: number | null;
  uploaded_at?: string;
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

/** Copy the fields the browser may see. Secret values, file bytes, and tokens are dropped. */
export function mapVaultPayload(raw: {
  enabled?: boolean;
  message?: string | null;
  secrets?: WireSecret[];
  files?: WireFile[];
  links?: WireLink[];
  connections?: WireConnection[];
}): PluginPanel {
  if (!raw.enabled) return offPanel(raw.message || VAULT_OFF_MESSAGE);
  return {
    enabled: true,
    message: null,
    secrets: (raw.secrets ?? []).flatMap((row) =>
      row.name
        ? [{ name: row.name, purpose: row.purpose ?? "", mask: row.mask || "••••••••", savedAt: row.saved_at ?? null }]
        : [],
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
            status: row.status ?? "ready",
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
            status: row.status ?? "not checked",
          }]
        : [],
    ),
    connections: (raw.connections ?? []).flatMap((row) =>
      row.id && row.name
        ? [{
            id: row.id,
            name: row.name,
            serverUrl: row.server_url ?? "",
            authType: row.auth_type ?? "token",
            status: row.status ?? "saved",
            lastUsed: row.last_used ?? null,
            hasToken: Boolean(row.has_token),
          }]
        : [],
    ),
  };
}

export function formatSaved(iso: string | null | undefined): string {
  if (!iso) return "No date";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "No date";
  const day = String(date.getUTCDate()).padStart(2, "0");
  const month = String(date.getUTCMonth() + 1).padStart(2, "0");
  return `${day} / ${month} / ${date.getUTCFullYear()}`;
}

export function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size < 0) return "No size";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function fileDetail(file: FileMeta): string {
  const kind = (file.type || "File").toUpperCase();
  const measure =
    file.pages != null ? `${file.pages} pages` : file.rows != null ? `${file.rows} rows` : "No page count";
  return `${kind} · ${measure} · ${formatBytes(file.size)} · uploaded ${formatSaved(file.uploadedAt)}`;
}
