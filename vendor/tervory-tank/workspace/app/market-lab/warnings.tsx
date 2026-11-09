"use client";
import { fetchWithDeadline as fetch } from "../../lib/request";

import { useRef, useState } from "react";
import type { MarketWarnings } from "@/lib/tank";

export function Warnings({ data, failed = false }: { data: MarketWarnings | null; failed?: boolean }) {
  const [choices, setChoices] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<Record<string, string>>({});
  const inFlight = useRef(false);
  async function react(id: string, useful: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(id);
    try {
      const response = await fetch("/market-lab/warnings/feedback", { method: "POST", signal: AbortSignal.timeout(30000), headers: { "content-type": "application/json" }, body: JSON.stringify({ warning_id: id, event_id: crypto.randomUUID(), useful }) });
      if (!response.ok) throw new Error("save failed");
      await response.json();
      setChoices(previous => ({ ...previous, [id]: useful }));
      setMessage(previous => ({ ...previous, [id]: "Your choice is saved." }));
    } catch {
      setMessage(previous => ({ ...previous, [id]: "Your choice could not be saved. Please try again." }));
    } finally { inFlight.current = false; setBusy(null); }
  }
  return <section id="warnings" className="ml-warnings" aria-label="Warnings">
    <h2>Warnings</h2>
    <p>Changes that may affect your market, based on what followed similar changes before.</p>
    {failed || !data ? <p role="alert">Warnings could not be read. Please try again shortly.</p> : <>
      {data.problem ? <p role="status">{data.problem}</p> : null}
      {data.warnings.length ? data.warnings.map(warning => {
        const choice = choices[warning.id] ?? warning.feedback;
        return <article className="ml-warning" key={warning.id}>
          <h3>{warning.title}</h3>
          {warning.facts.map(fact => <p key={fact.text}><a href={fact.href}>{fact.text}</a></p>)}
          <p className="ml-warning-status">{warning.resolution_href ? <a href={warning.resolution_href}>{warning.status}</a> : warning.status}</p>
          <div className="ml-warning-actions" aria-label="Was this warning useful?">
            <button type="button" aria-pressed={choice === true} disabled={busy === warning.id} onClick={() => react(warning.id, true)}>Useful</button>
            <button type="button" aria-pressed={choice === false} disabled={busy === warning.id} onClick={() => react(warning.id, false)}>Not useful</button>
          </div>
          <p role="status">{message[warning.id] ?? (choice === null ? "" : "Your choice is saved.")}</p>
        </article>;
      }) : <><p>No warnings yet.</p><p>Refresh feeds to collect observations; warnings appear after enough history is saved.</p></>}
      {data.history.length ? <details className="ml-warning-history" open={!data.warnings.length}><summary>History behind the warnings</summary>
        {data.history.map(pair => <div key={pair.href}><h3>{pair.name}</h3><p><a href={pair.href}>{pair.text}</a></p></div>)}
      </details> : null}
    </>}
  </section>;
}
