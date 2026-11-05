"use client";

import { useActionState } from "react";
import type { GhostResult } from "./actions";
import type { Shield, ShieldBoard } from "./shields-data";

type Action = (previous: GhostResult, form: FormData) => Promise<GhostResult>;

const kinds: Record<string, string> = {
  waf_managed: "Managed firewall rules",
  rate_limit: "Slow down repeated requests",
  block_multipart_post: "Block upload-style requests where no uploads belong",
  challenge_bots: "Ask suspicious bots to prove themselves",
};
const states: Record<string, string> = {
  proposed: "Proposed", dry_run: "Staged", brief: "Written up for a worker", up: "Up",
  auto_undone: "Taken down: site failed its check", refused: "Not raised: site was already failing",
  failed: "Change refused", undone: "Down", withdrawn: "Withdrawn", not_needed: "No longer needed",
  undo_failed: "Needs removing by hand",
};
const severity: Record<string, string> = {
  known_exploited: "Being exploited", critical: "Critical", high: "High", medium: "Medium", low: "Low", unknown: "Not rated",
};

function duration(seconds: number | null): string {
  if (seconds === null) return "None yet";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} days`;
}

function sloLine(metric: { median: number | null; worst: number | null; target: number }, goalLabel: string): string {
  if (metric.worst === null && metric.median === null) {
    return `No receipts yet · goal ${goalLabel}`;
  }
  const worst = metric.worst;
  const under = worst !== null && worst <= metric.target;
  const status = worst === null ? "Waiting on receipts" : under ? "Under goal" : "Over goal";
  return `Median ${duration(metric.median)} · slowest ${duration(metric.worst)} · ${status} (${goalLabel})`;
}

function ShieldRow({ item, act }: { item: Shield; act: Action }) {
  const [result, action, pending] = useActionState(act, null);
  const canRaise = item.state === "proposed" || item.state === "dry_run";
  const canUndo = ["proposed", "dry_run", "brief", "up", "undo_failed"].includes(item.state);
  return <article className="ghost-shield">
    <div className="ghost-issue-head"><h3>{kinds[item.kind] ?? item.kind} · {item.site}</h3><span className="ghost-state">{states[item.state] ?? item.state}</span></div>
    <p>{item.plan.plain}{item.reason ? ` Why: ${item.reason}.` : ""}</p>
    {item.note && <p className="ghost-meta">{item.note}</p>}
    {item.brief && <details><summary>Written up for a worker · needs approval</summary><h4>{item.brief.title}</h4><ol>{item.brief.steps.map((s) => <li key={s}>{s}</li>)}</ol></details>}
    <details><summary>Exactly what would change</summary><p className="ghost-meta">Rule: {item.plan.rule.action} when</p><code className="ghost-rule">{item.plan.rule.expression}</code></details>
    <details><summary>Receipts</summary><ol className="ghost-receipts">
      {item.receipts.map((r) => <li key={r.ledger_id}><strong>{r.step.replaceAll("_", " ")}</strong><code>{r.ledger_id}</code><span>{r.status}</span></li>)}
    </ol></details>
    {(canRaise || canUndo) && <form action={action} className="ghost-shield-actions">
      <input type="hidden" name="id" value={item.id} />
      {canRaise && <button type="submit" name="tap" value="raise" disabled={pending}>{pending ? "Working…" : "Shield"}</button>}
      {canUndo && <button type="submit" name="tap" value="undo" disabled={pending} className="ghost-secondary">Undo</button>}
      {result && <p role="status">{result.message}</p>}
    </form>}
  </article>;
}

export function ShieldsTile({ board, act }: { board: ShieldBoard | null; act: Action }) {
  if (!board) return <section className="ghost-tile" aria-label="Shields"><h2>Shields</h2><p className="ghost-error">Shield numbers are unavailable. An unavailable count is not a clean result.</p></section>;
  const t = board.tile;
  const open = Object.entries(t.open_by_severity);
  const modeLabel = t.mode === "live"
    ? "Live: shields change the edge"
    : "Autumbra Enforce · staged until you Shield";
  return <section className="ghost-tile" aria-label="Shields">
    <div className="ghost-section-title"><h2>Shields</h2><span>{modeLabel}</span></div>
    <dl className="ghost-numbers">
      <div><dt>Open issues</dt><dd>{t.open_total}</dd><p>{open.length ? open.map(([k, n]) => `${n} ${severity[k] ?? k}`).join(" · ") : "Nothing open"}</p></div>
      <div><dt>Time to know</dt><dd>{duration(t.time_to_know_seconds.worst)}</dd><p>{sloLine(t.time_to_know_seconds, "under 1 h")}</p></div>
      <div><dt>Time to shield</dt><dd>{duration(t.time_to_shield_seconds.worst)}</dd><p>{sloLine(t.time_to_shield_seconds, "under 24 h")}</p></div>
      <div><dt>Sites broken by a shield</dt><dd>{t.sites_broken_by_a_shield}</dd><p>Goal 0 · a failed check takes the shield down</p></div>
      <div><dt>Cost this month</dt><dd>${t.cost_this_month_usd.toFixed(4)}</dd><p>From {t.receipts_this_month} receipts</p></div>
    </dl>
    {board.shields.length ? board.shields.map((item) => <ShieldRow key={item.id} item={item} act={act} />) : <p className="ghost-empty">No shields proposed. Autumbra Enforce proposes one when a serious issue matches one of our sites.</p>}
  </section>;
}
