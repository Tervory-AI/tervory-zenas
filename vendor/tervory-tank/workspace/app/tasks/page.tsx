"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { FeedbackButtons } from "../feedback-buttons";
import "./tasks.css";

type Status = "queued" | "running" | "done" | "failed";
type Task = {
  id: string;
  title: string;
  task_type: string;
  status: Status;
  result: string | null;
  error: string | null;
  ledger_id: string | null;
  created_at: string;
  model?: string | null;
  rung?: number | null;
  cost_usd?: number | string | null;
  latency_ms?: number | null;
  feedback?: "accepted" | "rejected" | null;
};

const COLUMNS: { status: Status; label: string }[] = [
  { status: "queued", label: "Queued" },
  { status: "running", label: "Running" },
  { status: "done", label: "Done" },
  { status: "failed", label: "Failed" },
];

const KINDS = [
  { value: "tier0.answer", label: "Quick answer" },
  { value: "tier0.draft", label: "Draft a message" },
  { value: "reason.brief", label: "Brief analysis" },
];

export default function TasksPage() {
  const focusedTask = useRef<string | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [title, setTitle] = useState("");
  const [request, setRequest] = useState("");
  const [kind, setKind] = useState(KINDS[0].value);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const res = await fetch("/api/tasks", { cache: "no-store" });
    if (res.ok) setTasks((await res.json()) as Task[]);
  }, []);

  // Load once, then poll while anything is still moving.
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    const moving = tasks.some((t) => t.status === "queued" || t.status === "running");
    if (!moving) return;
    const id = setInterval(refresh, 2500);
    return () => clearInterval(id);
  }, [tasks, refresh]);

  // Dashboard deep links arrive before the asynchronous task list has rendered.
  useEffect(() => {
    const focusTask = () => {
      if (!window.location.hash.startsWith("#task-") || focusedTask.current === window.location.hash) return;
      let id: string;
      try { id = decodeURIComponent(window.location.hash.slice(1)); } catch { return; }
      const target = document.getElementById(id);
      if (target) {
        focusedTask.current = window.location.hash;
        target.scrollIntoView({ block: "center" });
        target.focus({ preventScroll: true });
      }
    };
    focusTask();
    window.addEventListener("hashchange", focusTask);
    return () => window.removeEventListener("hashchange", focusTask);
  }, [tasks]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!title.trim() || !request.trim()) {
      setError("Add a title and what you need done.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/tasks", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title, request, task_type: kind }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? `HTTP ${res.status}`);
      setTitle("");
      setRequest("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tasks-page">
      <h1>Tasks</h1>
      <form onSubmit={submit} className="task-form">
        <label htmlFor="task-title">Title</label>
        <input
          id="task-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Short title, e.g. Pricing email to a supplier"
          maxLength={200}
          disabled={busy}
        />
        <label htmlFor="task-request">What you need</label>
        <textarea
          id="task-request"
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          placeholder="What do you need done?"
          rows={4}
          disabled={busy}
        />
        <div className="task-form-row">
          <label htmlFor="task-kind">Kind</label>
          <select id="task-kind" value={kind} onChange={(e) => setKind(e.target.value)} disabled={busy}>
            {KINDS.map((k) => (
              <option key={k.value} value={k.value}>{k.label}</option>
            ))}
          </select>
          <button type="submit" disabled={busy}>
            {busy ? "Queueing…" : "Add task"}
          </button>
        </div>
        {error && <p className="err">{error}</p>}
      </form>

      <div className="board">
        {COLUMNS.map((col) => {
          const items = tasks.filter((t) => t.status === col.status);
          return (
            <section key={col.status} className="col">
              <h2>
                {col.label} <span className="count">{items.length}</span>
              </h2>
              {items.length === 0 && <p className="empty">—</p>}
              {items.map((t) => (
                <article id={`task-${t.id}`} tabIndex={-1} key={t.id} className={`card ${t.status}`}>
                  <header>
                    <b>{t.title}</b>
                    <span className="kind">{KINDS.find((k) => k.value === t.task_type)?.label ?? t.task_type}</span>
                  </header>
                  {t.status === "done" && t.result && <p className="result">{t.result}</p>}
                  {t.status === "failed" && <p className="err">{t.error ?? "Failed"}</p>}
                  {t.model && (
                    <div className="meta">
                      {t.model} · rung {t.rung} · ${Number(t.cost_usd ?? 0).toFixed(5)} · {t.latency_ms} ms
                    </div>
                  )}
                  {t.status === "done" && t.ledger_id && (
                    <div className="meta">
                      <FeedbackButtons ledgerId={t.ledger_id} initial={t.feedback ?? null} />
                    </div>
                  )}
                </article>
              ))}
            </section>
          );
        })}
      </div>
    </div>
  );
}
