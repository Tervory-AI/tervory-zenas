"use client";
import { useEffect, useId, useRef, useState } from "react";
import { dollars, sumAmounts, baselineNames, cheaper, type RowFilter, type Savings, type SavingsRows } from "./savings-types";
import "./savings.css";

type Loader = (filters: RowFilter) => Promise<SavingsRows>;
const pct = (part: number, whole: number) => whole ? `${(100 * part / whole).toFixed(1)}%` : "Not available yet";
function monthStart(asOf: string, months = 1) {
  const d = new Date(asOf); return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - months + 1, 1)).toISOString();
}
function Lines({ points, cumulative = false, names }: { points: Savings["daily"]; cumulative?: boolean; names: string }) {
  const keys = cumulative ? ["cumulative"] as const : ["spent", "would_have_cost"] as const;
  const max = Math.max(...points.flatMap(p => keys.map(k => Number(p[k]))), 0);
  const first = Date.parse(points[0]?.day ?? "2000-01-01"), last = Date.parse(points.at(-1)?.day ?? "2000-01-01");
  const x = (day: string) => 12 + (last === first ? 0.5 : (Date.parse(day) - first) / (last - first)) * 576;
  return <><svg className="savings-chart" viewBox="0 0 600 170" role="img" aria-label={cumulative ? "Savings growing over time" : "Daily spent and would have cost"}>
    <path d="M12 12V150H588" fill="none" stroke="#B8BFD0"/>
    {[0.5,1].map(y => <path key={y} d={`M12 ${150-y*130}H588`} stroke="#E4E8F0"/>)}
    {keys.map((key,i) => <g key={key}><polyline fill="none" stroke={i ? "#7C89A3" : "#001840"} strokeWidth="3" strokeDasharray={i ? "7 5" : undefined} points={points.map(p => `${x(p.day)},${150-Number(p[key])/(max||1)*130}`).join(" ")}/>{points.length===1 && <circle cx={x(points[0].day)} cy={150-Number(points[0][key])/(max||1)*130} r="4" fill="#001840"/>}</g>)}
  </svg><div className="savings-axis"><span>{points[0]?.day ?? "No work yet"}</span><span>{dollars(max)} · {names}</span><span>{points.at(-1)?.day}</span></div></>;
}

export function EvidenceButton({ label, filters, loadRows, founder = false, client = false, exportBase, clientExportBase }: { label: string; filters: RowFilter; loadRows: Loader; founder?: boolean; client?: boolean; exportBase?: string; clientExportBase?: string }) {
  const dialog = useRef<HTMLDialogElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const [data,setData] = useState<SavingsRows | null>(null), [error,setError] = useState(false), [busy,setBusy] = useState(false);
  const heading = useId();
  const openedFilters = useRef(filters);
  async function load(page = 0) {
    setBusy(true); setError(false); setData(null);
    try { setData(await loadRows({...openedFilters.current,page})); } catch { setError(true); }
    finally { setBusy(false); }
  }
  return <><button ref={trigger} className="savings-number" onClick={() => {openedFilters.current=filters; dialog.current?.showModal(); void load();}}>{label}</button>
    <dialog className="savings-drawer" ref={dialog} aria-labelledby={heading} onClose={() => trigger.current?.focus()}>
      <header><h2 id={heading}>{founder ? "Evidence rows" : "Your work"}</h2><button onClick={() => dialog.current?.close()} aria-label="Close work details">Close</button></header>
      <p>Through {openedFilters.current.end.slice(0,19).replace("T"," ")} UTC. Each row is one step of work; a job may have several steps.</p>
      {busy && <p role="status">Loading your work…</p>}{error && <p role="alert">These details could not be loaded. <button onClick={() => void load()}>Try again</button></p>}
      {data && <><p>{data.total.work_count} matching steps · saved {dollars(data.total.saved)} vs {client ? "the standard model" : baselineNames(data.total.standards)}{data.total.free_count ? ` · ${data.total.free_count} at no AI cost` : ""}</p>
        {!data.rows.length && <p>No matching work yet.</p>}
        {data.rows.map((r,i) => <article className="savings-job" key={i}><h3>{r.job_title || (r.job_id ? "Completed work" : "Unlinked work")}</h3><p>{r.member} · {r.day}{r.free_work ? " · No AI cost" : ""}</p>
          <dl><div><dt>Spent · {client ? "the standard model" : r.standard_name ?? "unknown"} comparison</dt><dd>{dollars(r.spent)}</dd></div><div><dt>Baseline: {client ? "the standard model" : r.standard_name ?? "unknown"}</dt><dd>{dollars(r.would_have_cost)}</dd></div><div><dt>Saved vs {client ? "the standard model" : r.standard_name ?? "unknown"}</dt><dd>{dollars(r.saved)}</dd></div><div><dt>Saved vs {client ? "the top model" : r.top_name ?? "unknown"}</dt><dd>{dollars(r.top_saved)}</dd></div>
            {!client && r.selected_name && r.selected_name!==r.standard_name && <div><dt>Selected baseline: {r.selected_name}</dt><dd>{dollars(r.selected_cost ?? null)}</dd></div>}</dl>
          {!r.quality_compared && <p>same work assumed; quality comparison pending</p>}
          {founder && <p>{r.comparison_status} · {r.compared_at}</p>}
          {founder && r.id && <p className="savings-internal">{r.id} · {r.model} · {r.excluded_reason ?? "Included in recorded costs"}</p>}
        </article>)}
        <nav aria-label="Work pages"><button disabled={!data.page || busy} onClick={() => void load(data.page-1)}>Previous</button><span>Page {data.page+1}</span><button disabled={!data.has_more || busy} onClick={() => void load(data.page+1)}>Next</button></nav>
        {clientExportBase && <p><a href={`${clientExportBase}&page=${data.page}&format=csv`}>Client copy of this page as CSV</a> · <a href={`${clientExportBase}&page=${data.page}&format=pdf`}>Client copy of this page as PDF</a></p>}
        {exportBase && <p><a href={`${exportBase}&page=${data.page}&format=csv`}>Export this page as CSV</a> · <a href={`${exportBase}&page=${data.page}&format=pdf`}>Export this page as PDF</a></p>}
      </>}
    </dialog></>;
}

