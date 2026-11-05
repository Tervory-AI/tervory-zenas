import { baselineNames, cheaper } from "./savings-types";
export type EvidenceRow = Record<string, string | number | boolean | null>;
type Pair = { primary_model: string; candidate_model: string; samples: number; agreement: number | null; primary_cost_usd: number; could_have_cost_usd: number; saved_usd?: number };
export type TaskProof = { standards?: string[]; tops?: string[]; top_saved_usd?: number; top_baseline_usd?: number; top_actual_usd?: number; quality_note?: string | null; task_type: string; calls: number; priced_calls: number; actual_usd: number; compared_actual_usd: number; unknown_cost_calls: number; baseline_usd: number; saved_usd: number; backfilled_calls: number; unpriced_calls: number; code_calls: number; code_share: number | null; shadow_pairs: Pair[] };
export type Proof = { tenant_names: Record<string,string>; tenant_names_note: string | null; tenants: string[]; all_tenant_total: { quality_note?: string | null; standards?: string[]; tops?: string[]; compared_actual_usd?: number; baseline_usd?: number; top_actual_usd?: number; top_baseline_usd?: number; top_saved_usd?: number; calls: number; saved_usd: number; priced_calls: number; unpriced_calls: number }; window: {start: string | null; end: string}; page: number; page_size: number; has_more: Record<string, boolean>; receipt_page_total: { calls: number; saved_usd: number }; tenant_id: string; region: string; tasks: TaskProof[]; ledger: EvidenceRow[]; shadows: EvidenceRow[]; runs: EvidenceRow[]; cases: EvidenceRow[]; promotions: EvidenceRow[]; policies: EvidenceRow[]; state: EvidenceRow[] };
const usd = (n: number) => `$${Number(n).toFixed(6)}`;
const percent = (n: number | null) => n === null ? "Not measured" : `${(Number(n) * 100).toFixed(1)}%`;


function Evidence({ title, id, rows }: { title: string; id: string; rows: EvidenceRow[] }) {
  const keys = [...new Set(rows.flatMap(r => Object.keys(r)))];
  return <section id={id} className="proof-evidence"><h2>{title}</h2>
    {!rows.length ? <p>No rows recorded yet.</p> : <div className="proof-scroll" tabIndex={0} aria-label={`${title} table`}><table><thead><tr>{keys.map(key => <th key={key}>{key.replaceAll("_", " ")}</th>)}</tr></thead>
      <tbody>{rows.map((row, index) => <tr key={String(row.id ?? index)} id={row.id ? `row-${row.id}` : undefined}>{keys.map(key => <td key={key}>{row[key] === null ? "—" : String(row[key] ?? "")}</td>)}</tr>)}</tbody></table></div>}
  </section>;
}

