import { useState } from "react";
import { useRouter } from "next/navigation";
import { contextReport, type ContextReport } from "./context-report";

export function ContextComparison({ report: storedReport, canRunTrial = false }: { report?: ContextReport | null; canRunTrial?: boolean }) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [latestReport, setLatestReport] = useState<ContextReport | null>(null);
  const report = latestReport ?? storedReport;
  const router = useRouter();
  async function run() {
    setPending(true);
    setMessage("");
    try {
      const response = await fetch("/api/base/context-trial", { method: "POST" });
      const result = await response.json();
      setMessage(response.ok ? `Records sent: ${result.records_sent}. ${result.message ?? "Comparison updated."}` : result.error);
      if (response.ok && Array.isArray(result.comparison_records)) setLatestReport(contextReport(result.comparison_records));
      router.refresh();
    } catch {
      setMessage("Trial interrupted. Refresh results before trying again.");
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="bcp-section" id="context-trial">
      <h2>Company data trial</h2>
      <p className="bcp-copy">Context.dev suggestions stay beside Base’s answers. Only checked answers count toward accuracy.</p>
      {canRunTrial && <button type="button" className="bcp-secondary" disabled={pending} onClick={run}>{pending ? "Running trial…" : "Run Context.dev trial"}</button>}
      {message && <p role="status">{message}</p>}
      {message.startsWith("A trial is already running") && <p className="bcp-copy">Start time is UTC.</p>}
      {report?.creditCapReached && <p role="status">credit cap reached</p>}
      {!report ? <p>Comparison could not load.</p> : !report.records && !latestReport ? (
        <p>No trial results yet. Selected public businesses can be compared once the trial key is set.</p>
      ) : (
        <>
          <p className="bcp-copy">{latestReport ? "This press" : "Latest 200 records in this workspace"}: records sent: {report.records}. Credits used: {report.creditsUsed.toFixed(2)}{report.unknownCharges ? " confirmed" : ""}. Credits per record: {report.creditsPerRecord?.toFixed(2) ?? "Not known"}.</p>
          {report.noTruth > 0 && <p>{report.noTruth} records: no truth yet. Credits counted; accuracy not scored.</p>}
          {report.unknownCharges > 0 && <p>{report.unknownCharges} charges need confirmation.</p>}
          {report.fields.length === 0 ? <p>No checked answers yet.</p> : (
            <div className="bcp-table" role="table" aria-label="Company data comparison">
              <div className="bcp-tr bcp-context-cols bcp-th" role="row">
                {["Field", "Checked", "Base correct", "Context.dev correct"].map((label) => <span role="columnheader" key={label}>{label}</span>)}
              </div>
              {report.fields.map((row) => (
                <div className="bcp-tr bcp-context-cols" role="row" key={row.field}>
                  <span role="cell">{row.field}</span>
                  <span role="cell" data-label="Checked">{row.checked}</span>
                  <span role="cell" data-label="Base correct">{(100 * row.baseCorrect / row.checked).toFixed(1)}%</span>
                  <span role="cell" data-label="Context.dev correct">{(100 * row.contextCorrect / row.checked).toFixed(1)}%</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
