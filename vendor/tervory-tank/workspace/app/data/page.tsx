"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import "./data.css";

type Field = { value: unknown; source: string; confidence: number };
type Rec = {
  id: string; task_id: string | null; batch: string | null; raw: Record<string, unknown>;
  profile: Record<string, Field>; passes: Record<string, Record<string, unknown>>;
  confidence: number; status: "queued" | "running" | "done" | "failed"; error: string | null;
};
type Batch = {
  batch: string; started: string; total: number; done: number; failed: number; pending: number;
  ready: number; aware: number; review: number; requeue: number; with_site: number; with_email: number;
};

const SAMPLE = `name,address,city,state,zip,phone,website
Example Domain,123 Main St,Boston,MA,02110,,example.com`;

function v(f?: Field): string {
  if (!f || f.value == null || f.value === "") return "";
  if (Array.isArray(f.value)) return f.value.map((x) => (typeof x === "object" && x ? JSON.stringify(x) : String(x))).join(", ");
  if (typeof f.value === "object") {
    const vals = Object.values(f.value as Record<string, unknown>);
    return vals.every((x) => x == null || typeof x !== "object") ? vals.filter((x) => x != null && x !== "").map(String).join(", ") : JSON.stringify(f.value);
  }
  return String(f.value);
}

const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : "0%");

