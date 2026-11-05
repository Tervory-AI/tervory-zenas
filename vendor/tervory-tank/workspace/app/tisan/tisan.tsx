"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { SquadLinks } from "../squad-links";
import {
  FILTERS,
  OPENING_THREAD,
  PAGE_NAV,
  RULES,
  groupMeta,
  primaryAction,
  replyTo,
  resultSummary,
  searchRecords,
  showsChat,
  showsResults,
  type Citation,
  type Dialog,
  type Filter,
  type Kind,
  type PluginTab,
  type View,
} from "./copy";
import { GoldReview } from "./gold-review";
import { Plugins } from "./plugins";
import type { PluginPanel } from "./vault-model";

function Pill({ tone, children }: { tone: "solid" | "quiet"; children: ReactNode }) {
  return <span className={tone === "solid" ? "pill solid" : "pill quiet"}><i />{children}</span>;
}

function Foot({ lines }: { lines: string[] }) {
  return (
    <footer className="foot">
      {lines.map((line, index) => <p key={line} className={index === lines.length - 1 ? "small" : undefined}>{line}</p>)}
    </footer>
  );
}

const GROUP_ORDER: Kind[] = ["Build", "Page", "Test"];

export function Tisan({ plugins, accountName, canApproveGold = false }: { plugins: PluginPanel; accountName: string | null; canApproveGold?: boolean }) {
  const [view, setView] = useState<View>("overview");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [dialog, setDialog] = useState<Dialog>("none");
  const [pluginTab, setPluginTab] = useState<PluginTab>("keys");
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [thread, setThread] = useState(OPENING_THREAD);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    const media = window.matchMedia("(max-width: 800px)");
    const apply = () => setMobile(media.matches);
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setDialog("none");
      setMenuOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const action = primaryAction({ view, query, filter, dialog, pluginTab, mobile });
  const chat = showsChat(view, query, filter);
  const results = showsResults(view, query, filter);

  function show(next: View) {
    setDialog("none");
    setMenuOpen(false);
    setQuery("");
    setFilter("all");
    setView(next);
    document.querySelector(".body")?.scrollTo(0, 0);
  }

  function openPlugins() {
    setMenuOpen(false);
    setDialog("plugins");
  }

  function send(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    const reply = replyTo(text);
    setThread((current) => [...current, { role: "user", text }, { role: "assistant", text: reply.text, when: reply.when, sources: reply.sources }]);
    setDraft("");
  }

  const tabs: { id: View | "plugins"; label: string }[] = [
    ...PAGE_NAV.filter((item) => item.id !== "settings"),
    { id: "plugins", label: "Plugins" },
  ];

  const rootClass = ["tisan", dialog !== "none" ? "dialog-open" : "", menuOpen ? "menu-open" : ""].filter(Boolean).join(" ");
  const hits = searchRecords(query, filter);

  return (
    <div className={rootClass}>
      <header className="topbar">
        <img src="/brand/tervory-logo-reversed.png" alt="Tervory" />
        <button type="button" className="menu" aria-expanded={menuOpen} aria-controls="tisan-panel" onClick={() => setMenuOpen((open) => !open)}>
          {menuOpen ? "Close" : "Menu"}
        </button>
      </header>

      <aside id="tisan-panel" className="panel" aria-label="Tisan">
        <img className="panel-mark" src="/brand/tisan-on-dark.png" alt="Tisan" width={2806} height={701} />
        <div className="panel-top">
          <p className="account">{accountName?.trim() || "Your account"}</p>
          <nav aria-label="Workspace">
            <a className="nav" href="https://app.tervory.com/">Dashboard</a>
            <a className="nav" href="/matters">Projects</a>
            <a className="nav" href="/chats">Chats</a>
          </nav>
          <SquadLinks className="squad-nav" linkClassName="nav" />
        </div>
        <div className="panel-own">
          <p className="agent">Tisan</p>
          <p className="role">Build agent</p>
          <nav aria-label="Tisan">
            {tabs.map((item) => {
              const id = item.id;
              const selected = id === "plugins" ? dialog === "plugins" : dialog !== "plugins" && view === id;
              if (id === "plugins") {
                return (
                  <button key={id} type="button" className={selected ? "nav on" : "nav"} aria-current={selected ? "page" : undefined} onClick={openPlugins}>
                    {item.label}
                  </button>
                );
              }
              return (
                <button key={id} type="button" className={selected ? "nav on" : "nav"} aria-current={selected ? "page" : undefined} onClick={() => show(id)}>
                  {item.label}
                </button>
              );
            })}
          </nav>
        </div>
        <div className="panel-bottom">
          <img className="panel-sign" src="/brand/tervory-logo-reversed.png" alt="Tervory" />
          <button type="button" className={view === "settings" && dialog !== "plugins" ? "nav on" : "nav"} aria-current={view === "settings" && dialog !== "plugins" ? "page" : undefined} onClick={() => show("settings")}>Settings</button>
          <a className="nav" href="https://tervory.com">Support</a>
        </div>
      </aside>

      <div className="body">
        {view === "overview" && canApproveGold && <GoldReview />}
        {view === "overview" ? (
          <Overview
            startPrimary={action === "start"}
            sendPrimary={action === "send"}
            query={query}
            filter={filter}
            hits={hits}
            results={results}
            chat={chat}
            thread={thread}
            draft={draft}
            onQuery={setQuery}
            onFilter={setFilter}
            onStart={() => { setMenuOpen(false); setDialog("build"); }}
            onOpen={show}
            onDraft={setDraft}
            onSend={send}
          />
        ) : null}
        {view === "builds" ? <Builds startPrimary={action === "start"} onStart={() => setDialog("build")} /> : null}
        {view === "design" ? <Design startPrimary={action === "start"} onStart={() => setDialog("build")} /> : null}
        {view === "tests" ? <Tests startPrimary={action === "start"} onStart={() => setDialog("build")} /> : null}
        {view === "settings" ? <Settings startPrimary={action === "start"} onStart={() => setDialog("build")} /> : null}
      </div>

      {dialog === "plugins" ? (
        <Plugins onClose={() => setDialog("none")} tab={pluginTab} setTab={setPluginTab} desktopAction={action} plugins={plugins} />
      ) : null}
      {dialog === "build" ? <StartBuild onClose={() => setDialog("none")} /> : null}
    </div>
  );
}

