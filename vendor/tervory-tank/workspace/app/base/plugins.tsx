"use client";

/*
 * Plugins tab: API keys, Files, Source links and MCP connections, wired to base.vault through Tank.
 * Same pattern as Market Lab's Plugins (PR #117): the page loads the vault on the server, the browser only gets
 * names, masks and metadata, and every save or remove is a server action that calls Tank.
 * While base.vault is off, the tab shows only: "This vault is off. Turn it on in Tank." There is no switch here.
 * A saved key or token only ever shows as dots. "Sources" is not used here: on this page it only means the
 * references that come back with an answer.
 */

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";
import { shortDate } from "./model";
import {
  addBaseConnection,
  addBaseLink,
  removeBaseConnection,
  removeBaseFile,
  removeBaseLink,
  removeBaseSecret,
  saveBaseKey,
  uploadBaseFiles,
  type PluginResult,
} from "./plugin-actions";
import {
  formatBytes,
  NEED_FILE,
  NEED_KEY,
  NEED_LINK,
  NEED_MCP,
  VAULT_OFF_MESSAGE,
  type ConnectionMeta,
  type FileMeta,
  type LinkMeta,
  type PluginPanel,
  type SecretMeta,
} from "./plugin-panel";

export type PluginsTab = "keys" | "files" | "links" | "mcp";

const TABS: { id: PluginsTab; label: string }[] = [
  { id: "keys", label: "API keys" },
  { id: "files", label: "Files" },
  { id: "links", label: "Source links" },
  { id: "mcp", label: "MCP connections" },
];

type Run = (job: () => Promise<PluginResult>) => Promise<void>;
type TabProps = { plugins: PluginPanel; busy: boolean; run: Run; say: (text: string) => void };

function Pill({ label, on }: { label: string; on: boolean }) {
  return (
    <span className={on ? "bcp-pill on" : "bcp-pill"}>
      <i aria-hidden="true" />
      {label}
    </span>
  );
}

function PartHead({ title, copy }: { title: string; copy: string }) {
  return (
    <div className="bcp-part-head">
      <div className="bcp-part-title">
        <h3>{title}</h3>
      </div>
      <p>{copy}</p>
    </div>
  );
}

function savedLabel(at: string | null): string {
  const day = shortDate(at);
  return day ? `saved ${day}` : "saved";
}

function KeyRow({ row, busy, run, say }: { row: SecretMeta } & Omit<TabProps, "plugins">) {
  return (
    <form
      className="bcp-key-row"
      onSubmit={(event) => {
        event.preventDefault();
        const form = event.currentTarget;
        const value = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
        form.reset();
        if (!value.trim()) return say(NEED_KEY);
        void run(() => saveBaseKey(row.name, value, row.purpose));
      }}
    >
      <div className="bcp-key-what">
        <p className="bcp-row-title">{row.name}</p>
        {row.purpose ? <p className="bcp-row-copy">{row.purpose}</p> : null}
      </div>
      <div>
        <Pill label="Saved" on />
      </div>
      <p className="bcp-input bcp-saved" aria-label={`${row.name} key, saved`}>
        <span aria-hidden="true">{row.mask}</span> {savedLabel(row.savedAt)}
      </p>
      <div className="bcp-actions bcp-replace">
        <input
          className="bcp-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="Replace key"
          aria-label={`New ${row.name} key`}
          disabled={busy}
        />
        <button type="submit" className="bcp-secondary" disabled={busy}>
          Replace
        </button>
        <button type="button" className="bcp-text-btn" disabled={busy} onClick={() => void run(() => removeBaseSecret(row.name))}>
          Remove
        </button>
      </div>
    </form>
  );
}

