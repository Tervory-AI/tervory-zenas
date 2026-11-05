"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { MOOT_LABEL, MOOT_LEAD, MOOT_PARTS } from "./moot";
import { fairnessReading, type Fairness, newerHearing, SEATS, SEAT_LABELS, turnFromForm, type Hearing, type Seat } from "./moot-hearing-types";

type Summary = { id: string; title: string; phase: string; first_side?: Seat };
type Participant = { user_id: string; name: string };
const BASE = "/api/zenas/moot/";
const ERROR: Record<string, string> = {
  stale_revision: "The hearing changed. Your draft is still here.",
  seat_not_owned: "This seat is controlled by someone else.",
  citation_check_failed: "That citation could not be verified.",
  unsupported_fact: "Use a fact from the record, or label it as an assumption.",
  reasoning_required: "Explain the facts, rule, material differences and opposing argument.",
  room_unavailable: "Room unavailable",
};
const FAILURE = "The request could not be completed. Check the details and try again.";

export function MootCourt() {
  const [summaries, setSummaries] = useState<Summary[]>([]);
  const [sources, setSources] = useState<{ id: string; title: string }[]>([]);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [founder, setFounder] = useState(false);
  const [operator, setOperator] = useState(false);
  const [selected, setSelected] = useState("");
  const selectedRef = useRef("");
  const [seat, setSeat] = useState<Seat | "">("");
  const seatRef = useRef<Seat | "">("");
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});
  const [hearing, setHearing] = useState<Hearing | null>(null);
  const [fairness, setFairness] = useState<Fairness | null>(null);
  const swappedStart = useRef<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [aiBusy, setAiBusy] = useState(false);
  const aiAbort = useRef<AbortController | null>(null);
  const aiRequest = useRef<string | null>(null);
  const commandRequests = useRef(new Map<string, string>());
  const [judgeKind, setJudgeKind] = useState("question");

  const accept = useCallback((candidate: Hearing, isOperator: boolean) => {
    if (candidate.id !== selectedRef.current) return;
    setHearing(current => newerHearing(current, candidate, selectedRef.current));
    setOperator(isOperator);
  }, []);
  const refresh = useCallback(async () => {
    const id = selectedRef.current;
    if (!id) return;
    const requestedSeat = seatRef.current;
    try {
      const res = await fetch(`${BASE}hearings/${id}${requestedSeat ? `?seat=${requestedSeat}` : ""}`, { cache: "no-store" });
      if (selectedRef.current !== id || seatRef.current !== requestedSeat) return;
      if (!res.ok) {
        if ([401, 403, 404].includes(res.status)) { setHearing(null); setOperator(false); seatRef.current = ""; setSeat(""); }
        else setHearing(current => {
          if (!current) return current;
          const problem = { ...current.problem }; delete problem.bench_notes;
          return { ...current, problem };
        });
        setMessage("Room unavailable"); return;
      }
      const data = await res.json();
      if (seatRef.current === requestedSeat) {
        accept(data.hearing, data.operator);
        if (data.hearing.fairness_pair_id) {
          const comparison = await fetch(`${BASE}hearings/${id}/fairness`, { cache: "no-store" });
          if (comparison.ok && selectedRef.current === id) setFairness((await comparison.json()).fairness);
        } else setFairness(null);
      }
    } catch { if (selectedRef.current === id) setMessage("Room unavailable"); }
  }, [accept, seat]);

  useEffect(() => {
    let active = true;
    Promise.all([fetch(BASE + "hearings"), fetch(BASE + "sources"), fetch(BASE + "participants")]).then(async ([h, s, p]) => {
      if (!active) return;
      if (h.ok) setSummaries((await h.json()).hearings);
      else setMessage("Room unavailable");
      if (s.ok) setSources((await s.json()).sources);
      if (p.ok) { setParticipants((await p.json()).participants); setFounder(true); }
    }).catch(() => { if (active) setMessage("Room unavailable"); });
    return () => { active = false; aiAbort.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!selected) return;
    void refresh();
    const timer = setInterval(() => { void refresh().catch(() => {}); }, 1000);
    return () => clearInterval(timer);
  }, [selected, refresh]);
  useEffect(() => {
    if (!seat && hearing?.my_seats.length) selectSeat(hearing.my_seats[0]);
  }, [hearing, seat]);

  async function command(action: string, payload: unknown = {}, runAI = false) {
    const current = hearing;
    if (!current) return false;
    const id = current.id;
    const requestedSeat = seatRef.current;
    const fingerprint = JSON.stringify([id, current.revision, action, payload]);
    const requestId = commandRequests.current.get(fingerprint) ?? crypto.randomUUID();
    commandRequests.current.set(fingerprint, requestId);
    const controller = runAI ? new AbortController() : null;
    if (controller) aiAbort.current = controller;
    if (runAI) setAiBusy(true); else setBusy(true);
    setMessage("");
    try {
      const res = await fetch(`${BASE}hearings/${id}/${action}${requestedSeat ? `?seat=${requestedSeat}` : ""}`, {
        method: "POST", headers: { "content-type": "application/json" }, signal: controller?.signal,
        body: JSON.stringify({ request_id: requestId, revision: current.revision, payload }),
      });
      const data = await res.json();
      if (!res.ok) { setMessage(ERROR[data.error] ?? FAILURE); void refresh(); return false; }
      if (seatRef.current === requestedSeat) {
        accept(data.hearing, data.operator);
        if (data.hearing.fairness_pair_id) {
          const comparison = await fetch(`${BASE}hearings/${id}/fairness`, { cache: "no-store" });
          if (comparison.ok && selectedRef.current === id) setFairness((await comparison.json()).fairness);
        } else setFairness(null);
      }
      else void refresh();
      if (action === "handover") aiAbort.current?.abort();
      return true;
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) setMessage("Room unavailable");
      return false;
    } finally {
      if (runAI) { setAiBusy(false); aiAbort.current = null; } else setBusy(false);
    }
  }

  // A durable AI seat continues from the current revision. Never auto-retry a
  // failed/uncertain model call; the operator can resolve or explicitly retry it.
  useEffect(() => {
    if (!hearing || hearing.phase !== "hearing" || hearing.seats[hearing.floor].kind !== "ai"
      || hearing.pending_ai || hearing.last_ai_resolution === "generation_failed" || aiBusy || busy) return;
    const key = `${hearing.id}:${hearing.revision}`;
    if (aiRequest.current === key) return;
    aiRequest.current = key;
    void command("ai", {}, true);
    // The exact revision is the scheduling key; polling the same state cannot add calls.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hearing?.id, hearing?.revision, hearing?.phase, aiBusy, busy]);

  async function startSwapped() {
    if (!fairness || !hearing || !operator) return;
    const target = fairness.first_hearing === hearing.id ? fairness.second_hearing : fairness.first_hearing;
    const identity = `${fairness.pair_id}:${target}`;
    if (swappedStart.current === identity) return;
    swappedStart.current = identity;
    setBusy(true);
    try {
      const res = await fetch(`${BASE}hearings/${target}`, { cache: "no-store" });
      if (!res.ok) { setMessage("Room unavailable"); return; }
      const partner = (await res.json()).hearing as Hearing;
      if (partner.phase === "locked") {
        const fingerprint = `start-paired:${target}:${partner.revision}`;
        const key = commandRequests.current.get(fingerprint) ?? crypto.randomUUID();
        commandRequests.current.set(fingerprint, key);
        const started = await fetch(`${BASE}hearings/${target}/start`, { method: "POST", headers: { "content-type": "application/json" },
          body: JSON.stringify({ request_id: key, revision: partner.revision, payload: {} }) });
        if (!started.ok) { setMessage(FAILURE); return; }
      }
      setSummaries(rows => rows.some(r => r.id === target) ? rows : [...rows, { id: target, title: partner.problem.title, phase: partner.phase, first_side: partner.first_side }]);
      selectHearing(target);
    } catch { setMessage("Room unavailable"); } finally { setBusy(false); }
  }
  useEffect(() => {
    if (operator && hearing?.phase === "closed" && fairness?.status === "incomplete" && hearing.id === fairness.first_hearing) void startSwapped();
    // A pair may advance only once per partner; failed/uncertain calls are not replayed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operator, hearing?.id, hearing?.phase, fairness?.pair_id, fairness?.status]);

  function selectHearing(id: string) {
    aiAbort.current?.abort(); selectedRef.current = id; setSelected(id); setHearing(null); setFairness(null); seatRef.current = ""; setSeat(""); setMessage("");
  }
  function selectSeat(value: Seat | "") {
    seatRef.current = value; setSeat(value);
    // Drop the prior role's private projection immediately. An old response may
    // not put it back after a role selection or controller handover.
    setHearing(current => {
      if (!current) return current;
      const clean = { ...current.problem };
      delete clean.bench_notes;
      return { ...current, problem: clean };
    });
  }
  const draftKey = `${selected}:${seat}`;
  const draft = drafts[draftKey] ?? {};
  const putDraft = (name: string, value: string) => setDrafts(all => ({ ...all, [draftKey]: { ...(all[draftKey] ?? {}), [name]: value } }));
  const area = (name: string, label: string, required = false) => <label key={name}>{label}<textarea name={name} required={required} maxLength={12000} value={draft[name] ?? ""} onChange={e => putDraft(name, e.target.value)} /></label>;
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setMessage("");
    const seats = Object.fromEntries(SEATS.map(s => { const value = String(form.get(s)); return [s, value === "ai" ? { kind: "ai" } : { kind: "human", user_id: value }]; }));
    const fingerprint = JSON.stringify(["create", form.get("worked"), seats]);
    const requestId = commandRequests.current.get(fingerprint) ?? crypto.randomUUID();
    commandRequests.current.set(fingerprint, requestId);
    try {
      const res = await fetch(BASE + "hearings", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ request_id: requestId, worked_id: form.get("worked"), seats }) });
      const data = await res.json();
      if (!res.ok) { setMessage(ERROR[data.error] ?? FAILURE); return; }
      commandRequests.current.delete(fingerprint);
      selectHearing(data.hearing.id); accept(data.hearing, true);
      setSummaries(rows => [{ id: data.hearing.id, title: data.hearing.problem.title, phase: data.hearing.phase }, ...rows]);
    } catch { setMessage("Room unavailable"); } finally { setBusy(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!hearing || !seat) return;
    const form = event.currentTarget;
    if (await command("turn", turnFromForm(new FormData(form), hearing, seat))) {
      setDrafts(all => ({ ...all, [draftKey]: {} })); form.reset();
    }
  }
  const allowed = operator ? SEATS : hearing?.my_seats ?? [];
  const canSubmit = hearing?.phase === "hearing" && seat === hearing.floor && hearing.seats[seat].kind === "human" && hearing.my_seats.includes(seat);

  return <section className="zn-moot" aria-label={MOOT_LABEL}>
    <header className="zn-head"><div><h1>{MOOT_LABEL}</h1><p>{MOOT_LEAD}</p></div></header>
    {message && <p role="alert">{message}</p>}
    <label className="zn-select">The hearing<select value={selected} onChange={e => selectHearing(e.target.value)}><option value="">—</option>{summaries.map(s => <option value={s.id} key={s.id}>{s.title}{s.first_side === "respondent" ? " · Swapped seats" : ""}</option>)}</select></label>
    <div className="zn-moot-ground">
      <article className="zn-card"><h2>{MOOT_PARTS[0].title}</h2>
        {founder && !hearing && <form onSubmit={create}>
          <label className="zn-select">Civil appeal<select name="worked" required><option value="">—</option>{sources.map(s => <option value={s.id} key={s.id}>{s.title}</option>)}</select></label>
          {SEATS.map(s => <label className="zn-select" key={s}>{SEAT_LABELS[s]}<select name={s} defaultValue="ai"><option value="ai">AI</option>{participants.map(p => <option value={p.user_id} key={p.user_id}>{p.name}</option>)}</select></label>)}
          <button className="zn-primary" disabled={busy || !sources.length}>Create hearing</button>
        </form>}
        {!hearing ? <p>{MOOT_PARTS[0].empty}</p> : <><h3>{hearing.problem.title}</h3><p>{hearing.problem.facts}</p>
          <ol>{hearing.problem.issues.map((issue, i) => <li key={i} aria-current={i === hearing.current_issue ? "step" : undefined}>{issue}</li>)}</ol>
          {operator && hearing.phase === "draft" && <><form onSubmit={e => { e.preventDefault(); const data = new FormData(e.currentTarget); void command("edit", { changes: { title: data.get("title"), facts: data.get("facts"), issues: String(data.get("issues")).split("\n").filter(Boolean), fact_records: [{ id: "facts", text: String(data.get("facts")), status: "sourced" }, ...String(data.get("assumptions")).split("\n").filter(Boolean).map((text, i) => ({ id: `assumption-${i + 1}`, text, status: "assumed" }))] } }); }}>
            <label>Title<input name="title" defaultValue={hearing.problem.title} required /></label><label>Sourced facts<textarea name="facts" defaultValue={hearing.problem.facts} required /></label><label>Assumptions<textarea name="assumptions" defaultValue={hearing.problem.fact_records.filter(f => f.status === "assumed").map(f => f.text).join("\n")} /></label><label>Issues<textarea name="issues" defaultValue={hearing.problem.issues.join("\n")} required /></label><button className="zn-outline" disabled={busy}>Save problem</button></form>
            <button className="zn-primary" disabled={busy} onClick={() => void command("lock")}>Lock problem</button></>}
          {operator && hearing.phase === "locked" && <button className="zn-primary" disabled={busy} onClick={() => void command("start")}>Start hearing</button>}
        </>}
      </article>
      <article className="zn-card"><h2>{MOOT_PARTS[1].title}</h2>
        {!hearing ? <p>{MOOT_PARTS[1].empty}</p> : <>
          <p>Floor: {SEAT_LABELS[hearing.floor]} · {hearing.phase}</p>
          {hearing.pending_ai && <p role="status">AI turn in progress</p>}
          {operator && SEATS.map(s => <label className="zn-select" key={s}>{SEAT_LABELS[s]}<select disabled={busy || hearing.phase === "closed"} value={hearing.seats[s].kind === "ai" ? "ai" : hearing.seats[s].user_id ?? ""} onChange={e => void command("handover", { seat: s, controller: e.target.value === "ai" ? { kind: "ai" } : { kind: "human", user_id: e.target.value } })}><option value="ai">AI</option>{participants.map(p => <option value={p.user_id} key={p.user_id}>{p.name}</option>)}</select></label>)}
          <label className="zn-select">Your role<select value={seat} onChange={e => selectSeat(e.target.value as Seat)}><option value="">—</option>{allowed.map(s => <option value={s} key={s}>{SEAT_LABELS[s]}</option>)}</select></label>
          {hearing.problem.bench_notes && <details><summary>Bench notes</summary><ul>{Object.values(hearing.problem.bench_notes).flatMap(value => Array.isArray(value) ? value : [value]).filter(value => typeof value === "string").map((value, i) => <li key={i}>{String(value)}</li>)}</ul></details>}
          {hearing.pending_question && <p>{hearing.pending_question.text}</p>}
          <ol className="zn-thread">{hearing.transcript.map(t => <li key={t.number}><strong>{SEAT_LABELS[t.seat]}</strong><p>{t.text}</p>{t.reasoning && <details><summary>Reasoning</summary><ReasonTrace trace={t.reasoning} problem={hearing.problem} /></details>}</li>)}</ol>
          {canSubmit && <form className="zn-composer" onSubmit={submit}>
            {seat === "judge" && <><label className="zn-select">Current issue<select name="issue" defaultValue={hearing.current_issue}>{hearing.problem.issues.map((issue, i) => <option value={i} key={i}>{issue}</option>)}</select></label><label className="zn-select">Judge<select name="kind" value={judgeKind} onChange={e => setJudgeKind(e.target.value)}><option value="question">Question</option><option value="direction">Give the floor</option><option value="ruling">Judgment</option></select></label>{judgeKind !== "ruling" && <label className="zn-select">Counsel<select name="target"><option value="applicant">Applicant</option><option value="respondent">Respondent</option></select></label>}</>}
            {area("text", "Submission", true)}
            {(seat !== "judge" || judgeKind !== "direction") && <>
              <label className="zn-select">Fact<select name="fact" value={draft.fact ?? hearing.problem.fact_records[0]?.id ?? ""} onChange={e => { putDraft("fact", e.target.value); putDraft("position", hearing.problem.fact_records.find(f => f.id === e.target.value)?.status ?? "sourced"); }}>{hearing.problem.fact_records.map(f => <option value={f.id} key={f.id}>{f.text}</option>)}</select></label>
              <label className="zn-select">Fact record<select name="position" value={draft.position ?? hearing.problem.fact_records[0]?.status ?? "sourced"} onChange={e => putDraft("position", e.target.value)}><option value="sourced">Sourced</option><option value="contested">Contested</option><option value="assumed">Assumed</option></select></label>
              {area("relevance", "Why this fact matters", true)}
              <label className="zn-select">Governing authority<select name="authority" value={draft.authority ?? ""} onChange={e => putDraft("authority", e.target.value)}><option value="">—</option>{hearing.problem.authorities.map(a => <option value={a.id} key={a.id}>{a.name} · {a.weight ?? "unknown"}</option>)}</select></label>
              {area("application", "Apply the rule", Boolean(draft.authority))}{area("difference", "Material factual difference")}{area("significance", "Legal significance", Boolean(draft.authority))}
              {area("opposing", "Opposing argument", true)}{area("response", "Response to the challenge", true)}{area("uncertainty", "Uncertainty", !draft.authority)}{area("conclusion", "Conclusion", true)}
            </>}
            {seat === "judge" && judgeKind === "ruling" && <><label className="zn-select">Decision<select name="decision" defaultValue="uncertain"><option value="uncertain">Uncertain or no single prevailing party</option><option value="applicant">Applicant</option><option value="respondent">Respondent</option></select></label>{area("order", "Order", true)}{area("reasons", "Reasons", true)}</>}
            <button className="zn-primary" disabled={busy}>Submit</button>
          </form>}
          {hearing.phase === "hearing" && hearing.seats[hearing.floor].kind === "ai" && !hearing.pending_ai && <button className="zn-outline" disabled={aiBusy} onClick={() => void command("ai", {}, true)}>Continue AI turn</button>}
          {operator && hearing.pending_ai && <button className="zn-outline" disabled={busy} onClick={() => void command("abandon")}>Resolve pending turn</button>}
        </>}
      </article>
      <article className="zn-card"><h2>{MOOT_PARTS[2].title}</h2>
        {fairness && <section aria-label="Fairness check"><h3>Fairness check</h3><p>{fairnessReading(fairness)}</p>
          {operator && fairness.status !== "incomplete" && <button className="zn-outline" onClick={() => selectHearing(fairness.first_hearing === hearing?.id ? fairness.second_hearing : fairness.first_hearing)}>View other hearing</button>}
          {operator && fairness.status === "incomplete" && hearing?.id === fairness.first_hearing && <button className="zn-outline" disabled={busy} onClick={() => { swappedStart.current = null; void startSwapped(); }}>Run swapped seats</button>}
        </section>}
        {!hearing?.ruling ? <p>{MOOT_PARTS[2].empty}</p> : <><h3>{hearing.ruling.order}</h3><ol>{hearing.ruling.reasons.map((r, i) => <li key={i}>{r}</li>)}</ol><h3>What the court actually held</h3><p>{hearing.reveal ?? "The original holding will be revealed after both hearings close."}</p>
          <p>Cost: {hearing.cost_usd === null ? "unavailable" : `$${hearing.cost_usd.toFixed(6)}`}</p>
          {operator && !hearing.published && <button className="zn-primary" disabled={busy || Boolean(hearing.fairness_pair_id && (!fairness || fairness.status === "incomplete"))} onClick={() => void command("publish")}>{hearing.fairness_pair_id ? "Publish paired hearings" : "Publish"}</button>}
          {hearing.published && <a href={`/moot/${hearing.id}${hearing.region === "za" ? "?region=za" : ""}`}>Public watch page</a>}
        </>}
      </article>
    </div>
  </section>;
}


