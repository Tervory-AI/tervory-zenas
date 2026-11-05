import { ledgerSummary } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const dynamic = "force-dynamic";

// The client's ledger view: what their work cost, by model and rung. Read straight from Tank.
export default async function LedgerPage() {
  let summary: Awaited<ReturnType<typeof ledgerSummary>> | null = null;
  let error: string | null = null;
  let orgName = "";
  try {
    const tenant = await currentTenant();
    orgName = tenant.orgName;
    summary = await ledgerSummary(tenant.tenantId, 30);
  } catch (err) {
    error = err instanceof Error ? err.message : "Tank unavailable";
  }

  if (error || !summary) {
    return (
      <>
        <h1>Ledger</h1>
        <p className="err">{error ?? "No data"}</p>
      </>
    );
  }

  const rungs = [1, 2, 3, 4].map((r) => ({
    rung: r,
    calls: summary.by_model.filter((m) => m.rung === r).reduce((a, m) => a + Number(m.calls), 0),
  }));
  const total = Math.max(summary.calls, 1);

  return (
    <>
      <h1>Ledger · {orgName} · last {summary.days} days</h1>
      <div className="tiles">
        <div className="tile"><b>{summary.calls}</b><span>model calls</span></div>
        <div className="tile"><b>{summary.delivered}</b><span>delivered</span></div>
        <div className="tile">
          <b>{summary.accepted} / {summary.rejected}</b>
          <span>accepted / rejected{summary.acceptance_rate != null ? ` · ${Math.round(summary.acceptance_rate * 100)}%` : ""}</span>
        </div>
        <div className="tile"><b>{summary.unpriced && summary.cost_usd === 0 ? "price not set" : `$${summary.cost_usd.toFixed(8)}`}</b><span>Recorded spend · {summary.unpriced} calls excluded: price not set</span></div>
        <div className="tile">
          <b>{summary.cost_per_delivered_usd == null ? "—" : `$${summary.cost_per_delivered_usd.toFixed(5)}`}</b>
          <span>per delivered answer</span>
        </div>
        <div className="tile">
          <b>{summary.cost_per_accepted_usd == null ? "—" : `$${summary.cost_per_accepted_usd.toFixed(5)}`}</b>
          <span>per accepted answer</span>
        </div>
      </div>

      <h2 style={{ marginTop: 28 }}>Where the work ran</h2>
      <table>
        <thead>
          <tr><th>Rung</th><th>Means</th><th>Calls</th><th>Share</th></tr>
        </thead>
        <tbody>
          {rungs.map((r) => (
            <tr key={r.rung}>
              <td>{r.rung}</td>
              <td>{["Frontier model", "Owned small model", "Code", "Cache"][r.rung - 1]}</td>
              <td>{r.calls}</td>
              <td>{Math.round((r.calls / total) * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2 style={{ marginTop: 28 }}>By model</h2>
      <table>
        <thead>
          <tr><th>Model</th><th>Rung</th><th>Calls</th><th>Accepted</th><th>Rejected</th><th>Tokens in</th><th>Tokens out</th><th>Cost</th></tr>
        </thead>
        <tbody>
          {summary.by_model.map((m) => (
            <tr key={`${m.model}-${m.rung}`}>
              <td>{m.model}</td><td>{m.rung}</td><td>{m.calls}</td><td>{m.accepted}</td><td>{m.rejected}</td>
              <td>{Number(m.tokens_in).toLocaleString()}</td><td>{Number(m.tokens_out).toLocaleString()}</td>
              <td>{m.cost_usd == null ? "price not set" : `$${Number(m.cost_usd).toFixed(8)}`}{m.unpriced > 0 && m.cost_usd != null ? ` · ${m.unpriced} excluded: price not set` : ""}</td>
            </tr>
          ))}
          {summary.by_model.length === 0 && (
            <tr><td colSpan={8} style={{ color: "var(--muted)" }}>No calls yet. Ask something in Chat.</td></tr>
          )}
        </tbody>
      </table>
    </>
  );
}