function KeysTab({ plugins, busy, run, say }: TabProps) {
  function onAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = form.querySelector<HTMLInputElement>("input[name=key-name]")?.value.trim() ?? "";
    const purpose = form.querySelector<HTMLInputElement>("input[name=key-purpose]")?.value.trim() ?? "";
    const value = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    form.reset();
    if (!name || !value.trim()) return say(NEED_KEY);
    void run(() => saveBaseKey(name, value, purpose || "Base key"));
  }
  return (
    <>
      <PartHead
        title="API keys"
        copy="Keys for the services and suppliers Base checks with. A key is write-only: once saved it is never shown again."
      />
      <div className="bcp-list">
        {plugins.secrets.length ? (
          plugins.secrets.map((row) => <KeyRow key={row.name} row={row} busy={busy} run={run} say={say} />)
        ) : (
          <p className="bcp-fine">No keys stored.</p>
        )}
        <form className="bcp-key-row bcp-add" onSubmit={onAdd}>
          <div className="bcp-key-what">
            <p className="bcp-row-title">Add a key</p>
            <p className="bcp-row-copy">For a supplier or service that gives you an API key.</p>
          </div>
          <label className="bcp-field">
            <span>Name</span>
            <input name="key-name" className="bcp-input" type="text" placeholder="e.g. Licence list supplier" autoComplete="off" disabled={busy} />
          </label>
          <label className="bcp-field">
            <span>What it is for (optional)</span>
            <input name="key-purpose" className="bcp-input" type="text" placeholder="e.g. Monthly licence list" autoComplete="off" disabled={busy} />
          </label>
          <label className="bcp-field">
            <span>Key</span>
            <input className="bcp-input" type="password" placeholder="Paste key (hidden once saved)" autoComplete="off" spellCheck={false} disabled={busy} />
          </label>
          <div className="bcp-actions">
            <button type="submit" className="bcp-primary" disabled={busy}>
              Save key
            </button>
          </div>
        </form>
      </div>
      <p className="bcp-fine">Keys are stored in Tank, on the server, never in the app code.</p>
    </>
  );
}

function FileRowView({ file, busy, onRemove }: { file: FileMeta; busy: boolean; onRemove: () => void }) {
  const count = file.rows != null ? `${file.rows.toLocaleString("en-US")} rows` : file.pages != null ? `${file.pages} pages` : "—";
  return (
    <div className="bcp-tr bcp-file-cols" role="row">
      <span role="cell" className="bcp-name">{file.name}</span>
      <span role="cell" data-label="Type">{file.type.toUpperCase() || "—"}</span>
      <span role="cell" data-label="Size">{`${count} · ${formatBytes(file.size)}`}</span>
      <span role="cell" data-label="Uploaded">{shortDate(file.uploadedAt) ?? "—"}</span>
      <span role="cell" data-label="Status">
        <Pill label={file.status === "ready" ? "Ready" : file.status} on={file.status === "ready"} />
      </span>
      <span role="cell">
        <button type="button" className="bcp-text-btn" disabled={busy} onClick={onRemove}>
          Remove
        </button>
      </span>
    </div>
  );
}

function FilesTab({ plugins, busy, run, say }: TabProps) {
  const input = useRef<HTMLInputElement>(null);
  function send(list: FileList | File[] | null) {
    const files = list ? Array.from(list) : [];
    if (input.current) input.current.value = "";
    if (!files.length) return say(NEED_FILE);
    const body = new FormData();
    for (const file of files) body.append("file", file);
    void run(() => uploadBaseFiles(body));
  }
  return (
    <>
      <PartHead title="Files" copy="Upload a list of businesses for Base to fill in, such as a licence list. CSV, XLSX, and PDF only, up to 8 MB." />
      <div
        className="bcp-drop"
        onDragOver={(event: DragEvent) => event.preventDefault()}
        onDrop={(event: DragEvent) => {
          event.preventDefault();
          if (!busy) send(event.dataTransfer.files);
        }}
      >
        <p className="bcp-row-title">Drag CSV, XLSX or PDF files here</p>
        <p className="bcp-row-copy">or</p>
        <button type="button" className="bcp-primary" disabled={busy} onClick={() => input.current?.click()}>
          Choose files
        </button>
        <input
          ref={input}
          type="file"
          hidden
          multiple
          accept=".csv,.xlsx,.pdf,text/csv,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={() => send(input.current?.files ?? null)}
        />
      </div>
      {plugins.files.length ? (
        <div className="bcp-table" role="table" aria-label="Files">
          <div className="bcp-tr bcp-th bcp-file-cols" role="row">
            <span role="columnheader">File</span>
            <span role="columnheader">Type</span>
            <span role="columnheader">Rows / pages</span>
            <span role="columnheader">Uploaded</span>
            <span role="columnheader">Status</span>
            <span role="columnheader" className="bcp-sr">Action</span>
          </div>
          {plugins.files.map((file) => (
            <FileRowView key={file.id} file={file} busy={busy} onRemove={() => void run(() => removeBaseFile(file.id))} />
          ))}
        </div>
      ) : (
        <p className="bcp-fine">No files stored.</p>
      )}
    </>
  );
}

