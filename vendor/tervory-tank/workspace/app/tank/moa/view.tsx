import type { MoaEvidence, MoaShape } from "@/lib/tank";
import { actOnMixture } from "./actions";
const money = (n: number | null | undefined) => n == null ? "Unknown" : `$${Number(n).toFixed(6)}`;
const pct = (n: number | null) => n == null ? "Not measured" : `${(Number(n) * 100).toFixed(1)}%`;
const label = (shape: string) => ({ single: "Single model", self: "Self-MoA", diverse: "Diverse MoA", optimised: "Optimised MoA" }[shape] ?? shape);

export function ShapeChart({ rows }: { rows: MoaShape[] }) {
  const complete = rows.filter(r => r.status === "complete" && r.score != null && r.cost_per_call != null);
  if (!complete.length) return <p>No comparable measurements yet. All seven shapes and modes must finish before promotion.</p>;
  const max = Math.max(...complete.map(r => Number(r.cost_per_call)), .000001);
  return <><svg className="moa-chart" viewBox="0 0 600 260" role="img" aria-label="Quality against cost: further left costs less; higher scores match more expected answers">
    <path d="M55 20V220H580" fill="none" stroke="#B8BFD0"/>
    <text x="12" y="28">100%</text><text x="25" y="222">0%</text><text x="55" y="246">$0</text><text x="490" y="246">{money(max)}</text>
    {complete.map((r, i) => <g key={`${r.shape}:${r.mode}`}><circle cx={55 + Number(r.cost_per_call) / max * 505} cy={220 - Number(r.score) * 195} r={5 + i % 2 * 2} fill={r.shape === "optimised" ? "#001840" : "#66758d"}><title>{label(r.shape)} · {r.mode}: {pct(r.score)}, {money(r.cost_per_call)}</title></circle></g>)}
  </svg><div className="moa-scroll"><table><caption>Measured quality and cost per answer</caption><thead><tr><th>Shape</th><th>Mode</th><th>Quality</th><th>Cost</th><th>Cases</th><th>Status</th></tr></thead><tbody>
    {rows.map(r => <tr key={`${r.shape}:${r.mode}`}><td>{label(r.shape)}</td><td>{r.mode}</td><td>{pct(r.score)}</td><td>{money(r.cost_per_call)}</td><td>{r.samples}</td><td>{r.status}</td></tr>)}
  </tbody></table></div></>;
}