function StartButton({ primary, onClick }: { primary: boolean; onClick: () => void }) {
  return (
    <button type="button" className={primary ? "primary page-primary" : "line page-primary"} data-primary={primary ? "true" : undefined} onClick={onClick}>
      Start build
    </button>
  );
}

function PageHead({ title, lede, primary, onStart }: { title: string; lede: string; primary: boolean; onStart: () => void }) {
  return (
    <header className="head">
      <div>
        <h1>{title}</h1>
        <p className="lede">{lede}</p>
      </div>
      <StartButton primary={primary} onClick={onStart} />
    </header>
  );
}

function Overview({
  startPrimary,
  sendPrimary,
  query,
  filter,
  hits,
  results,
  chat,
  thread,
  draft,
  onQuery,
  onFilter,
  onStart,
  onOpen,
  onDraft,
  onSend,
}: {
  startPrimary: boolean;
  sendPrimary: boolean;
  query: string;
  filter: Filter;
  hits: { id: string; kind: Kind; title: string; detail: string; view: View }[];
  results: boolean;
  chat: boolean;
  thread: { role: "user" | "assistant"; text: string; when?: string; sources?: Citation[] }[];
  draft: string;
  onQuery: (value: string) => void;
  onFilter: (value: Filter) => void;
  onStart: () => void;
  onOpen: (view: View) => void;
  onDraft: (value: string) => void;
  onSend: (event: FormEvent) => void;
}) {
  return (
    <>
      <PageHead
        title="Tisan"
        lede="Builds a website from a locked Figma page, tests it, and deploys it only when every check passes."
        primary={startPrimary}
        onStart={onStart}
      />
      <Search query={query} filter={filter} onQuery={onQuery} onFilter={onFilter} />
      {results ? <Results query={query} hits={hits} onOpen={onOpen} /> : null}
      {chat ? (
        <>
          <p className="empty">No builds yet.</p>
          <Chat thread={thread} draft={draft} sendPrimary={sendPrimary} onDraft={onDraft} onSend={onSend} onOpen={onOpen} />
          <Rules />
          <Foot lines={["Keys and tokens are stored on the server, never in the app code."]} />
        </>
      ) : null}
    </>
  );
}

function Search({ query, filter, onQuery, onFilter }: { query: string; filter: Filter; onQuery: (value: string) => void; onFilter: (value: Filter) => void }) {
  return (
    <form className="search" role="search" onSubmit={(event) => event.preventDefault()}>
      <label className="search-field">
        <span className="glass" aria-hidden="true">
          <svg viewBox="0 0 20 20" width="18" height="18"><circle cx="8.5" cy="8.5" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.6" /><path d="M13 13.5L17 17.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>
        </span>
        <span className="sr">Search builds, pages and tests</span>
        <input type="search" value={query} placeholder="Search builds, pages and tests" onChange={(event) => onQuery(event.target.value)} />
      </label>
      <div className="filters" role="group" aria-label="Record type">
        {FILTERS.map((item) => (
          <button key={item.id} type="button" className={filter === item.id ? "chip on" : "chip"} aria-pressed={filter === item.id} onClick={() => onFilter(item.id)}>
            {item.label}
          </button>
        ))}
        <p>Finds Tisan's own records only.</p>
      </div>
    </form>
  );
}

