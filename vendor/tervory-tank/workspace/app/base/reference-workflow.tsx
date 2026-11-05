"use client";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { parseBusinessCsv, MAX_CSV_BYTES } from "./csv";
import { costTrend } from "./cost-trend";
import { batchStatusLabel, changeLabel, comparisonLabel, costStep, countLabel, dateLabel, pageFields, fieldLabels, monthLabel, reasonLabel, receiptLabel, refreshLabel, selectionLabel, stateLabel } from "./display";
import type { Comparison, Cost, Expense, FieldEvidence, Group, Numbers, Reference, VerifiedRow } from "./product-types";

const metrics = ["licensees", "attempted", "website", "phone", "email", "email_and_phone", "email_only", "phone_only", "neither", "failed"] as const;
const words: Record<string, string> = { licensees: "Reference licences", attempted: "Attempted", website: "Website", phone: "Phone", email: "Email", email_and_phone: "Email and phone", email_only: "Email only", phone_only: "Phone only", neither: "Neither", failed: "Missing fields" };
const amount = (value: string | null) => value === null ? "price not set" : `$${Number(value).toFixed(6)}`;
async function read<T>(path: string, query = "", body?: Record<string, unknown>): Promise<T> {
  const response = await fetch(`/base/product/${path}${query ? `?${query}` : ""}`, { cache: "no-store", ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === "string" ? value.detail : "Base could not complete this request.");
  return value as T;
}
export function Evidence({ field }: { field: FieldEvidence }) {
  if (!field.verified) return <span>{reasonLabel(field.reason)}{field.source_url && <> · <a href={field.source_url} target="_blank" rel="noopener noreferrer">Source</a></>}</span>;
  return <>{(field.items || [field]).map((item, index) => <p key={index}>{Array.isArray(item.value) ? item.value.join(", ") : item.value}<br />
    {item.source_url && <a href={item.source_url} target="_blank" rel="noopener noreferrer">{item.source_url}</a>}<br />Checked <time dateTime={item.checked_at || undefined}>{dateLabel(item.checked_at)}</time></p>)}</>;
}

export function ReferenceWorkflow({ signedIn }: { signedIn: boolean }) {
  const router = useRouter();
  const [reference, setReference] = useState<Reference | null>(null);
  const [numbers, setNumbers] = useState<Numbers | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [states, setStates] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [collectors, setCollectors] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [records, setRecords] = useState<{ total: number; rows: VerifiedRow[] } | null>(null);
  const [expenses, setExpenses] = useState<{ rows: Expense[]; total: number; cost: Cost; unpricedOnly: boolean } | null>(null);
  const query = new URLSearchParams({ states: states.join(","), types: types.join(","), include_collectors: String(collectors) }).toString();
  const reload = useCallback(async () => {
    try {
      const [ref, data, other] = await Promise.all([read<Reference>("reference", "include_collectors=true"), read<Numbers>("numbers", query), read<Comparison>("comparison", query)]);
      setReference(ref); setNumbers(data); setComparison(other); setError("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The reference could not be loaded."); }
  }, [query]);
  useEffect(() => {
    if (!signedIn) return;
    void reload();
    const timer = setInterval(() => { void reload(); }, 15000);
    return () => clearInterval(timer);
  }, [signedIn, reload]);

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await read<{ records: number; states: number }>("reference/batches", "", { request_id: crypto.randomUUID(), name: "Reference list", states, types, include_collectors: collectors });
      setMessage(`${countLabel(result.records, "licence")} received across ${countLabel(result.states, "state")}. States run one after another.`);
      await reload(); router.refresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The batch could not be confirmed. Check States done before retrying."); }
    finally { setBusy(false); }
  }
  async function upload(event: FormEvent<HTMLFormElement>, apollo = false) {
    event.preventDefault(); const form = event.currentTarget;
    const file = new FormData(form).get("file");
    if (!(file instanceof File)) return;
    setBusy(true); setError("");
    try {
      if (file.size > (apollo ? 2_000_000 : MAX_CSV_BYTES)) throw new Error("This CSV is too large.");
      const csv = await file.text();
      if (apollo) await read("comparison", "", { csv });
      else await read("uploads", "", { request_id: crypto.randomUUID(), name: file.name.replace(/\.csv$/i, "").slice(0, 50), records: parseBusinessCsv(csv) });
      setMessage(apollo ? "Apollo comparison saved for the matching licences." : "Your list was received. Follow its states below.");
      form.reset(); router.refresh();
      setRecords(null); setExpenses(null);
      if (!apollo && (states.length || types.length || collectors)) {
        // Uploaded businesses need not have an ATF type. Show their state results too.
        setStates([]); setTypes([]); setCollectors(false);
      } else await reload();
    } catch (failure) { setError(failure instanceof Error ? failure.message : "The upload could not be completed."); }
    finally { setBusy(false); }
  }
  function download(group: Group, metric: string, reason?: string) {
    return `/base/product/records.csv?${new URLSearchParams({ states: group.state, types: group.licence_type, metric, include_collectors: String(collectors), ...(reason ? { reason } : {}) })}`;
  }
  async function showRecords() {
    try { setRecords(await read("records", query)); }
    catch (failure) { setError(String(failure)); }
  }
  async function showExpenses(cost: Cost, more = false, unpricedOnly = false) {
    try {
      const previous = more && expenses ? expenses.rows : [];
      const result = await read<{ rows: Expense[]; total: number }>("ledger", new URLSearchParams({ state: cost.state, month: cost.month, rung: String(cost.rung), types: types.join(","), include_collectors: String(collectors), offset: String(previous.length), ...(unpricedOnly ? { cost_status: "not_set" } : {}) }).toString());
      setExpenses({ rows: [...previous, ...result.rows], total: result.total, cost, unpricedOnly });
    }
    catch (failure) { setError(String(failure)); }
  }
  if (!signedIn) return <section className="bcp-workflow"><h2>Reference licences</h2><p>Sign in to read the public reference and start your own list.</p></section>;
  return <section className="bcp-workflow bcp-reference" aria-label="Reference licences and verified results">
    <h2>Reference licences</h2>
    {reference?.file ? <p><strong>{countLabel(reference.total_all, "ATF licence")}</strong> · File dated {dateLabel(reference.file.published_on)} · <a href={reference.file.source_url} target="_blank" rel="noopener noreferrer">Official source</a>. Includes {countLabel(reference.collectors, "Type 03 collector")}, excluded from your selection by default. New ATF files are checked daily.</p>
      : <p role={reference?.refresh?.message ? "alert" : undefined}>{reference ? reference.refresh?.message || "The platform reference is not available yet." : "Loading the platform reference…"}</p>}
    {reference?.file && <p>{countLabel(reference.duplicate_count || 0, "duplicate row")} {reference.duplicate_count === 1 ? "was" : "were"} found. The newest row for each licence is used.</p>}
    {reference?.file && reference.refresh?.message && <p role="alert">{reference.refresh.message}</p>}
    {error && <p role="alert">{reasonLabel(error)}</p>}{message && <p role="status">{message}</p>}
    {reference?.refresh && <p>Latest reference check: {dateLabel(reference.refresh.checked_at)} · {refreshLabel(reference.refresh.status)}</p>}
    <form className="bcp-upload" onSubmit={start}>
      <label>States <select className="bcp-input" aria-label="States" multiple value={states} onChange={e => setStates(Array.from(e.target.selectedOptions, option => option.value))}>{[...new Set(reference?.groups.map(group => group.state) || [])].sort().map(state => <option key={state} value={state}>{stateLabel(state)}</option>)}</select></label>
      <label>Licence types <select className="bcp-input" aria-label="Licence types" multiple value={types} onChange={e => setTypes(Array.from(e.target.selectedOptions, option => option.value))}>{[...new Set(reference?.groups.map(group => group.licence_type) || [])].sort().map(type => <option key={type} value={type}>Type {type}</option>)}</select></label>
      <label><input type="checkbox" checked={collectors} onChange={e => setCollectors(e.target.checked)} /> Include Type 03 collectors</label>
      <p className="bcp-copy">Leave a selection empty for all states or types. Select several with Ctrl or Command.</p>
      <button className="bcp-secondary" disabled={busy || !reference?.file}>Start from reference</button>
    </form>
    <form className="bcp-upload" onSubmit={e => upload(e)}><h3>Upload a business list</h3><label>Business CSV <input className="bcp-input" type="file" name="file" accept=".csv,text/csv" required disabled={busy} /></label><button className="bcp-secondary" disabled={busy}>Upload and verify</button><p>Include a name column. Optional website, phone, address, city, state and zip. Up to 1,000 rows, 500 KB.</p></form>
    <h3>Verified contact details</h3><p>Counts describe the latest attempt per licence. Checks still running carry that reason until they finish. A record can have a verified field and still have missing fields. Select any count to download its records with sources and check dates.</p>
    <div className="bcp-product-table"><table><thead><tr><th>State and licence type</th>{metrics.map(metric => <th key={metric}>{words[metric]}</th>)}</tr></thead><tbody>{numbers?.groups.map(group => <tr key={`${group.state}-${group.licence_type}`}><th>{selectionLabel(group.state, group.licence_type)}</th>{metrics.map(metric => <td key={metric}><a href={download(group, metric)}>{group[metric].toLocaleString()}</a></td>)}</tr>)}</tbody></table></div>
    {numbers?.groups.flatMap(group => Object.entries(group.failed_by_reason).map(([reason, count]) => <p key={`${group.state}-${group.licence_type}-${reason}`}><a href={download(group, "failed", reason)}>{countLabel(count, "record")} · {selectionLabel(group.state, group.licence_type)} · {reasonLabel(reason)}</a></p>))}
    <button className="bcp-secondary" onClick={showRecords}>View verified fields and sources</button>
    {records && <div aria-label="Verified fields"><p>{countLabel(records.total, "attempted record")}. Showing the first {records.rows.length}; downloads include every matching record.</p>{records.rows.map(row => <article key={row.record_id}><h4>{row.name} · {stateLabel(row.state)}</h4><dl>{pageFields(row.fields).map(([name, value]) => <div key={name}><dt>{fieldLabels[name]}</dt><dd><Evidence field={value} /></dd></div>)}</dl></article>)}</div>}
    <h3>Cost per verified record</h3><p>One verified record has at least one website, phone or email with a recorded source and check date. Costs include priced calls only, by month and step. Calls without a price stay out of these totals; select their count to see them.</p>
    <div className="bcp-product-table"><table><thead><tr><th>State</th><th>Month</th><th>Step</th><th>Verified records</th><th>Recorded spend</th><th>Cost per verified record</th></tr></thead><tbody>{numbers?.costs.map(cost => <tr key={`${cost.state}-${cost.month}-${cost.rung}`}><td>{stateLabel(cost.state)}</td><td>{monthLabel(cost.month)}</td><td>{costStep(cost)}</td><td>{cost.verified_records}</td><td><button className="bcp-text-button" onClick={() => showExpenses(cost)}>{amount(cost.known_cost_usd)}</button></td><td><button className="bcp-text-button" onClick={() => showExpenses(cost)}>{cost.status === "no verified records" ? "No records have been verified yet." : cost.status === "receipts unavailable" ? "We have not received all the cost details yet." : amount(cost.cost_per_verified_record)}</button>{cost.unpriced > 0 && <><br /><button className="bcp-text-button" onClick={() => showExpenses(cost, false, true)}>excludes {countLabel(cost.unpriced, "unpriced call")}</button></>}</td></tr>)}</tbody></table></div>
    {numbers?.costs.length === 0 && <p>No recorded costs yet.</p>}
    <ul aria-label="Monthly cost change">{costTrend(numbers?.costs || []).map(row => <li key={`${row.state}-${row.month}`}>{stateLabel(row.state)} · {monthLabel(row.month)} · {row.cost === null ? "We do not have a cost per verified record yet." : `$${row.cost.toFixed(6)} per verified record`} · {row.change}{row.unpriced > 0 && <> · {numbers?.costs.filter(cost => cost.state === row.state && cost.month === row.month && cost.unpriced > 0).map(cost => <button key={cost.rung} className="bcp-text-button" onClick={() => showExpenses(cost, false, true)}>excludes {countLabel(cost.unpriced, "unpriced call")} ({costStep(cost).toLowerCase()})</button>)}</>}</li>)}</ul>
    {Boolean(numbers?.unattributed_calls) && <p>{countLabel(numbers?.unattributed_calls || 0, "earlier call")} could not be matched to a record and {numbers?.unattributed_calls === 1 ? "is" : "are"} excluded from per-record costs.</p>}
    {expenses && <div aria-label="Cost receipts"><h4>{expenses.unpricedOnly ? "Calls without a price" : "Recorded calls"}</h4><p>Showing {expenses.rows.length} of {countLabel(expenses.total, "call")}.</p><ul>{expenses.rows.map(row => <li key={row.id}>{dateLabel(row.ts)} · {receiptLabel(row)} · {row.cost_status === "not_set" ? "price not set" : row.vendor_cost_status === "free_pass" ? `Free pass: $0 charged · list cost ${amount(row.vendor_list_cost_usd ?? null)}` : amount(row.cost_usd)}</li>)}</ul>{expenses.rows.length < expenses.total && <button className="bcp-secondary" onClick={() => showExpenses(expenses.cost, true, expenses.unpricedOnly)}>Show more calls</button>}</div>}
    {Boolean(numbers?.records_without_receipts) && <p>We have not received cost details for {countLabel(numbers?.records_without_receipts || 0, "record")}. Their complete costs cannot be calculated yet.</p>}
    <h3>States done</h3><p>{countLabel(numbers?.states_done || 0, "completed state batch", "completed state batches")}. States run one after another.</p><ul>{numbers?.states.map(state => <li key={`${state.id}-${state.state}`}>{stateLabel(state.state)} · {state.name} · {countLabel(state.records_count, "licence")} · {batchStatusLabel(state.status)}{state.error && ` ${reasonLabel(state.error)}`}</li>)}</ul>
    <h3>Reference changes</h3>
    <ul>{reference?.history?.map(file => <li key={file.id}>{dateLabel(file.published_on)} · {countLabel(file.row_count, "licence")} retained in this dated file</li>)}</ul>
    {reference?.changes.length ? <details><summary>{countLabel(reference.changes.length, "new, lapsed or moved licence")} in the latest file</summary><ul>{reference.changes.map(change => <li key={change.licence_key}>Licence {change.licence_key} · {changeLabel(change.change)}</li>)}</ul></details> : <p>No differences between dated files to show yet.</p>}
    <h3>Compare with Apollo</h3><form className="bcp-upload" onSubmit={e => upload(e, true)}><label>Apollo CSV <input className="bcp-input" type="file" name="file" accept=".csv,text/csv" required disabled={busy} /></label><button className="bcp-secondary" disabled={busy}>Compare the same licences</button><p>Upload the Apollo comparison CSV prepared for your Base licences. It contains matching licence numbers and Apollo’s website, phone and email results for the same businesses. Up to 2 MB. No service connection is required.</p></form>
    {comparison?.states.length ? <div className="bcp-product-table"><table><thead><tr><th>State and detail</th><th>Matches</th><th>Base only</th><th>Apollo only</th><th>Disagreements</th></tr></thead><tbody>{comparison.states.flatMap(state => (["website", "phone", "email"] as const).map(field => <tr key={`${state.state}-${field}`}><th>{stateLabel(state.state)} · {fieldLabels[field]}</th>{["matches", "base_only", "apollo_only", "disagreements"].map(outcome => <td key={outcome}>{state[field][outcome]}</td>)}</tr>))}</tbody></table><details><summary>See the values side by side</summary>{comparison.rows.map(row => <article key={row.licence_key}><h4>Licence {row.licence_key} · {stateLabel(row.state)}</h4>{(["website", "phone", "email"] as const).map(field => <p key={field}>{fieldLabels[field]} · Base: {String(row.base[field].value || "Not found")} · Apollo: {row.apollo[field] || "Not found"} · {comparisonLabel(row.outcomes[field])}</p>)}</article>)}</details></div> : <p>No comparison imported yet.</p>}
  </section>;
}
