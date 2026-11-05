"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import type { RadixRecord, Reference } from "./request-service";

type Message = { role: "user" | "assistant"; content: string; sources?: Reference[] };
const endpoint = "/radix/requests";
// Keep the request comfortably below the server limit even after long replies.
function recentHistory(thread: Message[]) {
  let remaining = 32000;
  return thread.slice(-10).reverse().map(({ role, content }) => {
    const text = content.slice(0, Math.min(12000, remaining));
    remaining -= text.length;
    return { role, content: text };
  }).filter((message) => message.content.length > 0).reverse();
}
async function read(response: Response) {
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.error === "string" ? data.error : "Radix could not complete that request.");
  return data;
}

export function RadixDesk() {
  const [query, setQuery] = useState("");
  const [records, setRecords] = useState<RadixRecord[]>([]);
  const [recordPage, setRecordPage] = useState(0);
  const pageIndex = Math.min(recordPage, Math.max(0, Math.ceil(records.length / 5) - 1));
  const [limited, setLimited] = useState(false);
  const [loading, setLoading] = useState(true);
  const [recordError, setRecordError] = useState("");
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<RadixRecord | null>(null);
  const [reference, setReference] = useState<Reference | null>(null);
  const [thread, setThread] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const sending = useRef<AbortController | null>(null);
  const reading = useRef<AbortController | null>(null);
  const replyEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setLoading(true);
      setRecordError("");
      try {
        const data = await read(await fetch(endpoint + "?q=" + encodeURIComponent(query), { cache: "no-store", signal: controller.signal }));
        if (!controller.signal.aborted) { setRecords(data.records); setLimited(data.limited); }
      } catch {
        if (!controller.signal.aborted) { setRecords([]); setRecordError("Your Radix records could not load. Try again."); }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, revision]);
  useEffect(() => () => { sending.current?.abort(); reading.current?.abort(); }, []);

  async function openRecord(id: string) {
    reading.current?.abort();
    const controller = new AbortController();
    reading.current = controller;
    setRecordError("");
    setSelected(null);
    try {
      const data = await read(await fetch(endpoint + "?id=" + encodeURIComponent(id), { cache: "no-store", signal: controller.signal }));
      if (!controller.signal.aborted) setSelected(data.record);
    } catch {
      if (!controller.signal.aborted) setRecordError("That Radix record could not load. Try again.");
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    const controller = new AbortController();
    sending.current = controller;
    setBusy(true); setError(""); setProgress("Sending request…");
    let accepted = false;
    try {
      const data = await read(await fetch(endpoint, {
        method: "POST", headers: { "content-type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({ text, history: recentHistory(thread), referenceId: reference?.id }),
      }));
      accepted = true;
      setThread((messages) => [...messages, { role: "user", content: text }]);
      setDraft(""); setReference(null); setRevision((value) => value + 1);
      let record: RadixRecord = data.record;
      const deadline = Date.now() + 90000;
      while (record.status === "queued" || record.status === "running") {
        setProgress(record.status === "queued" ? "Your request is queued." : "Radix is replying…");
        if (Date.now() > deadline) {
          setProgress("Your request is still running. Find it in recent Radix records.");
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (controller.signal.aborted) return;
        const next = await read(await fetch(endpoint + "?id=" + encodeURIComponent(record.id), { cache: "no-store", signal: controller.signal }));
        record = next.record;
      }
      if (record.status === "failed") throw new Error("Radix could not answer this request. It is saved in your records; you can send a new request.");
      if (!record.reply) throw new Error("The request finished without a reply. Check its record before trying again.");
      setThread((messages) => [...messages, { role: "assistant", content: record.reply!, sources: data.sources }]);
      setProgress("");
      requestAnimationFrame(() => replyEnd.current?.scrollIntoView({ block: "nearest" }));
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof Error ? cause.message : "The request could not be sent.");
        setProgress(accepted ? "Your request was saved. Check its record for the latest status." : "Not sent. Your text is still in the box.");
      }
    } finally {
      if (!controller.signal.aborted) { setBusy(false); setRevision((value) => value + 1); }
    }
  }

  return <div className="rx-desk">
    <section className="rx-card rx-record-search" aria-labelledby="rx-record-search-title">
      <div className="rx-finding-heading"><h2 id="rx-record-search-title">Search Radix records</h2>
        <button type="button" className="rx-text-button" onClick={() => setRevision((value) => value + 1)}>Refresh records</button></div>
      <label className="rx-search-label" htmlFor="rx-record-query">Find a recent request by title, reply, or status.</label>
      <input id="rx-record-query" type="search" value={query} maxLength={200}
        onChange={(event) => { setQuery(event.target.value); setRecordPage(0); }} placeholder="Search Radix requests…" />
      <div role="status" className="rx-record-status">
        {loading ? "Loading Radix records…" : recordError || (records.length ? records.length + " Radix records" : query ? "No matching Radix records." : "No recent Radix requests. Send one below to start.")}
      </div>
      {limited ? <p className="rx-record-limit">Results cover the latest 200 workspace requests and show Radix only. Older records are not included.</p> : null}
      {!loading && !recordError && records.length > 0 ? <ul className="rx-record-list">{records.slice(pageIndex * 5, pageIndex * 5 + 5).map((record) =>
        <li key={record.id}><button type="button" onClick={() => openRecord(record.id)}>
          <span>{record.title}</span><span className="rx-badge">{record.status}</span>
        </button></li>)}</ul> : null}
      {!loading && !recordError && records.length > 5 ? <div className="rx-record-pages"><button type="button" className="rx-text-button" disabled={pageIndex === 0} onClick={() => setRecordPage(pageIndex - 1)}>Previous records</button><span>{pageIndex * 5 + 1}–{Math.min(pageIndex * 5 + 5, records.length)} of {records.length}</span><button type="button" className="rx-text-button" disabled={(pageIndex + 1) * 5 >= records.length} onClick={() => setRecordPage(pageIndex + 1)}>Next records</button></div> : null}
      {selected ? <article className="rx-selected-record">
        <div className="rx-finding-heading"><h3>{selected.title}</h3><button type="button" className="rx-text-button" onClick={() => setSelected(null)}>Close record</button></div>
        <p className="rx-meta">Radix · {selected.status}</p>
        <p className="rx-reply">{selected.reply || (selected.status === "failed" ? "This request did not complete." : "A reply is not available yet.")}</p>
        <button type="button" className="rx-secondary" onClick={() => setReference({ id: selected.id, title: selected.title })}>Use in request</button>
      </article> : null}
    </section>

    <section className="rx-card rx-chat" aria-labelledby="rx-chat-title">
      <h2 id="rx-chat-title">Ask Radix</h2>
      <p>Ask about a finding or request help checking code.</p>
      <div className="rx-thread" role="log" aria-label="Radix conversation" aria-live="polite" aria-relevant="additions">
        {!thread.length ? <p className="rx-thread-empty">Your requests and Radix replies appear here.</p> : null}
        {thread.map((message, index) => <article className={"rx-message rx-message-" + message.role} key={index}>
          <p className="rx-message-author">{message.role === "user" ? "You" : "Radix"}</p>
          <p className="rx-reply">{message.content}</p>
          {message.sources?.length ? <details className="rx-answer-sources"><summary>Sources</summary>
            <p>Radix records included with this request:</p>
            <ul>{message.sources.map((source) => <li key={source.id}><button type="button" className="rx-text-button" onClick={() => openRecord(source.id)}>{source.title}</button></li>)}</ul>
          </details> : null}
        </article>)}
        <div ref={replyEnd} />
      </div>
      <form className="rx-request-form" onSubmit={send}>
        {reference ? <div className="rx-reference"><span>Using: {reference.title}</span><button type="button" className="rx-text-button" onClick={() => setReference(null)}>Remove reference</button></div> : null}
        <label htmlFor="rx-request">Your request</label>
        <textarea id="rx-request" rows={3} maxLength={4000} value={draft} disabled={busy}
          placeholder="Type a request…" onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault(); event.currentTarget.form?.requestSubmit();
            }
          }} />
        <div className="rx-send-row"><small>Enter to send. Shift + Enter for a new line.</small>
          <button type="submit" className="rx-primary" disabled={busy}>{busy ? "Sending…" : "Send"}</button></div>
        {progress ? <p className="rx-request-progress" role="status">{progress}</p> : null}
        {error ? <p className="rx-request-error" role="alert">{error}</p> : null}
      </form>
    </section>
  </div>;
}
