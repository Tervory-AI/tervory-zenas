"use client";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ApproveAllReady } from "../skala/approve-all";
import { pauseAction, switchAction, workersPauseAction } from "./actions";
import { count, sourceLink, type Candidate } from "./values";

export function ControlActions({ paused }: { paused: boolean }) {
  const [pending, start] = useTransition();
  const [failed, setFailed] = useState(false);
  const router = useRouter();
  return <button type="button" className="control-button danger" disabled={pending} aria-busy={pending} aria-invalid={failed || undefined} onClick={() => {
    start(async () => {
      const result = await pauseAction(!paused);
      setFailed(Boolean(result.error));
      router.refresh();
    });
  }}>{paused ? "Resume paid work" : "Pause all paid work"}</button>;
}

export function WorkersActions({ paused }: { paused: boolean | null }) {
  const [pending, start] = useTransition();
  const [failed, setFailed] = useState(false);
  const router = useRouter();
  return <button type="button" className="control-button" disabled={pending || paused == null} aria-busy={pending} aria-invalid={failed || undefined} onClick={() => {
    start(async () => {
      const result = await workersPauseAction(!paused);
      setFailed(Boolean(result.error));
      router.refresh();
    });
  }}>{paused == null ? "Workers pause unavailable" : paused ? "Resume workers" : "Pause workers"}</button>;
}

function Switch({ taskType }: { taskType: string }) {
  const [pending, start] = useTransition();
  const [failed, setFailed] = useState(false);
  const router = useRouter();
  return <button type="button" className="control-button green" disabled={pending} aria-busy={pending} aria-invalid={failed || undefined} onClick={() => {
    start(async () => {
      const result = await switchAction(taskType);
      setFailed(Boolean(result.error));
      router.refresh();
    });
  }}>Switch</button>;
}

export function NeedsYou({ skills, models, checks }: { skills: number; models: Candidate[]; checks: number }) {
  const router = useRouter();
  // Local to this mounted visit. A refresh after an action preserves Later; a new visit resets it.
  const [later, setLater] = useState<string[]>([]);
  const visible = models.filter(row => !later.includes(row.taskType));
  const total = Number(skills > 0) + visible.length + Number(checks > 0);
  return <section className="control-card control-needs">
    <div className="control-head"><h2>Needs you</h2>{total > 0 && <a className="control-needs-count" href="#control-needs-rows">{total}</a>}</div>
    {total === 0 ? <p className="control-empty">Nothing needs you.</p> : <div id="control-needs-rows" className="control-needs-rows">
      {skills > 0 && <div className="control-decision">
        <a className="control-decision-copy" href={sourceLink("skills")}><span className="control-number">{skills}</span> skills passed their tests</a>
        <ApproveAllReady label="Approve all" className="control-button primary" onApproved={async () => { router.refresh(); }} />
      </div>}
      {visible.map(row => <div className="control-decision" key={row.taskType}>
        <a className="control-decision-copy" href={sourceLink("models", { task: row.taskType })}>Cheaper model proven for {row.taskLabel} · saves <span className="control-number">${count(Math.round(row.monthlySaving))}</span>/mo</a>
        <Switch taskType={row.taskType} />
        <button type="button" className="control-button" onClick={() => setLater(previous => [...previous, row.taskType])}>Later</button>
      </div>)}
      {checks > 0 && <div className="control-decision">
        <a className="control-decision-copy" href={sourceLink("checks")}>Daily check: <span className="control-number">{checks}</span> examples</a>
        <a className="control-button primary" href="/skala/gold">Start</a>
      </div>}
    </div>}
  </section>;
}
