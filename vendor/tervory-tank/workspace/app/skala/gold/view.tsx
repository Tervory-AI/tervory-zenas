"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Skill = { agent: string; skill_id: string; checked: number; target: number; holdout: number; candidates: number; last_score: number | null; trend: number | null; ran_at: string | null };
type Summary = { skills: Skill[]; pairs: Record<string, number>; region: string };
type Example = { id: string; input: unknown; expected: Record<string, unknown>; source: string; auto_label?: boolean; label_source?: string };
const agents = ["tank", "base", "tiger", "market_lab", "zenas", "radix", "tisan", "rig"];
const names: Record<string, string> = { tank: "Tank", base: "Base", tiger: "Tiger", market_lab: "Market Lab", zenas: "Zenas", radix: "Radix", tisan: "Tisan", rig: "RIG" };
const pct = (n: number | null) => n === null ? "Not tested" : `${(n * 100).toFixed(1)}%`;

export function GoldView({ founder }: { founder: boolean }) {
  const [scope, setScope] = useState("platform");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [agent, setAgent] = useState(founder ? "base" : "zenas");
  const [skill, setSkill] = useState("");
  const [example, setExample] = useState<Example | null>(null);
  const [after, setAfter] = useState("");
  const [revision, setRevision] = useState(0);
  const [correction, setCorrection] = useState("");
  const [correcting, setCorrecting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const correctionRef = useRef<HTMLTextAreaElement>(null);
  const saving = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setSummary(null);
    fetch(`/api/skala/gold?scope=${scope}`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error();
      setSummary(await response.json());
    }).catch(() => { if (!controller.signal.aborted) setError("Training totals are unavailable. Try reloading."); });
    return () => controller.abort();
  }, [scope, revision]);

  const skills = summary?.skills.filter(s => s.agent === agent) ?? [];
  useEffect(() => {
    if (summary && !summary.skills.some(s => s.agent === agent && s.skill_id === skill)) {
      setSkill(summary.skills.find(s => s.agent === agent)?.skill_id ?? "");
      setAfter("");
    }
  }, [summary, agent, skill]);

  useEffect(() => {
    const controller = new AbortController();
    setExample(null); setCorrecting(false); setCorrection("");
    if (!skill) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    const query = new URLSearchParams({ scope, view: "next", agent, skill });
    if (after) query.set("after", after);
    fetch(`/api/skala/gold?${query}`, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error();
      setExample(await response.json());
    }).catch(() => { if (!controller.signal.aborted) setError("The next example could not be loaded."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [scope, agent, skill, after, revision]);

  const submit = useCallback(async (action: "right" | "wrong" | "skip") => {
    if (!example || saving.current || loading) return;
    if (action === "skip") { setAfter(example.id); setNotice("Skipped. It stays in the review queue."); return; }
    if (action === "wrong" && !correcting) {
      setCorrection(JSON.stringify(example.expected, null, 2)); setCorrecting(true);
      requestAnimationFrame(() => correctionRef.current?.focus()); return;
    }
    let corrected: Record<string, unknown> | undefined;
    if (action === "wrong") {
      try {
        corrected = JSON.parse(correction);
        if (!corrected || typeof corrected !== "object" || Array.isArray(corrected) || !Object.keys(corrected).length) throw new Error();
      } catch { setError("Enter the corrected answer as a non-empty JSON object."); return; }
    }
    saving.current = true; setBusy(true); setError("");
    try {
      const response = await fetch(`/api/skala/gold?scope=${scope}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id: example.id, action, correction: corrected }) });
      if (!response.ok) throw new Error();
      setNotice(action === "right" ? "Checked. Thank you." : "Wrong answer rejected. The correction is queued for a separate check.");
      setAfter(""); setExample(null); setRevision(v => v + 1);
    } catch { setError("The check could not be saved. Reload before trying again."); }
    finally { saving.current = false; setBusy(false); }
  }, [example, loading, correcting, correction, scope]);

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
      const target = event.target as HTMLElement;
      if (target.closest("input,textarea,select,button") || target.isContentEditable) return;
      const action = ({ r: "right", w: "wrong", s: "skip" } as const)[event.key.toLowerCase() as "r" | "w" | "s"];
      if (action) { event.preventDefault(); void submit(action); }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, [submit]);

  const chosen = skills.find(s => s.skill_id === skill);
  return <main className="gold-page">
    <header className="gold-header"><a href="/skala">← Skala</a><span>SKALA / TRAINING</span></header>
    <section className="gold-intro"><p className="gold-eyebrow">THE SQUAD, CHECKED BY PEOPLE</p><h1>Better answers start here<span>.</span></h1>
      <p>Check real examples, follow re-test scores, and build the squad’s training collection.</p>
      <label className="gold-scope">Examples <select value={scope} disabled={busy} onChange={e => { setScope(e.target.value); setSkill(""); setAfter(""); setError(""); }}>
        <option value="platform">Shared skill examples</option><option value="tenant">Current workspace examples</option>
      </select></label><p className="gold-meta">{summary ? `Stored in ${summary.region === "za" ? "South Africa" : "United States"}` : "Loading training totals…"}</p>
    </section>
    <section aria-labelledby="squad-title"><h2 id="squad-title">Squad training</h2><div className="gold-cards">
      {(founder ? agents : ["zenas"]).map(name => {
        const rows = summary?.skills.filter(s => s.agent === name) ?? [];
        const latest = rows.filter(s => s.ran_at).sort((a, b) => (b.ran_at ?? "").localeCompare(a.ran_at ?? ""))[0];
        return <article className="gold-card" key={name}><h3>{names[name]}</h3>
          <p className="gold-count">{summary ? rows.reduce((n, s) => n + s.checked, 0) : "—"}<span> / {Math.max(rows.length, 1) * 100} checked</span></p>
          <dl><div><dt>Latest skill score</dt><dd>{pct(latest?.last_score ?? null)}</dd></div>
            <div><dt>Change</dt><dd>{latest?.trend == null ? "—" : `${latest.trend > 0 ? "+" : ""}${(latest.trend * 100).toFixed(1)} points`}</dd></div>
            <div><dt>Training pairs</dt><dd>{summary ? summary.pairs[name] ?? 0 : "—"}</dd></div>
            <div><dt>Held back</dt><dd>{summary ? rows.reduce((n, s) => n + s.holdout, 0) : "—"}</dd></div></dl>
          {latest && <small>{latest.skill_id}</small>}
        </article>;
      })}
    </div></section>
    <section className="gold-review" aria-labelledby="review-title"><div className="gold-review-heading"><div><p className="gold-eyebrow">ONE EXAMPLE AT A TIME</p><h2 id="review-title">Sample queue</h2></div>
      <p className="gold-meta">Ten machine-labelled examples per source for each skill, plus every judgement example.<br />A source counts after 9 of 10 checks agree.</p></div>
      <nav aria-label="Skills closest to 100" className="gold-filters">
        {summary?.skills.filter(s => s.candidates > 0).map(s => <button key={s.skill_id} disabled={busy}
          onClick={() => { setAgent(s.agent); setSkill(s.skill_id); setAfter(""); }}>
          {s.skill_id} · {s.checked}/100
        </button>)}
      </nav>
      <div className="gold-filters"><label>Agent<select value={agent} disabled={busy} onChange={e => { setAgent(e.target.value); setSkill(""); setAfter(""); }}>
        {(founder ? agents : ["zenas"]).map(name => <option key={name} value={name}>{names[name]}</option>)}</select></label>
        <label>Skill<select value={skill} disabled={busy || !skills.length} onChange={e => { setSkill(e.target.value); setAfter(""); }}>
          {!skills.length && <option value="">No examples yet</option>}{skills.map(s => <option key={s.skill_id} value={s.skill_id}>{s.skill_id}</option>)}</select></label>
        {chosen && <p className="gold-meta">{chosen.checked} / 100 checked · {chosen.holdout} held back<br />Last score: {pct(chosen.last_score)}</p>}
      </div>
      {error && <p role="alert" className="gold-error">{error} <button onClick={() => { setError(""); setRevision(v => v + 1); }}>Reload</button></p>}
      <p role="status" className="gold-notice">{notice}</p>
      {loading ? <p className="gold-empty">Loading the next example…</p> : example ? <>
        <div className="gold-example"><section><h3>Input</h3><pre>{JSON.stringify(example.input, null, 2)}</pre></section>
          <section><h3>Expected answer</h3><pre>{JSON.stringify(example.expected, null, 2)}</pre></section></div>
        {correcting && <label className="gold-correction">Corrected answer (JSON)<textarea ref={correctionRef} value={correction} onChange={e => setCorrection(e.target.value)} rows={8} disabled={busy} /><span>The original will be rejected. This correction needs a separate check.</span></label>}
        <div className="gold-actions"><button className="gold-primary" disabled={busy || correcting} onClick={() => void submit("right")}>Right <kbd>R</kbd></button>
          <button disabled={busy} onClick={() => void submit("wrong")}>{correcting ? "Save correction" : "Wrong"} {!correcting && <kbd>W</kbd>}</button>
          <button disabled={busy} onClick={() => void submit("skip")}>Skip <kbd>S</kbd></button>
          {correcting && <button disabled={busy} onClick={() => setCorrecting(false)}>Cancel correction</button>}
          <span className="gold-meta">{busy ? "Saving…" : `${agent === "zenas" ? "Lawyer check · " : ""}Source: ${example.label_source ?? example.source.replaceAll("_", " ")}`}</span></div>
      </> : <div className="gold-empty"><h3>{after ? "You’ve reached the end of this queue." : "No examples waiting for this skill."}</h3>
        <p>{after ? "Skipped examples are still available when you start again." : "Existing skill examples and accepted work appear here as they arrive."}</p>
        {after && <button onClick={() => setAfter("")}>Review skipped examples</button>}</div>}
    </section>
    <footer className="gold-meta">Weekly re-tests use checked examples only. Held-back examples stay out of weekly runs and training exports.</footer>
  </main>;
}
