"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type DragEvent, type FormEvent, type ReactNode } from "react";
import { useFormStatus } from "react-dom";
import { MARKET_LAB_LOGO_SRC } from "./catalog";
import type { LinkGroupView, OnCard, PageModel } from "./model";
import { formatDay, formatRefreshed } from "./format";
import type { KeyFlags } from "./catalog";
import {
  PLUGIN_TABS,
  POPUP_TITLE,
  REFERENCE_LABEL,
  NEED_FILE,
  NEED_KEY,
  NEED_LINK,
  NEED_MCP,
  VAULT_OFF_MESSAGE,
  type AnswerSource,
  type ConnectionMeta,
  type FileMeta,
  type PluginPanel,
  type PluginTab,
  type SecretMeta,
  feedKeyCatalog,
  formatBytes,
  searchMarketLab,
} from "./plugins";
import {
  addLabConnection,
  addLabLink,
  removeLabConnection,
  removeLabFile,
  removeLabLink,
  removeLabSecret,
  saveLabKey,
} from "./plugin-actions";
import { SquadLinks } from "../squad-links";

const SECTIONS = [
  { id: "overview", label: "Overview" },
  { id: "on", label: "On" },
  { id: "linked", label: "Linked pages" },
] as const;

const TOP_LINKS = [
  { href: "/ledger", label: "Usage" },
  { href: "/tasks", label: "Tasks" },
  { href: "/chats", label: "Chats" },
] as const;

const BOTTOM_LINKS = [
  { href: "/onboarding", label: "Workspace setup" },
] as const;

type Fact = { text: string; href: string; name: string };
type Turn = { facts?: Fact[]; role: "user" | "assistant"; content: string; sources?: AnswerSource[]; error?: boolean };

function RefreshButton() {
  const { pending } = useFormStatus();
  return (
    <button className="ml-refresh" type="submit" disabled={pending}>
      {pending ? "Refreshing…" : "Refresh feeds"}
    </button>
  );
}

function SearchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <circle cx="7.5" cy="7.5" r="4.75" fill="none" stroke="#6b7590" strokeWidth="1.5" />
      <path d="M11.2 11.2 L15.5 15.5" fill="none" stroke="#6b7590" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function scrollToSection(id: string) {
  const root = document.querySelector<HTMLElement>("[data-scroll-root]");
  const target = document.getElementById(id);
  if (!root || !target) return;
  const top = target.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop;
  root.scrollTo({ top, behavior: "smooth" });
}

function OnCardView({ card }: { card: OnCard }) {
  const bare = !card.headline && card.lines.length === 0 && card.notices.length === 0 && !card.problem;
  const meta = [card.cadence, card.asOf ? `Updated ${card.asOf}` : null, card.sourceLabel].filter(Boolean).join(" · ");
  return (
    <article className="ml-card">
      <div className="ml-card-top">
        <h3>{card.title}</h3>
        <span className="ml-chip">
          <span className="ml-dot" aria-hidden="true" />
          On
        </span>
      </div>
      <p className="ml-summary">{card.summary}</p>
      {card.headline ? <p className="ml-value"><a href={card.sourceHref}>{card.headline}</a></p> : null}
      {card.detail ? <p className="ml-detail">{card.detail}</p> : null}
      {card.lines.length ? (
        <ul className="ml-lines">
          {card.lines.map((line) => (
            <li key={`${line.label}-${line.value}`}>
              <span>{line.label}</span>
              <strong>{line.value}</strong>
            </li>
          ))}
        </ul>
      ) : null}
      {card.notices.length ? (
        <ul className="ml-notices">
          {card.notices.slice(0, 3).map((notice) => (
            <li key={notice.title}>{notice.title}</li>
          ))}
        </ul>
      ) : null}
      {bare ? <p className="ml-value ml-empty-value">No reading yet</p> : null}
      {card.problem ? <p className="ml-problem">{card.problem}</p> : null}
      <p className="ml-card-meta">{meta}</p>
    </article>
  );
}

