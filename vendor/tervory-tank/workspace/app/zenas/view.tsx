"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { DeadlinesPanel } from "./deadlines-panel";
import { AREAS, DEADLINE_ROWS, MATTER_ROWS, REVIEW_ROWS, SEARCH_KINDS, hitsFor, type SearchKind } from "./frame";
import { FORUMS, showCourt, weightFor, type CitedCase, type PrecedentRule } from "./precedent";
import { MOOT_LABEL } from "./moot";
import { MootCourt } from "./moot-court";
import { PluginsDialog } from "./plugins-dialog";
import { PLUGIN_TABS, SEARCH_PLACEHOLDER, type PluginPanel, type PluginTab } from "./plugins";
import { SquadLinks } from "../squad-links";
import type { AskReply } from "../api/zenas/rules";

const TOP_LINKS = [
  { href: "https://app.tervory.com/", label: "Dashboard" },
  { href: "/matters", label: "Projects" },
] as const;

const PAGE_LINKS = [
  { href: "/matters", label: "Matters" },
  { href: "/review", label: "Review" },
] as const;

const BOTTOM_LINKS = [
  { href: "/onboarding", label: "Settings" },
  { href: "https://tervory.com", label: "Support" },
] as const;

export function ZenasView({
  plugins,
  workspaceName,
  founderLine,
  draftLabel = "",
  rules = [],
  citedCases = [],
}: {
  plugins: PluginPanel;
  workspaceName: string | null;
  founderLine?: string | null;
  draftLabel?: string;
  rules?: PrecedentRule[];
  citedCases?: CitedCase[];
}) {
  const accountName = workspaceName?.trim() || "Your account";
  const initial = accountName.slice(0, 1).toUpperCase();
  const searchRef = useRef<HTMLInputElement>(null);
  const askRef = useRef<HTMLTextAreaElement>(null);
  const listId = useId();
  const [menu, setMenu] = useState(false);
  const [pluginsOpen, setPluginsOpen] = useState(false);
  const [tab, setTab] = useState<PluginTab>(PLUGIN_TABS[0]);
  const [areasOpen, setAreasOpen] = useState(false);
  const [areasPos, setAreasPos] = useState({ top: 8, left: 248 });
  const [areaPick, setAreaPick] = useState("All areas (civil)");
  const areasRef = useRef<HTMLButtonElement>(null);
  const areasMenuRef = useRef<HTMLDivElement>(null);
  const areasMenuId = useId();
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<SearchKind>("All");
  const [cursor, setCursor] = useState(0);
  const [asked, setAsked] = useState<string | null>(null);
  const [reply, setReply] = useState<AskReply | null>(null);
  const [asking, setAsking] = useState(false);
  const [messages, setMessages] = useState(0);
  const [attached, setAttached] = useState<string | null>(null);
  const [actionNote, setActionNote] = useState("");
  const [section, setSection] = useState<"chats" | "moot" | "deadlines">("chats");
  const [forum, setForum] = useState("Western Cape Division, single judge");
  const bodyRef = useRef<HTMLDivElement>(null);

  const hits = hitsFor(kind, query);

  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearchOpen(true);
        searchRef.current?.focus();
      }
      if (event.key === "Escape") {
        setSearchOpen(false);
        setPluginsOpen(false);
        setMenu(false);
        setAreasOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!areasOpen) return;
    function onPointer(event: PointerEvent) {
      const target = event.target as Node;
      if (areasRef.current?.contains(target) || areasMenuRef.current?.contains(target)) return;
      setAreasOpen(false);
    }
    window.addEventListener("pointerdown", onPointer);
    return () => window.removeEventListener("pointerdown", onPointer);
  }, [areasOpen]);

  function openPlugins() {
    setMenu(false);
    setSearchOpen(false);
    setAreasOpen(false);
    setPluginsOpen(true);
  }

  function showChats() {
    setSection("chats");
    setPluginsOpen(false);
    setAreasOpen(false);
    setMenu(false);
    bodyRef.current?.scrollTo(0, 0);
  }

  function showMoot() {
    setSection("moot");
    setPluginsOpen(false);
    setSearchOpen(false);
    setAreasOpen(false);
    setMenu(false);
    bodyRef.current?.scrollTo(0, 0);
  }

  function showDeadlines() {
    setSection("deadlines");
    setPluginsOpen(false);
    setSearchOpen(false);
    setAreasOpen(false);
    setMenu(false);
    bodyRef.current?.scrollTo(0, 0);
  }

  function toggleAreas() {
    if (areasOpen) {
      setAreasOpen(false);
      return;
    }
    const rect = areasRef.current?.getBoundingClientRect();
    if (rect) {
      const menuWidth = 260;
      const left = rect.right + 8 + menuWidth > window.innerWidth
        ? Math.max(8, window.innerWidth - menuWidth - 8)
        : rect.right + 8;
      setAreasPos({ top: Math.max(8, rect.top), left });
    }
    setAreasOpen(true);
  }

  function closePlugins() {
    setPluginsOpen(false);
  }

  function onSearchKey(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSearchOpen(true);
      setCursor((value) => Math.min(value + 1, Math.max(hits.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((value) => Math.max(value - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      setKind("All");
      setSearchOpen(true);
    }
  }

  async function onAsk(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (asking) return;
    const text = askRef.current?.value.trim() ?? "";
    if (!text) {
      setActionNote("Type a question first. Nothing was sent.");
      return;
    }
    setAsked(text);
    setReply(null);
    setAsking(true);
    setMessages((count) => count + 2);
    setActionNote("");
    if (askRef.current) askRef.current.value = "";
    try {
      const res = await fetch("/api/zenas/answers", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      const payload = (await res.json().catch(() => null)) as AskReply | null;
      setReply(payload && typeof payload.kind === "string" ? payload : { kind: "error", message: "Zenas could not answer just now. Please try again in a few minutes." });
    } catch {
      setReply({ kind: "error", message: "Zenas could not be reached. Check your connection and try again." });
    } finally {
      setAsking(false);
    }
  }

  return (
    <div className="zn">
      <button className="zn-menu" type="button" onClick={() => setMenu(true)}>
        Menu
      </button>
      <button className="zn-dim" type="button" hidden={!menu} aria-label="Close menu" onClick={() => setMenu(false)} />
      <aside className={menu ? "zn-panel open" : "zn-panel"}>
        <div className="zn-panel-main">
        <div className="zn-brand">
          <img className="zn-zenas-logo" src="/brand/zenas-on-dark.png" alt="Zenas" width={150} height={53} />
        </div>
        <button className="zn-close" type="button" onClick={() => setMenu(false)}>
          Close
        </button>
        <nav className="zn-nav" aria-label="Account">
          <p className="zn-account">
            <span className="zn-avatar" aria-hidden="true">{initial}</span>
            {accountName}
          </p>
          {founderLine ? (
            <Link href="/zenas/feeds" onClick={() => setMenu(false)}>
              {founderLine}
            </Link>
          ) : null}
          {TOP_LINKS.map((link) => (
            <Link key={link.label} href={link.href} onClick={() => setMenu(false)}>
              {link.label}
            </Link>
          ))}
          <button className={!pluginsOpen && section === "chats" ? "active" : ""} type="button" onClick={showChats}>
            Chats
          </button>
        </nav>
        <SquadLinks className="zn-nav squad-nav" />
        <div className="zn-rule" />
        <nav className="zn-nav" aria-label="Zenas">
          <p className="zn-kicker">ZENAS · CIVIL · SOUTH AFRICA</p>
          {PAGE_LINKS.map((link) => (
            <Link key={link.label} href={link.href} onClick={() => setMenu(false)}>
              {link.label}
            </Link>
          ))}
          <button
            className={!pluginsOpen && section === "moot" ? "active" : ""}
            type="button"
            aria-current={!pluginsOpen && section === "moot" ? "page" : undefined}
            onClick={showMoot}
          >
            {MOOT_LABEL}
          </button>
          <button className={pluginsOpen ? "active" : ""} type="button" onClick={openPlugins}>
            Plugins
          </button>
          <button
            className={!pluginsOpen && section === "deadlines" ? "active" : ""}
            type="button"
            aria-current={!pluginsOpen && section === "deadlines" ? "page" : undefined}
            onClick={showDeadlines}
          >
            Deadlines
          </button>
        </nav>
        <button
          ref={areasRef}
          className={areasOpen ? "zn-areas-row open" : "zn-areas-row"}
          type="button"
          aria-expanded={areasOpen}
          aria-controls={areasMenuId}
          onClick={toggleAreas}
        >
          <span>Areas of law</span>
          <span>{areaPick === AREAS[0] ? AREAS.length : areaPick}</span>
        </button>
        <section className="zn-side" aria-label="Diary">
          <div className="zn-side-head">
            <p className="zn-kicker">DIARY</p>
          </div>
          <p className="zn-empty">No matters yet.</p>
        </section>
        <section className="zn-side" aria-label="Upcoming court dates">
          <div className="zn-side-head">
            <p className="zn-kicker">UPCOMING COURT DATES</p>
          </div>
          <p className="zn-empty">No matters yet.</p>
        </section>
        <div className="zn-spacer" />
        </div>
        <div className="zn-panel-foot">
        <div className="zn-rule" />
        <img className="zn-foot-logo" src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} />
        <nav className="zn-nav" aria-label="Account settings">
          {BOTTOM_LINKS.map((link) => (
            <Link key={link.label} href={link.href} onClick={() => setMenu(false)}>
              {link.label}
            </Link>
          ))}
        </nav>
        </div>
      </aside>
      <div ref={bodyRef} className={pluginsOpen ? "zn-body dimmed" : "zn-body"} data-scroll-root>
        {section === "moot" ? <MootCourt /> : null}
        {section === "deadlines" ? <DeadlinesPanel /> : null}
        {section === "chats" ? (
        <>
        <header className="zn-head">
          <div>
            <h1>Zenas</h1>
            <p>Civil law, South Africa. Search your own records, or ask Zenas a legal question.</p>
            {draftLabel ? <p className="zn-draft">{draftLabel}</p> : null}
          </div>
          <div className="zn-search">
            <label className="zn-sr" htmlFor="zenas-search">Search</label>
            <svg className="zn-search-icon" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <circle cx="7" cy="7" r="4.25" fill="none" stroke="#6b7590" strokeWidth="1.4" />
              <path d="M10.2 10.2 L14 14" fill="none" stroke="#6b7590" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
            <input
              id="zenas-search"
              ref={searchRef}
              value={query}
              placeholder={SEARCH_PLACEHOLDER}
              role="combobox"
              aria-expanded={searchOpen}
              aria-controls={listId}
              aria-autocomplete="list"
              onChange={(event) => {
                setQuery(event.target.value);
                setSearchOpen(true);
                setCursor(0);
              }}
              onFocus={() => setSearchOpen(true)}
              onKeyDown={onSearchKey}
            />
            <button className="zn-kbd" type="button" onClick={() => { setSearchOpen(true); searchRef.current?.focus(); }}>
              ⌘K
            </button>
            {searchOpen ? (
              <div className="zn-results" id={listId} role="listbox" aria-label="Search results">
                <div className="zn-results-head">
                  <strong>Results in your records · {hits.length}</strong>
                  <button type="button" onClick={() => setSearchOpen(false)}>Esc to close</button>
                </div>
                <div className="zn-result-filters">
                  {SEARCH_KINDS.map((name) => (
                    <button key={name} className={kind === name ? "zn-chip zn-chip-fill" : "zn-chip"} type="button" onClick={() => { setKind(name); setCursor(0); }}>
                      {name}
                    </button>
                  ))}
                </div>
                {SEARCH_KINDS.filter((name) => name !== "All").map((group) => {
                  const rows = hits.filter((hit) => hit.kind === group);
                  if (!rows.length) return null;
                  return (
                    <section key={group}>
                      <h2>{group.toUpperCase()}</h2>
                      {rows.map((hit, index) => {
                        const flat = hits.indexOf(hit);
                        return (
                          <div className={flat === cursor ? "zn-hit on" : "zn-hit"} key={`${hit.kind}-${index}`} role="option" aria-selected={flat === cursor}>
                            <span className="zn-hit-mark" aria-hidden="true" />
                            <span>
                              <strong>{hit.title}</strong>
                              <em>{hit.meta}</em>
                              <i />
                            </span>
                            {hit.status ? <span className="zn-chip">{hit.status}</span> : null}
                          </div>
                        );
                      })}
                    </section>
                  );
                })}
                {hits.length === 0 ? <p className="zn-empty">{query.trim() ? "No records match that search." : "No matters yet."}</p> : null}
                <p className="zn-results-foot">
                  Enter to see all {hits.length} results · ↑↓ to move · Searches matters, drafts, documents, citations and review in Zenas only.
                </p>
              </div>
            ) : null}
          </div>
        </header>
        <section className="zn-ask" aria-label="Ask Zenas">
          <div className="zn-ask-head">
            <h2>Ask Zenas</h2>
            <p>Thread · {messages} {messages === 1 ? "message" : "messages"}</p>
            <label className="zn-forum" htmlFor="zenas-forum">
              Your forum
              <select id="zenas-forum" value={forum} onChange={(event) => setForum(event.target.value)}>
                {FORUMS.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="zn-thread">
            {asked ? (
              <>
                <article className="zn-you">
                  <h3>You</h3>
                  <p>{asked}</p>
                </article>
                <article className="zn-answer" aria-label="Answer">
                  <header>
                    <span className="zn-mark" aria-hidden="true">Z</span>
                    <strong>Zenas</strong>
                  </header>
                  {asking ? <p role="status" aria-live="polite">Zenas is checking the law. This can take up to a minute.</p> : null}
                  {!asking && reply?.kind === "answer" ? <p className="zn-answer-text">{reply.text}</p> : null}
                  {!asking && reply && reply.kind !== "answer" ? (
                    <div role={reply.kind === "error" ? "alert" : "status"}>
                      <p>{reply.message}</p>
                      {reply.kind === "refused" && reply.referral ? <p>{reply.referral}</p> : null}
                      {(reply.kind === "in_review" || reply.kind === "unverified") && reply.matterId ? <p><Link href="/matters">Open Matters</Link></p> : null}
                    </div>
                  ) : null}
                  {!asking && reply?.kind === "answer" ? (
                  <section className="zn-sources" aria-label="Sources">
                    <header>
                      <h3>Sources</h3>
                      <p>{draftLabel || "Each point in this answer cites the source below."}</p>
                    </header>
                    <ul className="zn-cited">
                      {reply.sources.map((source) => (
                        <li key={`ask-${source.citation}`}>
                          <strong>{source.url ? <a href={source.url}>{source.citation}</a> : source.citation}</strong>
                          {source.quote ? <span>{source.quote}</span> : null}
                        </li>
                      ))}
                      {citedCases.map((item) => {
                        const answer = weightFor(forum, {
                          court: item.court,
                          division: item.division,
                          seat: item.seat,
                          bench_size: item.bench_size,
                        }, rules);
                        const href = item.url
                          ? `${item.url}${item.paragraph ? `#para-${item.paragraph}` : ""}`
                          : "";
                        return (
                          <li key={`${item.citation}-${item.paragraph}`}>
                            <strong>{item.citation || "unknown"}</strong>
                            <span>{showCourt(item)}</span>
                            <span>{answer.weight === "not_precedent" ? "not precedent" : answer.weight}. {answer.reason}</span>
                            {item.treatment ? <span>Treatment: {item.treatment}</span> : null}
                            {item.warning ? <span>{item.warning}</span> : null}
                            {href ? <a href={href}>Full judgment{item.paragraph ? `, paragraph ${item.paragraph}` : ""}</a> : <span>Paragraph {item.paragraph || "unknown"}</span>}
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                  ) : null}
                </article>
              </>
            ) : (
              <p className="zn-empty">No matters yet.</p>
            )}
          </div>
          <form className="zn-composer" onSubmit={onAsk}>
            <label className="zn-sr" htmlFor="zenas-ask">Ask Zenas</label>
            <textarea id="zenas-ask" ref={askRef} rows={3} placeholder="Ask Zenas a civil legal question" />
            <div className="zn-composer-actions">
              <div>
                <label className="zn-outline zn-attach">
                  + Attach
                  <input
                    className="zn-sr"
                    type="file"
                    onChange={(event) => setAttached(event.target.files?.[0]?.name ?? null)}
                  />
                </label>
                {attached ? <span className="zn-attached">{attached}</span> : null}
              </div>
              <div className="zn-send-row">
                <button className="zn-primary" type="submit" disabled={asking}>{asking ? "Asking…" : "Send"}</button>
              </div>
            </div>
          </form>
          <p className="zn-disclaimer">Zenas provides legal information, not legal advice.</p>
          {actionNote ? <p className="zn-note-line">{actionNote}</p> : null}
        </section>
        <section className="zn-work" aria-label="Your work">
          <article className="zn-card">
            <header>
              <h2>Recent matters</h2>
              <Link href="/matters">View all</Link>
            </header>
            {MATTER_ROWS === 0 ? <p className="zn-empty">No matters yet.</p> : null}
          </article>
          <div className="zn-work-side">
            <article className="zn-card">
              <header>
                <h2>Review</h2>
                <Link href="/review">View all</Link>
              </header>
              {REVIEW_ROWS === 0 ? <p className="zn-empty">No matters yet.</p> : null}
            </article>
            <article className="zn-card">
              <header>
                <h2>Deadlines</h2>
                <button type="button" onClick={showDeadlines}>View all</button>
              </header>
              {DEADLINE_ROWS.length === 0 ? <p className="zn-empty">No matters yet.</p> : null}
            </article>
          </div>
        </section>
        </>
        ) : null}
      </div>
      {areasOpen ? (
        <div
          ref={areasMenuRef}
          id={areasMenuId}
          className="zn-areas-menu"
          role="menu"
          aria-label="Areas of law"
          style={{ top: areasPos.top, left: areasPos.left }}
        >
          {AREAS.map((name) => (
            <button
              className={areaPick === name ? "on" : ""}
              key={name}
              type="button"
              role="menuitem"
              onClick={() => {
                setAreaPick(name);
                setAreasOpen(false);
              }}
            >
              {name}
            </button>
          ))}
        </div>
      ) : null}
      <PluginsDialog open={pluginsOpen} tab={tab} plugins={plugins} onTab={setTab} onClose={closePlugins} />
    </div>
  );
}
