"use client";

import { useState, type DragEvent, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { PLUGIN_TABS, type PluginTab } from "./copy";
import {
  addTisanConnection,
  addTisanLink,
  removeTisanConnection,
  removeTisanFile,
  removeTisanLink,
  removeTisanSecret,
  saveTisanKey,
  uploadTisanFiles,
  type PluginResult,
} from "./plugin-actions";
import {
  formatBytes,
  savedDay,
  TISAN_VAULT,
  VAULT_OFF_MESSAGE,
  type ConnectionMeta,
  type FileMeta,
  type LinkMeta,
  type PluginPanel,
  type SecretMeta,
} from "./vault-model";

function btn(primary: boolean): string {
  return primary ? "primary" : "line";
}

function hostOf(raw: string): string {
  try {
    const withScheme = raw.includes("://") ? raw : `https://${raw}`;
    return new URL(withScheme).host;
  } catch {
    return raw;
  }
}

function VaultOff({ message }: { message: string }) {
  if (message !== VAULT_OFF_MESSAGE) {
    return <p className="intro">{message}</p>;
  }
  return (
    <div className="vault-off">
      <h3>Plugins are off</h3>
      <p>Tisan cannot read or save any plugin while its vault is off.</p>
      <div className="vault-card">
        <div className="vault-card-top">
          <strong>{TISAN_VAULT}</strong>
          <span className="pill quiet"><i />Off</span>
        </div>
        <p className="vault-line">{VAULT_OFF_MESSAGE}</p>
        <p>API keys, files, source links and MCP connections are kept in this vault. Nothing can be added or used until it is on.</p>
      </div>
    </div>
  );
}

export function Plugins({
  onClose,
  tab,
  setTab,
  desktopAction,
  plugins,
}: {
  onClose: () => void;
  tab: PluginTab;
  setTab: (tab: PluginTab) => void;
  desktopAction: string;
  plugins: PluginPanel;
}) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [often, setOften] = useState<"Daily" | "Weekly">("Daily");
  const [signIn, setSignIn] = useState<"provider" | "token">("token");
  const [busy, setBusy] = useState(false);
  const enabled = plugins.enabled;

  async function finish(result: PluginResult) {
    setNote(result.message);
    if (result.ok) router.refresh();
  }

  async function onSaveKey(event: FormEvent<HTMLFormElement>, name: string, purpose: string) {
    event.preventDefault();
    if (!enabled || busy) return;
    const form = event.currentTarget;
    const value = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    form.reset();
    if (!name.trim() || !value.trim()) {
      setNote("Add a key first. Nothing was stored.");
      return;
    }
    setBusy(true);
    try {
      await finish(await saveTisanKey(name, value, purpose));
    } finally {
      setBusy(false);
    }
  }

  async function onFiles(list: FileList | null) {
    if (!enabled || busy) return;
    const files = list ? Array.from(list) : [];
    if (!files.length) {
      setNote("Choose a file first. Nothing was saved.");
      return;
    }
    const body = new FormData();
    for (const file of files) body.append("file", file);
    setBusy(true);
    try {
      await finish(await uploadTisanFiles(body));
    } finally {
      setBusy(false);
    }
  }

  async function onAddLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enabled || busy) return;
    const form = event.currentTarget;
    const url = form.querySelector<HTMLInputElement>("input[name=link]")?.value.trim() ?? "";
    const label = form.querySelector<HTMLInputElement>("input[name=label]")?.value.trim() ?? "";
    const frequency = often === "Daily" ? "daily" : "weekly";
    form.reset();
    setOften("Daily");
    if (!url) {
      setNote("Add a link first. Nothing was saved.");
      return;
    }
    setBusy(true);
    try {
      await finish(await addTisanLink(url, label, frequency));
    } finally {
      setBusy(false);
    }
  }

  async function onAddConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!enabled || busy) return;
    const form = event.currentTarget;
    const name = form.querySelector<HTMLInputElement>("input[name=conn-name]")?.value.trim() ?? "";
    const server = form.querySelector<HTMLInputElement>("input[name=server]")?.value.trim() ?? "";
    const token = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    const auth = signIn;
    form.reset();
    if (!name || !server) {
      setNote("Add a name and a server URL first. Nothing was saved.");
      return;
    }
    setBusy(true);
    try {
      await finish(await addTisanConnection(name, server, auth, token));
    } finally {
      setBusy(false);
    }
  }

  const off = <VaultOff message={plugins.message || VAULT_OFF_MESSAGE} />;

  return (
    <div className="scrim" role="presentation">
      <div className="popup" role="dialog" aria-modal="true" aria-labelledby="plugins-title">
        <PopupHead onClose={onClose} titleId="plugins-title" />
        <div className="tabs" role="tablist">
          {PLUGIN_TABS.map((item) => (
            <button key={item.id} type="button" role="tab" aria-selected={tab === item.id} className={tab === item.id ? "on" : ""} onClick={() => { setNote(""); setTab(item.id); }}>
              {item.label}
            </button>
          ))}
        </div>
        {note ? <p className="note">{note}</p> : null}
        {!enabled ? off : tab === "keys" ? (
          <KeysPart
            secrets={plugins.secrets}
            busy={busy}
            saveClass={btn(desktopAction === "save-key")}
            onSaveKey={onSaveKey}
            onRemove={(name) => { setBusy(true); void removeTisanSecret(name).then(finish).finally(() => setBusy(false)); }}
          />
        ) : tab === "files" ? (
          <FilesPart
            files={plugins.files}
            busy={busy}
            chooseClass={btn(desktopAction === "choose-files")}
            inputId="tisan-files"
            onFiles={onFiles}
            onRemove={(id) => { setBusy(true); void removeTisanFile(id).then(finish).finally(() => setBusy(false)); }}
          />
        ) : tab === "links" ? (
          <LinksPart
            links={plugins.links}
            busy={busy}
            often={often}
            setOften={setOften}
            addClass={btn(desktopAction === "add-link")}
            onAddLink={onAddLink}
            onRemove={(id) => { setBusy(true); void removeTisanLink(id).then(finish).finally(() => setBusy(false)); }}
          />
        ) : (
          <ConnectionsPart
            connections={plugins.connections}
            busy={busy}
            signIn={signIn}
            setSignIn={setSignIn}
            addClass={btn(desktopAction === "add-connection")}
            onAddConnection={onAddConnection}
            onRemove={(id) => { setBusy(true); void removeTisanConnection(id).then(finish).finally(() => setBusy(false)); }}
          />
        )}
      </div>

      <div className="groups" role="dialog" aria-modal="true" aria-labelledby="plugins-sheet-title">
        <PopupHead onClose={onClose} titleId="plugins-sheet-title" />
        {note ? <p className="note">{note}</p> : null}
        {!enabled ? off : (
          <>
            <KeysPart
              secrets={plugins.secrets}
              busy={busy}
              saveClass="primary"
              onSaveKey={onSaveKey}
              onRemove={(name) => { setBusy(true); void removeTisanSecret(name).then(finish).finally(() => setBusy(false)); }}
            />
            <FilesPart
              files={plugins.files}
              busy={busy}
              chooseClass="line"
              inputId="tisan-files-mobile"
              onFiles={onFiles}
              onRemove={(id) => { setBusy(true); void removeTisanFile(id).then(finish).finally(() => setBusy(false)); }}
            />
            <LinksPart
              links={plugins.links}
              busy={busy}
              often={often}
              setOften={setOften}
              addClass="line"
              onAddLink={onAddLink}
              onRemove={(id) => { setBusy(true); void removeTisanLink(id).then(finish).finally(() => setBusy(false)); }}
            />
            <ConnectionsPart
              connections={plugins.connections}
              busy={busy}
              signIn={signIn}
              setSignIn={setSignIn}
              addClass="line"
              onAddConnection={onAddConnection}
              onRemove={(id) => { setBusy(true); void removeTisanConnection(id).then(finish).finally(() => setBusy(false)); }}
            />
          </>
        )}
      </div>
    </div>
  );
}