function LinkRowView({ link, busy, onRemove }: { link: LinkMeta; busy: boolean; onRemove: () => void }) {
  return (
    <div className="bcp-link-row">
      <div>
        <p className="bcp-row-title">{link.label}</p>
        <p className="bcp-env">{link.url}</p>
      </div>
      <div>
        <p className="bcp-env">{link.frequency === "daily" ? "Daily" : "Weekly"} · last checked</p>
        <p className="bcp-row-copy">{shortDate(link.lastChecked) ?? "Not checked yet"}</p>
      </div>
      <Pill label={link.status === "not checked" ? "Not checked" : link.status} on={link.status === "watching"} />
      <button type="button" className="bcp-text-btn" disabled={busy} onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

function LinksTab({ plugins, busy, run, say }: TabProps) {
  const [often, setOften] = useState<"daily" | "weekly">("daily");
  function onAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const url = form.querySelector<HTMLInputElement>("input[name=link]")?.value.trim() ?? "";
    const label = form.querySelector<HTMLInputElement>("input[name=label]")?.value.trim() ?? "";
    form.reset();
    const frequency = often;
    setOften("daily");
    if (!url) return say(NEED_LINK);
    void run(() => addBaseLink(url, label, frequency));
  }
  return (
    <>
      <PartHead title="Source links" copy="Lists Base can import from a link, such as a shared Drive file or a public licence list." />
      <form className="bcp-box bcp-link-form" onSubmit={onAdd}>
        <label className="bcp-field bcp-grow">
          <span>Link</span>
          <input name="link" className="bcp-input" type="url" placeholder="Paste a link, e.g. https://…" autoComplete="off" disabled={busy} />
        </label>
        <label className="bcp-field">
          <span>Label (optional)</span>
          <input name="label" className="bcp-input" type="text" placeholder="e.g. ATF licence list" autoComplete="off" disabled={busy} />
        </label>
        <div className="bcp-field">
          <span>How often to check</span>
          <div className="bcp-toggle" role="group" aria-label="How often to check">
            <button type="button" aria-pressed={often === "daily"} onClick={() => setOften("daily")}>
              Daily
            </button>
            <button type="button" aria-pressed={often === "weekly"} onClick={() => setOften("weekly")}>
              Weekly
            </button>
          </div>
        </div>
        <div className="bcp-actions">
          <button type="submit" className="bcp-primary" disabled={busy}>
            Add link
          </button>
        </div>
      </form>
      {plugins.links.length ? (
        <div className="bcp-list">
          {plugins.links.map((link) => (
            <LinkRowView key={link.id} link={link} busy={busy} onRemove={() => void run(() => removeBaseLink(link.id))} />
          ))}
        </div>
      ) : (
        <p className="bcp-fine">No links stored.</p>
      )}
    </>
  );
}

function ConnectionRowView({ item, busy, onRemove }: { item: ConnectionMeta; busy: boolean; onRemove: () => void }) {
  return (
    <div className="bcp-mcp-row">
      <div>
        <p className="bcp-row-title">{item.name}</p>
        <p className="bcp-env">{item.serverUrl}</p>
      </div>
      <p className="bcp-row-copy">
        {item.authType === "token" ? "Token" : "Sign in with the provider"}
        {item.hasToken ? <span className="bcp-dots"> ••••••••</span> : null}
      </p>
      <Pill label={item.status === "saved" ? "Saved" : item.status} on={item.status === "saved" || item.status === "connected"} />
      <div>
        <p className="bcp-env">Last used</p>
        <p className="bcp-row-copy">{shortDate(item.lastUsed) ?? "Never"}</p>
      </div>
      <button type="button" className="bcp-text-btn" disabled={busy} onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

function McpTab({ plugins, busy, run, say }: TabProps) {
  const [how, setHow] = useState<"provider" | "token">("token");
  function onAdd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = form.querySelector<HTMLInputElement>("input[name=connection-name]")?.value.trim() ?? "";
    const url = form.querySelector<HTMLInputElement>("input[name=server]")?.value.trim() ?? "";
    const token = how === "token" ? form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "" : "";
    form.reset();
    if (!name || !url) return say(NEED_MCP);
    void run(() => addBaseConnection(name, url, how, token));
  }
  return (
    <>
      <PartHead title="MCP connections" copy="Connections to outside tools and data servers that Base can read from." />
      {plugins.connections.length ? (
        <div className="bcp-list">
          {plugins.connections.map((item) => (
            <ConnectionRowView key={item.id} item={item} busy={busy} onRemove={() => void run(() => removeBaseConnection(item.id))} />
          ))}
        </div>
      ) : (
        <p className="bcp-fine">No connections stored.</p>
      )}
      <form className="bcp-box bcp-mcp-form" onSubmit={onAdd}>
        <p className="bcp-row-title">Add connection</p>
        <div className="bcp-mcp-grid">
          <label className="bcp-field">
            <span>Name</span>
            <input name="connection-name" className="bcp-input" type="text" placeholder="e.g. Customer records" autoComplete="off" disabled={busy} />
          </label>
          <label className="bcp-field">
            <span>Server URL</span>
            <input name="server" className="bcp-input" type="url" placeholder="https://…" autoComplete="off" disabled={busy} />
          </label>
          <div className="bcp-field">
            <span>How it signs in</span>
            <div className="bcp-toggle" role="group" aria-label="How it signs in">
              <button type="button" aria-pressed={how === "provider"} onClick={() => setHow("provider")}>
                Sign in with the provider
              </button>
              <button type="button" aria-pressed={how === "token"} onClick={() => setHow("token")}>
                Token
              </button>
            </div>
          </div>
          <div className="bcp-token-row">
            <label className="bcp-field bcp-grow">
              <span>Token</span>
              <input
                className="bcp-input"
                type="password"
                placeholder="Paste token (hidden once saved)"
                autoComplete="off"
                spellCheck={false}
                disabled={busy || how !== "token"}
              />
            </label>
            <div className="bcp-actions">
              <button type="submit" className="bcp-primary" disabled={busy}>
                Add connection
              </button>
            </div>
          </div>
        </div>
        <p className="bcp-fine">A saved token shows only as dots and is never shown again.</p>
      </form>
    </>
  );
}

function TabBody({ id, ...props }: { id: PluginsTab } & TabProps) {
  if (id === "keys") return <KeysTab {...props} />;
  if (id === "files") return <FilesTab {...props} />;
  if (id === "links") return <LinksTab {...props} />;
  return <McpTab {...props} />;
}

export function PluginsDialog({
  tab,
  plugins,
  onTab,
  onClose,
}: {
  tab: PluginsTab;
  plugins: PluginPanel;
  onTab: (tab: PluginsTab) => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const close = useRef<HTMLButtonElement>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    close.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const run: Run = async (job) => {
    if (!plugins.enabled || busy) return;
    setBusy(true);
    try {
      const result = await job();
      setNote(result.message);
      if (result.ok) router.refresh();
    } finally {
      setBusy(false);
    }
  };

  function pick(next: PluginsTab) {
    setNote("");
    onTab(next);
  }

  return (
    <div className="bcp-modal">
      <button type="button" className="bcp-modal-dim" aria-label="Close Plugins" tabIndex={-1} onClick={onClose} />
      <div className="bcp-popup" role="dialog" aria-modal="true" aria-labelledby="bcp-plugins-title">
        <span className="bcp-handle" aria-hidden="true" />
        <div className="bcp-popup-head">
          <div>
            <h2 id="bcp-plugins-title">Plugins</h2>
            <p>What Base connects to. Add a key, upload a list, paste a link, or connect an MCP server.</p>
          </div>
          <button ref={close} type="button" className="bcp-text-btn" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="bcp-tabs" role="tablist" aria-label="Plugins">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={`bcp-tab-${item.id}`}
              aria-selected={tab === item.id}
              aria-controls={`bcp-panel-${item.id}`}
              className={tab === item.id ? "bcp-tab active" : "bcp-tab"}
              onClick={() => pick(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {TABS.map((item) => (
          <section
            key={item.id}
            className={tab === item.id ? "bcp-group open" : "bcp-group"}
            id={`bcp-panel-${item.id}`}
            role="tabpanel"
            aria-labelledby={`bcp-tab-${item.id}`}
          >
            <button type="button" className="bcp-group-head" aria-expanded={tab === item.id} onClick={() => pick(item.id)}>
              <span>{item.label}</span>
              <span aria-hidden="true">{tab === item.id ? "−" : "+"}</span>
            </button>
            {tab === item.id ? (
              <div className="bcp-group-body">
                {!plugins.enabled ? (
                  <p className="bcp-vault-off" role="status">
                    {plugins.message || VAULT_OFF_MESSAGE}
                  </p>
                ) : (
                  <>
                    <TabBody id={item.id} plugins={plugins} busy={busy} run={run} say={setNote} />
                    <p className="bcp-notice" role="status" aria-live="polite">
                      {note}
                    </p>
                  </>
                )}
              </div>
            ) : null}
          </section>
        ))}
      </div>
    </div>
  );
}
