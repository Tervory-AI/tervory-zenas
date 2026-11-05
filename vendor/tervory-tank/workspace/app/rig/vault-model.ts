/** What the browser may know about Plugins. Secret values are not in this shape. */

export const VAULT_OFF_MESSAGE = "This vault is off. Turn it on in Tank.";
export const MASK = "••••••••";

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
  message: string;
  secrets: SecretMeta[];
  files: FileMeta[];
  links: LinkMeta[];
  connections: ConnectionMeta[];
};

export function offPanel(): PluginPanel {
  return { enabled: false, message: VAULT_OFF_MESSAGE, secrets: [], files: [], links: [], connections: [] };
}

type WireSecret = { name?: string; purpose?: string; mask?: string; saved_at?: string | null };
type WireFile = {
  id?: string;
  name?: string;
  type?: string;
  size?: number;
  rows?: number | null;
  pages?: number | null;
  uploaded_at?: string;
  status?: string;
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
};

/** Keep names, masks, and file metadata. Drop anything else Tank may have attached. */
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
    message: "",
    secrets: (raw.secrets ?? []).flatMap((row) =>
      row.name
        ? [{ name: row.name, purpose: row.purpose ?? "", mask: row.mask || MASK, savedAt: row.saved_at ?? null }]
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
            authType: row.auth_type ?? "provider",
            status: row.status ?? "saved",
            lastUsed: row.last_used ?? null,
            hasToken: Boolean(row.has_token),
          }]
        : [],
    ),
  };
}
