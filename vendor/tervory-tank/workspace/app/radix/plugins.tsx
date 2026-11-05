"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { PLUGIN_TABS, offPanel, type PluginPanel } from "./plugins-model";
import {
  readRadixPlugins, saveRadixKey, removeRadixSecret, removeRadixFile,
  addRadixLink, removeRadixLink, addRadixConnection, removeRadixConnection,
  type PluginResult,
} from "./plugin-actions";

export function Plugins({ open, onEnabled }: { open: boolean; onEnabled: (value: boolean) => void }) {
  const [panel, setPanel] = useState<PluginPanel>(offPanel());
  const [selected, setSelected] = useState(0);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const revision = useRef(0);
  const id = useId();

  useEffect(() => {
    const current = ++revision.current;
    setPanel(offPanel("Loading Plugins…"));
    onEnabled(false);
    setNote("");
    if (!open) return;
    void readRadixPlugins().then((next) => {
      if (current !== revision.current) return;
      setPanel(next); onEnabled(next.enabled);
    }).catch(() => {
      if (current === revision.current) setPanel(offPanel("Plugins could not be loaded from Tank. No vault data is shown."));
    });
    return () => { revision.current++; };
  }, [open, onEnabled]);

  async function run(action: () => Promise<PluginResult>) {
    if (!panel.enabled || busy) return;
    const current = revision.current;
    setBusy(true); setNote("");
    try {
      const result = await action();
      // Reload even after a refused write: Tank may have turned the vault off.
      const next = await readRadixPlugins();
      if (current !== revision.current) return;
      setPanel(next); onEnabled(next.enabled); setNote(result.message);
    } catch {
      if (current === revision.current) {
        setPanel(offPanel("Plugins could not be loaded from Tank. No vault data is shown."));
        onEnabled(false); setNote("The change could not be confirmed. Reopen Plugins to check.");
      }
    } finally { setBusy(false); }
  }

  function submit(event: FormEvent<HTMLFormElement>, kind: number) {
    event.preventDefault();
    if (!panel.enabled || busy) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const value = (name: string) => String(data.get(name) ?? "");
    // Clear password inputs immediately; never keep secrets in component state.
    form.reset();
    if (kind === 0) void run(() => saveRadixKey(value("name"), value("value"), value("purpose")));
    if (kind === 1) void run(async () => {
      for (const file of data.getAll("file")) {
        if (!(file instanceof File) || file.size > 8 * 1024 * 1024) return { ok: false, message: "Each file must be 8 MB or smaller." };
      }
      for (const file of data.getAll("file")) {
        const body = new FormData(); body.append("file", file);
        const response = await fetch("/radix/plugins/files", { method: "POST", body });
        const result = await response.json() as PluginResult;
        if (!response.ok || !result.ok) return { ok: false, message: result.message || "The file was not saved." };
      }
      return { ok: true, message: "Files saved." };
    });
    if (kind === 2) void run(() => addRadixLink(value("url"), value("label"), value("frequency")));
    if (kind === 3) void run(() => addRadixConnection(value("name"), value("url"), "token", value("token")));
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % 4;
    else if (event.key === "ArrowLeft") next = (index + 3) % 4;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = 3;
    else return;
    event.preventDefault(); setSelected(next); setNote(""); tabs.current[next]?.focus();
  }

  function content(index: number) {
    if (!panel.enabled) return <p className="rx-vault-off">{panel.message}</p>;
    return <div className="rx-plugin-content">
      {index === 0 ? <>
        <p>Save or replace a key for Radix. A saved key is hidden. Saving it does not start a scan.</p>
        {panel.secrets.filter((row) => !row.name.startsWith("mcp:")).map((row) => <article className="rx-card" key={row.name}>
          <h3>{row.name}</h3><p>{row.purpose}</p>
          <p aria-label="Saved key, hidden">••••••••</p>
          <button type="button" className="rx-text-button" disabled={busy} onClick={() => void run(() => removeRadixSecret(row.name))}>Remove {row.name}</button>
        </article>)}
        {!panel.secrets.some((row) => !row.name.startsWith("mcp:")) ? <p>No keys saved.</p> : null}
        <form onSubmit={(event) => submit(event, 0)} className="rx-plugin-form">
          <label>Key name<input name="name" required maxLength={80} autoComplete="off" disabled={busy} /></label>
          <label>What it is for<input name="purpose" maxLength={240} disabled={busy} /></label>
          <label>Key<input name="value" type="password" required autoComplete="new-password" disabled={busy} /></label>
          <p className="rx-muted">Use an existing name to replace its key.</p>
          <button type="submit" className="rx-primary" disabled={busy}>Save key</button>
        </form>
      </> : index === 1 ? <>
        <p>Store review documents in Radix. CSV, XLSX and PDF, up to 8 MB each. Uploading does not run a code scan.</p>
        {panel.files.map((row) => <article className="rx-card" key={row.id}>
          <h3>{row.name}</h3><p>{row.status} · {Math.ceil(row.size / 1024)} KB</p>
          <button type="button" className="rx-text-button" disabled={busy} onClick={() => void run(() => removeRadixFile(row.id))}>Remove {row.name}</button>
        </article>)}
        {!panel.files.length ? <p>No files saved.</p> : null}
        <form onSubmit={(event) => submit(event, 1)} className="rx-plugin-form">
          <label>Choose files<input name="file" type="file" required multiple accept=".csv,.xlsx,.pdf" disabled={busy} /></label>
          <button type="submit" className="rx-primary" disabled={busy}>Upload files</button>
        </form>
      </> : index === 2 ? <>
        <p>Save reference links. Links are stored only. No page is fetched and no hostname is approved.</p>
        {panel.links.map((row) => <article className="rx-card" key={row.id}>
          <h3>{row.label}</h3><p>{row.url}</p><p>{row.status} · {row.frequency}</p>
          <button type="button" className="rx-text-button" disabled={busy} onClick={() => void run(() => removeRadixLink(row.id))}>Remove {row.label}</button>
        </article>)}
        {!panel.links.length ? <p>No links saved.</p> : null}
        <form onSubmit={(event) => submit(event, 2)} className="rx-plugin-form">
          <label>Link<input name="url" type="url" required disabled={busy} /></label>
          <label>Label<input name="label" maxLength={80} disabled={busy} /></label>
          <label>Check frequency<select name="frequency" defaultValue="weekly" disabled={busy}><option value="weekly">Weekly</option><option value="daily">Daily</option></select></label>
          <p className="rx-muted">The link is saved only. Checks stay off.</p>
          <button type="submit" className="rx-primary" disabled={busy}>Save link</button>
        </form>
      </> : <>
        <p>Save a connection. No server is contacted. Tokens stay hidden after saving.</p>
        {panel.connections.map((row) => <article className="rx-card" key={row.id}>
          <h3>{row.name}</h3><p>{row.serverUrl}</p><p>{row.status}{row.hasToken ? " · ••••••••" : ""}</p>
          <button type="button" className="rx-text-button" disabled={busy} onClick={() => void run(() => removeRadixConnection(row.id))}>Remove {row.name}</button>
        </article>)}
        {!panel.connections.length ? <p>No connections saved.</p> : null}
        <form onSubmit={(event) => submit(event, 3)} className="rx-plugin-form">
          <label>Connection name<input name="name" required maxLength={80} disabled={busy} /></label>
          <label>Server URL<input name="url" type="url" required disabled={busy} /></label>
          <label>Token<input name="token" type="password" required autoComplete="new-password" disabled={busy} /></label>
          <button type="submit" className="rx-primary" disabled={busy}>Save connection</button>
        </form>
      </>}
    </div>;
  }
  return <>
    {note ? <p className="rx-plugin-note" role="status">{note}</p> : null}
    {busy ? <p role="status">Saving…</p> : null}
    <div className="rx-plugin-desktop">
      <div className="rx-plugin-tabs" role="tablist" aria-label="Plugin types">
        {PLUGIN_TABS.map((label, index) => <button key={label} type="button"
          ref={(node) => { tabs.current[index] = node; }} id={id + "-tab-" + index}
          role="tab" aria-selected={selected === index} aria-controls={id + "-panel-" + index}
          tabIndex={selected === index ? 0 : -1} onClick={() => { setSelected(index); setNote(""); }}
          onKeyDown={(event) => onTabKey(event, index)}>{label}</button>)}
      </div>
      {PLUGIN_TABS.map((label, index) => <section key={label} role="tabpanel"
        id={id + "-panel-" + index} aria-labelledby={id + "-tab-" + index} hidden={selected !== index} tabIndex={0}>
        <h3 className="rx-plugin-heading">{label}</h3>{selected === index ? content(index) : null}
      </section>)}
    </div>
    <div className="rx-plugin-mobile">
      {PLUGIN_TABS.map((label, index) => <section key={label}>
        <button className="rx-accordion" type="button" aria-expanded={selected === index}
          aria-controls={id + "-mobile-" + index} onClick={() => { setSelected(index); setNote(""); }}>{label}</button>
        <div id={id + "-mobile-" + index} hidden={selected !== index}>{selected === index ? content(index) : null}</div>
      </section>)}
    </div>
  </>;
}