function Results({ query, hits, onOpen }: { query: string; hits: { id: string; kind: Kind; title: string; detail: string; view: View }[]; onOpen: (view: View) => void }) {
  return (
    <div className="results">
      <p className="result-count">{resultSummary(query, hits.length)}</p>
      {hits.length === 0 ? <p className="empty">No builds yet.</p> : null}
      {GROUP_ORDER.map((kind) => {
        const rows = hits.filter((hit) => hit.kind === kind);
        if (rows.length === 0) return null;
        const meta = groupMeta(kind, rows.length);
        return (
          <section key={kind} className="result-group">
            <h2>{meta.heading}</h2>
            <p className="countline">{meta.countLabel}</p>
            <div className="result-card">
              {rows.map((row) => (
                <button key={row.id} type="button" className="result-row" onClick={() => onOpen(row.view)}>
                  <span className="pill quiet"><i />{row.kind}</span>
                  <span className="result-copy">
                    <strong>{row.title}</strong>
                    <span>{row.detail}</span>
                  </span>
                  <span className="open">Open →</span>
                </button>
              ))}
            </div>
          </section>
        );
      })}
      <Foot lines={["Search looks only at Tisan's own builds, pages and tests."]} />
    </div>
  );
}

function Chat({
  thread,
  draft,
  sendPrimary,
  onDraft,
  onSend,
  onOpen,
}: {
  thread: { role: "user" | "assistant"; text: string; when?: string; sources?: Citation[] }[];
  draft: string;
  sendPrimary: boolean;
  onDraft: (value: string) => void;
  onSend: (event: FormEvent) => void;
  onOpen: (view: View) => void;
}) {
  return (
    <section className="ask" aria-label="Chat">
      <h2>Ask Tisan</h2>
      <p className="intro">Type a request in plain English. The thread shows above the box, and each answer lists its sources.</p>
      <div className="thread">
        {thread.map((message, index) => (
          <article key={`${message.role}-${index}`} className={message.role === "user" ? "bubble you" : "bubble"}>
            <p className="who">{message.role === "user" ? "You" : "Tisan"}</p>
            <p>{message.text}</p>
            {message.when ? <p className="when">{message.when}</p> : null}
            {message.sources && message.sources.length > 0 ? (
              <div className="citations">
                <h3>Sources</h3>
                <ol>
                  {message.sources.map((source, sourceIndex) => (
                    <li key={source.title}>
                      <span className="cite-no">{sourceIndex + 1}</span>
                      <span>
                        <strong>{source.title}</strong>
                        <span>{source.detail}</span>
                      </span>
                      <button type="button" className="text" onClick={() => onOpen(source.view)}>Open →</button>
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}
          </article>
        ))}
      </div>
      <form className="composer" onSubmit={onSend}>
        <label>
          <span className="sr">Request</span>
          <textarea
            value={draft}
            placeholder="Ask Tisan to build or change something…"
            rows={2}
            onChange={(event) => onDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }}
          />
        </label>
        <button type="submit" className={sendPrimary ? "primary send" : "line send"} data-primary={sendPrimary ? "true" : undefined}>
          Send
        </button>
      </form>
    </section>
  );
}

function Rules() {
  return (
    <section className="block">
      <h2>How Tisan works</h2>
      <p className="intro">Rules Tisan follows on every build.</p>
      <div className="rules">
        {RULES.map((rule) => (
          <article key={rule.title}>
            <h3>{rule.title}</h3>
            <p>{rule.detail}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function Builds({ startPrimary, onStart }: { startPrimary: boolean; onStart: () => void }) {
  return (
    <>
      <PageHead title="Builds" lede="Every build Tisan has run, and how it did on the test gate." primary={startPrimary} onStart={onStart} />
      <section className="block tight">
        <h2>All builds</h2>
        <p className="intro">A build can deploy only when every check passes and the scan is clean.</p>
        <p className="empty">No builds yet.</p>
      </section>
      <Foot lines={["Tisan never merges a build. You review each draft pull request."]} />
    </>
  );
}

function Design({ startPrimary, onStart }: { startPrimary: boolean; onStart: () => void }) {
  return (
    <>
      <PageHead title="Design" lede="The locked Figma page Tisan builds from, the tokens it read, and the gaps it found." primary={startPrimary} onStart={onStart} />
      <section className="block tight">
        <h2>Locked page</h2>
        <p className="intro">Tisan reads one saved page. It never calls Figma while it builds.</p>
        <p className="empty">No builds yet.</p>
      </section>
      <Foot lines={["The saved page is the approved read. A live Figma call is not approved."]} />
    </>
  );
}

function Tests({ startPrimary, onStart }: { startPrimary: boolean; onStart: () => void }) {
  return (
    <>
      <PageHead title="Tests" lede="The test gate every build must pass before it can go any further." primary={startPrimary} onStart={onStart} />
      <section className="block tight">
        <h2>Checks</h2>
        <p className="intro">What each check proves, and how it did on the last build.</p>
        <p className="empty">No builds yet.</p>
      </section>
      <section className="block">
        <h2>Shared agent tests</h2>
        <p className="intro">Seven tests every Tervory agent will share.</p>
        <div className="waiting">
          <h3>Not added yet <Pill tone="quiet">Waiting</Pill></h3>
          <p>They need a gold set of examples and a trap-case runner first. Tisan will add them when those exist.</p>
        </div>
      </section>
      <Foot lines={["Tests run on every build. A failed check stops the build."]} />
    </>
  );
}

function Settings({ startPrimary, onStart }: { startPrimary: boolean; onStart: () => void }) {
  return (
    <>
      <PageHead title="Settings" lede="How Tisan builds, where it reads from, and when it may deploy." primary={startPrimary} onStart={onStart} />
      <section className="block tight">
        <h2>Building</h2>
        <div className="settings">
          <Setting title="Repository" detail="Tisan works in tervory-tank and opens draft pull requests only." control={<span className="repo">tervory-tank</span>} />
          <Setting title="Merging" detail="Tisan never merges. You review and merge each pull request." control={<Pill tone="quiet">Off</Pill>} />
          <Setting title="Public internet" detail="Tisan has no route to the public internet while it builds." control={<Pill tone="quiet">Off</Pill>} />
        </div>
      </section>
      <section className="block">
        <h2>Design source</h2>
        <div className="settings">
          <Setting title="Saved Figma page" detail="The approved read. Tisan builds from the saved copy in the repo." control={<Pill tone="solid">On</Pill>} />
          <Setting title="Live Figma reads" detail="Not approved. Tisan does not call Figma live." control={<Pill tone="quiet">Off</Pill>} />
        </div>
      </section>
      <section className="block">
        <h2>Deploy</h2>
        <div className="settings">
          <Setting title="Coolify deploy" detail="Deploys only after the tests pass and the Radix scan is clean." control={<Pill tone="quiet">Off</Pill>} />
          <Setting title="Deploy key" detail="Set by your admin in Plugins. Shown as dots once saved." control={<Pill tone="quiet">Not set</Pill>} />
        </div>
      </section>
      <Foot lines={["Keys and tokens are stored on the server, never in the app code."]} />
    </>
  );
}

function Setting({ title, detail, control }: { title: string; detail: string; control: ReactNode }) {
  return (
    <div className="setting">
      <div>
        <h3>{title}</h3>
        <p>{detail}</p>
      </div>
      <div className="setting-ctl">{control}</div>
    </div>
  );
}

function StartBuild({ onClose }: { onClose: () => void }) {
  return (
    <div className="scrim" role="presentation">
      <div className="popup build-popup" role="dialog" aria-modal="true" aria-labelledby="start-title">
        <div className="popup-head">
          <h2 id="start-title">Start a build</h2>
          <button type="button" className="text" onClick={onClose}>Close</button>
        </div>
        <p className="sub">Tell Tisan what to build. It uses the locked Figma page for the design.</p>
        <form onSubmit={(event) => event.preventDefault()}>
          <label className="stack-label">
            What should Tisan build?
            <textarea placeholder="e.g. A one-page site for a gunsmith in Boise, with a contact form." rows={4} />
          </label>
          <p className="kicker">Design</p>
          <p className="empty">No saved page yet.</p>
          <div className="next">
            <h3>What happens next</h3>
            <ol>
              <li>Tisan builds the site on a new branch.</li>
              <li>It runs every test: code checks, three screen sizes, accessibility and Lighthouse.</li>
              <li>It opens a draft pull request for you. It does not merge or deploy.</li>
            </ol>
          </div>
          <div className="dialog-actions">
            <button type="button" className="line" onClick={onClose}>Cancel</button>
            <button type="submit" className="primary" data-primary="true">Start build</button>
          </div>
        </form>
      </div>
    </div>
  );
}
