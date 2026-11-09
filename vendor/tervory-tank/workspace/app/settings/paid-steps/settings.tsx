"use client";
import { fetchWithDeadline as fetch } from "../../../lib/request";
import { useCallback, useEffect, useState } from "react";
import { pauseAction } from "../../control/actions";

type Mode = "ask" | "auto";
export type PaidStep = { step_id: string; product: string; kind: string; source: string; task_type?: string | null; model?: string | null; mode: Mode };
export function PaidSettings({ founder, paused: initialPaused }: { founder: boolean; paused: boolean | null }) {
  const [steps, setSteps] = useState<PaidStep[] | null>(null);
  const [modes, setModes] = useState<Record<string, Mode>>({});
  const [paused, setPaused] = useState(initialPaused);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [saved, setSaved] = useState(false);
  const load = useCallback(async () => {
    setError(false); setSaved(false); setSteps(null);
    try {
      const response = await fetch("/settings/paid-steps/api", { cache: "no-store" });
      if (!response.ok) throw new Error();
      const body = await response.json();
      if (!Array.isArray(body.steps) || body.steps.some((step: PaidStep) => typeof step.step_id !== "string" || !["ask", "auto"].includes(step.mode))) throw new Error();
      setSteps(body.steps);
      setModes(Object.fromEntries(body.steps.map((step: PaidStep) => [step.step_id, step.mode])));
    } catch { setError(true); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { setPaused(initialPaused); }, [initialPaused]);
  async function save() {
    if (!steps || busy) return;
    setBusy(true); setSaved(false); setError(false);
    try {
      for (const step of steps) {
        if (step.mode === modes[step.step_id]) continue;
        const response = await fetch("/settings/paid-steps/api", { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step_id: step.step_id, mode: modes[step.step_id] }) });
        if (!response.ok) throw new Error();
        const result = await response.json();
        if (result.step_id !== step.step_id || result.mode !== modes[step.step_id]) throw new Error();
      }
      setSteps(steps.map(step => ({ ...step, mode: modes[step.step_id] })));
      setSaved(true);
    } catch { setError(true); }
    finally { setBusy(false); }
  }
  async function togglePause() {
    if (!founder || paused === null || busy) return;
    setBusy(true); setSaved(false);
    try {
      const result = await pauseAction(!paused);
      if (result.error) throw new Error();
      setPaused(!paused);
    } catch { setError(true); }
    finally { setBusy(false); }
  }
  const groups = new Map<string, PaidStep[]>();
  for (const step of steps ?? []) {
    const task = step.task_type ?? (step.step_id.includes(".model.") ? step.step_id.split(".model.")[0] : step.step_id.slice(0, step.step_id.lastIndexOf(".")));
    const group = groups.get(task) ?? [];
    group.push(step); groups.set(task, group);
  }
  return <section className={`paid-settings ${paused ? "is-paused" : ""}`}>
    <h1>Paid steps settings</h1>
    {founder && <p className="paid-owner">Founder only</p>}
    {error ? <div className="paid-panel" role="alert"><h2>Something went wrong</h2><button className="primary paid-wide" disabled={busy} onClick={() => void load()}>Try again</button></div>
      : steps === null ? <div className="paid-panel" role="status"><h2>Loading…</h2>{[1, 2, 3].map(n => <div className="paid-skeleton" key={n} />)}</div>
      : !steps.length ? <div className="paid-panel"><h2>No paid steps yet.</h2><p>Free steps stay automatic.</p></div>
      : <>
        <div className="paid-panel"><h2>{paused ? "Paid work is paused." : "Paid work"}</h2><p>Ask / Auto is set separately for every paid step.</p>
          {founder && <button className="primary paid-wide" disabled={busy || paused === null} onClick={() => void togglePause()}>{paused ? "Resume paid work" : "Pause all paid work"}</button>}
          {paused && <p>Ask / Auto settings are kept.</p>}
          {saved && <h3 role="status">Settings saved.</h3>}
        </div>
        <p>Free steps stay automatic.</p>
        <div className="paid-groups">{Array.from(groups, ([task, items]) => <section className="paid-task" key={task}><h2>{task}</h2>
          {items.map(step => <div className="paid-panel paid-step" key={step.step_id} data-step-id={step.step_id}>
            <h3>{step.model ?? step.step_id}</h3><p>{step.source} · Cost per call: —{paused ? ` · ${modes[step.step_id] === "ask" ? "Ask" : "Auto"}` : ""}</p>
            <div className="paid-modes" role="group" aria-label={step.step_id}>{(["ask", "auto"] as const).map(mode => <button type="button" key={mode} disabled={busy}
              aria-pressed={modes[step.step_id] === mode} onClick={() => { setModes({ ...modes, [step.step_id]: mode }); setSaved(false); }}>
              {mode === "ask" ? "Ask" : "Auto"}</button>)}</div>
          </div>)}
        </section>)}</div>
        <button className="primary paid-wide" disabled={busy} aria-busy={busy} onClick={() => void save()}>Save settings</button>
      </>}
  </section>;
}