function ChatBox() {
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [sending, setSending] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (sending) return;
    if (!text) return;
    const next: Turn[] = [...turns, { role: "user", content: text }];
    setTurns(next);
    setDraft("");
    setSending(true);
    try {
      const res = await fetch("/market-lab/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      const data = (await res.json().catch(() => null)) as { content?: string; error?: string; facts?: Fact[]; sources?: AnswerSource[] } | null;
      if (!res.ok || !data?.content) {
        setTurns([...next, { role: "assistant", content: data?.error || "Market Lab could not answer.", error: true, sources: [] }]);
      } else {
        setTurns([...next, { role: "assistant", content: data.content, facts: data.facts ?? [], sources: data.sources ?? [] }]);
      }
    } catch {
      setTurns([...next, { role: "assistant", content: "Market Lab could not answer. Nothing was changed.", error: true, sources: [] }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="ml-chat" aria-label="Ask Market Lab">
      <h2>Ask Market Lab</h2>
      {turns.length ? (
        <div className="ml-thread">
          {turns.map((turn, index) =>
            turn.role === "user" ? (
              <div className="ml-bubble ml-you" key={`you-${index}`}>
                <p className="ml-who">You</p>
                <p className="ml-said">{turn.content}</p>
              </div>
            ) : (
              <div className="ml-bubble ml-answer" key={`lab-${index}`}>
                <p className="ml-who">Market Lab</p>
                <p className="ml-said">{turn.content}</p>
                {turn.facts?.length ? <ul className="ml-lines">{turn.facts.map((fact, index) => <li key={`${fact.href}-${index}`}><a href={fact.href}>{fact.text}</a></li>)}</ul> : null}
                <div className="ml-refs">
                  <p className="ml-refs-label">{REFERENCE_LABEL}</p>
                  {turn.sources && turn.sources.length ? turn.sources.map((source) => (
                    <div className="ml-ref" key={`${source.name}-${source.href ?? ""}`}>
                      <span className="ml-ref-name">{source.name}</span>
                      {source.href ? (
                        <a className="ml-ref-link" href={source.href}>
                          Open stored source
                        </a>
                      ) : (
                        <span />
                      )}
                      {source.date ? <span className="ml-ref-date">{source.date}</span> : null}
                    </div>
                  )) : <p>No sources for this answer.</p>}
                </div>
              </div>
            ),
          )}
        </div>
      ) : null}
      <form className="ml-composer" onSubmit={onSubmit}>
        <label className="ml-sr" htmlFor="ml-ask">
          Ask Market Lab
        </label>
        <input
          id="ml-ask"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask Market Lab about your market…"
          disabled={sending}
        />
        <button className="ml-send" type="submit" disabled={sending}>
          {sending ? "Sending…" : "Send"}
        </button>
      </form>
    </section>
  );
}

function PluginsDialog({
  open,
  tab,
  plugins,
  envKeys,
  onTab,
  onClose,
}: {
  open: boolean;
  tab: PluginTab;
  plugins: PluginPanel;
  envKeys: KeyFlags;
  onTab: (tab: PluginTab) => void;
  onClose: () => void;
}) {
  const router = useRouter();
  const titleId = useId();
  const fileRef = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState("");
  const [cadence, setCadence] = useState<"Daily" | "Weekly">("Daily");
  const [signIn, setSignIn] = useState<"provider" | "token">("token");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) setNote("");
  }, [open]);

  if (!open) return null;

  async function finish(result: { ok: boolean; message: string }) {
    setNote(result.message);
    if (result.ok) router.refresh();
  }

  async function onSaveKey(event: FormEvent<HTMLFormElement>, name: string, purpose: string) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const value = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    form.reset();
    if (!value.trim()) {
      setNote(NEED_KEY);
      return;
    }
    setBusy(true);
    try {
      await finish(await saveLabKey(name, value, purpose));
    } finally {
      setBusy(false);
    }
  }

  async function onRemoveKey(name: string) {
    if (!plugins.enabled || busy) return;
    setBusy(true);
    try {
      await finish(await removeLabSecret(name));
    } finally {
      setBusy(false);
    }
  }

  async function onFiles(list: FileList | File[] | null) {
    if (!plugins.enabled || busy) return;
    const files = list ? Array.from(list) : [];
    if (fileRef.current) fileRef.current.value = "";
    if (!files.length) {
      setNote(NEED_FILE);
      return;
    }
    const body = new FormData();
    for (const file of files) body.append("file", file);
    setBusy(true);
    setNote("Reading your files. Large reports can take a little longer.");
    try {
      const response = await fetch("/market-lab/files", { method: "POST", body });
      await finish(await response.json());
    } catch {
      setNote("The upload could not be confirmed. Check the file list before trying again.");
    } finally {
      setBusy(false);
    }
  }

  async function onAddLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const url = form.querySelector<HTMLInputElement>("input[name=link]")?.value.trim() ?? "";
    const label = form.querySelector<HTMLInputElement>("input[name=label]")?.value.trim() ?? "";
    form.reset();
    setCadence("Daily");
    if (!url) {
      setNote(NEED_LINK);
      return;
    }
    setBusy(true);
    try {
      await finish(await addLabLink(url, label, cadence === "Daily" ? "daily" : "weekly"));
    } finally {
      setBusy(false);
    }
  }

  async function onAddConnection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!plugins.enabled || busy) return;
    const form = event.currentTarget;
    const name = form.querySelector<HTMLInputElement>("input[name=connection-name]")?.value.trim() ?? "";
    const url = form.querySelector<HTMLInputElement>("input[name=server]")?.value.trim() ?? "";
    const token = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
    form.reset();
    if (!name || !url) {
      setNote(NEED_MCP);
      return;
    }
    setBusy(true);
    try {
      await finish(await addLabConnection(name, url, signIn, token));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ml-shade" onClick={onClose}>
      <div
        className="ml-plugins"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="ml-sheet-handle" aria-hidden="true" />
        <div className="ml-plugins-head">
          <div>
            <h2 id={titleId}>{POPUP_TITLE}</h2>
            <p>Manage saved keys, files, links and connections. Answers use stored feeds and files marked Learned.</p>
          </div>
          <button className="ml-text" type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="ml-tabs" role="tablist">
          {PLUGIN_TABS.map((name) => (
            <button
              key={name}
              className={name === tab ? "ml-tab active" : "ml-tab"}
              type="button"
              role="tab"
              aria-selected={name === tab}
              onClick={() => {
                setNote("");
                onTab(name);
              }}
            >
              {name}
            </button>
          ))}
        </div>
        {note ? <p className="ml-status">{note}</p> : null}
        {!plugins.enabled ? (
          <p className="ml-lead">{plugins.message || VAULT_OFF_MESSAGE}</p>
        ) : tab === "Access keys" ? (
          <div className="ml-tab-body">
            <p className="ml-lead">Keys supplied by public data providers and your suppliers. Once saved, a key is hidden and cannot be read back.</p>
            <div className="ml-plist">
              {feedKeyCatalog().map((row) => {
                const saved = secretFor(plugins.secrets, row.name);
                const fromEnv = Boolean(envKeys[row.name]);
                const connected = Boolean(saved || fromEnv);
                return (
                  <form className="ml-prow" key={row.name} onSubmit={(event) => onSaveKey(event, row.name, row.summary)}>
                    <div className="ml-prow-top">
                      <h3>{row.title}</h3>
                      <span className={connected ? "ml-chip" : "ml-chip ml-chip-off"}>
                        <span className="ml-dot" aria-hidden="true" />
                        {connected ? "Key provided" : "Not set"}
                      </span>
                    </div>
                    <p>{row.summary}</p>
                    {saved ? <p className="ml-secret">{saved.mask} Saved {saved.savedAt ? formatDay(saved.savedAt) : ""}</p> : null}
                    {fromEnv ? <p className="ml-secret">Provided by your workspace administrator</p> : null}
                    <div className="ml-keyline">
                      <label className="ml-sr" htmlFor={`key-${row.name}`}>
                        {row.title} key
                      </label>
                      <input id={`key-${row.name}`} type="password" autoComplete="off" placeholder={saved ? "Replace key" : "Paste key"} disabled={busy} />
                      <button className="ml-outline" type="submit" disabled={busy}>
                        {saved ? "Replace" : "Save"}
                      </button>
                      {saved ? (
                        <button className="ml-text" type="button" disabled={busy} onClick={() => onRemoveKey(row.name)}>
                          Remove
                        </button>
                      ) : null}
                    </div>
                  </form>
                );
              })}
              {plugins.secrets.filter((row) => !isFeedKey(row.name)).map((row) => (
                <div className="ml-prow" key={row.name}>
                  <div className="ml-prow-top">
                    <h3>{row.name}</h3>
                    <span className="ml-chip">
                      <span className="ml-dot" aria-hidden="true" />
                      Saved
                    </span>
                  </div>
                  <p>{row.purpose}</p>
                  <p className="ml-secret">{row.mask}{row.savedAt ? ` Saved ${formatDay(row.savedAt)}` : ""}</p>
                  <button className="ml-text" type="button" disabled={busy} onClick={() => onRemoveKey(row.name)}>
                    Remove
                  </button>
                </div>
              ))}
            </div>
            <form
              className="ml-supplier"
              onSubmit={(event) => {
                event.preventDefault();
                const form = event.currentTarget;
                const name = form.querySelector<HTMLInputElement>("input[name=supplier]")?.value.trim() ?? "";
                const value = form.querySelector<HTMLInputElement>("input[type=password]")?.value ?? "";
                form.reset();
                if (!name || !value.trim()) {
                  setNote(NEED_KEY);
                  return;
                }
                setBusy(true);
                void saveLabKey(name, value, "Supplier key")
                  .then((result) => finish(result))
                  .finally(() => setBusy(false));
              }}
            >
              <h3>Add a supplier key</h3>
              <p>For a supplier that gives you an access key.</p>
              <div className="ml-keyline">
                <label>
                  Supplier name
                  <input name="supplier" type="text" autoComplete="off" placeholder="Supplier name" disabled={busy} />
                </label>
                <label>
                  Key
                  <input type="password" autoComplete="off" placeholder="Paste key (hidden once saved)" disabled={busy} />
                </label>
                <button className="ml-primary" type="submit" disabled={busy}>
                  Save key
                </button>
              </div>
            </form>
            <p className="ml-fine">Saved keys are hidden and cannot be read back.</p>
          </div>
        ) : tab === "Files" ? (
          <div className="ml-tab-body">
            <h3 className="ml-subhead">Files</h3>
            <p className="ml-lead">Upload price lists, inventory, reports or rules. CSV, XLSX, and PDF only.</p>
            <div
              className="ml-drop"
              onDragOver={(event: DragEvent) => event.preventDefault()}
              onDrop={(event: DragEvent) => {
                event.preventDefault();
                void onFiles(event.dataTransfer.files);
              }}
            >
              <p className="ml-drop-title">Drag CSV, XLSX or PDF files here</p>
              <p className="ml-or">or</p>
              <button className="ml-primary" type="button" disabled={busy} onClick={() => fileRef.current?.click()}>
                Choose files
              </button>
              <input
                ref={fileRef}
                className="ml-sr"
                type="file"
                accept=".csv,.xlsx,.pdf,text/csv,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                multiple
                onChange={() => void onFiles(fileRef.current?.files ?? null)}
              />
            </div>
            {plugins.files.length ? (
              <div className="ml-plist">
                {plugins.files.map((file) => (
                  <FileRow key={file.id} file={file} busy={busy} onRemove={() => void removeLabFile(file.id).then((result) => finish(result))} />
                ))}
              </div>
            ) : (
              <p className="ml-fine">No files stored.</p>
            )}
          </div>
        ) : tab === "Source links" ? (
          <div className="ml-tab-body">
            <h3 className="ml-subhead">Source links</h3>
            <p className="ml-lead">Save useful pages here.</p>
            <form className="ml-linkform" onSubmit={onAddLink}>
              <label>
                Link
                <input name="link" type="url" placeholder="Paste a link, e.g. https://…" disabled={busy} />
              </label>
              <label>
                Label (optional)
                <input name="label" type="text" placeholder="e.g. Texas seasons" disabled={busy} />
              </label>
              <div>
                <p className="ml-field-label">Preferred check frequency</p>
                <div className="ml-choice">
                  <button className={cadence === "Daily" ? "active" : ""} type="button" onClick={() => setCadence("Daily")}>
                    Daily
                  </button>
                  <button className={cadence === "Weekly" ? "active" : ""} type="button" onClick={() => setCadence("Weekly")}>
                    Weekly
                  </button>
                </div>
              </div>
              <button className="ml-primary" type="submit" disabled={busy}>
                Add link
              </button>
            </form>
            {plugins.links.length ? (
              <div className="ml-plist">
                {plugins.links.map((link) => (
                  <div className="ml-prow" key={link.id}>
                    <h3>{link.label}</h3>
                    <p>{link.url}</p>
                    <p className="ml-fine">
                      {link.frequency === "daily" ? "Daily" : "Weekly"} · {link.status}
                      {link.lastChecked ? ` · ${formatDay(link.lastChecked)}` : ""}
                    </p>
                    <button className="ml-text" type="button" disabled={busy} onClick={() => void removeLabLink(link.id).then((result) => finish(result))}>
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="ml-fine">No links stored.</p>
            )}
          </div>
        ) : (
          <div className="ml-tab-body">
            <h3 className="ml-subhead">Connections</h3>
            <p className="ml-lead">Save connection details for outside tools and data sources.</p>
            {plugins.connections.length ? (
              <div className="ml-plist">
                {plugins.connections.map((item) => (
                  <ConnectionRow key={item.id} item={item} busy={busy} onRemove={() => void removeLabConnection(item.id).then((result) => finish(result))} />
                ))}
              </div>
            ) : (
              <p className="ml-fine">No connections stored.</p>
            )}
            <form className="ml-mcp" onSubmit={onAddConnection}>
              <h3>Add connection</h3>
              <div className="ml-mcp-row">
                <label>
                  Name
                  <input name="connection-name" type="text" placeholder="e.g. Supplier catalogue" disabled={busy} />
                </label>
                <label>
                  Server address
                  <input name="server" type="url" placeholder="https://…" disabled={busy} />
                </label>
              </div>
              <div className="ml-mcp-row">
                <div>
                  <p className="ml-field-label">How it signs in</p>
                  <div className="ml-choice">
                    <button className={signIn === "provider" ? "active" : ""} type="button" onClick={() => setSignIn("provider")}>
                      Sign in with the provider
                    </button>
                    <button className={signIn === "token" ? "active" : ""} type="button" onClick={() => setSignIn("token")}>
                      Access key
                    </button>
                  </div>
                </div>
                {signIn === "token" ? (
                  <label>
                    Access key
                    <input type="password" autoComplete="off" placeholder="Paste access key (hidden once saved)" disabled={busy} />
                  </label>
                ) : null}
                <button className="ml-primary" type="submit" disabled={busy}>
                  Add connection
                </button>
              </div>
              <p className="ml-fine">A saved access key is shown only as dots, and is never shown again.</p>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}

export function MarketLabView({
  model,
  plugins,
  envKeys,
  refresh,
  workspaceName,
  quarantine,
  warnings,
}: {
  model: PageModel;
  plugins: PluginPanel;
  envKeys: KeyFlags;
  refresh: () => Promise<void>;
  workspaceName: string | null;
  quarantine?: ReactNode;
  warnings?: ReactNode;
}) {
  const [menu, setMenu] = useState(false);
  const [active, setActive] = useState("overview");
  const [pluginsOpen, setPluginsOpen] = useState(false);
  const [tab, setTab] = useState<PluginTab>("Access keys");
  const [query, setQuery] = useState("");
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const searchId = useId();

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setMenu(false);
      setPluginsOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const root = document.querySelector<HTMLElement>("[data-scroll-root]");
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible?.target.id) setActive(visible.target.id);
      },
      { root, rootMargin: "-15% 0px -55% 0px", threshold: [0.15, 0.4] },
    );
    for (const section of SECTIONS) {
      const el = document.getElementById(section.id);
      if (el) observer.observe(el);
    }
    const held = document.getElementById("quarantine");
    if (held) observer.observe(held);
    return () => observer.disconnect();
  }, []);

  const scope = searchMarketLab(model, query);
  const onCards = [...model.featured, ...model.cards].filter((card) => scope.onIds.includes(card.id));
  const groups = model.groups
    .map((group) => ({ ...group, pages: group.pages.filter((page) => scope.linkIds.includes(page.id)) }))
    .filter((group) => group.pages.length > 0);
  const offFeeds = model.off.filter((feed) => scope.offIds.includes(feed.id));
  const searching = query.trim().length > 0;
  const noHits = searching && onCards.length === 0 && offFeeds.length === 0 && groups.length === 0;
  const accountName = workspaceName?.trim() || "Your account";

  function openSection(id: string) {
    setMenu(false);
    setPluginsOpen(false);
    setActive(id);
    scrollToSection(id);
  }

  return (
    <div className="ml">
      <header className="ml-top">
        <img src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} />
        <button className="ml-menu" type="button" onClick={() => setMenu(true)}>
          Menu
        </button>
      </header>
      <button className="ml-dim" type="button" hidden={!menu} aria-label="Close menu" onClick={() => setMenu(false)} />
      <aside className={menu ? "ml-panel open" : "ml-panel"}>
        <div className="ml-brand">
          <img className="ml-agent" src={MARKET_LAB_LOGO_SRC} alt="Market Lab" width={242} height={40} />
        </div>
        <button className="ml-close" type="button" onClick={() => setMenu(false)}>
          Close
        </button>
        <nav className="ml-nav" aria-label="Account">
          <p className="ml-account">{accountName}</p>
          {TOP_LINKS.map((link) => (
            <a key={link.label} className="ml-link" href={link.href} onClick={() => setMenu(false)}>
              {link.label}
            </a>
          ))}
        </nav>
        <SquadLinks className="ml-nav squad-nav" linkClassName="ml-link" />
        <div className="ml-rule" />
        <nav className="ml-nav" aria-label="Market Lab">
          <p className="ml-kicker">Market Lab</p>
          {SECTIONS.map((section) => (
            <button
              key={section.id}
              className={!pluginsOpen && active === section.id ? "ml-navbtn active" : "ml-navbtn"}
              type="button"
              onClick={() => openSection(section.id)}
            >
              {section.label}
            </button>
          ))}
          <button
            className={pluginsOpen ? "ml-navbtn active" : "ml-navbtn"}
            type="button"
            onClick={() => {
              setMenu(false);
              setPluginsOpen(true);
            }}
          >
            Plugins
          </button>
          {quarantine ? (
            <button
              className={!pluginsOpen && active === "quarantine" ? "ml-navbtn active" : "ml-navbtn"}
              type="button"
              onClick={() => openSection("quarantine")}
            >
              Feed review
            </button>
          ) : null}
        </nav>
        <div className="ml-spacer" />
        <div className="ml-rule" />
        <div className="ml-bottom">
          <img className="ml-logo" src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} />
          <nav className="ml-nav" aria-label="Workspace">
            {BOTTOM_LINKS.map((link) => (
              <a key={link.label} className="ml-link" href={link.href} onClick={() => setMenu(false)}>
                {link.label}
              </a>
            ))}
          </nav>
        </div>
      </aside>
      <div className="ml-main" data-scroll-root>
        <div className="ml-body">
          <header className="ml-header" id="overview">
            <div>
              <h1>Market Lab</h1>
              <p className="ml-intro">Public data feeds that show how your market is moving. Search what Market Lab has, or ask it a question.</p>
            </div>
            <div className="ml-search">
              <SearchIcon />
              <label className="ml-sr" htmlFor={searchId}>
                Search Market Lab
              </label>
              <input
                id={searchId}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search feeds and linked pages"
              />
            </div>
          </header>
          {warnings}
          <ChatBox />
          <RefreshStatus status={model.refreshStatus} />
          <div className="ml-counts">
            <article>
              <p className="ml-count">{model.onCount}</p>
              <p>On</p>
            </article>
            <article>
              <p className="ml-count">{model.linkedCount}</p>
              <p>Linked pages</p>
            </article>
          </div>
          {model.loadError ? (
            <section className="ml-note">
              <h2>Feeds could not load</h2>
              <p>We could not read the feed status. Nothing has changed. Try Refresh feeds, or check back in a few minutes.</p>
            </section>
          ) : null}
          {noHits ? <p className="ml-none">Nothing in Market Lab matches that.</p> : null}
          {(!searching || onCards.length > 0) ? <section id="on" className="ml-section">
            <h2>On</h2>
            {onCards.length > 0 ? <p className="ml-lead">
              These feeds stored observations in their last refresh. Open a feed to check its sources.
            </p> : null}
            {model.onEmpty && !searching ? (
              <div className="ml-note">
                <h3>No feed data yet</h3>
                <p>No observations are stored from the latest refresh. Choose Refresh feeds to collect data.</p>
              </div>
            ) : onCards.length ? (
              <div className="ml-grid">
                {onCards.map((card) => (
                  <OnCardView key={card.id} card={card} />
                ))}
              </div>
            ) : null}
          </section> : null}
          {offFeeds.length ? <section className="ml-section" aria-label="Off feeds">
            <h2>Off</h2>
            <div className="ml-grid">{offFeeds.map((feed) => <article className="ml-card" key={feed.id}>
              <h3>{feed.title}</h3><p>{feed.reason}</p>
            </article>)}</div>
          </section> : null}
          {groups.length > 0 ? <section id="linked" className="ml-section">
            <h2>Linked pages</h2>
            <p className="ml-lead">Open these sources on their publishers’ websites. Their figures are not included in answers from stored feeds.</p>
            <div className="ml-links">
              {groups.map((group) => (
                <GroupCard
                  key={group.id}
                  group={group}
                  open={Boolean(openGroups[group.id])}
                  onToggle={() => setOpenGroups((current) => ({ ...current, [group.id]: !current[group.id] }))}
                />
              ))}
            </div>
          </section> : null}
          {quarantine}
          <footer className="ml-foot">
            <p>
              Stored public observations only. Sources that do not permit storage stay linked.{" "}
              {model.refreshedLabel === "Not refreshed yet" ? "Not refreshed yet." : `Last refreshed ${model.refreshedLabel}.`}
            </p>
            <form action={refresh}>
              <RefreshButton />
            </form>
          </footer>
        </div>
      </div>
      <PluginsDialog
        open={pluginsOpen}
        tab={tab}
        plugins={plugins}
        envKeys={envKeys}
        onTab={setTab}
        onClose={() => setPluginsOpen(false)}
      />
    </div>
  );
}

