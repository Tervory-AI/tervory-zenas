"use client";
import { dateLabel } from "./display";

/*
 * Search and Ask Base sit in the body. Both only read Base records: they never start, re-run or change anything.
 * Sources are the references that come back with an answer. Connections live under Plugins.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import type { Answer, FoundValue, RecordHit } from "./records";
import type { AskResult, SearchResult } from "./search";

export type SearchAction = (query: string) => Promise<SearchResult>;
export type AskAction = (request: string) => Promise<AskResult>;

function Value({ label, found }: { label: string; found: FoundValue | null }) {
  return (
    <div className="bcp-hit-field">
      <span className="bcp-hit-label">{label}</span>
      {found ? (
        <>
          <span className="bcp-hit-value">{found.value}</span>
          {found.sourceUrl && found.checkedAt ? <span className="bcp-hit-source"><a href={found.sourceUrl} target="_blank" rel="noopener noreferrer">Source link</a> · Checked <time dateTime={found.checkedAt}>{dateLabel(found.checkedAt)}</time></span> : <span className="bcp-hit-source">Source and check date not recorded.</span>}
        </>
      ) : (
        <span className="bcp-hit-none">None found</span>
      )}
    </div>
  );
}

export function RecordSearch({ search }: { search: SearchAction }) {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SearchResult | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q || busy) return;
    setBusy(true);
    try {
      setResult(await search(q));
    } catch {
      setResult({ ok: false, query: q });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="bcp-search">
      <form className="bcp-search-bar" role="search" onSubmit={submit}>
        <label className="bcp-sr" htmlFor="bcp-search-input">
          Search Base records
        </label>
        <input
          id="bcp-search-input"
          className="bcp-input"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search records by shop name, city, phone, website or licence number"
          autoComplete="off"
        />
        <button type="submit" className="bcp-secondary" disabled={busy || !query.trim()}>
          {busy ? "Searching…" : "Search"}
        </button>
      </form>
      {result === null ? null : !result.ok ? (
        <div className="bcp-state" role="status">
          <h3>Search could not run</h3>
          <p>We could not read the records just now. Nothing has changed. Try again in a minute.</p>
        </div>
      ) : result.hits.length === 0 ? (
        <div className="bcp-state" role="status">
          <h3>No records match “{result.query}”</h3>
          <p>Try a shop name, a city, a phone number or a website.</p>
        </div>
      ) : (
        <div className="bcp-hits" role="region" aria-label="Search results">
          <p className="bcp-hits-count" role="status">
            {result.total === result.hits.length
              ? `${result.total.toLocaleString("en-US")} ${result.total === 1 ? "record matches" : "records match"} “${result.query}”`
              : `Showing ${result.hits.length} of ${result.total.toLocaleString("en-US")} records that match “${result.query}”`}
          </p>
          <ul className="bcp-list">
            {result.hits.map((hit: RecordHit) => (
              <li key={hit.id} className="bcp-hit">
                <div className="bcp-hit-who">
                  <p className="bcp-row-title">{hit.name}</p>
                  <p className="bcp-row-copy">{[hit.place, hit.batch].filter(Boolean).join(" · ")}</p>
                </div>
                <Value label="Website" found={hit.website} />
                <Value label="Email" found={hit.email} />
                <Value label="Phone" found={hit.phone} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

type Turn = { role: "you" | "base"; text: string; answer?: Answer; failed?: boolean };

export function AskBase({ ask }: { ask: AskAction }) {
  const [thread, setThread] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest" });
  }, [thread]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const request = draft.trim();
    if (!request || busy) return;
    setThread((turns) => [...turns, { role: "you", text: request }]);
    setDraft("");
    setBusy(true);
    let reply: Turn;
    try {
      const result = await ask(request);
      reply = result.ok
        ? { role: "base", text: result.answer.text, answer: result.answer }
        : { role: "base", text: "I could not read the records just now. Nothing has changed. Try again in a minute.", failed: true };
    } catch {
      reply = { role: "base", text: "I could not read the records just now. Nothing has changed. Try again in a minute.", failed: true };
    }
    setThread((turns) => [...turns, reply]);
    setBusy(false);
  }

  return (
    <div className="bcp-chat">
      <div className="bcp-thread" aria-live="polite">
        {thread.length === 0 ? (
          <p className="bcp-thread-empty">
            Ask about your records, for example “Which Kauai shops have no website?” Answers come from Base records only, and
            the records each answer used are listed under Sources.
          </p>
        ) : (
          thread.map((turn, index) => (
            <div key={index} className={turn.role === "you" ? "bcp-msg you" : "bcp-msg base"}>
              <p className="bcp-msg-who">{turn.role === "you" ? "You" : "Base"}</p>
              <p className="bcp-msg-text">{turn.text}</p>
              {turn.answer && turn.answer.sources.length > 0 ? (
                <div className="bcp-answer-sources">
                  <p className="bcp-answer-sources-title">Sources</p>
                  <ol>
                    {turn.answer.sources.map((source, i) => (
                      <li key={i}>
                        <span className="bcp-row-title">{source.title}</span>
                        <span className="bcp-row-copy">{source.detail}</span>
                        {source.source && source.checkedAt ? <span className="bcp-hit-source"><a href={source.source} target="_blank" rel="noopener noreferrer">Source link</a> · Checked <time dateTime={source.checkedAt}>{dateLabel(source.checkedAt)}</time></span> : null}
                      </li>
                    ))}
                  </ol>
                </div>
              ) : null}
            </div>
          ))
        )}
        {busy ? <p className="bcp-msg-wait">Reading records…</p> : null}
        <div ref={end} />
      </div>
      <form className="bcp-composer" onSubmit={send}>
        <label className="bcp-sr" htmlFor="bcp-ask-input">
          Type a request
        </label>
        <textarea
          id="bcp-ask-input"
          className="bcp-input"
          rows={2}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder="Type a request…"
          disabled={busy}
        />
        <button type="submit" className="bcp-send" disabled={busy || !draft.trim()}>
          {busy ? "Working…" : "Send"}
        </button>
      </form>
      <p className="bcp-fine">Ask Base reads records only. It can't start, re-run or change anything, and it costs nothing.</p>
    </div>
  );
}
