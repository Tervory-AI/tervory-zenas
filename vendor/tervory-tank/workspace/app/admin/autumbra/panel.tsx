"use client";

import Link from "next/link";
import { useActionState, type ReactNode } from "react";
import type { GhostIncident, GhostPanel } from "@/lib/tank";
import type { GhostResult } from "./actions";
import type { AutumbraThreatReceipt } from "./watch-fixtures";
import "./autumbra.css";

type Action = (previous: GhostResult, form: FormData) => Promise<GhostResult>;
const targets = ["workspace", "site", "za", "tank", "base", "tiger", "market_lab", "rig", "tisan", "radix", "zenas", "ghost"];
const words = (value: string) => value.replaceAll("_", " ");
const labelTarget = (value: string) => (value === "ghost" ? "Autumbra Watch" : words(value));

function actionLabel(action: AutumbraThreatReceipt["action"]) {
  if (action === "propose_shield") return "Propose shield";
  if (action === "open_match") return "Open match";
  if (action === "write_brief") return "Write brief";
  return "Handoff";
}

function statusLabel(status: AutumbraThreatReceipt["status"]) {
  if (status === "propose_only") return "Propose only — burn on fail";
  if (status === "burned") return "Burned — no action taken";
  return "Awaiting founder";
}

function Incident({ item, resolve }: { item: GhostIncident; resolve: Action }) {
  const [result, action, pending] = useActionState(resolve, null);
  return <article className="ghost-issue">
    <div className="ghost-issue-head"><h2>{words(item.target)} · {words(item.signal)}</h2><span className="ghost-state">{words(item.state)}</span></div>
    <p>{item.summary ?? "Waiting for evidence."}</p>
    <p className="ghost-meta">{new Date(item.created_at).toLocaleString("en-US", { timeZone: "UTC" })} UTC · ${item.cost_usd.toFixed(6)} · {item.steps.length} recorded steps</p>
    {!!item.previous?.length && <p>{item.previous.length} similar solved {item.previous.length === 1 ? "issue found" : "issues found"} in Autumbra’s memory.</p>}
    {item.brief && <details><summary>Worker brief · needs approval</summary>
      <h3>{item.brief.title}</h3><p>Cause: {item.brief.cause}</p>
      <ol>{item.brief.instructions.map((text) => <li key={text}>{text}</li>)}</ol>
      <p>Choose the allowed files and approve the brief before handing it to a worker.</p>
    </details>}
    <details><summary>Investigation receipts</summary><ol className="ghost-receipts">
      {item.steps.map((step) => <li key={step.ledger_id}><strong>{words(step.step)}</strong><code>{step.ledger_id}</code><span>${step.cost_usd.toFixed(6)}</span></li>)}
    </ol></details>
    {["needs_review", "not_reproduced", "blocked"].includes(item.state) && <details><summary>Confirm a completed fix</summary>
      <form action={action} className="ghost-resolve">
        <input type="hidden" name="id" value={item.id} />
        <label>Confirmed cause<select name="cause" required><option value="application_bug">Application bug</option><option value="crashed_room">Crashed room</option><option value="failed_deploy">Failed deploy</option><option value="dns_configuration">DNS configuration</option><option value="certificate_expired">Expired certificate</option><option value="missing_headers">Missing security headers</option><option value="dependency_failure">Dependency failure</option></select></label>
        <label>Completed fix<select name="fix" required><option value="reviewed_pr_deployed">Reviewed fix deployed</option><option value="room_restarted">Room restarted</option><option value="deploy_rerun">Deploy rerun</option><option value="dns_corrected">DNS corrected</option><option value="certificate_renewed">Certificate renewed</option><option value="headers_corrected">Headers corrected</option></select></label>
        <button disabled={pending} type="submit">{pending ? "Checking…" : "Verify and remember"}</button>
        {result && <p role="status">{result.message}</p>}
      </form>
    </details>}
  </article>;
}