export function SavingsSet({ data, title, loadRows, client = false }: { data: Savings; title: string; loadRows: Loader; client?: boolean }) {
  const [months,setMonths] = useState(1);
  const heading = useId();
  const label = (names?: string[], top = false) => client ? (top ? "the top model" : "the standard model") : baselineNames(names);
  const names = label(data.total.standards);
  const start = monthStart(data.as_of, months), month = monthStart(data.as_of);
  // Include quiet days so two-line graphs have honest daily spacing.
  const byDay = new Map(data.daily.map(p => [p.day,p]));
  const days: Savings["daily"] = [];
  for (let day = new Date(start); day <= new Date(data.as_of); day.setUTCDate(day.getUTCDate()+1)) {
    const key = day.toISOString().slice(0,10);
    days.push(byDay.get(key) ?? {day:key,spent:"0",would_have_cost:"0",saved:"0",cumulative:"0",work_count:0,jobs_count:0,free_count:0,standards:data.total.standards,tops:data.total.tops,top_cost:"0",top_spent:"0",top_saved:"0",top_count:0,quality_pending:false});
  }
  const number = (label: string, extra: Partial<RowFilter> = {}) => <EvidenceButton label={label} filters={{end:data.as_of,...extra}} loadRows={loadRows} client={client} founder={!client}/>;
  return <section className="savings-set" aria-labelledby={heading}>
    <header className="savings-heading"><div><p className="savings-eyebrow">Your savings</p><h2 id={heading}>{title}</h2></div><p>Updated {new Date(data.as_of).toISOString().slice(11,19)} UTC</p></header>
    <div className="savings-headline"><strong>{number(`${cheaper(data.total.spent,data.total.would_have_cost)} vs ${names}`)}</strong>
      <p>{number(`${cheaper(data.total.top_spent,data.total.top_cost)} vs ${client ? "the top model" : `the top model (${label(data.total.tops,true)})`} · ${dollars(data.total.top_saved)} saved`)} · {data.total.top_count} of {data.total.work_count} steps compared</p>
      {data.total.quality_pending && <p>same work assumed; quality comparison pending</p>}</div>
    <p className="savings-note">Only work with a checked comparison and a linked job is included. All amounts are in US dollars. Open any amount to see the work behind it.</p>
    {!data.total.work_count && <p className="savings-empty">No comparable work yet. Your savings will appear here when a checked comparison is available.</p>}
    <div className="savings-grid">
      <section className="savings-card savings-wide"><div className="savings-card-top"><h3>Spent and would have cost</h3><label>Show<select aria-label="Show" value={months} onChange={e => setMonths(Number(e.target.value))}><option value={1}>This month</option><option value={3}>Last 3 months</option><option value={12}>Last 12 months</option></select></label></div>
        <p className="savings-legend">Solid: spent · Dashed: would have cost</p><Lines points={days} names={names}/>
        <p>{number(`Spent ${dollars(sumAmounts(days.map(p => p.spent)))} · ${names} comparison`,{start})} <span> / </span>{number(`${names} would have cost ${dollars(sumAmounts(days.map(p => p.would_have_cost)))}`,{start})}</p>
        <details><summary>Daily amounts</summary><div className="savings-daily">{days.map(p => <div key={p.day}><span>{p.day}</span>{number(`Spent ${dollars(p.spent)} / ${label(p.standards)} ${dollars(p.would_have_cost)}`, {start:`${p.day}T00:00:00Z`,until:new Date(Date.parse(p.day)+86400000).toISOString()})}</div>)}</div></details>
      </section>
      <section className="savings-card savings-wide"><h3>Saved to date</h3><p className="savings-big">{number(`${dollars(data.total.saved)} vs ${names}`)}</p><Lines points={data.daily} cumulative names={names}/><p>{number(`${data.total.jobs_count} jobs · ${data.total.work_count} steps of work`)}</p></section>
      <section className="savings-card"><h3>Saved this month</h3><p className="savings-big">{number(`${dollars(data.month.saved)} vs ${label(data.month.standards)}`,{start:month})}</p><p>{number(`${Number(data.month.would_have_cost)>0 ? (Number(data.month.saved)/Number(data.month.would_have_cost)*100).toFixed(1)+"% less than it would have cost" : "No measured saving"}${Number(data.month.spent)>0 ? ` (${(Number(data.month.would_have_cost)/Number(data.month.spent)).toFixed(2)}× cheaper)` : ""}`,{start:month})}</p><meter min={0} max={1} value={Number(data.month.would_have_cost) ? Number(data.month.saved)/Number(data.month.would_have_cost) : 0} aria-label="Share saved this month"/></section>
      <section className="savings-card"><h3>Done at no AI cost</h3><p className="savings-big">{number(pct(data.total.free_count,data.total.work_count),{free_only:true})}</p><p>{number(`${data.total.free_count} of ${data.total.work_count} steps of work`)} done by our own tools or reused answers.</p><meter min={0} max={1} value={data.total.work_count ? data.total.free_count/data.total.work_count : 0} aria-label="Share done at no AI cost"/></section>
      <section className="savings-card savings-wide"><h3>Saved by team member</h3>{!data.members.length && <p>No comparable work yet.</p>}{data.members.map(p => <div className="savings-member" key={p.member}><div><span>{p.member}</span>{number(`${dollars(p.saved)} vs ${label(p.standards)}`,{member:p.member})}</div><div className="savings-bar" role="img" aria-label={`${p.member}: ${dollars(p.saved)} saved vs ${label(p.standards)}`}><span style={{width:`${Math.max(...data.members.map(m=>Number(m.saved))) ? Number(p.saved)/Math.max(...data.members.map(m=>Number(m.saved)))*100 : 0}%`}}/></div></div>)}</section>
    </div>
  </section>;
}

