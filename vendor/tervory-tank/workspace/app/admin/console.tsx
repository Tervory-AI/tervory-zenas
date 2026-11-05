"use client";

import Link from "next/link";
import { useActionState, useMemo, useState } from "react";
import { useFormStatus } from "react-dom";
import type { ReactNode } from "react";
import type { Counters, FocusRow, Graduation, LadderTask, Point, PriceRow, Total } from "@/lib/admin";
import type { SaveResult } from "./actions";

type Save = (state: SaveResult | null, formData: FormData) => Promise<SaveResult>;
type AutoSave = (formData: FormData) => Promise<void>;

function dueText(reason: string | null): string | null {
  if (reason === "never searched") return "Not searched yet.";
  if (reason === "roster changed") return "A model was added. Search again.";
  if (reason === "price changed") return "A price changed. Search again.";
  if (reason === "monthly") return "Monthly search is due.";
  return reason;
}

type Day = {
  day: string;
  calls: number;
  proven: number;
  off: number;
  spend: number;
  unpriced: number;
  booked: number;
  outcomes: number;
  rungs: [number, number, number, number];
};

const RUNG_LABELS = ["Paid model", "Our model", "Code", "Cache"] as const;

function money(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  const places = abs !== 0 && abs < 0.01 ? Math.max(4, digits) : digits;
  return `${value < 0 ? "−" : ""}$${abs.toFixed(places)}`;
}

function recordedSpend(value: number, _unpriced = 0): string {
  return money(value);
}

function UnpricedNote({ count, query = "" }: { count?: number; query?: string }) {
  return count ? <a href={`/admin/unpriced${query ? `?${query}` : ""}`} onClick={event => event.stopPropagation()}>excludes {count} unpriced {count === 1 ? "call" : "calls"}</a> : null;
}