function ThreatReceipts({ items }: { items: AutumbraThreatReceipt[] }) {
  return <section className="autumbra-threats" aria-label="ThreatPayload and advisory receipts">
    <div className="ghost-section-title autumbra-threats-title">
      <h2>ThreatPayload / advisory receipts</h2>
      <span>{items.length} recorded</span>
    </div>
    <p className="autumbra-threats-lead">
      Bounded propose-only receipts that crossed the air-gap schema (<code>ghost.threat.v1</code>).
      Autumbra Watch never auto-enforces; burn-on-fail means nothing was changed.
    </p>
    {items.length ? items.map((item) => (
      <article key={item.id} className="autumbra-threat">
        <div className="autumbra-threat-head">
          <h3>{actionLabel(item.action)} · {words(item.target)}</h3>
          <span className="autumbra-threat-status">{statusLabel(item.status)}</span>
        </div>
        <p>{item.summary}</p>
        <dl className="autumbra-threat-meta">
          <div><dt>Schema</dt><dd><code>{item.schema_version}</code></dd></div>
          <div><dt>Severity</dt><dd>{words(item.severity)}</dd></div>
          <div><dt>Flagged</dt><dd>{new Date(item.flagged_at).toLocaleString("en-US", { timeZone: "UTC" })} UTC</dd></div>
          {typeof item.confidence === "number" && <div><dt>Confidence</dt><dd>{item.confidence.toFixed(2)}</dd></div>}
          {item.match_id && <div><dt>Match</dt><dd><code>{item.match_id}</code></dd></div>}
          {!!item.cve_ids?.length && <div><dt>CVE</dt><dd>{item.cve_ids.join(", ")}</dd></div>}
        </dl>
      </article>
    )) : <p className="ghost-empty">No ThreatPayload receipts yet. Advisory matches appear here after propose-only triage.</p>}
  </section>;
}

export function AutumbraConsole({
  data,
  error,
  look,
  resolve,
  tile,
  threatReceipts = [],
}: {
  data: GhostPanel | null;
  error?: string;
  look: Action;
  resolve: Action;
  tile?: ReactNode;
  threatReceipts?: AutumbraThreatReceipt[];
}) {
  const [result, action, pending] = useActionState(look, null);
  return <main className="ghost-panel">
    <header className="ghost-head"><div><img src="/brand/tervory-logo.png" width="160" height="36" alt="Tervory" /><p className="ghost-eyebrow">Founder console / Autumbra Watch</p><h1>Find the problem.<br />Keep the proof.</h1><p>Point Autumbra Watch at an issue. Follow its checks, review the proposed fix, and keep what worked.</p></div><Link href="/admin">Back to console</Link></header>
    <section className="ghost-watch" aria-label="Monitoring status"><div><h2>On watch</h2><p>{data?.monitor.running ? "Public site and room checks run every five minutes." : "Monitoring has not reported a completed check cycle."}</p></div><span>{data ? `${data.monitor.unavailable_checks} unavailable checks` : "Room unavailable"}</span></section>
    {(error || data?.monitor.unavailable_checks) ? <p role="alert" className="ghost-error">{error ?? "Some checks could not finish. An unavailable check is not a healthy result."}</p> : null}
    {tile}
    <form action={action} className="ghost-look"><h2>Take a look</h2><label>Where<select name="target">{targets.map(target => <option key={target} value={target}>{labelTarget(target)}</option>)}</select></label><label>What to check<select name="signal"><option value="health">Availability</option><option value="slow">Response time</option><option value="dns">DNS</option><option value="certificate">Certificate expiry</option><option value="headers">Security headers</option></select></label><button type="submit" disabled={pending}>{pending ? "Looking…" : "Look"}</button>{result && <p role="status" className="ghost-result">{result.message}</p>}</form>
    <div className="ghost-section-title"><h2>Investigations</h2><span>{data?.incidents.length ?? 0} recorded</span></div>
    {data?.incidents.length ? data.incidents.map(item => <Incident key={item.id} item={item} resolve={resolve} />) : <p className="ghost-empty">{data ? "No issues recorded yet. Use Look to start a check." : "Investigations will appear when Autumbra’s room is connected."}</p>}
    {threatReceipts.length > 0 && <ThreatReceipts items={threatReceipts} />}
    <aside className="ghost-connections"><h2>Connections still required</h2><p>Autumbra’s assistant that explains what it finds, its private storage, the log and deployment feeds, handing work to a worker, and the step that carries out a recovery are not connected yet. Their absence is shown here so a healthy site check cannot imply full coverage.</p><p>Autumbra Watch cannot change keys, settings, production data, or merge a pull request. Human checks require a person.</p></aside>
  </main>;
}