export function SavingsLive({ initial, loadReport, loadRevision, loadRows }: { initial: Savings | null; loadReport: () => Promise<Savings>; loadRevision: () => Promise<{revision:string}>; loadRows: Loader }) {
  const [data,setData] = useState(initial), [error,setError] = useState(!initial);
  useEffect(() => {
    let disposed=false, running=false, revision: string | undefined = initial?.revision;
    async function refresh() { if(running) return; running=true; try {const next=await loadReport();if(!disposed){setData(next);setError(false);}} catch {if(!disposed)setError(true);} finally {running=false;} }
    async function checkJobs() { try {const next=await loadRevision(); if(revision!==undefined && revision!==next.revision) await refresh();revision=next.revision;} catch {if(!disposed)setError(true);} }
    void checkJobs();
    const periodic=setInterval(refresh,60000), jobs=setInterval(checkJobs,20000);
    const focus=() => {void refresh();}; window.addEventListener("focus",focus);
    return () => {disposed=true;clearInterval(periodic);clearInterval(jobs);window.removeEventListener("focus",focus);};
  },[loadReport,loadRevision,initial?.revision]);
  return <div className="savings-live">{error && <p role="alert">Your savings could not be refreshed. {data ? "The last update is shown below." : "Please refresh to try again."}</p>}{data && <SavingsSet data={data} title="What you saved" loadRows={loadRows} client/>}</div>;
}