function ReasonTrace({ trace, problem }: { trace: Record<string, unknown>; problem: Hearing["problem"] }) {
  const paragraphs = [trace.opposing_argument, trace.response, trace.conclusion];
  const comparisons = Array.isArray(trace.comparisons) ? trace.comparisons as { legal_significance?: string; differences?: { explanation: string }[] }[] : [];
  const rules = Array.isArray(trace.rules) ? trace.rules as { authority_id?: string; application?: string; court_weight?: string }[] : [];
  const facts = Array.isArray(trace.facts) ? trace.facts as { id?: string; position?: string; relevance?: string }[] : [];
  return <div>{facts.map((f, i) => <p key={`fact-${i}`}><strong>{problem.fact_records.find(record => record.id === f.id)?.text}</strong> ({f.position}) {f.relevance}</p>)}
    {rules.map((r, i) => { const authority = problem.authorities.find(a => a.id === r.authority_id); return <div key={`rule-${i}`}>
      {authority && <><a href={authority.source_url}>{authority.name}</a><blockquote>{authority.quote}</blockquote></>}
      <p>{r.court_weight}: {r.application}</p></div>; })}
    {comparisons.map((c, i) => <div key={`comparison-${i}`}><p>{c.legal_significance}</p>{c.differences?.map((d, j) => <p key={j}>{d.explanation}</p>)}</div>)}
    {paragraphs.filter(p => typeof p === "string").map((p, i) => <p key={i}>{String(p)}</p>)}
    {Array.isArray(trace.uncertainty) && trace.uncertainty.filter(u => typeof u === "string").map((u, i) => <p key={`uncertainty-${i}`}>{u}</p>)}
  </div>;
}
