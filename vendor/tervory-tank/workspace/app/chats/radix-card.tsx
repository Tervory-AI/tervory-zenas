"use client";
import { useEffect, useState } from "react";
import type { Card, RadixView } from "./contract";

function CopyValue({ label, value }: { label: string; value?: string }) {
  const [state, setState] = useState("");
  if (value === undefined) return null;
  return <div className="cw-radix-field"><span>{label}</span><code>{value}</code><button type="button" onClick={async () => {
    try { await navigator.clipboard.writeText(value); setState("Copied"); }
    catch { setState("Copy unavailable. Select the value above to copy it."); }
  }}>Copy {label === "TXT record name" ? "record name" : label === "Scan ID" ? "scan ID" : label}</button><span role="status">{state}</span></div>;
}
function timestamp(value?: string) {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(undefined, { timeZoneName: "short" });
}
export function RadixCard({ view, status, onPrepare, disabled = false }: { view: RadixView; status?: Card["status"]; onPrepare?: (message: string) => void; disabled?: boolean }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => { setNow(Date.now()); const timer = setInterval(() => setNow(Date.now()), 30_000); return () => clearInterval(timer); }, []);
  const expired = !!view.expires_at && now !== null && Date.parse(view.expires_at) <= now;
  const failed = status === "failed" || view.status === "failed";
  const running = status === "executing" || ["pending", "running"].includes(view.status ?? "");
  const hasFindings = view.findings !== undefined;
  const title = view.kind === "proof" ? expired ? "Proof expired" : "Radix · Verify your domain" : failed ? "Scan did not finish" : running ? "Scan running" : hasFindings && !view.findings!.length ? "No findings returned" : "Radix · Scan results";
  return <section className="cw-radix" aria-label={title}>
    <h3>{title}</h3>
    {view.kind === "proof" ? <>
      {!expired && view.value && <p>Add this TXT record in your domain’s DNS settings. Then return here to run the scan.</p>}
      {view.domain && <p>Domain<br />{view.domain}</p>}
      {!expired && <><CopyValue label="TXT record name" value={view.name} /><CopyValue label="TXT value" value={view.value} /></>}
      {view.expires_at && <p>{expired ? "Expired" : "Expires"}<br /><time dateTime={view.expires_at}>{timestamp(view.expires_at)}</time></p>}
      {expired && <p>Generate a new TXT record to verify this domain.</p>}
      {view.domain && onPrepare && <button type="button" disabled={disabled} onClick={() => onPrepare(`Radix, generate a new domain proof for ${view.domain}.`)}>Generate new proof</button>}
      {!expired && view.value && view.domain && onPrepare && <button type="button" disabled={disabled} onClick={() => onPrepare(`I’ve added the TXT record for ${view.domain}. Radix, prepare a scan and ask me for any missing target or artifact details.`)}>I’ve added the record · check proof</button>}
      <p>A proof record does not start a scan. Scan requires its own confirmation.</p>
    </> : <>
      <CopyValue label="Scan ID" value={view.scan_id} />
      {view.target && <p>Target: {view.target}</p>}
      {view.completed_at && <p>Completed: <time dateTime={view.completed_at}>{timestamp(view.completed_at)}</time></p>}
      {(view.high !== undefined || view.critical !== undefined) && <p>{view.high !== undefined && `High: ${view.high}`}{view.high !== undefined && view.critical !== undefined && " · "}{view.critical !== undefined && `Critical: ${view.critical}`}</p>}
      {failed ? <p>We couldn’t finish this scan. No completed result is available.</p> : running ? <p>Your scan is running. Results will appear here when it finishes.</p> : <>
        {hasFindings && <p>{view.findings!.length ? `All findings · ${view.findings!.length}` : "0 findings"}</p>}
        {view.findings?.map((finding, index) => <details className="cw-radix-finding" key={index} open={index === 0}><summary>{index + 1} · {finding.severity ?? "Severity unavailable"} · {finding.rule ?? "Rule unavailable"}
          {(finding.tool || finding.file) && <span className="cw-meta">{finding.tool && `Tool: ${finding.tool}`}{finding.tool && finding.file && " · "}{finding.file && `Location: ${finding.file}${finding.line !== undefined ? `:${finding.line}` : ""}`}</span>}</summary>
          <p>Explanation<br />{finding.explanation ?? "No explanation supplied by this rule."}</p><p>How to fix<br />{finding.remediation ?? "No fix supplied by this rule."}</p>
        </details>)}
        {!hasFindings && view.scan_id && <p>Scan recorded. Ask Radix for the findings using this scan ID.</p>}
      </>}
    </>}
  </section>;
}