export function MoaView({ data, status }: { data: MoaEvidence; status?: string }) {
  return <article className="moa"><header><a href="/tank/proof">← Tank proof</a><p className="moa-meta">Founder · {data.region}</p><h1>Mixtures, measured</h1><p>Compare answers and cost before changing a task. Only you can promote a shape or a seat.</p></header>
    {status === "unavailable" && <p role="alert">The action could not finish. Check the evidence and current policy before trying again.</p>}
    {status === "saved" && <p role="status">Action finished. The current evidence is shown below.</p>}
    {!data.tasks.length && <p>No task policies in this scope.</p>}
    {data.tasks.map(task => <section key={task.task_type}><div className="moa-heading"><h2>{task.task_type}</h2><strong>{task.enabled ? "Enabled" : "Off"}</strong></div>
      <p>{label(task.config.shape)} · {task.config.mode} · seat proposals need at least {task.config.min_samples} comparisons.</p>
      {!task.config.seats.length ? <p>No seats configured. This task keeps its existing route.</p> : <div className="moa-seats">{task.config.seats.map(seat => <div key={seat.name}><h3>{seat.name}</h3><dl><dt>Current</dt><dd>{seat.model}</dd><dt>Candidate</dt><dd>{seat.candidate ?? "None"}</dd><dt>Time limit</dt><dd>{seat.timeout_s}s{seat.critical ? " · critical data" : ""}</dd></dl></div>)}</div>}
      {task.config.seats.length > 0 && <div className="moa-seats">{[["When seats agree",task.config.cheap_aggregator,task.config.cheap_candidate],["When seats disagree",task.config.frontier_aggregator,task.config.frontier_candidate]].map(([name,current,candidate]) => <div key={name}><h3>{name}</h3><dl><dt>Aggregator</dt><dd>{current}</dd><dt>Candidate</dt><dd>{candidate ?? "None"}</dd></dl></div>)}</div>}
      <ShapeChart rows={task.shapes}/>
      <div className="moa-actions">{task.config.bench_enabled && <form action={actOnMixture}><Fields data={data} task={task}/><button name="action" value="bench">Run comparison</button></form>}
        {task.shapes.length === 7 && task.shapes.every(s => s.status === "complete") && <form action={actOnMixture}><Fields data={data} task={task}/><button name="action" value="promote">Promote measured winner</button></form>}
      </div>
      {task.proposals.map(p => <form key={p.seat} action={actOnMixture}><Fields data={data} task={task}/><input type="hidden" name="seat" value={p.seat}/><p>{p.seat}: {p.candidate_model} matched in {p.samples} comparisons ({pct(p.agreement)}). Promotion turns the mixture off until it is compared again.</p><button name="action" value="promote">Promote {p.seat}</button></form>)}
    </section>)}
    <section><h2>Run receipts</h2><p>Baselines price the same measured tokens on the top model. They are estimates. Unknown spend claims no saving; negative savings remain visible. Latest 100 runs in this tenant and region.</p>
      {!data.runs.length && <p>No mixtures have run yet.</p>}
      {data.runs.map(run => <details key={run.id}><summary>{run.task_type} · {run.kind} · {run.status} · {money(run.receipt.cost_usd)}</summary>
        {(run.receipt.error || run.receipt.shadow_error) && <p role="status">{run.receipt.error || run.receipt.shadow_error}</p>}
        <dl className="moa-baselines"><div><dt>All-frontier estimate · {run.receipt.baseline_model ?? "Unpriced"}</dt><dd>{money(run.receipt.all_frontier_usd)}</dd><dt>Saving</dt><dd>{money(run.receipt.saved_vs_frontier_usd)}</dd></div><div><dt>Single top model estimate · {run.receipt.baseline_model ?? "Unpriced"}</dt><dd>{money(run.receipt.single_top_usd)}</dd><dt>Saving</dt><dd>{money(run.receipt.saved_vs_single_usd)}</dd></div></dl>
        <div className="moa-scroll"><table><thead><tr><th>Seat</th><th>Model</th><th>Status</th><th>Tokens in / out</th><th>Cost</th></tr></thead><tbody>{run.receipt.seats?.flatMap(seat => seat.attempts.length ? seat.attempts.map((a,i) => <tr key={`${seat.name}:${i}`}><td>{seat.name}</td><td>{a.model}</td><td>{seat.status}</td><td>{a.tokens_in} / {a.tokens_out}</td><td>{money(a.cost_usd)}</td></tr>) : <tr key={seat.name}><td>{seat.name}</td><td>{seat.model}</td><td>{seat.error ?? seat.status}</td><td>Unknown</td><td>Unknown</td></tr>)}</tbody></table></div>
      </details>)}
    </section><section><h2>Seat shadows</h2><p>Candidate answers are compared with the current seat and the final answer. Failed comparisons cannot propose a swap.</p>
      {!data.shadows.length ? <p>No shadow comparisons yet.</p> : <div className="moa-scroll"><table><thead><tr><th>Seat</th><th>Candidate</th><th>Against current</th><th>Against final</th><th>Cost</th><th>Status</th></tr></thead><tbody>{data.shadows.map(s => <tr key={s.id}><td>{s.seat}</td><td>{s.candidate_model}</td><td>{pct(s.against_current)}</td><td>{pct(s.against_final)}</td><td>{money(s.candidate_cost)}</td><td>{s.status}</td></tr>)}</tbody></table></div>}
    </section>
  </article>;
}
function Fields({ data, task }: { data: MoaEvidence; task: MoaEvidence["tasks"][number] }) {
  return <><input type="hidden" name="tenant_id" value={data.tenant_id}/><input type="hidden" name="task" value={task.task_type}/><input type="hidden" name="config_hash" value={task.config_hash}/></>;
}