function PopupHead({ onClose, titleId }: { onClose: () => void; titleId: string }) {
  return (
    <div className="popup-head">
      <div>
        <h2 id={titleId}>Plugins</h2>
        <p className="sub">Everything Tisan reads from. Add a key, upload a file, or paste a link.</p>
      </div>
      <button type="button" className="text" onClick={onClose}>Close</button>
    </div>
  );
}

function KeysPart({
  secrets,
  busy,
  saveClass,
  onSaveKey,
  onRemove,
}: {
  secrets: SecretMeta[];
  busy: boolean;
  saveClass: string;
  onSaveKey: (event: FormEvent<HTMLFormElement>, name: string, purpose: string) => void;
  onRemove: (name: string) => void;
}) {
  return (
    <div className="part">
      <div className="part-title"><h3>API keys</h3></div>
      <p className="intro">Keys for tools Tisan may use. A key is write-only: once saved it is never shown again.</p>
      <div className="list">
        {secrets.map((row) => (
          <form key={row.name} className="key-row" onSubmit={(event) => onSaveKey(event, row.name, row.purpose || "Tool key")}>
            <div>
              <h4>{row.name}</h4>
              <p>{row.purpose}</p>
            </div>
            <span className="pill solid"><i />Saved</span>
            <div className="mask">{row.mask}{row.savedAt ? ` saved ${savedDay(row.savedAt)}` : ""}</div>
            <div className="row-actions">
              <button type="submit" className="text" disabled={busy}>Replace</button>
              <button type="button" className="text" disabled={busy} onClick={() => onRemove(row.name)}>Remove</button>
            </div>
          </form>
        ))}
        {secrets.length === 0 ? <p className="empty-row">No keys saved.</p> : null}
        <form className="add" onSubmit={(event) => {
          const name = event.currentTarget.querySelector<HTMLInputElement>("input[name=tool]")?.value.trim() ?? "";
          onSaveKey(event, name, "Tool key");
        }}>
          <div className="who">
            <h4>Add a tool key</h4>
            <p>For a host or tool that gives you a key.</p>
          </div>
          <label>
            Tool name
            <input name="tool" type="text" placeholder="e.g. Font host" autoComplete="off" disabled={busy} />
          </label>
          <label>
            Key
            <input type="password" aria-label="Tool key" placeholder="Paste key (hidden once saved)" autoComplete="off" disabled={busy} />
          </label>
          <button type="submit" className={saveClass} disabled={busy}>Save key</button>
        </form>
      </div>
      <p className="note">Keys are stored on the server, never in the app code.</p>
    </div>
  );
}

