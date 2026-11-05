"use client";
import { useState } from "react";
import { CalibrationChart } from "./h7-chart";
import type { Brief, Curve, Load } from "./h7-model";
import { cohortKey, deliveryLabel, percent, period } from "./h7-model";
import { formatDay } from "./format";

export function H7State({ state, page }: { state: "empty" | "error" | "loading"; page: "brief" | "calibration" }) {
  const text = state === "loading" ? "Loading…" : state === "error" ? page === "brief" ? "Brief detail could not be loaded." : "Calibration could not be loaded."
    : page === "brief" ? "No brief selected." : "No resolved predictions yet.";
  return <section className="h7-card" role={state === "error" ? "alert" : "status"}><h3>{text}</h3>
    {page === "calibration" ? <p>Confidence: — · Actual hit rate: — · Samples: —</p> : null}
    {state === "error" ? <a className="h7-button h7-retry" href={page === "brief" ? "" : "/market-lab/calibration"}>Try again</a> : null}</section>;
}
export function BriefView({ value }: { value: Load<Brief> }) {
  return <><h2>Brief detail</h2>{value.state !== "ready" ? <H7State state={value.state} page="brief" /> : <>
    <article className="h7-card"><h3>{value.data.name}</h3><p>{formatDay(value.data.made_at)}</p><p className="h7-saved-text">{value.data.summary}</p>
      <p className="h7-delivery">{value.data.deliveries.map(row => `${row.channel === "email" ? "Email" : "Webhook"} · ${deliveryLabel(row.status)}`).join("    ")}</p></article>
    <h3>{value.data.sources.length} · Sources cited</h3>
    {value.data.sources.map((source, index) => <article className="h7-card" key={index}><h3>Citation {index + 1}</h3>
      <p>{source.claim ?? source.text}</p><p>Date: {source.date ? formatDay(source.date) : "—"}</p><p>{source.url ?? "—"}</p>
      {source.prediction_id ? <p>{source.prediction_id}</p> : null}
      {source.stored_href ? <a className="h7-button" href={source.stored_href}>Open stored source</a> : <button className="h7-button" disabled>Open stored source</button>}
    </article>)}
    <a className="h7-button" href="/market-lab/briefs">Monthly briefs</a>
  </>}</>;
}
export function CalibrationView({ value }: { value: Load<Curve[]> }) {
  const [selected, setSelected] = useState<string | null>(null);
  const rows = value.state === "ready" ? value.data : [];
  const selectedKey = rows.some(row => cohortKey(row) === selected) ? selected : rows[0] ? cohortKey(rows[0]) : null;
  return <><h2>Calibration</h2>{value.state === "ready" ? <p>Resolved originals only · Unknown and unmeasurable excluded</p> : null}
    {value.state !== "ready" ? <H7State state={value.state} page="calibration" /> : <>
      <section className="h7-card h7-table"><h3>Resolved predictions</h3><table><thead><tr>{["Warning pair / feed", "Frozen horizon", "Confidence", "Actual hit rate", "Samples"].map(label => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{value.data.map(row => <tr key={cohortKey(row)}><td><button className="h7-select-cohort" aria-pressed={cohortKey(row) === selectedKey} onClick={() => setSelected(cohortKey(row))}>{row.kind} / {row.feed}</button></td><td>{period(row.horizon_seconds)}</td><td>{percent(row.mean_confidence)}</td><td>{percent(row.realised_rate)}</td><td>{row.count}</td></tr>)}</tbody></table></section>
      {value.data.map(row => <section className="h7-cohort" key={cohortKey(row)}><div className="h7-cohort-summary"><h3><button className="h7-select-cohort" aria-pressed={cohortKey(row) === selectedKey} onClick={() => setSelected(cohortKey(row))}>{row.kind} · Horizon: {period(row.horizon_seconds)}</button></h3><p>{row.kind} / {row.feed}</p>
        <p>Confidence: {percent(row.mean_confidence)} · Actual hit rate: {percent(row.realised_rate)}</p><p>Samples: {row.count} · Hits: {row.hits ?? "—"}</p></div>
        {cohortKey(row) === selectedKey ? <><CalibrationChart row={row}/><p className="h7-cohort-caption">Dashed: equal confidence and hit rate. Dot: {row.horizon_seconds / 86400}-day sample, {row.count} predictions.</p></> : null}
      </section>)}<p>Each warning pair and frozen horizon stays separate.</p>
    </>}
  </>;
}