// Base: upload or paste a batch of records, run all seven passes, watch the profiles fill in, export the result.
export default function DataPage() {
  const [batch, setBatch] = useState("");
  const [csv, setCsv] = useState(SAMPLE);
  const [file, setFile] = useState<File | null>(null);
  const [link, setLink] = useState("");
  const preview = false;
  const [engine, setEngine] = useState("");
  const [splitByState, setSplitByState] = useState(true);
  const [engines, setEngines] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [records, setRecords] = useState<Rec[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshBatches = useCallback(async () => {
    const res = await fetch("/api/base?batches=1", { cache: "no-store" });
    if (res.ok) {
      const list = (await res.json()) as Batch[];
      setBatches(list);
      setSelected((s) => s || list[0]?.batch || "");
    }
  }, []);
  const refreshRecords = useCallback(async (b: string) => {
    if (!b) { setRecords([]); return; }
    const res = await fetch(`/api/base?batch=${encodeURIComponent(b)}&limit=200`, { cache: "no-store" });
    if (res.ok) setRecords((await res.json()) as Rec[]);
  }, []);

  useEffect(() => { refreshBatches(); }, [refreshBatches]);
  useEffect(() => { refreshRecords(selected); }, [selected, refreshRecords]);
  useEffect(() => {
    fetch("/api/base?engines=1", { cache: "no-store" }).then(async (r) => { if (r.ok) setEngines((await r.json()) as string[]); }).catch(() => {});
  }, []);
  const current = batches.find((b) => b.batch === selected);
  useEffect(() => {
    if (!current || current.pending === 0) return;
    const id = setInterval(() => { refreshBatches(); refreshRecords(selected); }, 4000);
    return () => clearInterval(id);
  }, [current, selected, refreshBatches, refreshRecords]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!batch.trim()) {
      setError("Add a batch name.");
      return;
    }
    if (!file && !link.trim() && !csv.trim()) {
      setError("Add a file, a link, or paste the rows.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      let res: Response;
      if (file) {
        const fd = new FormData();
        fd.set("batch", batch); fd.set("preview", String(preview)); fd.set("engine", engine); fd.set("file", file);
        fd.set("split_by_state", String(splitByState));
        res = await fetch("/api/base", { method: "POST", body: fd });
      } else {
        res = await fetch("/api/base", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ batch, csv: link.trim() ? "" : csv, url: link.trim() || undefined, preview, engine: engine || null, split_by_state: splitByState }),
        });
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
      const data2 = data as { labels?: string[] };
      setSelected(data2.labels?.[0] ?? batch);
      setBatch("");
      setFile(null);
      setLink("");
      if (fileRef.current) fileRef.current.value = "";
      await refreshBatches();
      await refreshRecords(data2.labels?.[0] ?? batch);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="data-page">
      <h1>Data</h1>
      <p style={{ color: "var(--muted)" }}>
        Upload a CSV (or paste one; header row first: name, address, city, state, zip, phone, website — any you have). Base runs
        all seven passes: clean, match, find the site, scrape it, extract facts, contacts, socials, business data, the industry
        module, then verification and a 0–100 score. Pick an engine to feed it that industry&apos;s own list — the FFL engine
        reads the ATF licensee export as-is (comma or tab delimited). Up to 100,000 rows per upload; with &quot;one batch per
        state&quot; the upload is split by state and the states run one after another, smallest first, so finished states are
        auditable while the big ones are still going. A restart resumes where it stopped.
      </p>
      <form onSubmit={submit} className="task-form">
        <label htmlFor="batch-name">Batch name</label>
        <input id="batch-name" value={batch} onChange={(e) => setBatch(e.target.value)} placeholder="Batch name, e.g. ATF full list Oct 2026" maxLength={120} disabled={busy} />
        <div className="task-form-row">
          <input ref={fileRef} type="file" accept=".csv,.txt,.tsv,text/csv,text/plain" disabled={busy}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <span style={{ color: "var(--muted)", fontSize: 13 }}>{file ? `${file.name} · ${(file.size / 1024).toFixed(0)} KB` : "or paste below"}</span>
        </div>
        {!file && (
          <input value={link} onChange={(e) => setLink(e.target.value)} placeholder="…or a link to the file (Drive: share as “Anyone with the link”)" disabled={busy} />
        )}
        {!file && !link.trim() && (
          <textarea value={csv} onChange={(e) => setCsv(e.target.value)} rows={6} disabled={busy} style={{ fontFamily: "ui-monospace, monospace", fontSize: 13 }} />
        )}
        <div className="task-form-row">
          <select value={engine} onChange={(e) => setEngine(e.target.value)} disabled={busy} aria-label="Industry engine">
            <option value="">No engine (generic records)</option>
            {engines.map((e) => <option key={e} value={e}>{e.toUpperCase()} engine</option>)}
          </select>
          <label style={{ color: "var(--muted)", fontSize: 13 }}>
            <input type="checkbox" checked={splitByState} onChange={(e) => setSplitByState(e.target.checked)} /> one batch per state, smallest state first
          </label>
          <button type="submit" disabled={busy}>{busy ? "Importing… (a big file takes a couple of minutes)" : "Run Base"}</button>
        </div>
        {error && <p className="err">{error}</p>}
      </form>

      {batches.length > 0 && (
        <div className="task-form-row" style={{ marginBottom: 12 }}>
          <select value={selected} onChange={(e) => setSelected(e.target.value)} aria-label="Batch">
            {batches.map((b) => <option key={b.batch} value={b.batch}>{b.batch} · {b.total.toLocaleString()} records</option>)}
          </select>
          {current && current.done > 0 && (
            <a href={`/api/base/export?batch=${encodeURIComponent(selected)}`} style={{ fontSize: 14 }}>Export CSV</a>
          )}
          {current && current.done > 0 && current.pending === 0 && current.with_site < current.done && (
            <button type="button" className="sm ghost" onClick={async () => {
              const r = await fetch("/api/base?action=rerun", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ batch: selected, flag: "no_website" }) });
              if (r.ok) { await refreshBatches(); await refreshRecords(selected); }
            }}>Re-run {(current.done - current.with_site).toLocaleString()} without a website</button>
          )}
          {current && current.failed > 0 && (
            <button type="button" style={{ padding: "6px 10px", fontSize: 13 }} onClick={async () => {
              const r = await fetch("/api/base?action=retry", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ batch: selected }) });
              if (r.ok) { await refreshBatches(); await refreshRecords(selected); }
            }}>Retry {current.failed} failed</button>
          )}
        </div>
      )}

      {current && (
        <div className="tiles">
          <div className="tile"><b>{current.total.toLocaleString()}</b><span>records</span></div>
          <div className="tile"><b>{current.done.toLocaleString()}</b><span>enriched{current.pending ? ` · ${current.pending.toLocaleString()} to go` : ""}</span></div>
          <div className="tile"><b>{pct(current.with_site, current.done)}</b><span>with a website</span></div>
          <div className="tile"><b>{pct(current.with_email, current.done)}</b><span>with an email</span></div>
          <div className="tile"><b>{current.ready.toLocaleString()}</b><span>ready (80+)</span></div>
          <div className="tile"><b>{current.aware.toLocaleString()}</b><span>use with awareness (60–79)</span></div>
          <div className="tile"><b>{(current.review + current.requeue).toLocaleString()}</b><span>need review (&lt;60)</span></div>
          <div className="tile"><b>{current.failed.toLocaleString()}</b><span>failed</span></div>
        </div>
      )}

      <div className="data-scroll">
      <table>
        <thead>
          <tr><th>Name</th><th>City</th><th>Website</th><th>Emails</th><th>Decision maker</th><th>Segment</th><th>Tier</th><th>Conf.</th><th>Status</th></tr>
        </thead>
        <tbody>
          {records.map((r) => (
            <Fragment key={r.id}>
              <tr onClick={() => setOpen(open === r.id ? null : r.id)} style={{ cursor: "pointer" }}>
                <td>{v(r.profile.name) || String(r.raw.name ?? r.raw.business_name ?? r.raw.license_name ?? "")}</td>
                <td>{v(r.profile.city)}{v(r.profile.state) ? `, ${v(r.profile.state)}` : ""}</td>
                <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v(r.profile.website)}</td>
                <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v(r.profile.emails)}</td>
                <td style={{ maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v(r.profile.decision_maker)}</td>
                <td>{v(r.profile.ffl_segment) || v(r.profile.business_model)}</td>
                <td>{v(r.profile.tier)}</td>
                <td>{r.status === "done" ? r.confidence : ""}</td>
                <td><span className={`state ${r.status}`}>{r.status}</span></td>
              </tr>
              {open === r.id && (
                <tr>
                  <td colSpan={9}>
                    {r.error && <p className="err">{r.error}</p>}
                    <table>
                      <thead><tr><th>Field</th><th>Value</th><th>Source</th><th>Confidence</th></tr></thead>
                      <tbody>
                        {Object.entries(r.profile).map(([k, f]) => (
                          <tr key={k}><td>{k}</td><td style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{v(f)}</td><td>{f.source}</td><td>{f.confidence}</td></tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="meta" style={{ wordBreak: "break-all" }}>passes: {JSON.stringify(r.passes)}</div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {records.length === 0 && <tr><td colSpan={9} style={{ color: "var(--muted)" }}>No records in this batch yet.</td></tr>}
        </tbody>
      </table>
      </div>
      {current && current.total > records.length && (
        <p style={{ color: "var(--muted)", fontSize: 13 }}>Showing the newest {records.length} of {current.total.toLocaleString()}. Export CSV for the full batch.</p>
      )}
    </div>
  );
}
