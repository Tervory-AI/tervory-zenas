"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { SquadLinks, SQUAD_MEMBERS } from "../squad-links";
import { RadixCard } from "./radix-card";
import { clientLabel, visibleCard } from "./presentation";
import { applyReceipt, canConfirm, dollars, readEvents, safeLink, upsert, type Attachment, type Card, type Event, type Message, type ModelOption, type Receipt, type Snapshot, type Step, type Thread } from "./contract";
import doneDot from "./assets/done.svg";
import attentionDot from "./assets/attention.svg";
import plannedDot from "./assets/planned.svg";
import "./window.css";

async function api(path: string, init: RequestInit = {}) {
  const response = await fetch(`/chats/api/${path}`, { ...init, headers: { "Content-Type": "application/json", ...init.headers }, cache: "no-store" });
  if (!response.ok) { const body = await response.json().catch(() => ({})); throw new Error(body.error ?? "Your request could not be completed."); }
  return response;
}
const problem = (error: unknown) => error instanceof Error ? error.message : "Your request could not be completed.";
const statusAsset = (status: Step["status"]) => { const asset = status === "done" ? doneDot : status === "failed" ? attentionDot : plannedDot; return typeof asset === "string" ? asset : asset.src; };
const statusLabel = { planned: "Planned", running: "Running", done: "Done", failed: "Failed" };

function CostReceipt({ receipt, founder, threadId, messageId }: { receipt: Receipt; founder: boolean; threadId: string; messageId: string }) {
  const pending = receipt.status === "pending";
  const rows = <div className="cw-receipt-rows">{pending && <p className="cw-note">Cost pending. Totals remain unavailable until the work is accounted for.</p>}{receipt.rows.map((row, index) => <a className="cw-receipt-row" key={`${row.ledger_id}-${index}`} href={`/chats/evidence/${encodeURIComponent(threadId)}/${encodeURIComponent(row.ledger_id)}`}>
    <span>{founder ? row.step_id : `Step ${index + 1}`}{row.paid_step_mode && <small>{row.paid_step_id} · {row.paid_step_mode}</small>}</span>
    {founder && <span>{row.model === "code" ? "code (no model)" : row.model}<small>{row.tokens_in ?? "—"} in · {row.tokens_out ?? "—"} out</small></span>}
    <span>Cost {dollars(row.cost_usd)}</span><span>{founder && row.baseline_model ? `${row.baseline_model} ` : "Would have cost "}{dollars(row.baseline_cost_usd)}</span><span className="cw-saving">Saved {dollars(row.saved_usd)}{row.baseline_status === "baseline pending" && <small>Comparison pending</small>}</span>
    {founder && <small>{row.ledger_id}</small>}
  </a>)}<a className="cw-receipt-total" href={`/chats/evidence/${encodeURIComponent(threadId)}`}><span>Total</span><span>Cost {dollars(receipt.totals.cost_usd)}</span><span>Would have cost {dollars(receipt.totals.baseline_cost_usd)}</span><span>Saved {dollars(receipt.totals.saved_usd)}</span>{receipt.totals.auto_cost_usd !== undefined && <span>Auto would have cost {dollars(receipt.totals.auto_cost_usd)}</span>}</a></div>;
  return <section className="cw-receipt" id={`cost-${messageId}`} aria-label="Answer cost"><div className="cw-receipt-desktop"><header><strong>Receipt{pending ? " · Cost pending" : ""}</strong><small>Each step links to its cost details</small></header>{rows}</div><details className="cw-receipt-mobile"><summary>Receipt{pending ? " · Cost pending" : ""} · {dollars(receipt.totals.cost_usd)} · would have cost {dollars(receipt.totals.baseline_cost_usd)} · saved {dollars(receipt.totals.saved_usd)}</summary>{rows}</details></section>;
}

/** Header totals belong to a selected chat; with none selected there is nothing to total. */
export function ThreadTotals({ selected, snapshot }: { selected: string | null; snapshot: Snapshot | null }) {
  if (!selected) return null;
  const evidence = `/chats/evidence/${encodeURIComponent(selected)}`;
  return <div className="cw-totals">{snapshot?.thread.receipt_status === "pending" && <span>Cost pending</span>}<a href={evidence}>Spent {dollars(snapshot?.thread.spent_usd)}</a><a className="cw-saving" href={evidence}>Saved {dollars(snapshot?.thread.saved_usd)}</a></div>;
}

