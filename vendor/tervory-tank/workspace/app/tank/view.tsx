"use client";

import { useId, useState, type FormEvent, type ReactNode } from "react";
import { SquadLinks } from "../squad-links";
import { searchTank, VAULT_LINE } from "./records";
import { MODEL_KEY_LINE, modelsMatching, priceLine, type ModelRow } from "./squads";
import type { WorkersFeed } from "./workers";
import { WorkersPanel } from "./workers-panel";

// Workspace destinations are shared across product hosts.
// These point at the nearest existing workspace routes.
const TOP_LINKS = [
  { href: "https://app.tervory.com/", label: "Dashboard" },
  { href: "/matters", label: "Projects" },
  { href: "/chats", label: "Chats" },
] as const;

const MIDDLE_LINKS = [
  { href: "/ledger", label: "Ledger" },
  { href: "/tasks", label: "Tasks" },
  { href: "/data", label: "Data" },
] as const;

const BOTTOM_LINKS = [
  { href: "/onboarding", label: "Settings" },
  { href: "https://tervory.com", label: "Support" },
] as const;

type Turn = { role: "user" | "assistant"; content: string; error?: boolean; sources?: string[] };

function SearchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <circle cx="7.5" cy="7.5" r="4.75" fill="none" stroke="#6b7590" strokeWidth="1.5" />
      <path d="M11.2 11.2 L15.5 15.5" fill="none" stroke="#6b7590" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function AskTank() {
  const askId = useId();
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sending, setSending] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;
    const next: Turn[] = [...turns, { role: "user", content: text }];
    setTurns(next);
    setDraft("");
    setSending(true);
    try {
      const res = await fetch("/api/tank/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ messages: next.map(({ role, content }) => ({ role, content })) }),
      });
      const data = (await res.json().catch(() => null)) as { content?: string; error?: string } | null;
      if (!res.ok || !data?.content) {
        setTurns([...next, { role: "assistant", content: data?.error || "Tank could not answer. Nothing was changed.", error: true }]);
      } else {
        setTurns([...next, { role: "assistant", content: data.content }]);
      }
    } catch {
      setTurns([...next, { role: "assistant", content: "Tank could not answer. Nothing was changed.", error: true }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="tk-ask" aria-label="Ask Tank">
      <h2>Ask Tank</h2>
      {turns.length ? (
        <div className="tk-thread">
          {turns.map((turn, index) => (
            <div className={turn.role === "user" ? "tk-bubble tk-you" : "tk-bubble tk-answer"} key={`${turn.role}-${index}`}>
              <p className="tk-who">{turn.role === "user" ? "You" : "Tank"}</p>
              <p className={turn.error ? "tk-said tk-err" : "tk-said"}>{turn.content}</p>
              {turn.role === "assistant" ? (
                <div className="tk-sources">
                  <p>Sources</p>
                  {turn.sources && turn.sources.length ? turn.sources.map((source) => <p key={source}>{source}</p>) : <p>No sources for this answer.</p>}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      <form className="tk-composer" onSubmit={onSubmit}>
        <label className="tk-sr" htmlFor={askId}>
          Ask Tank
        </label>
        <input
          id={askId}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask Tank"
          disabled={sending}
        />
        <button className="tk-send" type="submit" disabled={sending || !draft.trim()}>
          {sending ? "Sending…" : "Send"}
        </button>
      </form>
    </section>
  );
}

export function TankView({
  workspaceName,
  graduation,
  models,
  modelsUnavailable = false,
  workers = { available: false, tasks: [] },
}: {
  workspaceName: string | null;
  graduation?: ReactNode;
  models: ModelRow[];
  modelsUnavailable?: boolean;
  workers?: WorkersFeed;
}) {
  const searchId = useId();
  const [menu, setMenu] = useState(false);
  const [query, setQuery] = useState("");
  const [plugins, setPlugins] = useState(false);
  const accountName = workspaceName?.trim() || "Your account";
  const hits = searchTank(query);
  const shownModels = modelsMatching(query, models);
  const searching = query.trim().length > 0;
  const noHits = searching && hits.length === 0 && shownModels.length === 0;

  function closeMenu() {
    setMenu(false);
  }

  return (
    <div className="tk">
      <header className="tk-top">
        <img src="/brand/tank-on-dark.png" alt="Tank" width={150} height={37} />
        <button className="tk-menu" type="button" onClick={() => setMenu(true)}>
          Menu
        </button>
      </header>
      <button className="tk-dim" type="button" hidden={!menu} aria-label="Close menu" onClick={closeMenu} />
      <aside className={menu ? "tk-panel open" : "tk-panel"}>
        <div className="tk-brand">
          <img src="/brand/tank-on-dark.png" alt="Tank" width={186} height={45} />
        </div>
        <button className="tk-close" type="button" onClick={closeMenu}>
          Close
        </button>
        <nav className="tk-nav" aria-label="Account">
          <p className="tk-account">{accountName}</p>
          {TOP_LINKS.map((link) => (
            <a key={link.label} className="tk-link" href={link.href} onClick={closeMenu}>
              {link.label}
            </a>
          ))}
        </nav>
        <SquadLinks className="tk-nav squad-nav" linkClassName="tk-link" />
        <div className="tk-rule" />
        <nav className="tk-nav" aria-label="Tank">
          <p className="tk-kicker">Tank</p>
          {MIDDLE_LINKS.map((link) => (
            <a key={link.label} className="tk-link" href={link.href} onClick={closeMenu}>
              {link.label}
            </a>
          ))}
          <button
            className={plugins ? "tk-navbtn active" : "tk-navbtn"}
            type="button"
            aria-current={plugins ? "true" : undefined}
            onClick={() => {
              closeMenu();
              setPlugins(true);
              document.getElementById("plugins")?.scrollIntoView({ block: "nearest" });
            }}
          >
            Plugins
          </button>
        </nav>
        <div className="tk-spacer" />
        <div className="tk-rule" />
        <img className="tk-tervory" src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} />
        <nav className="tk-nav" aria-label="Workspace">
          {BOTTOM_LINKS.map((link) => (
            <a key={link.label} className="tk-link" href={link.href} onClick={closeMenu}>
              {link.label}
            </a>
          ))}
        </nav>
      </aside>
      <div className="tk-main" data-scroll-root>
        <div className="tk-body">
          <header className="tk-header">
            <div>
              <h1>Tank</h1>
              <p className="tk-intro">Search what Tank keeps, or ask it a question.</p>
            </div>
            <div className="tk-search">
              <SearchIcon />
              <label className="tk-sr" htmlFor={searchId}>
                Search Tank
              </label>
              <input
                id={searchId}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search agents, models, ledger and tasks"
              />
            </div>
          </header>
          {searching && hits.length ? (
            <ul className="tk-hits">
              {hits.map((hit) => (
                <li key={hit.id}>
                  {hit.href ? <a href={hit.href}>{hit.title}</a> : <span>{hit.title}</span>}
                  <p>{hit.detail}</p>
                </li>
              ))}
            </ul>
          ) : null}
          {noHits ? <p className="tk-none">Nothing in Tank matches that.</p> : null}
          <AskTank />
          <p className={plugins ? "tk-vault on" : "tk-vault"} id="plugins">
            {VAULT_LINE}
          </p>
          {!searching || shownModels.length || modelsUnavailable ? (
            <section className="tk-section" aria-label="Models">
              <h2>Models</h2>
              <p className="tk-lead">{MODEL_KEY_LINE}</p>
              {modelsUnavailable ? (
                <p className="tk-none">Tank could not list models.</p>
              ) : shownModels.length ? (
                <ul className="tk-models">
                  {shownModels.map((model) => (
                    <li key={model.name}>
                      <span className="tk-model-name">{model.name}</span>
                      {model.providerModel ? <span className="tk-model-provider">{model.providerModel}</span> : null}
                      <span className="tk-model-price">{priceLine(model.inputPerM, model.outputPerM)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="tk-none">No models on the roster.</p>
              )}
            </section>
          ) : null}
          {!searching ? graduation : null}
          {!searching ? <WorkersPanel feed={workers} /> : null}
        </div>
      </div>
    </div>
  );
}