function secretFor(secrets: SecretMeta[], name: string): SecretMeta | undefined {
  return secrets.find((row) => row.name === name);
}

function isFeedKey(name: string): boolean {
  return feedKeyCatalog().some((row) => row.name === name);
}

function FileRow({ file, busy, onRemove }: { file: FileMeta; busy: boolean; onRemove: () => void }) {
  const count = file.rows != null ? `${file.rows} ${file.rows === 1 ? "row" : "rows"}` : file.pages != null ? `${file.pages} ${file.pages === 1 ? "page" : "pages"}` : "";
  return (
    <div className="ml-prow">
      <h3>{file.status === "Learned" ? <a href={`/market-lab/sources/${file.id}`}>{file.name}</a> : file.name}</h3>
      <p className="ml-fine">
        {file.status}{file.reason ? `: ${file.reason}` : ""} · {formatBytes(file.size)}
        {count ? ` · ${count}` : ""}
        {file.uploadedAt ? ` · ${formatRefreshed(file.uploadedAt)}` : ""}
      </p>
      <button className="ml-text" type="button" disabled={busy} onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

function ConnectionRow({ item, busy, onRemove }: { item: ConnectionMeta; busy: boolean; onRemove: () => void }) {
  return (
    <div className="ml-prow">
      <h3>{item.name}</h3>
      <p>{item.serverUrl}</p>
      <p className="ml-fine">
        {item.authType === "token" ? "Access key" : "Sign in with the provider"} · {item.status}
        {item.hasToken ? " · ••••••••" : ""}
        {item.lastUsed ? ` · ${formatDay(item.lastUsed)}` : ""}
      </p>
      <button className="ml-text" type="button" disabled={busy} onClick={onRemove}>
        Remove
      </button>
    </div>
  );
}

function GroupCard({ group, open, onToggle }: { group: LinkGroupView; open: boolean; onToggle: () => void }) {
  return (
    <article className="ml-linkcard">
      <h3>{group.title}</h3>
      <p>{group.lead}</p>
      <button className="ml-expand" type="button" onClick={onToggle} aria-expanded={open}>
        {open ? "Hide links" : "Show links"}
      </button>
      {open ? (
        <ul>
          {group.pages.map((page) => (
            <li key={page.id}>
              <a href={page.href}>{page.label}</a>
              <span>{page.note}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

function RefreshStatus({ status }: { status?: string | null }) {
  const router = useRouter();
  const busy = status === "queued" || status === "running";
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => router.refresh(), 10000);
    return () => clearInterval(timer);
  }, [busy, router]);
  if (busy) return <p role="status">The feeds are refreshing. Stored observations appear as each feed finishes.</p>;
  if (status === "failed") return <p role="status">The refresh stopped before it finished. You can try Refresh feeds again.</p>;
  return null;
}
