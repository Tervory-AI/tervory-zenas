"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent } from "react";
import {
  addZenasConnection,
  addZenasLink,
  removeZenasConnection,
  removeZenasFile,
  removeZenasLink,
  removeZenasSecret,
  saveZenasKey,
  uploadZenasFiles,
} from "./plugin-actions";
import {
  fileDetail,
  formatSaved,
  PLUGIN_TABS,
  POPUP_TITLE,
  NEED_FILE,
  NEED_KEY,
  NEED_LINK,
  NEED_MCP,
  VAULT_NAME,
  VAULT_NOTE,
  VAULT_OFF_DETAIL,
  VAULT_OFF_MESSAGE,
  type PluginPanel,
  type PluginTab,
} from "./plugins";

const ACCEPT = ".csv,.xlsx,.pdf,text/csv,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export function PluginsDialog({
  open,
  tab,
  plugins,
  onTab,
  onClose,
}: {
  open: boolean;
  tab: PluginTab;
  plugins: PluginPanel;
  onTab: (tab: PluginTab) => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const titleId = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [note, setNote] = useState("");
  const [cadence, setCadence] = useState<"daily" | "weekly">("weekly");
  const [busy, setBusy] = useState(false);
  const [replacing, setReplacing] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setNote("");
      setReplacing(null);
      return;
    }
    closeRef.current?.focus();
  }, [open]);

  if (!open) return null;

  async function finish(result: { ok: boolean; message: string }) {
    setNote(result.message);
    if (result.ok) router.refresh();
  }

  async function onSaveKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const name = form.querySelector<HTMLInputElement>("input[name=key-name]")?.value ?? "";
    const value = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    const purpose = form.querySelector<HTMLInputElement>("input[name=purpose]")?.value ?? "What this key unlocks";
    form.reset();
    setReplacing(null);
    if (!name.trim() || !value.trim()) {
      setNote(NEED_KEY);
      return;
    }
    setBusy(true);
    try {
      await finish(await saveZenasKey(name, value, purpose));
    } finally {
      setBusy(false);
    }
  }

  async function onReplace(event: FormEvent<HTMLFormElement>, name: string, purpose: string) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const value = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    form.reset();
    if (!value.trim()) {
      setNote(NEED_KEY);
      return;
    }
    setBusy(true);
    try {
      await finish(await saveZenasKey(name, value, purpose || "What this key unlocks"));
      setReplacing(null);
    } finally {
      setBusy(false);
    }
  }

  async function onRemoveKey(name: string) {
    if (!plugins.enabled || busy) return;
    setBusy(true);
    try {
      await finish(await removeZenasSecret(name));
    } finally {
      setBusy(false);
    }
  }

  async function onRemoveFile(fileId: string) {
    if (!plugins.enabled || busy) return;
    setBusy(true);
    try {
      await finish(await removeZenasFile(fileId));
    } finally {
      setBusy(false);
    }
  }

  async function onRemoveLink(linkId: string) {
    if (!plugins.enabled || busy) return;
    setBusy(true);
    try {
      await finish(await removeZenasLink(linkId));
    } finally {
      setBusy(false);
    }
  }

  async function onRemoveConnection(connectionId: string) {
    if (!plugins.enabled || busy) return;
    setBusy(true);
    try {
      await finish(await removeZenasConnection(connectionId));
    } finally {
      setBusy(false);
    }
  }

  async function onFiles(list: FileList | File[] | null) {
    if (!plugins.enabled || busy) return;
    const files = list ? Array.from(list) : [];
    if (fileRef.current) fileRef.current.value = "";
    if (!files.length) {
      setNote(NEED_FILE);
      return;
    }
    const body = new FormData();
    for (const file of files) body.append("file", file);
    setBusy(true);
    try {
      await finish(await uploadZenasFiles(body));
    } finally {
      setBusy(false);
    }
  }

  async function onAddLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const url = form.querySelector<HTMLInputElement>("input[name=link]")?.value.trim() ?? "";
    const label = form.querySelector<HTMLInputElement>("input[name=label]")?.value.trim() ?? "";
    form.reset();
    if (!url) {
      setNote(NEED_LINK);
      return;
    }
    setBusy(true);
    try {
      await finish(await addZenasLink(url, label, cadence));
    } finally {
      setBusy(false);
    }
  }

  async function onAddConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const name = form.querySelector<HTMLInputElement>("input[name=connection-name]")?.value.trim() ?? "";
    const url = form.querySelector<HTMLInputElement>("input[name=server]")?.value.trim() ?? "";
    const token = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    form.reset();
    if (!name || !url) {
      setNote(NEED_MCP);
      return;
    }
    setBusy(true);
    try {
      await finish(await addZenasConnection(name, url, token));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="zn-shade" onClick={onClose}>
      <div
        className="zn-plugins"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="zn-plugins-head">
          <h2 id={titleId}>{POPUP_TITLE}</h2>
          <button ref={closeRef} className="zn-text" type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="zn-lead">Everything Zenas reads from. Add a key, upload a file, paste a link, or connect a tool.</p>
        <div className="zn-vault-row">
          <span className="zn-chip">{VAULT_NAME}</span>
          <span className={plugins.enabled ? "zn-chip zn-chip-fill" : "zn-chip"}>{plugins.enabled ? "On" : "Off"}</span>
        </div>
        {note ? <p className="zn-note-line">{note}</p> : null}
        {!plugins.enabled ? (
          <div className="zn-off">
            <h3>{plugins.message || VAULT_OFF_MESSAGE}</h3>
            <p>{VAULT_OFF_DETAIL}</p>
          </div>
        ) : (
          <>
            <div className="zn-tabs" role="tablist">
              {PLUGIN_TABS.map((name) => (
                <button
                  key={name}
                  className={name === tab ? "zn-tab active" : "zn-tab"}
                  type="button"
                  role="tab"
                  aria-selected={name === tab}
                  onClick={() => {
                    setNote("");
                    onTab(name);
                  }}
                >
                  {name}
                </button>
              ))}
            </div>
            {tab === "API keys" ? (
              <div className="zn-tab-body">
                <div className="zn-tab-title">
                  <h3>API keys</h3>
                </div>
                <p className="zn-lead">Keys for the providers Zenas reads from. A key is write-only: once saved it is never shown again.</p>
                <div className="zn-list">
                  {plugins.secrets.map((row) => (
                    <form className="zn-key-row" key={row.name} onSubmit={(event) => onReplace(event, row.name, row.purpose)}>
                      <div className="zn-key-name">
                        <strong>{row.name}</strong>
                        <span>{row.purpose || "What this key unlocks"}</span>
                        <code>{row.name}</code>
                      </div>
                      <span className="zn-chip zn-chip-dot">Connected</span>
                      {replacing === row.name ? (
                        <input type="password" autoComplete="off" placeholder="Paste key" disabled={busy} aria-label={`Replace ${row.name}`} />
                      ) : (
                        <span className="zn-mask">{row.mask} saved {formatSaved(row.savedAt)}</span>
                      )}
                      <span className="zn-row-actions">
                        {replacing === row.name ? (
                          <button className="zn-outline" type="submit" disabled={busy}>Save</button>
                        ) : (
                          <button className="zn-text" type="button" disabled={busy} onClick={() => setReplacing(row.name)}>Replace</button>
                        )}
                        <button className="zn-text" type="button" disabled={busy} onClick={() => onRemoveKey(row.name)}>Remove</button>
                      </span>
                    </form>
                  ))}
                  {plugins.secrets.length === 0 ? <p className="zn-empty">No keys saved.</p> : null}
                </div>
                <form className="zn-add" onSubmit={onSaveKey}>
                  <div>
                    <strong>Add a key</strong>
                    <span>For a provider that gives you a key.</span>
                  </div>
                  <label>
                    Key name
                    <input name="key-name" type="text" autoComplete="off" placeholder="KEY_NAME" disabled={busy} />
                  </label>
                  <label>
                    Key
                    <input type="password" autoComplete="off" placeholder="Paste key (hidden once saved)" disabled={busy} />
                  </label>
                  <button className="zn-primary" type="submit" disabled={busy}>Save key</button>
                </form>
                <p className="zn-fine">Keys are stored in Tank, encrypted, never in the app code. A saved key only ever shows as ••••••••.</p>
              </div>
            ) : null}
            {tab === "Files" ? (
              <div className="zn-tab-body">
                <div className="zn-tab-title">
                  <h3>Files</h3>
                </div>
                <p className="zn-lead">CSV, XLSX or PDF, up to 8 MB each. Files are stored in Tank, not in the app.</p>
                <div
                  className="zn-drop"
                  onDragOver={(event: DragEvent) => event.preventDefault()}
                  onDrop={(event: DragEvent) => {
                    event.preventDefault();
                    void onFiles(event.dataTransfer.files);
                  }}
                >
                  <p>Drop a CSV, XLSX or PDF here</p>
                  <button className="zn-primary" type="button" disabled={busy} onClick={() => fileRef.current?.click()}>
                    Upload file
                  </button>
                  <span>Up to 8 MB · other types are refused</span>
                  <input
                    ref={fileRef}
                    className="zn-sr"
                    type="file"
                    accept={ACCEPT}
                    multiple
                    onChange={() => void onFiles(fileRef.current?.files ?? null)}
                  />
                </div>
                <div className="zn-list">
                  {plugins.files.map((file) => (
                    <div className="zn-file-row" key={file.id}>
                      <div>
                        <strong>{file.name}</strong>
                        <span>{fileDetail(file)}</span>
                      </div>
                      <span className="zn-chip">{file.status === "ready" ? "Ready" : file.status}</span>
                      <button className="zn-text" type="button" disabled={busy} onClick={() => void onRemoveFile(file.id)}>
                        Remove
                      </button>
                    </div>
                  ))}
                  {plugins.files.length === 0 ? <p className="zn-empty">No files saved.</p> : null}
                </div>
              </div>
            ) : null}
            {tab === "Source links" ? (
              <div className="zn-tab-body">
                <div className="zn-tab-title">
                  <h3>Source links</h3>
                </div>
                <p className="zn-lead">Pages Zenas should keep an eye on. Nothing fetches them yet, so each one stays “Not checked”.</p>
                <form className="zn-add zn-add-link" onSubmit={onAddLink}>
                  <label>
                    Link
                    <input name="link" type="url" placeholder="https://" disabled={busy} />
                  </label>
                  <label>
                    Label
                    <input name="label" type="text" placeholder="Label" disabled={busy} />
                  </label>
                  <div className="zn-segment" role="group" aria-label="Check">
                    <span>Check</span>
                    <div>
                      <button className={cadence === "daily" ? "on" : ""} type="button" onClick={() => setCadence("daily")}>Daily</button>
                      <button className={cadence === "weekly" ? "on" : ""} type="button" onClick={() => setCadence("weekly")}>Weekly</button>
                    </div>
                  </div>
                  <button className="zn-primary" type="submit" disabled={busy}>Add link</button>
                </form>
                <div className="zn-list">
                  {plugins.links.map((link) => (
                    <div className="zn-file-row" key={link.id}>
                      <div>
                        <strong>{link.url}</strong>
                        <span>{link.label || link.url} · {link.frequency === "daily" ? "Daily" : "Weekly"} · last checked {link.lastChecked ? formatSaved(link.lastChecked) : "—"}</span>
                      </div>
                      <span className="zn-chip">{link.status === "not checked" ? "Not checked" : link.status}</span>
                      <button className="zn-text" type="button" disabled={busy} onClick={() => void onRemoveLink(link.id)}>
                        Remove
                      </button>
                    </div>
                  ))}
                  {plugins.links.length === 0 ? <p className="zn-empty">No links saved.</p> : null}
                </div>
              </div>
            ) : null}
            {tab === "MCP connections" ? (
              <div className="zn-tab-body">
                <div className="zn-tab-title">
                  <h3>MCP connections</h3>
                </div>
                <p className="zn-lead">Connect a tool over MCP. The token is a password field and is not shown again.</p>
                <form className="zn-add zn-add-mcp" onSubmit={onAddConnection}>
                  <label>
                    Connection name
                    <input name="connection-name" type="text" placeholder="Name" disabled={busy} />
                  </label>
                  <label>
                    Server URL
                    <input name="server" type="url" placeholder="https://" disabled={busy} />
                  </label>
                  <label className="zn-token">
                    Token
                    <input type="password" autoComplete="off" placeholder="Paste token (hidden once saved)" disabled={busy} />
                  </label>
                  <button className="zn-primary" type="submit" disabled={busy}>Add connection</button>
                </form>
                <div className="zn-list">
                  {plugins.connections.map((row) => (
                    <div className="zn-file-row" key={row.id}>
                      <div>
                        <strong>{row.name}</strong>
                        <span>{row.serverUrl} · Token {row.hasToken ? "••••••••" : "—"}</span>
                      </div>
                      <span className="zn-chip">{row.status === "saved" ? "Saved" : row.status}</span>
                      <button className="zn-text" type="button" disabled={busy} onClick={() => void onRemoveConnection(row.id)}>
                        Remove
                      </button>
                    </div>
                  ))}
                  {plugins.connections.length === 0 ? <p className="zn-empty">No connections saved.</p> : null}
                </div>
              </div>
            ) : null}
          </>
        )}
        <p className="zn-callout">{VAULT_NOTE}</p>
      </div>
    </div>
  );
}