function pct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)}%`;
}

function move(value: number | null, kind: "pts" | "usd"): string {
  if (value == null) return "no earlier period";
  if (value === 0) return "flat";
  const dir = value > 0 ? "up" : "down";
  const mag = Math.abs(value);
  return kind === "pts" ? `${dir} ${mag.toFixed(1)} pts` : `${dir} ${money(mag)}`;
}

function byDay(points: Point[]): Day[] {
  const map = new Map<string, Day>();
  for (const point of points) {
    const row = map.get(point.day) ?? {
      day: point.day, calls: 0, proven: 0, off: 0, spend: 0, unpriced: 0, booked: 0, outcomes: 0, rungs: [0, 0, 0, 0],
    };
    row.calls += point.calls;
    row.proven += point.proven_calls;
    row.off += point.off_paid_calls;
    row.spend += point.spend_usd;
    row.unpriced += point.unpriced_calls || 0;
    row.booked += point.booked_usd;
    row.outcomes += point.outcomes;
    row.rungs[0] += point.calls_by_rung["1"] ?? 0;
    row.rungs[1] += point.calls_by_rung["2"] ?? 0;
    row.rungs[2] += point.calls_by_rung["3"] ?? 0;
    row.rungs[3] += point.calls_by_rung["4"] ?? 0;
    map.set(point.day, row);
  }
  return [...map.values()].sort((a, b) => a.day.localeCompare(b.day));
}

function rate(part: number, whole: number): number | null {
  return whole ? (100 * part) / whole : null;
}

export function FounderConsole({
  days, counters, taskSeries, agentSeries, focus, graduations, prices, ladders, savePrice, setAutoPromote, initialTask = null,
}: {
  days: number;
  counters: Counters;
  taskSeries: { points: Point[]; totals: Total[] };
  agentSeries: { totals: Total[] };
  focus: FocusRow[];
  graduations: Graduation[];
  prices: PriceRow[];
  ladders: LadderTask[];
  savePrice: Save;
  setAutoPromote: AutoSave;
  initialTask?: string | null;
}) {
  const [task, setTask] = useState<string | null>(initialTask);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const daily = useMemo(() => byDay(taskSeries.points), [taskSeries.points]);
  const taskDaily = useMemo(() => byDay(taskSeries.points.filter((point) => point.key === task)), [taskSeries.points, task]);
  const taskGrads = graduations.filter((row) => row.task_type === task);
  const shownLadders = task ? ladders.filter((row) => row.task_type === task) : ladders;
  const maxSpend = Math.max(1, ...focus.map((row) => row.spend_usd));
  const lost = counters.routes.filter((row) => row.flag);

  return (
    <div className="shell">
      <header className="head">
        <div>
          <picture>
            <source srcSet="/brand/tervory-logo-reversed.png" media="(prefers-color-scheme: dark)" />
            <img src="/brand/tervory-logo.png" alt="Tervory" />
          </picture>
          <h1>Founder console</h1>
        </div>
        <div>
          <nav className="range" aria-label="Range">
            {[30, 90, 365].map((span) => (
              <Link key={span} href={`/admin?days=${span}`} className={span === days ? "on" : ""}>{span} days</Link>
            ))}
          </nav>
          <Link className="back" href="/admin/feeds">Zenas feeds</Link>
          <Link className="back" href="/admin/homes">Home region</Link>
          <Link className="back" href="/admin/autumbra">Autumbra Watch · onsite checks</Link>
          <Link className="back" href="/">Back to the workspace</Link>
        </div>
      </header>
      <p className="lede">How much of the work is proven, what it earns, and what it costs.</p>
      <p className="note">{counters.note}</p>
      {Boolean(counters.lifetime.unpriced_calls) && <p className="flag">Costs and margins use priced calls only. <UnpricedNote count={counters.lifetime.unpriced_calls} />.</p>}

      {lost.length > 0 && (
        <div className="flags">
          {lost.map((row) => (
            <p className="flag" key={`${row.task_type}-${row.model}-${row.rung}`}>
              {row.task_type} on {row.model} lost proven status: {row.flag}.
            </p>
          ))}
        </div>
      )}

      <section className="counters" aria-label="Last 30 days">
        <Counter label="Proven" value={pct(counters.last_30_days.proven_pct)} change={move(counters.change.proven_pct, "pts")} extra={`Lifetime ${pct(counters.lifetime.proven_pct)}`} />
        <Counter label="Off paid models" value={pct(counters.last_30_days.off_paid_pct)} change={move(counters.change.off_paid_pct, "pts")} extra={`Lifetime ${pct(counters.lifetime.off_paid_pct)}`} />
        <Counter excluded={counters.last_30_days.unpriced_calls} label="Margin" value={pct(counters.last_30_days.margin_pct)} change={move(counters.change.margin_pct, "pts")} extra={`Lifetime ${pct(counters.lifetime.margin_pct)}`} />
        <Counter label="Booked revenue" value={money(counters.last_30_days.booked_usd)} change={move(counters.change.booked_usd, "usd")} extra={`Lifetime ${money(counters.lifetime.booked_usd)}`} />
        <Counter excluded={counters.last_30_days.unpriced_calls} label="Recorded spend" value={recordedSpend(counters.last_30_days.spend_usd, counters.last_30_days.unpriced_calls)} change={move(counters.change.spend_usd, "usd")} extra={`Lifetime ${recordedSpend(counters.lifetime.spend_usd, counters.lifetime.unpriced_calls)}`} />
        <Counter excluded={counters.last_30_days.unpriced_calls} label="Cost per outcome" value={money(counters.last_30_days.cost_per_outcome, 4)} change={move(counters.change.cost_per_outcome, "usd")} extra={`Lifetime ${money(counters.lifetime.cost_per_outcome, 4)}`} />
      </section>

      <section className="section">
        <h2>Where our paid spend goes</h2>
        <p className="hint">Last {days} days. Ranked by what the paid model cost. This is the list for moving work down the ladder.</p>
        {focus.length === 0 ? <p className="empty">No paid routes yet.</p> : (
          <div className="focus">
            {focus.map((row) => (
              <div key={row.task_type} className={task === row.task_type ? "focus-row on" : "focus-row"}><button type="button" onClick={() => setTask(row.task_type)} className="focus-name">Show task details</button>
                <div className="focus-top">
                  <span className="focus-name">{row.task_type} <span className="evidence">· {row.model}</span></span>
                  <span className={`chip ${chipClass(row.status)}`}>{row.status}</span>
                </div>
                <div className="track" aria-hidden><i style={{ width: `${(row.spend_usd / maxSpend) * 100}%` }} /></div>
                <div className="focus-top">
                  <span className="num">{recordedSpend(row.spend_usd, row.unpriced_calls)} · {row.share_of_paid_spend_pct.toFixed(1)}% of paid spend · {row.calls} calls</span>
                  <span className="evidence">{row.next_step}</span>
                </div>
                <UnpricedNote count={row.unpriced_calls} query={`days=${days}&task=${encodeURIComponent(row.task_type)}`} /><p className="evidence">{evidenceLine(row)}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="section">
        <h2>Ladder</h2>
        <p className="hint">Paid model, then a cheaper model, then code. Samples, agreement, cost per call, and the monthly saving sit on each step.</p>
        {shownLadders.length === 0 ? <p className="empty">No ladder for this task yet.</p> : shownLadders.map((row) => (
          <div className="ladder-block" key={row.task_type}>
            <div className="ladder-meta">
              <h3>{row.task_type}</h3>
              <form action={setAutoPromote}>
                <input type="hidden" name="task_type" value={row.task_type} />
                <input type="hidden" name="auto_promote" value={row.auto_promote ? "false" : "true"} />
                <button className="quiet" type="submit">{row.auto_promote ? "Wait for a person" : "Promote by itself"}</button>
              </form>
            </div>
            {row.note && <p className="flag">{row.note}</p>}
            {row.proposed && <p className="evidence">Proposed: {row.proposed}</p>}
            {dueText(row.research_due) && <p className="evidence">{dueText(row.research_due)}</p>}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Step</th><th>What answers</th><th>Samples</th><th>Agreement</th><th>Cost per call</th><th>Monthly saving</th></tr>
                </thead>
                <tbody>
                  {row.steps.map((step) => (
                    <tr key={`${row.task_type}-${step.label}`}>
                      <td>{step.label}{step.current ? " · now" : ""}</td>
                      <td>{step.name ?? "None yet"}</td>
                      <td className="num">{step.samples}</td>
                      <td className="num">{step.agreement == null ? "—" : pct(step.agreement * 100)}</td>
                      <td className="num">{money(step.cost_per_call)}</td>
                      <td className="num">{step.monthly_saving_usd == null ? "—" : money(step.monthly_saving_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </section>

      <section className="section">
        <h2>Proven share</h2>
        <p className="hint">Daily proven share and the share off paid models, over calls by rung. A mark is a graduation.</p>
        <Legend />
        <ChartCard>
          <ComboChart days={daily} marks={graduations} onTip={setTip} />
        </ChartCard>
      </section>

      <section className="section">
        <h2>Revenue and spend</h2>
        <p className="hint">Booked revenue against the cost of priced calls. The shaded gap is the margin based on those calls.</p>
        <ChartCard>
          <MoneyChart days={daily} />
        </ChartCard>
      </section>

      <section className="section">
        <h2>By agent and by task</h2>
        <p className="hint">Click a task to see its own graph and graduations. Independence is the share of calls off a paid model.</p>
        <div className="tables">
          <SortTable days={days} title="By agent" rows={agentSeries.totals} picked={null} onPick={null} />
          <SortTable days={days} title="By task type" rows={taskSeries.totals} picked={task} onPick={setTask} />
        </div>
        {task && (
          <div className="drill">
            <h3>{task}</h3>
            <ChartCard>
              <ComboChart days={taskDaily} marks={taskGrads} onTip={setTip} />
            </ChartCard>
            {taskGrads.length === 0 ? <p className="empty">No graduation in this range.</p> : (
              <div className="table-wrap" style={{ marginTop: 10 }}>
                <table>
                  <thead><tr><th>When</th><th>Move</th><th>From</th><th>To</th><th>Saving</th></tr></thead>
                  <tbody>
                    {taskGrads.map((row) => (
                      <tr key={row.id}>
                        <td>{row.day}</td>
                        <td>{row.action}</td>
                        <td>{row.from_model ?? "—"}</td>
                        <td>{row.to_model ?? "—"}</td>
                        <td className="num">{row.saving_pct == null ? "—" : `${row.saving_pct}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="section">
        <h2>Routes</h2>
        <p className="hint">A route stays proven only while the evals, the shadow sample, and client acceptance still hold.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Task</th><th>Model</th><th>Rung</th><th>Eval</th><th>Shadow</th><th>Acceptance</th><th>Proven</th><th>Since</th></tr>
            </thead>
            <tbody>
              {counters.routes.length === 0 ? <tr><td colSpan={8}>No routes measured yet.</td></tr> : counters.routes.map((row) => (
                <tr key={`${row.task_type}-${row.model}-${row.rung}`}>
                  <td>{row.task_type}</td>
                  <td>{row.model}</td>
                  <td className="num">{row.rung}</td>
                  <td className="num">{row.eval_score == null ? "—" : row.eval_score.toFixed(2)}</td>
                  <td className="num">{row.shadow_n === 0 ? "—" : `${row.shadow_n} · ${row.shadow_agreement == null ? "—" : pct(row.shadow_agreement * 100)}`}</td>
                  <td className="num">{row.acceptance_rate == null ? "—" : pct(row.acceptance_rate * 100)}</td>
                  <td>{row.proven ? "Yes" : row.flag ? "Lost" : "No"}</td>
                  <td>{row.proven_at ? row.proven_at.slice(0, 10) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="section">
        <h2>Price book</h2>
        <p className="hint">Saving adds a new row and older prices stay. Outcome prices apply from their effective date. Tool expenses use the price recorded when each new call starts; setting a price never changes older receipts.</p>
        <div className="price-grid">
          <PriceForm action={savePrice} />
          <div className="table-wrap">
            <table>
              <thead><tr><th>Outcome</th><th>Price</th><th>From</th><th>Current</th></tr></thead>
              <tbody>
                {prices.length === 0 ? <tr><td colSpan={4}>No prices yet. Booked revenue stays at zero until you add one.</td></tr> : prices.map((row) => (
                  <tr key={row.id}>
                    <td>{row.outcome_type}{row.note ? <span className="evidence"> · {row.note}</span> : null}</td>
                    <td className="num">{row.price_status === "not_set" || row.unit_price === null ? "price not set" : `${money(row.unit_price, 8)} ${row.currency}`}</td>
                    <td>{row.effective_from ? row.effective_from.slice(0, 10) : "—"}</td>
                    <td>{row.current ? "Current" : "Earlier"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
      {tip && <div className="tip" style={{ left: tip.x + 12, top: tip.y + 12 }}>{tip.text}</div>}
    </div>
  );
}

function Counter({ label, value, change, extra, excluded }: { label: string; value: string; change: string; extra: string; excluded?: number }) {
  return (
    <article className="counter">
      <span>{label}</span>
      <b className="num">{value}</b><UnpricedNote count={excluded} query="days=30" />
      <em className="num">{change}</em>
      <em className="num">{extra}</em>
    </article>
  );
}

function chipClass(status: string): string {
  if (status === "ready to promote") return "ready";
  if (status === "testing" || status === "proven") return status;
  return "";
}

function evidenceLine(row: FocusRow): string {
  const evalText = row.eval_score == null ? "No eval yet" : `Eval ${row.eval_score.toFixed(2)}`;
  const shadow = row.candidate_model
    ? `shadow ${row.sample_size}/${row.min_samples}${row.agreement == null ? "" : ` at ${pct(row.agreement * 100)}`}`
    : "no shadow candidate";
  const accept = row.acceptance_rate == null ? "no client verdict yet" : `acceptance ${pct(row.acceptance_rate * 100)}`;
  const cache = row.cache_hit_rate_pct == null ? "" : ` · cache ${row.cache_hit_rate_pct.toFixed(0)}%`;
  return `${evalText} · ${shadow} · ${accept}${cache}`;
}

function Legend() {
  return (
    <div className="legend">
      {RUNG_LABELS.map((label, index) => <span key={label}><i className="swatch" style={{ background: `var(--r${index + 1})` }} />{label}</span>)}
      <span><i className="swatch line" />Proven</span>
      <span><i className="swatch line dashed" />Off paid models</span>
      <span>Mark · graduation</span>
    </div>
  );
}

function ChartCard({ children }: { children: ReactNode }) {
  return <div className="chart-card">{children}</div>;
}

function ComboChart({ days, marks, onTip }: { days: Day[]; marks: Graduation[]; onTip: (tip: { x: number; y: number; text: string } | null) => void }) {
  if (days.length === 0) return <p className="empty">No calls in this range.</p>;
  const W = 760;
  const H = 230;
  const padL = 36;
  const padR = 32;
  const padT = 16;
  const padB = 24;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const maxCalls = Math.max(1, ...days.map((day) => day.calls));
  const gap = innerW / days.length;
  const xAt = (index: number) => padL + gap * index + gap / 2;
  const yCalls = (value: number) => padT + innerH - (value / maxCalls) * innerH;
  const yPct = (value: number) => padT + innerH - (value / 100) * innerH;
  const line = (values: number[]) => values.map((value, index) => `${index === 0 ? "M" : "L"}${xAt(index).toFixed(1)},${yPct(value).toFixed(1)}`).join(" ");
  const proven = days.map((day) => rate(day.proven, day.calls) ?? 0);
  const off = days.map((day) => rate(day.off, day.calls) ?? 0);
  const byDayMarks = new Map<string, Graduation[]>();
  for (const mark of marks) {
    const list = byDayMarks.get(mark.day) ?? [];
    list.push(mark);
    byDayMarks.set(mark.day, list);
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Proven share and calls by day">
      {[0, 50, 100].map((tick) => (
        <text key={tick} x={W - 4} y={yPct(tick) + 3} textAnchor="end" fill="currentColor" fontSize="10" opacity="0.6">{tick}</text>
      ))}
      {days.map((day, index) => {
        let stacked = 0;
        const x = padL + gap * index + gap * 0.15;
        const width = Math.max(1, gap * 0.7);
        return (
          <g key={day.day}>
            {day.rungs.map((count, rung) => {
              const h = (count / maxCalls) * innerH;
              const y = yCalls(stacked + count);
              stacked += count;
              if (count === 0) return null;
              return <rect key={rung} x={x} y={y} width={width} height={h} fill={`var(--r${rung + 1})`} opacity="0.9"><title>{`${day.day}: ${RUNG_LABELS[rung]} ${count}`}</title></rect>;
            })}
          </g>
        );
      })}
      <path d={line(proven)} fill="none" stroke="var(--ink)" strokeWidth="2" />
      <path d={line(off)} fill="none" stroke="var(--ink)" strokeWidth="2" strokeDasharray="4 3" />
      {days.map((day, index) => {
        const list = byDayMarks.get(day.day);
        if (!list?.length) return null;
        const text = list.map((mark) => `${mark.task_type}: ${mark.action} ${mark.from_model ?? "?"} → ${mark.to_model ?? "?"}${mark.saving_pct == null ? "" : `, saving ${mark.saving_pct}%`}`).join("\n");
        return (
          <circle
            key={day.day}
            cx={xAt(index)}
            cy={padT + 4}
            r={4}
            fill="var(--ink)"
            onMouseEnter={(event) => onTip({ x: event.clientX, y: event.clientY, text })}
            onMouseLeave={() => onTip(null)}
          >
            <title>{text}</title>
          </circle>
        );
      })}
      <text x={padL} y={H - 6} fill="currentColor" fontSize="10" opacity="0.6">{days[0]?.day}</text>
      <text x={W - padR} y={H - 6} textAnchor="end" fill="currentColor" fontSize="10" opacity="0.6">{days[days.length - 1]?.day}</text>
    </svg>
  );
}

function MoneyChart({ days }: { days: Day[] }) {
  if (days.length === 0) return <p className="empty">No calls in this range.</p>;
  const W = 760;
  const H = 220;
  const padL = 44;
  const padR = 12;
  const padT = 16;
  const padB = 24;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const max = Math.max(1, ...days.map((day) => Math.max(day.booked, day.spend)));
  const gap = innerW / days.length;
  const xAt = (index: number) => padL + gap * index + gap / 2;
  const yAt = (value: number) => padT + innerH - (value / max) * innerH;
  const line = (pick: (day: Day) => number) => days.map((day, index) => `${index === 0 ? "M" : "L"}${xAt(index).toFixed(1)},${yAt(pick(day)).toFixed(1)}`).join(" ");
  const forward = days.map((day, index) => `${index === 0 ? "M" : "L"}${xAt(index).toFixed(1)},${yAt(day.booked).toFixed(1)}`).join(" ");
  const back = [...days].reverse().map((day) => {
    const index = days.indexOf(day);
    return `L${xAt(index).toFixed(1)},${yAt(day.spend).toFixed(1)}`;
  }).join(" ");
  return (
    <><svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Booked revenue and model spend">
      <path d={`${forward} ${back} Z`} fill="var(--ink)" opacity="0.12" />
      <path d={line((day) => day.booked)} fill="none" stroke="var(--ink)" strokeWidth="2" />
      <path d={line((day) => day.spend)} fill="none" stroke="var(--ink)" strokeWidth="2" strokeDasharray="4 3" />
      <text x={4} y={padT + 8} fill="currentColor" fontSize="10" opacity="0.6">{money(max, 0)}</text>
      <text x={padL} y={H - 6} fill="currentColor" fontSize="10" opacity="0.6">{days[0]?.day} · solid revenue, dashed spend</text>
    </svg><table aria-label="Daily priced costs and margins"><thead><tr><th>Day</th><th>Priced spend</th><th>Margin</th><th>Excluded calls</th></tr></thead><tbody>{days.map(day => <tr key={day.day}><td>{day.day}</td><td>{money(day.spend, 6)}</td><td>{day.booked ? pct(100 * (day.booked - day.spend) / day.booked) : "—"}</td><td><UnpricedNote count={day.unpriced} query={`day=${day.day}`} /></td></tr>)}</tbody></table></>
  );
}

const COLUMNS: { key: keyof Total; label: string }[] = [
  { key: "key", label: "Name" },
  { key: "calls", label: "Calls" },
  { key: "proven_pct", label: "Proven" },
  { key: "independence_pct", label: "Independence" },
  { key: "spend_usd", label: "Spend" },
  { key: "outcomes", label: "Outcomes" },
  { key: "cost_per_outcome", label: "Cost / outcome" },
  { key: "margin_pct", label: "Margin" },
];

function SortTable({ title, rows, picked, onPick, days }: { days: number; title: string; rows: Total[]; picked: string | null; onPick: ((key: string) => void) | null }) {
  const [sort, setSort] = useState<{ key: keyof Total; dir: number }>({ key: "spend_usd", dir: -1 });
  const sorted = [...rows].sort((a, b) => compare(a[sort.key], b[sort.key]) * sort.dir);
  function choose(key: keyof Total) {
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir * -1 } : { key, dir: key === "key" ? 1 : -1 }));
  }
  return (
    <div>
      <h3>{title}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((column) => (
                <th key={column.key}><button type="button" onClick={() => choose(column.key)}>{column.label}</button></th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? <tr><td colSpan={8}>Nothing in this range.</td></tr> : sorted.map((row) => (
              <tr key={row.key} className={[picked === row.key ? "picked" : "", onPick ? "clickable" : ""].filter(Boolean).join(" ") || undefined} onClick={onPick ? () => onPick(row.key) : undefined}>
                <td>{labelFor(row.key)}</td>
                <td className="num">{row.calls}</td>
                <td className="num">{pct(row.proven_pct)}</td>
                <td className="num">{pct(row.independence_pct)}</td>
                <td className="num">{recordedSpend(row.spend_usd, row.unpriced_calls)} <UnpricedNote count={row.unpriced_calls} query={`days=${days}&${title === "By agent" ? "agent" : "task"}=${encodeURIComponent(row.key)}`} /></td>
                <td className="num">{row.outcomes}</td>
                <td className="num">{money(row.cost_per_outcome, 4)}</td>
                <td className="num">{pct(row.margin_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function labelFor(key: string): string {
  return key.endsWith("#shadow") ? key.replace("#shadow", " shadow") : key;
}

function compare(a: Total[keyof Total], b: Total[keyof Total]): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "string" && typeof b === "string") return a.localeCompare(b);
  return Number(a) - Number(b);
}

function PriceForm({ action }: { action: Save }) {
  const [state, formAction] = useActionState(action, null);
  const today = new Date().toISOString().slice(0, 10);
  return (
    <form className="price-form" action={formAction}>
      <label>Outcome type
        <input name="outcome_type" list="outcome-types" required placeholder="base.record_enriched" />
        <datalist id="outcome-types">
          <option value="base.record_enriched" />
          <option value="rig.setup_quoted" />
          <option value="zenas.document_released" />
          <option value="tier0.answer_accepted" />
          <option value="apollo.contact" />
          <option value="brave.search.query" />
          <option value="google.places.request" />
          <option value="context.dev.credit" />
        </datalist>
      </label>
      <label>Unit price
        <input name="unit_price" type="number" min="0" step="0.00000001" required />
      </label>
      <label>Currency
        <input name="currency" defaultValue="USD" maxLength={3} />
      </label>
      <label>Effective from (UTC)
        <input name="effective_from" type="date" defaultValue={today} min="2020-01-01" suppressHydrationWarning />
      </label>
      <label>Note
        <input name="note" maxLength={500} />
      </label>
      <SaveButton />
      {state?.ok === false && <p className="form-error">{state.error}</p>}
      {state?.ok === true && <p className="evidence">Saved as a new price.</p>}
    </form>
  );
}

function SaveButton() {
  const { pending } = useFormStatus();
  return <button className="save" type="submit" disabled={pending}>{pending ? "Saving…" : "Save price"}</button>;
}
