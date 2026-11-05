"use client";

import { useState } from "react";
import {
  addRigConnectionAction,
  addRigLinkAction,
  removeRigConnectionAction,
  removeRigFileAction,
  removeRigKey,
  removeRigLinkAction,
  saveRigKey,
  uploadRigFileForm,
} from "./plugin-actions";
import { PLUGIN_TABS, type PluginPanel, type PluginTab } from "./vault-model";

function Pill({ children, on = true }: { children: string; on?: boolean }) {
  return (
    <span className={on ? "pill" : "pill off"}>
      <i />
      {children}
    </span>
  );
}

export function Plugins({ panel, onClose }: { panel: PluginPanel; onClose: () => void }) {
  const [tab, setTab] = useState<PluginTab>("API keys");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [often, setOften] = useState<"daily" | "weekly">("daily");
  const [signIn, setSignIn] = useState<"provider" | "token">("token");

  async function run(work: () => Promise<{ ok: boolean; message: string }>) {
    if (!panel.enabled || busy) return;
    setBusy(true);
    try {
      const result = await work();
      setNote(result.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="popup" role="dialog" aria-modal="true" aria-labelledby="plugins-title">
        <div className="popup-head">
          <div>
            <h2 id="plugins-title">Plugins</h2>
            {panel.enabled ? <p className="sub">Everything Rig reads from. Add a key, upload a file, or paste a link.</p> : null}
          </div>
          <button type="button" className="text close" onClick={onClose}>
            Close
          </button>
        </div>

        {panel.enabled ? (
          <>
            <div className="tabs" role="tablist">
              {PLUGIN_TABS.map((name) => (
                <button key={name} type="button" role="tab" aria-selected={tab === name} className={tab === name ? "on" : ""} onClick={() => { setNote(""); setTab(name); }}>
                  {name}
                </button>
              ))}
            </div>
            {note ? <p className="note">{note}</p> : null}

            {tab === "API keys" ? (
              <div className="part">
                <h3>API keys</h3>
                <p className="intro">Keys for public feeds and your suppliers. A key is write-only: once saved it is never shown again.</p>
                <div className="list">
                  {panel.secrets.map((row) => (
                    <div className="key-row" key={row.name}>
                      <div>
                        <h4>{row.name}</h4>
                        <p>{row.purpose}</p>
                      </div>
                      <Pill>Saved</Pill>
                      <div className="mask">{row.mask}</div>
                      <button type="button" className="text" disabled={busy} onClick={() => void run(() => removeRigKey(row.name))}>
                        Remove
                      </button>
                    </div>
                  ))}
                  <form
                    className="add"
                    onSubmit={(event) => {
                      event.preventDefault();
                      const form = event.currentTarget;
                      const name = form.querySelector<HTMLInputElement>("input[name=key-name]")?.value ?? "";
                      const purpose = form.querySelector<HTMLInputElement>("input[name=purpose]")?.value ?? "";
                      const field = form.querySelector<HTMLInputElement>("input[type=password]");
                      const secret = field?.value ?? "";
                      if (field) field.value = "";
                      form.reset();
                      void run(() => saveRigKey(name, secret, purpose));
                    }}
                  >
                    <label>
                      Key name
                      <input name="key-name" type="text" placeholder="Key name" autoComplete="off" disabled={busy} />
                    </label>
                    <label>
                      What it unlocks
                      <input name="purpose" type="text" placeholder="What this key is for" autoComplete="off" disabled={busy} />
                    </label>
                    <label>
                      Key
                      <input type="password" aria-label="Key" placeholder="Paste key (hidden once saved)" autoComplete="off" disabled={busy} />
                    </label>
                    <button type="submit" className="primary" disabled={busy}>
                      Save key
                    </button>
                  </form>
                </div>
              </div>
            ) : null}

            {tab === "Files" ? (
              <div className="part">
                <h3>Files</h3>
                <p className="intro">Upload price lists, inventory, reports or rules. CSV, XLSX, and PDF only.</p>
                <div className="drop">
                  <strong>Drag CSV, XLSX or PDF files here</strong>
                  <span>or</span>
                  <button type="button" className="primary" disabled={busy} onClick={() => document.getElementById("rig-files")?.click()}>
                    Choose files
                  </button>
                  <input
                    id="rig-files"
                    type="file"
                    accept=".csv,.xlsx,.xls,.pdf"
                    multiple
                    hidden
                    onChange={(event) => {
                      const list = event.target.files;
                      event.target.value = "";
                      if (!list?.length) return;
                      const body = new FormData();
                      for (const file of Array.from(list)) body.append("file", file);
                      void run(() => uploadRigFileForm(body));
                    }}
                  />
                </div>
                <div className="list">
                  {panel.files.map((file) => (
                    <div className="key-row" key={file.id}>
                      <div>
                        <h4>{file.name}</h4>
                        <p>{file.type} · {file.status}</p>
                      </div>
                      <button type="button" className="text" disabled={busy} onClick={() => void run(() => removeRigFileAction(file.id))}>
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {tab === "Source links" ? (
              <div className="part">
                <h3>Source links</h3>
                <p className="intro">Pages Rig checks for changes, such as rule pages, season dates or port stats.</p>
                <form
                  className="list"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const url = form.querySelector<HTMLInputElement>("input[name=link]")?.value ?? "";
                    const label = form.querySelector<HTMLInputElement>("input[name=label]")?.value ?? "";
                    const frequency = often;
                    form.reset();
                    void run(() => addRigLinkAction(url, label, frequency));
                  }}
                >
                  <div className="stack link-add">
                    <label>
                      Link
                      <input name="link" type="url" placeholder="Paste a link, e.g. https://…" autoComplete="off" disabled={busy} />
                    </label>
                    <label>
                      Label (optional)
                      <input name="label" type="text" placeholder="e.g. Season dates" autoComplete="off" disabled={busy} />
                    </label>
                    <div>
                      <span className="env-label">How often to check</span>
                      <div className="seg" role="group" aria-label="How often to check">
                        <button type="button" className={often === "daily" ? "on" : ""} onClick={() => setOften("daily")}>Daily</button>
                        <button type="button" className={often === "weekly" ? "on" : ""} onClick={() => setOften("weekly")}>Weekly</button>
                      </div>
                    </div>
                    <button type="submit" className="primary" disabled={busy}>Add link</button>
                  </div>
                </form>
                <div className="list">
                  {panel.links.map((link) => (
                    <div className="link-row" key={link.id}>
                      <div>
                        <h4>{link.label}</h4>
                        <span className="host">{link.url}</span>
                      </div>
                      <Pill on={link.status !== "not checked"}>{link.status}</Pill>
                      <button type="button" className="text" disabled={busy} onClick={() => void run(() => removeRigLinkAction(link.id))}>
                        Remove
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {tab === "MCP connections" ? (
              <div className="part">
                <h3>MCP connections</h3>
                <p className="intro">Connections to outside tools and data servers that Rig can read from.</p>
                <div className="list">
                  {panel.connections.map((row) => (
                    <div className="conn-row" key={row.id}>
                      <div>
                        <h4>{row.name}</h4>
                        <span className="host">{row.serverUrl}</span>
                      </div>
                      <p>{row.hasToken ? row.status : "Sign in with the provider"}</p>
                      <Pill on={row.hasToken}>{row.status}</Pill>
                      <button type="button" className="text" disabled={busy} onClick={() => void run(() => removeRigConnectionAction(row.id))}>
                        Disconnect
                      </button>
                    </div>
                  ))}
                </div>
                <form
                  className="list"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const name = form.querySelector<HTMLInputElement>("input[name=conn-name]")?.value ?? "";
                    const server = form.querySelector<HTMLInputElement>("input[name=server]")?.value ?? "";
                    const field = form.querySelector<HTMLInputElement>("input[type=password]");
                    const token = field?.value ?? "";
                    if (field) field.value = "";
                    const authType = signIn;
                    form.reset();
                    void run(() => addRigConnectionAction(name, server, authType, token));
                  }}
                >
                  <div className="stack">
                    <h4>Add connection</h4>
                    <div className="pair">
                      <label>
                        Name
                        <input name="conn-name" type="text" placeholder="e.g. Supplier catalogue" autoComplete="off" disabled={busy} />
                      </label>
                      <label>
                        Server URL
                        <input name="server" type="url" placeholder="https://…" autoComplete="off" disabled={busy} />
                      </label>
                    </div>
                    <div className="pair end">
                      <div>
                        <span className="env-label">How it signs in</span>
                        <div className="seg" role="group" aria-label="How it signs in">
                          <button type="button" className={signIn === "provider" ? "on" : ""} onClick={() => setSignIn("provider")}>Sign in with the provider</button>
                          <button type="button" className={signIn === "token" ? "on" : ""} onClick={() => setSignIn("token")}>Token</button>
                        </div>
                      </div>
                      {signIn === "token" ? (
                        <label>
                          Token
                          <input type="password" aria-label="Token" placeholder="Paste token (hidden once saved)" autoComplete="off" disabled={busy} />
                        </label>
                      ) : null}
                      <button type="submit" className="primary" disabled={busy}>Add connection</button>
                    </div>
                  </div>
                </form>
              </div>
            ) : null}
          </>
        ) : (
          <p className="vault-off">{panel.message}</p>
        )}
      </div>
    </div>
  );
}
