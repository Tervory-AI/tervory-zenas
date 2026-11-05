"use client";

import { useRef, useState, type DragEvent, type FormEvent } from "react";
import {
  addTigerConnection,
  addTigerLink,
  removeTigerConnection,
  removeTigerFile,
  removeTigerLink,
  removeTigerSecret,
  saveTigerKey,
  uploadTigerFiles,
} from "./plugin-actions";
import {
  PLUGIN_TABS,
  NEED_FILE,
  NEED_KEY,
  NEED_LINK,
  NEED_MCP,
  VAULT_OFF_MESSAGE,
  type PluginPanel,
  type PluginTab,
} from "./plugins-data";

export function Plugins({ plugins, onClose, initialTab = "API keys" }: { plugins: PluginPanel; onClose: () => void; initialTab?: PluginTab }) {
  const [tab, setTab] = useState<PluginTab>(initialTab);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [often, setOften] = useState<"Daily" | "Weekly">("Daily");
  const [signIn, setSignIn] = useState<"provider" | "token">("token");
  const fileRef = useRef<HTMLInputElement>(null);

  async function finish(result: { ok: boolean; message: string }) {
    setNote(result.message);
  }

  async function onSaveKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const name = form.querySelector<HTMLInputElement>("input[name=key-name]")?.value.trim() ?? "";
    const purpose = form.querySelector<HTMLInputElement>("input[name=purpose]")?.value.trim() ?? "";
    const value = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    form.reset();
    if (!name || !value.trim()) {
      setNote(NEED_KEY);
      return;
    }
    setBusy(true);
    try {
      await finish(await saveTigerKey(name, value, purpose || "Key"));
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
      await finish(await uploadTigerFiles(body));
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
      await finish(await addTigerLink(url, label, often === "Daily" ? "daily" : "weekly"));
    } finally {
      setBusy(false);
    }
  }

  async function onAddConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const name = form.querySelector<HTMLInputElement>("input[name=conn-name]")?.value.trim() ?? "";
    const server = form.querySelector<HTMLInputElement>("input[name=server]")?.value.trim() ?? "";
    const token = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    form.reset();
    if (!name || !server) {
      setNote(NEED_MCP);
      return;
    }
    setBusy(true);
    try {
      await finish(await addTigerConnection(name, server, signIn, token));
    } finally {
      setBusy(false);
    }
  }

  const primary = plugins.enabled ? "primary" : "line";

  return (
    <div className="scrim" role="presentation" onClick={onClose}>
      <div className="popup" role="dialog" aria-modal="true" aria-labelledby="plugins-title" onClick={(event) => event.stopPropagation()}>
        <div className="popup-head">
          <div>
            <h2 id="plugins-title">Plugins</h2>
            <p className="sub">Everything Tiger connects to. Add a key, upload a file, or paste a link.</p>
          </div>
          <button type="button" className="text" onClick={onClose}>Close</button>
        </div>
        <div className="tabs" role="tablist" aria-label="Plugins">
          {PLUGIN_TABS.map((item) => (
            <button key={item} type="button" role="tab" aria-selected={tab === item} className={tab === item ? "on" : ""} onClick={() => { setTab(item); setNote(""); }}>
              {item}
            </button>
          ))}
        </div>
        {note ? <p className="notice">{note}</p> : null}
        {!plugins.enabled ? (
          <p className="vault-off">{plugins.message || VAULT_OFF_MESSAGE}</p>
        ) : tab === "API keys" ? (
          <div className="part" role="tabpanel">
            <div className="part-title"><h3>API keys</h3></div>
            <p className="intro">A key is write-only. Once saved it is shown only as dots and is never shown again.</p>
            <div className="list">
              {plugins.secrets.map((row) => (
                <div className="key-row" key={row.name}>
                  <div>
                    <h4>{row.name}</h4>
                    <p>{row.purpose}</p>
                  </div>
                  <span className="pill"><i />Connected</span>
                  <div className="mask">{row.mask}</div>
                  <button type="button" className="text" disabled={busy} onClick={() => { setBusy(true); void removeTigerSecret(row.name).then((result) => finish(result)).finally(() => setBusy(false)); }}>Remove</button>
                </div>
              ))}
              <form className="add" onSubmit={onSaveKey}>
                <div className="who">
                  <h4>Add a key</h4>
                  <p>The key is sent to Tank and is not kept in the browser.</p>
                </div>
                <label>
                  Name
                  <input name="key-name" type="text" autoComplete="off" disabled={busy} />
                </label>
                <label>
                  What it is for
                  <input name="purpose" type="text" autoComplete="off" disabled={busy} />
                </label>
                <label>
                  Key
                  <input type="password" aria-label="Key" placeholder="Paste key" autoComplete="off" disabled={busy} />
                </label>
                <button type="submit" className={primary} disabled={busy}>Save key</button>
              </form>
            </div>
          </div>
        ) : tab === "Files" ? (
          <div className="part" role="tabpanel">
            <div className="part-title"><h3>Files</h3></div>
            <p className="intro">Upload CSV, XLSX, or PDF files. The file is stored by Tank, not in the page.</p>
            <div className="drop" onDragOver={(event: DragEvent) => event.preventDefault()} onDrop={(event: DragEvent) => { event.preventDefault(); void onFiles(event.dataTransfer.files); }}>
              <strong>Drag CSV, XLSX or PDF files here</strong>
              <span>or</span>
              <button type="button" className={primary} disabled={busy} onClick={() => fileRef.current?.click()}>Choose files</button>
              <input ref={fileRef} id="tiger-files" type="file" accept=".csv,.xlsx,.pdf,text/csv,application/pdf" multiple hidden onChange={() => void onFiles(fileRef.current?.files ?? null)} />
            </div>
            {plugins.files.length ? (
              <table className="file-table">
                <thead><tr><th>File</th><th>Type</th><th>Status</th><th /></tr></thead>
                <tbody>
                  {plugins.files.map((file) => (
                    <tr key={file.id}>
                      <td className="name">{file.name}</td>
                      <td>{file.type}</td>
                      <td>{file.status}</td>
                      <td><button type="button" className="text" disabled={busy} onClick={() => { setBusy(true); void removeTigerFile(file.id).then((result) => finish(result)).finally(() => setBusy(false)); }}>Remove</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <p className="fine">No files stored.</p>}
          </div>
        ) : tab === "Source links" ? (
          <div className="part" role="tabpanel">
            <div className="part-title"><h3>Source links</h3></div>
            <p className="intro">Product pages Tiger reads for price and stock. Public pages only.</p>
            <form className="link-form" onSubmit={onAddLink}>
              <label>
                Link
                <input name="link" type="url" placeholder="https://…" autoComplete="off" disabled={busy} />
              </label>
              <label>
                Label (optional)
                <input name="label" type="text" autoComplete="off" disabled={busy} />
              </label>
              <div>
                <span className="field-label">How often to check</span>
                <div className="seg">
                  <button type="button" className={often === "Daily" ? "on" : ""} onClick={() => setOften("Daily")}>Daily</button>
                  <button type="button" className={often === "Weekly" ? "on" : ""} onClick={() => setOften("Weekly")}>Weekly</button>
                </div>
              </div>
              <button type="submit" className={primary} disabled={busy}>Add link</button>
            </form>
            <div className="list">
              {plugins.links.map((link) => (
                <div className="link-row" key={link.id}>
                  <div>
                    <h4>{link.label}</h4>
                    <span className="host">{link.url}</span>
                  </div>
                  <span className="pill"><i />{link.status}</span>
                  <button type="button" className="text" disabled={busy} onClick={() => { setBusy(true); void removeTigerLink(link.id).then((result) => finish(result)).finally(() => setBusy(false)); }}>Remove</button>
                </div>
              ))}
            </div>
            {plugins.links.length === 0 ? <p className="fine">No links stored.</p> : null}
          </div>
        ) : (
          <div className="part" role="tabpanel">
            <div className="part-title"><h3>MCP connections</h3></div>
            <p className="intro">Connections to outside tools Tiger can use. A saved token is shown only as dots.</p>
            <div className="list">
              {plugins.connections.map((row) => (
                <div className="conn-row" key={row.id}>
                  <div>
                    <h4>{row.name}</h4>
                    <span className="host">{row.serverUrl}</span>
                  </div>
                  <p>{row.hasToken ? "••••••••" : "Signed in with the provider"}</p>
                  <span className="pill"><i />{row.status}</span>
                  <button type="button" className="text" disabled={busy} onClick={() => { setBusy(true); void removeTigerConnection(row.id).then((result) => finish(result)).finally(() => setBusy(false)); }}>Disconnect</button>
                </div>
              ))}
            </div>
            <form className="conn-form" onSubmit={onAddConnection}>
              <h4>Add connection</h4>
              <div className="pair">
                <label>
                  Name
                  <input name="conn-name" type="text" autoComplete="off" disabled={busy} />
                </label>
                <label>
                  Server URL
                  <input name="server" type="url" placeholder="https://…" autoComplete="off" disabled={busy} />
                </label>
              </div>
              <div className="pair end">
                <div>
                  <span className="field-label">How it signs in</span>
                  <div className="seg">
                    <button type="button" className={signIn === "provider" ? "on" : ""} onClick={() => setSignIn("provider")}>Sign in with the provider</button>
                    <button type="button" className={signIn === "token" ? "on" : ""} onClick={() => setSignIn("token")}>Token</button>
                  </div>
                </div>
                {signIn === "token" ? (
                  <label>
                    Token
                    <input type="password" aria-label="Token" placeholder="Paste token (hidden once saved)" autoComplete="off" disabled={busy} />
                  </label>
                ) : <span />}
                <button type="submit" className={primary} disabled={busy}>Add connection</button>
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
