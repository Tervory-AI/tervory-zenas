// Plugins for Base: what the browser may see from base.vault. Same shape and rules as Market Lab's Plugins (PR #117).
// Pure code, no network. Secret values and file bytes are dropped here, before anything reaches the page.

/** Base's own vault in Tank. Base never reads another squad's vault or tank.vault. */
export const BASE_VAULT = "base.vault";
/** The squad header Tank checks against the vault in the path. */
export const BASE_SQUAD = "base";

export const VAULT_OFF_MESSAGE = "This vault is off. Turn it on in Tank.";
export const SIGN_IN_MESSAGE = "Sign in to use Plugins.";
export const LOAD_ERROR_MESSAGE = "Plugins could not be loaded from Tank. No vault data is shown.";

export const NEED_KEY = "Add a key first. Nothing was stored.";
export const NEED_FILE = "Choose a file first. Nothing was saved.";
export const NEED_LINK = "Add a link first. Nothing was saved.";
export const NEED_MCP = "Add a name and a server URL first. Nothing was saved.";

export const MASK = "••••••••";

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

/** Copy the fields the browser may see. Secret values, tokens and file bytes are dropped. An off vault shows nothing. */
export function mapVaultPayload(raw: {
  vault?: string;
  enabled?: boolean;
  message?: string | null;
  secrets?: WireSecret[];
  files?: WireFile[];
  links?: WireLink[];
  connections?: WireConnection[];
}): PluginPanel {
  if (raw.vault && raw.vault !== BASE_VAULT) return offPanel(LOAD_ERROR_MESSAGE);
  if (!raw.enabled) return offPanel(raw.message || VAULT_OFF_MESSAGE);
  return {
    enabled: true,
    message: null,
    secrets: (raw.secrets ?? []).flatMap((row) =>
      row.name ? [{ name: row.name, purpose: row.purpose ?? "", mask: row.mask || MASK, savedAt: row.saved_at ?? null }] : [],
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

export function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** Turns a Tank error into plain words. Never passes Tank's raw text to the page. */
export function safeMessage(error: unknown, fallback: string): string {
  const text = error instanceof Error ? error.message : "";
  if (text.includes(VAULT_OFF_MESSAGE)) return VAULT_OFF_MESSAGE;
  if (text.includes("Only CSV, XLSX, and PDF")) return "Only CSV, XLSX, and PDF files can be uploaded.";
  if (text.includes("Sign in")) return "Sign in to change Plugins.";
  return fallback;
}