function FilesPart({
  files,
  busy,
  chooseClass,
  inputId,
  onFiles,
  onRemove,
}: {
  files: FileMeta[];
  busy: boolean;
  chooseClass: string;
  inputId: string;
  onFiles: (list: FileList | null) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="part">
      <div className="part-title"><h3>Files</h3></div>
      <p className="intro">Upload copy, briefs or lists for the site. CSV, XLSX, and PDF only.</p>
      <div
        className="drop"
        onDragOver={(event: DragEvent) => event.preventDefault()}
        onDrop={(event: DragEvent) => {
          event.preventDefault();
          onFiles(event.dataTransfer.files);
        }}
      >
        <strong>Drag CSV, XLSX or PDF files here</strong>
        <span>or</span>
        <button type="button" className={chooseClass} disabled={busy} onClick={() => document.getElementById(inputId)?.click()}>Choose files</button>
        <input id={inputId} type="file" accept=".csv,.xlsx,.xls,.pdf" multiple hidden onChange={(event) => { onFiles(event.target.files); event.target.value = ""; }} />
      </div>
      <div className="list">
        {files.map((file) => (
          <div className="key-row" key={file.id}>
            <div>
              <h4>{file.name}</h4>
              <p>{file.type || "File"} · {formatBytes(file.size)}{file.rows != null ? ` · ${file.rows} rows` : ""}{file.pages != null ? ` · ${file.pages} pages` : ""}</p>
            </div>
            <span className="pill quiet"><i />{file.status}</span>
            <span>{file.uploadedAt ? `Uploaded ${savedDay(file.uploadedAt)}` : ""}</span>
            <button type="button" className="text" disabled={busy} onClick={() => onRemove(file.id)}>Remove</button>
          </div>
        ))}
        {files.length === 0 ? <p className="empty-row">No files stored.</p> : null}
      </div>
    </div>
  );
}

