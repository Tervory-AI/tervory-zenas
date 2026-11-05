"use client";

import { useEffect, useState } from "react";
import { batchLabel } from "./model";
import { countLabel, pageFields, fieldLabels, stateLabel } from "./display";
import { Evidence } from "./reference-workflow";
import type { VerifiedRow } from "./product-types";

export function BatchWorkflow({ signedIn, selected }: { signedIn: boolean; selected: string | null }) {
  const [result, setResult] = useState<{ total: number; rows: VerifiedRow[] } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setResult(null); setError("");
    if (!selected || !signedIn) return;
    let stopped = false;
    const query = new URLSearchParams({ batch: selected, include_collectors: "true" });
    async function poll() {
      try {
        const response = await fetch(`/base/product/records?${query}`, { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) throw new Error("The results could not be loaded. They will be checked again shortly.");
        if (!stopped) { setResult(data); setError(""); }
      } catch { if (!stopped) setError("The results could not be loaded. They will be checked again shortly."); }
    }
    void poll();
    const timer = setInterval(() => { void poll(); }, 15000);
    return () => { stopped = true; clearInterval(timer); };
  }, [selected, signedIn]);

  if (!selected || !signedIn) return null;
  return <section className="bcp-workflow bcp-batch-result" aria-label="Batch results" aria-live="polite">
    <h3>{batchLabel(selected)}</h3>
    {error && <p role="alert">{error}</p>}
    {!result ? <p>Loading results…</p> : <>
      <p>{countLabel(result.total, "attempted record")}. Sources, check dates and missing-field reasons update automatically.</p>
      <a className="bcp-secondary" href={`/base/download?batch=${encodeURIComponent(selected)}`}>Download batch fields and sources</a>
      <p className="bcp-copy">Showing the first {countLabel(result.rows.length, "record")}. The download includes all matching records, with unverified values left blank and their reasons included.</p>
      {result.rows.map(row => <article key={row.record_id}><h4>{row.name} · {stateLabel(row.state)}</h4><dl>{pageFields(row.fields).map(([name, value]) => <div key={name}><dt>{fieldLabels[name]}</dt><dd><Evidence field={value} /></dd></div>)}</dl></article>)}
    </>}
  </section>;
}