export function CommandWindow({ initialThreads, models, founder, account, initialTools = [], initialError = "" }: { initialThreads: Thread[]; models: ModelOption[]; founder: boolean; account: string; initialTools?: string[]; initialError?: string }) {
  const [availableTools, setAvailableTools] = useState(initialTools);
  const [pendingAsk, setPendingAsk] = useState("");
  const [threads, setThreads] = useState(initialThreads);
  const [selected, setSelected] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [query, setQuery] = useState("");
  const [text, setText] = useState("");
  const [model, setModel] = useState("auto");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [cardBusy, setCardBusy] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [work, setWork] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState("");
  const generation = useRef(0);
  const streamAbort = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  const uncertainCards = useRef(new Set<string>());
  const oneTimeResults = useRef(new Map<string, Card["results"]>());
  const active = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const workButton = useRef<HTMLButtonElement>(null);
  const workClose = useRef<HTMLButtonElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const menuClose = useRef<HTMLButtonElement>(null);
  const clean = (value: string) => founder ? value : clientLabel(value);

  function rememberUncertain(cardId: string, uncertain: boolean) {
    if (uncertain) uncertainCards.current.add(cardId); else uncertainCards.current.delete(cardId);
    try { if (uncertain) sessionStorage.setItem(`command-uncertain:${cardId}`, "1"); else sessionStorage.removeItem(`command-uncertain:${cardId}`); } catch { /* Memory still protects this mounted window if storage is unavailable. */ }
  }
  function isUncertain(cardId: string) {
    try { if (sessionStorage.getItem(`command-uncertain:${cardId}`) === "1") uncertainCards.current.add(cardId); } catch { /* Use the in-memory guard. */ }
    return uncertainCards.current.has(cardId);
  }
  async function refreshThreads() { const rows = await (await api("threads")).json() as Thread[]; setThreads(rows); }
  async function load(id: string, version = generation.current) {
    const next = await (await api(`threads/${encodeURIComponent(id)}`)).json() as Snapshot;
    if (generation.current !== version || active.current !== id) return;
    next.cards = next.cards.map(card => {
      if (!isUncertain(card.card_id)) return card;
      if (["pending", "awaiting_confirmation"].includes(card.status ?? "pending")) return { ...card, status: "unknown", retry_safe: false, error: "Confirmation could not be verified. Do not repeat this action until its outcome is checked." };
      rememberUncertain(card.card_id, false); return card;
    });
    if (!founder) {
      let tools: string[] = [];
      try { tools = await (await api("tools")).json() as string[]; }
      catch { if (generation.current === version) setError("Tool access could not be loaded. Chat is still available."); }
      if (generation.current !== version || active.current !== id) return;
      setAvailableTools(tools);
    }
    // A one-time Confirm response stays in this mounted view only. Polling
    // never retrieves it again, and switching chats discards the local value.
    next.cards = next.cards.map(card => oneTimeResults.current.has(card.card_id)
      ? { ...card, results: oneTimeResults.current.get(card.card_id) } : card);
    setSnapshot(next); setThreads(rows => upsert(rows, next.thread, "thread_id"));
  }
  async function choose(id: string) {
    if (sendingRef.current || cardBusy) return;
    setAttachments([]);
    oneTimeResults.current.clear();
    const version = ++generation.current;
    active.current = id; setSelected(id); setSnapshot(null); setBusy(true); setError(""); setMenu(false); setRenaming(false);
    window.history.replaceState(null, "", `/chats?thread=${encodeURIComponent(id)}`);
    try { await load(id, version); } catch (err) { if (version === generation.current) setError(problem(err)); }
    finally { if (version === generation.current) setBusy(false); }
  }
  useEffect(() => {
    const params = new URL(window.location.href).searchParams;
    const ask = params.get("ask")?.slice(0, 20000);
    if (ask?.trim()) {
      // Consume before creating work so Strict Mode or reload cannot resend it.
      window.history.replaceState(null, "", "/chats");
      void newThread(ask);
    } else if (params.get("draft")) {
      const draft = params.get("draft")!;
      let cancelled = false;
      void (async () => {
        try {
          const response = await fetch(`/api/mobile/shares/${encodeURIComponent(draft)}`, { cache: "no-store" });
          if (!response.ok) throw new Error("The shared item could not be opened.");
          const saved = await response.json() as { thread_id: string; text: string; attachments: Attachment[] };
          if (cancelled) return;
          await choose(saved.thread_id);
          if (cancelled) return;
          window.history.replaceState(null, "", `/chats?draft=${encodeURIComponent(draft)}`);
          setText(saved.text); setAttachments(saved.attachments);
        } catch { if (!cancelled) setError("The shared item could not be opened. Please share it again."); }
      })();
      return () => { cancelled = true; generation.current++; streamAbort.current?.abort(); };
    } else { const id = params.get("thread"); if (id) void choose(id); }
    return () => { generation.current++; streamAbort.current?.abort(); };
    // A saved URL is loaded once; subsequent changes use choose to reject stale reads.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!selected || sending || busy || cardBusy) return;
    let stopped = false; let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState === "visible" && !sendingRef.current) {
        try { await load(selected); } catch { if (!stopped) setError("Live updates are unavailable. Your last saved work is still shown."); }
      }
      if (!stopped) timer = setTimeout(tick, 4000);
    };
    timer = setTimeout(tick, 4000);
    return () => { stopped = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, sending, busy, cardBusy]);
  useEffect(() => {
    if (!pendingAsk || !snapshot || busy || sendingRef.current) return;
    const firstMessage = pendingAsk; setPendingAsk("");
    void send(undefined, firstMessage);
    // The consumed dashboard prompt is sent once, never retried automatically.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingAsk, snapshot, busy]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [snapshot?.messages.length, snapshot?.messages.at(-1)?.content, sending]);
  useEffect(() => { if (work) workClose.current?.focus(); }, [work]);
  useEffect(() => { if (menu) menuClose.current?.focus(); }, [menu]);
  function closeWork() { setWork(false); requestAnimationFrame(() => workButton.current?.focus()); }
  function closeMenu() { setMenu(false); requestAnimationFrame(() => menuButton.current?.focus()); }

  async function newThread(firstMessage?: string) {
    if (busy || cardBusy || sendingRef.current) return;
    setBusy(true); setError("");
    try { const created = await (await api("threads", { method: "POST", body: "{}" })).json(); await refreshThreads(); await choose(created.thread_id); setText(firstMessage ?? ""); setAttachments([]); if (firstMessage) setPendingAsk(firstMessage); }
    catch (err) { setError(problem(err)); } finally { setBusy(false); }
  }
  async function rename(event: FormEvent) {
    event.preventDefault(); if (!selected || busy || cardBusy) return;
    ++generation.current; setBusy(true); setError("");
    try { await api(`threads/${selected}`, { method: "PATCH", body: JSON.stringify({ title }) }); await load(selected); await refreshThreads(); setRenaming(false); }
    catch (err) { setError(problem(err)); } finally { setBusy(false); }
  }
  async function decide(card: Card, action: "confirm" | "cancel") {
    if (cardBusy || !selected || sendingRef.current || !canConfirm(card) || (!founder && (!card.tool || !availableTools.includes(card.tool)))) return;
    const id = selected;
    ++generation.current; setCardBusy(card.card_id); setError("");
    if (action === "confirm") {
      rememberUncertain(card.card_id, true);
      setSnapshot(current => current && ({ ...current, cards: upsert(current.cards, { ...card, status: "executing", retry_safe: false }, "card_id") }));
    }
    try {
      const saved = await (await api(`cards/${encodeURIComponent(card.card_id)}`, { method: "POST", body: JSON.stringify({ action }) })).json() as Card;
      if (saved.tool === "market_lab.alerts.webhook" && saved.status === "confirmed"
        && saved.results?.some(row => row.label === "Response")) oneTimeResults.current.set(saved.card_id, saved.results);
      if (saved.card_id === card.card_id && saved.status && !["pending", "awaiting_confirmation"].includes(saved.status)) {
        rememberUncertain(card.card_id, false);
        setSnapshot(current => current && ({ ...current, cards: upsert(current.cards, saved, "card_id") }));
      }
      await load(id);
    } catch (err) {
      if (uncertainCards.current.has(card.card_id)) setSnapshot(current => current && ({ ...current, cards: upsert(current.cards, { ...card, status: "unknown", retry_safe: false, error: "Confirmation could not be verified. Do not repeat this action until its outcome is checked." }, "card_id") }));
      setError(problem(err));
    } finally { setCardBusy(null); }
  }
  async function attach(files: FileList | null) {
    if (!files || !selected || busy) return;
    const combinedSize = attachments.reduce((n, file) => n + (file.size ?? 0), 0) + Array.from(files).reduce((n, file) => n + file.size, 0);
    if (attachments.length + files.length > 5 || combinedSize > 5 * 1024 * 1024) { setError("Attach up to five files, totaling no more than 5 MB."); return; }
    const id = selected; const version = generation.current;
    setBusy(true); setError("");
    try {
      // Sequential uploads keep successful files usable if a later file fails.
      for (const file of Array.from(files)) {
        let addition: Attachment;
        const isText = file.type.startsWith("text/") || /\.(txt|md|csv|tsv|json|xml|html|log|yaml|yml)$/i.test(file.name);
        if (isText) {
          const text = new TextDecoder("utf-8", { fatal: true }).decode(await file.arrayBuffer());
          if (text.length > 200000 || text.includes("\0")) throw new Error("Attach UTF-8 text files with no more than 200,000 characters each.");
          addition = { name: file.name, content_type: file.type || "text/plain", size: file.size, text, model_readable: true };
        } else {
          const saved = await (await api(`threads/${id}/attachments?${new URLSearchParams({ name: file.name })}`, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file })).json();
          addition = { name: saved.name, content_type: file.type || "application/octet-stream", size: saved.size, attachment_id: saved.attachment_id, model_readable: saved.model_readable === true };
        }
        if (generation.current !== version || active.current !== id) return;
        setAttachments(current => [...current, addition]);
      }
    } catch (err) { if (generation.current === version) setError(problem(err)); } finally { setBusy(false); }
  }
  async function send(event?: FormEvent, firstMessage?: string) {
    event?.preventDefault(); const requestText = firstMessage ?? text; if (!requestText.trim() || sendingRef.current || busy || cardBusy || !snapshot || !selected) return;
    const id = selected; const files = attachments;
    window.history.replaceState(null, "", `/chats?thread=${encodeURIComponent(id)}`);
    sendingRef.current = true; setSending(true); setError("");
    // Invalidate any snapshot already in flight before the new stream starts.
    const version = ++generation.current;
    const controller = new AbortController(); streamAbort.current = controller;
    const localId = `pending-${Date.now()}`; let completed = false; let lastSeq = snapshot.seq ?? -1; let rejected = false;
    setSnapshot(current => current && ({ ...current, messages: [...current.messages, { message_id: `${localId}-user`, role: "user", content: requestText, attachments: files }, { message_id: localId, role: "assistant", content: "" }] }));
    try {
      const response = await api(`threads/${id}/turn`, { method: "POST", body: JSON.stringify({ text: requestText, model: founder ? model : "auto", attachments: files }), signal: controller.signal });
      if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) throw new Error("The live response is unavailable. Reopen this chat before sending again.");
      setText(""); setAttachments([]);
      const receive = (event: Event) => {
        if (generation.current !== version) return;
        if (event.seq !== undefined) { if (event.seq <= lastSeq) return; lastSeq = event.seq; }
        const data = event.data;
        if (event.type === "error") { rejected = true; throw new Error(String(data.message)); }
        if (event.type === "done" || (event.type === "step" && data.phase_finished === true)) completed = true;
        setSnapshot(current => {
          if (!current) return current;
          if (event.type === "step") return { ...current, messages: data.phase_finished === true ? current.messages.filter(message => message.message_id !== localId) : current.messages, steps: upsert(current.steps, data as unknown as Step, "step_id") };
          if (event.type === "card" && visibleCard(data as unknown as Card, founder)) return { ...current, cards: upsert(current.cards, data as unknown as Card, "card_id") };
          if (event.type === "receipt") return applyReceipt(current, data as unknown as Receipt, localId);
          if (event.type === "token") return { ...current, messages: current.messages.map(message => message.message_id !== localId ? message : { ...message, content: (data.replace === true ? "" : message.content) + String(data.text ?? "") }) };
          return current;
        });
      };
      // A dropped GET/stream can be replayed; a turn POST must never be retried.
      try { await readEvents(response.body, receive, controller.signal); }
      catch (err) { if (rejected || controller.signal.aborted) throw err; }
      if (!completed && !controller.signal.aborted) {
        const replay = await api(`threads/${id}/events?after=${Math.max(0, lastSeq)}`, { signal: controller.signal });
        if (replay.body && replay.headers.get("content-type")?.includes("text/event-stream")) await readEvents(replay.body, receive, controller.signal);
      }
      if (!completed) throw new Error("The connection ended before the answer finished. Reopen this chat before sending again.");
      await load(id, version); await refreshThreads();
    } catch (err) {
      // An interrupted preview is never presented as a saved, complete answer,
      // even if refreshing the authoritative thread also fails.
      setSnapshot(current => current && ({ ...current, messages: current.messages.filter(message => message.message_id !== localId) }));
      if (!controller.signal.aborted) { setError(problem(err)); await load(id, version).catch(() => {}); }
    } finally { sendingRef.current = false; setSending(false); streamAbort.current = null; }
  }

  const done = snapshot?.steps.filter(step => step.status === "done").length ?? 0;
  const steps = snapshot?.steps ?? [];
  const cards = snapshot?.cards.filter(card => visibleCard(card, founder)) ?? [];
  const lastAnswer = snapshot?.messages.filter(message => message.role === "assistant").at(-1)?.message_id;
  const cardLabels: Record<string, string> = { pending: "NEEDS YOUR OK", awaiting_confirmation: "NEEDS YOUR OK", executing: "RUNNING", unknown: "OUTCOME UNKNOWN", failed: "FAILED", confirmed: "CONFIRMED", cancelled: "CANCELLED" };
  const confirmation = cards.map(card => <section className="cw-confirm" key={card.card_id} data-status={card.status ?? "pending"}><strong className="cw-eyebrow">{cardLabels[card.status ?? "pending"] ?? "STATUS UNAVAILABLE"}</strong><p>{clean(card.summary)}</p>
    {!!card.targets?.length && <dl className="cw-card-details">{card.targets.map((target, index) => <div key={index}><dt>{target.label}</dt><dd>{safeLink(target.href) ? <a href={safeLink(target.href)} target="_blank" rel="noreferrer">{target.value}</a> : target.value}</dd></div>)}</dl>}
    {card.cost_estimate_usd !== undefined && <p className="cw-meta">Estimated {dollars(card.cost_estimate_usd)}</p>}
    {card.radix && <RadixCard view={card.radix} status={card.status} disabled={sending || busy || !!cardBusy} onPrepare={message => { setText(message); document.getElementById("cw-message")?.focus(); }} />}
    {!!card.results?.length && <dl className="cw-card-details" aria-label="Action result">{card.results.map((result, index) => <div key={index}><dt>{result.label}</dt><dd>{safeLink(result.href) ? <a href={safeLink(result.href)} target="_blank" rel="noreferrer">{result.value}</a> : result.value}</dd></div>)}</dl>}
    {card.error && ["failed", "unknown"].includes(card.status ?? "") && <p className="cw-note">{clean(card.error)}</p>}
    {card.status === "unknown" && <p className="cw-note">Do not repeat this action until its outcome is checked. Retrying is disabled.</p>}
    {card.status === "executing" && <p className="cw-note">The action is running. Its result will appear here.</p>}
    {card.status === "failed" && !card.retry_safe && <p className="cw-note">This action cannot safely be retried.</p>}
    {card.receipt_status === "pending" && <p className="cw-note">Cost pending.</p>}
    {canConfirm(card) && (founder || (card.tool && availableTools.includes(card.tool))) && <><p className="cw-note">{card.status === "failed" ? "The service verified that the action did not start. You can confirm a safe retry." : "Nothing runs until you confirm."}</p><div className="cw-actions"><button className="cw-primary" disabled={!!cardBusy || sending || busy} onClick={() => void decide(card, "confirm")}>{cardBusy === card.card_id ? "Saving…" : card.status === "failed" ? "Retry safely" : "Confirm"}</button><button disabled={!!cardBusy || sending || busy} onClick={() => void decide(card, "cancel")}>Cancel</button></div></>}
  </section>);
  return <div className={`command-window${menu ? " cw-menu-open" : ""}${work ? " cw-work-open" : ""}`} onKeyDown={event => { if (event.key === "Escape") { if (work) closeWork(); if (menu) closeMenu(); } }}>
    <a className="cw-skip" href="#cw-message">Skip to message</a>
    <header className="cw-mobile-top"><img src="/brand/tervory-logo-reversed.png" alt="Tervory" /><button ref={menuButton} aria-expanded={menu} aria-controls="cw-sidebar" onClick={() => setMenu(true)}>Menu</button></header>
    <aside className="cw-sidebar" id="cw-sidebar" aria-label="Chats and navigation" inert={work ? true : undefined}>
      <div className="cw-brand"><img src="/brand/tervory-logo-reversed.png" alt="Tervory" /><button className="cw-mobile-only" ref={menuClose} onClick={closeMenu}>Close</button></div>
      <a className="cw-account" href="/onboarding">{account}</a>
      <a href="/access?section=checks">Needs a person</a>
      <nav aria-label="Workspace"><a href={founder ? "https://app.tervory.com/control" : "https://app.tervory.com/"}>Dashboard</a><a href="/matters">Projects</a><a href="/chats" aria-current="page">Chats</a>{founder ? <SquadLinks active founder /> : SQUAD_MEMBERS.filter(member => !member.founderOnly && availableTools.some(tool => tool.startsWith(`${member.href.slice(1).replace(/-/g, "_")}.`))).map(member => <a key={member.href} href={member.href}>{member.label}</a>)}</nav>
      <p className="cw-eyebrow">CHATS</p><button className="cw-new" disabled={busy || sending || !!cardBusy} onClick={() => void newThread()}>+ New chat</button>
      <input type="search" aria-label="Search chats" placeholder="Search chats" value={query} onChange={event => setQuery(event.target.value)} />
      <div className="cw-threads">{threads.filter(thread => thread.title.toLowerCase().includes(query.toLowerCase())).map(thread => <button key={thread.thread_id} className={selected === thread.thread_id ? "selected" : ""} disabled={sending || busy || !!cardBusy} aria-current={selected === thread.thread_id ? "page" : undefined} onClick={() => void choose(thread.thread_id)}><span>{thread.title}</span><small>Spent {dollars(thread.spent_usd)} · saved {dollars(thread.saved_usd)}</small></button>)}{!threads.length && <p className="cw-note">Your saved chats will appear here.</p>}</div>
      <nav className="cw-bottom-nav">{founder && <a href="/ledger">Ledger</a>}<a href="/billing">Billing</a><a href="/data">Files</a><a href="/onboarding">Settings</a></nav>
    </aside>
    <main className="cw-chat" inert={menu || work ? true : undefined}>
      <header className="cw-thread-header"><div><h1>{snapshot ? snapshot.thread.title : selected ? "Loading chat…" : "A new conversation"}</h1>{snapshot && <button className="cw-rename" disabled={sending || busy || !!cardBusy} onClick={() => { setTitle(snapshot.thread.title); setRenaming(true); }}>Rename</button>}</div><button className="cw-work-tab" ref={workButton} onClick={() => setWork(true)} aria-expanded={work}>Work {done}/{steps.length}</button><ThreadTotals selected={selected} snapshot={snapshot} /></header>
      {renaming && <form className="cw-rename-form" onSubmit={rename}><label>Chat name<input autoFocus value={title} maxLength={160} required onChange={event => setTitle(event.target.value)} /></label><button disabled={busy}>Save</button><button type="button" onClick={() => setRenaming(false)}>Cancel</button></form>}
      {error && <div className="cw-error" role="alert">{error}<button disabled={sending || busy} onClick={() => { setError(""); void (selected ? load(selected) : refreshThreads()).catch(err => setError(problem(err))); }}>Reload</button></div>}
      <div className="cw-messages" aria-label="Conversation" aria-busy={sending || busy}>
        {!selected && <section className="cw-empty"><h2>What would you like to do?</h2><p>Start a chat to ask a question or give your team some work. Your conversation and progress stay together.</p><button disabled={busy} onClick={() => void newThread()}>Start a new chat</button></section>}
        <span id="cw-costs" className="cw-anchor" />
        {snapshot?.messages.map(message => <article key={message.message_id} className={`cw-message cw-${message.role}`}><div className="cw-message-text">{message.content || (sending && message.role === "assistant" ? "Working…" : "")}</div>{message.attachments?.length ? <ul className="cw-files">{message.attachments.map((file, i) => <li key={i}>{message.message_id.startsWith("pending-") ? file.name : <a href={`/chats/api/threads/${encodeURIComponent(selected!)}/files/${encodeURIComponent(message.message_id)}/${i}`} download>{file.name}</a>}{file.model_readable === false && <small> Stored only · cannot be read in this chat</small>}</li>)}</ul> : null}{message.role === "assistant" && (message.receipt ? <CostReceipt receipt={message.receipt} founder={founder} threadId={selected!} messageId={message.message_id} /> : <p className="cw-note">{sending ? "Cost details arrive when this answer is complete." : "Cost details are not available for this answer."}</p>)}{message.message_id === lastAnswer && confirmation}</article>)}
        {!lastAnswer && confirmation}{snapshot?.job_receipts?.map((receipt, index) => <section key={index}><h2>Work cost</h2>{receipt.step_id && <p className="cw-note">{clean(steps.find(step => step.step_id === receipt.step_id)?.label ?? cards.find(card => card.card_id === receipt.step_id)?.summary ?? (founder ? receipt.step_id : "Saved work"))}</p>}<CostReceipt receipt={receipt} founder={founder} threadId={selected!} messageId={`work-${index}`} /></section>)}<div ref={bottom} />
      </div>
      <form className="cw-composer" onSubmit={send}><div className="cw-input"><textarea id="cw-message" aria-label="Message Tervory" placeholder="Ask Tervory, or describe a change to build…" maxLength={20000} value={text} disabled={!snapshot || sending} onChange={event => setText(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} /><label className="cw-attach" title="Text can be read; other formats are stored for download only">Attach<input type="file" multiple aria-label="Attach files" disabled={!snapshot || busy || sending} onChange={event => { void attach(event.target.files); event.currentTarget.value = ""; }} /></label><button className="cw-send" disabled={!snapshot || !text.trim() || sending || busy || !!cardBusy}>{sending ? "Sending…" : "Send"}</button></div><div className="cw-composer-meta"><label>{founder ? "Model" : "Answer option"}<select aria-label={founder ? "Model" : "Answer option"} value={model} disabled={sending} onChange={event => setModel(event.target.value)}><option value="auto">Auto</option>{(founder ? models : []).map(option => <option key={option.value} value={option.value}>{option.label} · {dollars(option.inputPerM)} in / {dollars(option.outputPerM)} out per million tokens</option>)}</select></label><small>Auto chooses the cheapest proven option. Text can be read; other formats are stored only.</small></div>{!!attachments.length && <ul className="cw-files">{attachments.map((file, i) => <li key={i}>{file.name}{file.model_readable === false && <small> Stored only · cannot be read in this chat</small>} <button type="button" aria-label={`Remove ${file.name}`} disabled={sending} onClick={() => setAttachments(files => files.filter((_, index) => index !== i))}>×</button></li>)}</ul>}</form>
    </main>
    <aside className="cw-work" aria-label="Work" inert={menu ? true : undefined}><header><h2>Work</h2><span className="cw-meta">{done} of {steps.length} done</span><button className="cw-mobile-only" ref={workClose} aria-label="Close work" onClick={closeWork}>×</button></header>{!steps.length && <p className="cw-note">The steps for this chat will appear here when work begins.</p>}<ol>{steps.map(step => <li className={`cw-step cw-step-${step.status}`} key={step.step_id}><img className="cw-status-dot" src={statusAsset(step.status)} alt="" /><div><strong>{clean(step.label)}</strong><small>{clean(step.actor)}</small>{safeLink(step.link) && <a href={safeLink(step.link)} target="_blank" rel="noreferrer">Open result</a>}</div><span className="cw-step-state">{statusLabel[step.status]}</span></li>)}</ol><p className="cw-live" role="status">{sending ? "Receiving your answer…" : busy ? "Loading…" : "Progress is saved with this chat."}</p></aside>
  </div>;
}