function LinksPart({
  links,
  busy,
  often,
  setOften,
  addClass,
  onAddLink,
  onRemove,
}: {
  links: LinkMeta[];
  busy: boolean;
  often: "Daily" | "Weekly";
  setOften: (value: "Daily" | "Weekly") => void;
  addClass: string;
  onAddLink: (event: FormEvent<HTMLFormElement>) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="part">
      <div className="part-title"><h3>Source links</h3></div>
      <p className="intro">Pages Tisan checks for changes, such as the live site or a page named in the brief.</p>
      <form className="list link-add" onSubmit={onAddLink}>
        <label>
          Link
          <input name="link" type="url" placeholder="Paste a link, e.g. https://…" autoComplete="off" disabled={busy} />
        </label>
        <label>
          Label (optional)
          <input name="label" type="text" placeholder="e.g. Contact page" autoComplete="off" disabled={busy} />
        </label>
        <div>
          <span className="env-label">How often to check</span>
          <div className="seg" role="group" aria-label="How often to check">
            <button type="button" className={often === "Daily" ? "on" : ""} onClick={() => setOften("Daily")}>Daily</button>
            <button type="button" className={often === "Weekly" ? "on" : ""} onClick={() => setOften("Weekly")}>Weekly</button>
          </div>
        </div>
        <button type="submit" className={addClass} disabled={busy}>Add link</button>
      </form>
      <div className="list">
        {links.map((link) => (
          <div className="link-row" key={link.id}>
            <div>
              <h4>{link.label}</h4>
              <span className="host">{hostOf(link.url)}</span>
            </div>
            <span>Last checked {link.lastChecked ? savedDay(link.lastChecked) : "not yet"}</span>
            <span className="pill quiet"><i />{link.status}</span>
            <button type="button" className="text" disabled={busy} onClick={() => onRemove(link.id)}>Remove</button>
          </div>
        ))}
        {links.length === 0 ? <p className="empty-row">No links saved.</p> : null}
      </div>
    </div>
  );
}

function ConnectionsPart({
  connections,
  busy,
  signIn,
  setSignIn,
  addClass,
  onAddConnection,
  onRemove,
}: {
  connections: ConnectionMeta[];
  busy: boolean;
  signIn: "provider" | "token";
  setSignIn: (value: "provider" | "token") => void;
  addClass: string;
  onAddConnection: (event: FormEvent<HTMLFormElement>) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="part">
      <div className="part-title"><h3>MCP connections</h3></div>
      <p className="intro">Connections to outside tools that Tisan can read from.</p>
      <div className="list">
        {connections.map((row) => (
          <div className="conn-row" key={row.id}>
            <div>
              <h4>{row.name}</h4>
              <span className="host">{hostOf(row.serverUrl)}</span>
            </div>
            <p>{row.hasToken ? "••••••••" : "Sign in with the provider"}</p>
            <span className={row.status === "saved" || row.status === "Connected" ? "pill solid" : "pill quiet"}><i />{row.status}</span>
            <span>Last used {row.lastUsed ? savedDay(row.lastUsed) : "not yet"}</span>
            <button type="button" className="text" disabled={busy} onClick={() => onRemove(row.id)}>Disconnect</button>
          </div>
        ))}
        {connections.length === 0 ? <p className="empty-row">No connections saved.</p> : null}
      </div>
      <form className="list conn-add" onSubmit={onAddConnection}>
        <h4>Add connection</h4>
        <div className="pair">
          <label>
            Name
            <input name="conn-name" type="text" placeholder="e.g. Saved design" autoComplete="off" disabled={busy} />
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
          ) : <span />}
          <button type="submit" className={addClass} disabled={busy}>Add connection</button>
        </div>
        <p className="note">A saved token shows only as ••••••••, with Disconnect. It is never shown again.</p>
      </form>
    </div>
  );
}