export function ProofView({ data, task }: { data: Proof; task?: string }) {
  const filters = new URLSearchParams({ tenant_id: data.tenant_id, end: data.window.end });
  if (data.window.start) filters.set("start", data.window.start);
  if (task) filters.set("task", task);
  const url = (selectedTask: string | undefined, section: string, page = 0) => {
    const query = new URLSearchParams(filters);
    if (selectedTask) query.set("task", selectedTask); else query.delete("task");
    query.set("page", String(page));
    return `/tank/proof?${query}#${section}`;
  };
  const query = `&${filters}&page=${data.page}`;
  const allLink = `/tank/proof?tenant_id=${data.tenant_id==="clients" ? "clients" : "all"}&end=${encodeURIComponent(data.window.end)}#receipts`;
  const pagination = <nav aria-label="Evidence pages">
    {data.page > 0 && <a href={url(task, "receipts", data.page - 1)}>Previous page</a>}
    <span>Evidence page {data.page + 1} · up to {data.page_size} rows per table</span>
    {Object.values(data.has_more).some(Boolean) && <a href={url(task, "receipts", data.page + 1)}>Next page</a>}
  </nav>;
  return <article className="proof">
    <header><div><a href="/tank">← Tank</a><p className="proof-kicker">Founder · {data.region} · {data.tenant_id === "all" ? "Everything" : data.tenant_id === "clients" ? "All clients" : data.tenant_names[data.tenant_id] ?? "Unlinked account"}</p><h1>Tank proof</h1><p>What we tested, what it cost, and what we saved.</p></div>
      <nav aria-label="Export evidence"><a href={`/tank/proof/export?format=csv${query}`}>Export CSV</a><a href={`/tank/proof/export?format=pdf${query}`}>Export PDF</a></nav></header>
    <aside>Savings compare the same tokens at baseline prices with actual spend. Backfilled rows use the price book available when filled. Unknown costs and vendor fees have no token baseline and claim no savings. Known vendor fees count only as spend. Bench and shadow spend stay separate. Zero-token cache hits claim no token savings.</aside>
    <section className="proof-total"><h2>{data.tenant_id==="clients" ? "All clients" : "Everything"} · savings against the standard model ({baselineNames(data.all_tenant_total.standards)})</h2><p><a href={allLink}>{cheaper(data.all_tenant_total.compared_actual_usd??0,data.all_tenant_total.baseline_usd??0)} than the standard model</a></p><p><a href={allLink}>{cheaper(data.all_tenant_total.top_actual_usd??0,data.all_tenant_total.top_baseline_usd??0)} vs the top model ({baselineNames(data.all_tenant_total.tops)}) · {usd(data.all_tenant_total.top_saved_usd??0)} saved</a></p>
      <p><a href={allLink}>{usd(data.all_tenant_total.saved_usd)} vs standard ({baselineNames(data.all_tenant_total.standards)}) · {data.all_tenant_total.calls} calls · {data.all_tenant_total.unpriced_calls} unpriced</a></p>
      {data.all_tenant_total.quality_note && <p>{data.all_tenant_total.quality_note}</p>}<p>On this box, through {data.window.end}. Negative savings remain visible.</p></section>
    {data.tenant_names_note && <p role="status">{data.tenant_names_note}</p>}
    <form className="proof-filters" action="/tank/proof" method="get">
      <label>Tenant<select aria-label="Tenant" name="tenant_id" defaultValue={data.tenant_id}><option value="all">Everything</option><option value="clients">All clients</option>{data.tenants.map(id => <option key={id} value={id}>{data.tenant_names[id]}</option>)}</select></label>
      <label>Task<input name="task" defaultValue={task ?? ""} placeholder="All tasks" /></label>
      <label>From (UTC)<input type="datetime-local" name="start" defaultValue={data.window.start?.slice(0,16) ?? ""}/></label>
      <label>Until (UTC, exclusive)<input type="datetime-local" name="end" defaultValue={data.window.end.slice(0,16)}/></label>
      <button type="submit">Apply filters</button>
    </form>
    <p>Cards total all matching live receipts in the selected time window. Tables and exports contain the selected evidence page. Baselines below actual cost or from another model family are flagged on each receipt.</p>
    {task && <p><a href={url(undefined, "receipts")}>All tasks</a> / {task}</p>}
    {!data.tasks.length && <p>No evidence recorded yet.</p>}
    <div className="proof-cards">{data.tasks.map(row => <section key={row.task_type} className="proof-task"><h2><a href={url(row.task_type, "receipts")}>{row.task_type}</a></h2>
      <p><a href={url(row.task_type,"receipts")}>{cheaper(row.compared_actual_usd,row.baseline_usd)} vs {baselineNames(row.standards)}</a></p>
      <p><a href={url(row.task_type,"receipts")}>{cheaper(row.top_actual_usd??0,row.top_baseline_usd??0)} vs the top model ({baselineNames(row.tops)}) · {usd(row.top_saved_usd??0)} saved</a></p>
      {row.quality_note && <p>{row.quality_note}</p>}
      <dl><div><dt>Money saved vs standard ({baselineNames(row.standards)})</dt><dd><a href={url(row.task_type,"receipts")}>{usd(row.saved_usd)}</a></dd></div>
        <div><dt>Standard ({baselineNames(row.standards)}) / compared spend</dt><dd><a href={url(row.task_type,"receipts")}>{usd(row.baseline_usd)} / {usd(row.compared_actual_usd)}</a></dd></div>
        <div><dt>Total recorded spend, including fees · standard ({baselineNames(row.standards)}) comparison</dt><dd><a href={url(row.task_type,"receipts")}>{row.unknown_cost_calls && Number(row.actual_usd) === 0 ? "price not set" : usd(row.actual_usd)} · {row.unknown_cost_calls} unknown costs excluded</a></dd></div>
        <div><dt>Calls served by code</dt><dd><a href={url(row.task_type,"receipts")}>{percent(row.code_share)} ({row.code_calls} / {row.calls})</a></dd></div>
        <div><dt>Receipt coverage</dt><dd><a href={url(row.task_type,"receipts")}>{row.priced_calls} priced · {row.backfilled_calls} backfilled · {row.unpriced_calls} unpriced</a></dd></div></dl>
      <p className="proof-links"><a href={url(row.task_type,"benchmarks")}>Tests and cost per call</a><a href={url(row.task_type,"promotions")}>Promotion dates</a></p>
      {row.shadow_pairs.map(pair => <div className="proof-pair" key={`${pair.primary_model}:${pair.candidate_model}`}><h3>{pair.primary_model} → {pair.candidate_model}</h3><p>Shadow comparison</p>
        <a href={url(row.task_type,"shadows")}>{pair.samples} pairs · {percent(pair.agreement)} agreement</a><p><a href={url(row.task_type,"shadows")}>Primary ({pair.primary_model}) {usd(pair.primary_cost_usd)} · comparison ({pair.candidate_model}) {usd(pair.could_have_cost_usd)}{pair.candidate_model.startsWith("routellm:") && pair.saved_usd !== undefined && <> · {usd(pair.primary_cost_usd)} − {usd(pair.could_have_cost_usd)} = {usd(pair.saved_usd)}</>}</a></p></div>)}
      {!row.shadow_pairs.length && <p>No shadow pairs recorded yet.</p>}
    </section>)}</div>
    {pagination}
    <Evidence id="schedule" title="Benchmark status" rows={data.state}/>
    <Evidence id="policies" title="Current routes and baselines" rows={data.policies}/>
    <Evidence id="benchmarks" title="Tests and cost per call" rows={data.runs}/>
    <Evidence id="cases" title="Individual test receipts" rows={data.cases}/>
    <Evidence id="shadows" title="Shadow pairs" rows={data.shadows}/>
    <Evidence id="promotions" title="Global promotion history" rows={data.promotions}/>
    <section id="receipts"><h2>Live ledger receipts</h2><p>This page: {data.receipt_page_total.calls} calls · saved {usd(data.receipt_page_total.saved_usd)} vs standard ({baselineNames([...new Set(data.tasks.flatMap(t=>t.standards??[]))])}). Sum pages to reconcile with the cards.</p>
      <Evidence id="receipt-rows" title="Counted calls" rows={data.ledger}/></section>
    {pagination}
  </article>;
}
