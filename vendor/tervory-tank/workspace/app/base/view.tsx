"use client";

import { useEffect, useState } from "react";
import { useFormStatus } from "react-dom";
import { batchLabel, formatCount, type BatchRow, type PageModel } from "./model";
import { AskBase, RecordSearch, type AskAction, type SearchAction } from "./ask";
import type { PluginPanel } from "./plugin-panel";
import { PluginsDialog, type PluginsTab } from "./plugins";
import { SquadLinks } from "../squad-links";
import { BatchWorkflow } from "./batch-workflow";
import { ContextComparison } from "./context-comparison";
import { ReferenceWorkflow } from "./reference-workflow";

export const WORKSPACE_HREF = "https://app.tervory.com/";

/** Standard panel items, the same on every squad page. Top: account name, then these. Bottom: settings and support. */
export const STANDARD_TOP = [
  { href: "https://app.tervory.com/", label: "Dashboard" },
  { href: "/matters", label: "Projects" },
  { href: "/chats", label: "Chats" },
] as const;
export const STANDARD_BOTTOM = [
  { href: "/settings", label: "Settings" },
  { href: "https://tervory.com", label: "Support" },
] as const;

const SECTIONS = ["overview", "search", "ask", "batches", "skills"] as const;

function RefreshButton({ hidden }: { hidden: boolean }) {
  const { pending } = useFormStatus();
  return (
    <button className="bcp-primary" type="submit" disabled={pending} aria-hidden={hidden || undefined} tabIndex={hidden ? -1 : undefined}>
      {pending ? "Refreshing…" : "Refresh"}
    </button>
  );
}

function scrollToSection(id: string) {
  const root = document.querySelector<HTMLElement>("[data-scroll-root]");
  const target = document.getElementById(id);
  if (!root || !target) return;
  const top = target.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop;
  root.scrollTo({ top, behavior: "smooth" });
}

function Status({ row }: { row: BatchRow }) {
  return (
    <span className={row.settled ? "bcp-pill on" : "bcp-pill"}>
      <i aria-hidden="true" />
      {row.status}
    </span>
  );
}

function skippedText(value: number | null): string {
  return value === null ? "Not reported" : formatCount(value);
}

export function BaseView({
  model,
  plugins: vault,
  accountName,
  refresh,
  search,
  ask,
  canRunTrial = false,
  signedIn = false,
}: {
  model: PageModel;
  plugins: PluginPanel;
  accountName: string | null;
  refresh: () => Promise<void>;
  search: SearchAction;
  ask: AskAction;
  canRunTrial?: boolean;
  signedIn?: boolean;
}) {
  const [selectedBatch, setSelectedBatch] = useState<string | null>(null);
  const [active, setActive] = useState<string>("overview");
  const [menu, setMenu] = useState(false);
  const [plugins, setPlugins] = useState<PluginsTab | null>(null);

  useEffect(() => {
    const root = document.querySelector<HTMLElement>("[data-scroll-root]");
    if (!root) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        const id = visible?.target.id;
        if (id) setActive(id);
      },
      { root, rootMargin: "-20% 0px -55% 0px", threshold: [0.15, 0.4] },
    );
    for (const id of SECTIONS) {
      const section = document.getElementById(id);
      if (section) observer.observe(section);
    }
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!menu) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMenu(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu]);

  function go(id: string) {
    setActive(id);
    setMenu(false);
    scrollToSection(id);
  }

  function openPlugins() {
    setMenu(false);
    setPlugins("keys");
  }

  const pageNav = [
    { id: "overview", label: "Overview", count: null as number | null },
    { id: "search", label: "Search", count: null },
    { id: "ask", label: "Ask Base", count: null },
    { id: "batches", label: "Batches", count: model.loadError ? null : model.batches.length },
    { id: "skills", label: "Skills", count: model.skillsError ? null : model.skills.length },
  ].filter((item) => item.id !== "skills" || canRunTrial);
  const dialogOpen = plugins !== null;

  return (
    <div className={dialogOpen ? "bcp bcp-dialog-open" : "bcp"}>
      <header className="bcp-top">
        <img src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} />
        <button type="button" className="bcp-menu" aria-expanded={menu} aria-controls="bcp-panel" onClick={() => setMenu(true)}>
          <span className="bcp-burger" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          Menu
        </button>
      </header>

      {menu ? <button type="button" className="bcp-dim" aria-label="Close menu" onClick={() => setMenu(false)} /> : null}

      <aside id="bcp-panel" className={menu ? "bcp-panel open" : "bcp-panel"} data-panel-anchor="fixed">
        <div className="bcp-panel-top">
          <img className="bcp-logo" src="/brand/base-on-dark.png" alt="Base" width={168} height={43} />
          <button type="button" className="bcp-close" aria-label="Close" onClick={() => setMenu(false)}>
            <span className="bcp-x" />
          </button>
        </div>

        <nav aria-label="Tervory" className="bcp-standard">
          <p className="bcp-account" title={accountName ?? undefined}>
            {accountName ?? "Your account"}
          </p>
          {STANDARD_TOP.map((link) => (
            <a key={link.href} className="bcp-link" href={link.href}>
              {link.label}
            </a>
          ))}
        </nav>
        <SquadLinks className="bcp-standard squad-nav" linkClassName="bcp-link" />

        <div className="bcp-rule" />

        <p className="bcp-kicker">Base</p>
        <nav aria-label="Base" className="bcp-own">
          {pageNav.map((item) => (
            <a
              key={item.id}
              className={!dialogOpen && active === item.id ? "bcp-link active" : "bcp-link"}
              href={`#${item.id}`}
              aria-current={!dialogOpen && active === item.id ? "true" : undefined}
              onClick={(event) => {
                event.preventDefault();
                go(item.id);
              }}
            >
              <span>{item.label}</span>
              {item.count === null ? null : <span className="bcp-count">{item.count}</span>}
            </a>
          ))}
          <button
            type="button"
            className={dialogOpen ? "bcp-link active" : "bcp-link"}
            aria-haspopup="dialog"
            aria-expanded={dialogOpen}
            onClick={openPlugins}
          >
            <span>Plugins</span>
          </button>
        </nav>

        <div className="bcp-panel-bottom">
          <div className="bcp-rule" />
          <img className="bcp-foot-logo" src="/brand/tervory-logo-reversed.png" alt="Tervory" width={168} height={31} />
          <nav aria-label="Account">
            {STANDARD_BOTTOM.map((link) => (
              <a key={link.href} className="bcp-link" href={link.href}>
                {link.label}
              </a>
            ))}
          </nav>
        </div>
      </aside>

      <div className="bcp-main" data-scroll-root inert={dialogOpen || undefined}>
        <div className="bcp-body">
          <section id="overview" className="bcp-intro">
            <div>
              <p className="bcp-crumb">
                <a href={WORKSPACE_HREF}>Workspace</a> / Base
              </p>
              <h1>Base</h1>
              <p className="bcp-lead">
                Start with the ATF reference or your business list. Check contact details, their sources and the cost of each verified record.
              </p>
            </div>
            <div className="bcp-refresh">
              <form action={refresh}>
                <RefreshButton hidden={dialogOpen} />
              </form>
              <p className="bcp-refreshed-label">Last refreshed {model.refreshedLabel}</p>
            </div>
          </section>

          <ReferenceWorkflow signedIn={signedIn} />

          {model.loadError ? (
            <div className="bcp-state">
              <h3>Base could not load</h3>
              <p>We could not read the batch counts. Nothing has changed. Try Refresh, or check back in a few minutes.</p>
            </div>
          ) : (
            <div className="bcp-stats">
              <div className="bcp-stat">
                <b>{formatCount(model.totals.records)}</b>
                <span>{model.totals.records === 1 ? "record" : "records"} in all batches</span>
              </div>
              <div className="bcp-stat">
                <b>{formatCount(model.totals.withSite)}</b>
                <span>with a website</span>
              </div>
              <div className="bcp-stat">
                <b>{formatCount(model.totals.withEmail)}</b>
                <span>with an email</span>
              </div>
              <div className="bcp-stat tint">
                <b>{skippedText(model.totals.skipped)}</b>
                <span>skipped steps waiting for a re-run</span>
              </div>
            </div>
          )}

          <section id="search" className="bcp-section">
            <h2>Search records</h2>
            <p className="bcp-copy">Find any Base record by shop name, city, phone number, website or licence number.</p>
            <RecordSearch search={search} />
          </section>

          <section id="ask" className="bcp-section">
            <h2>Ask Base</h2>
            <p className="bcp-copy">Type a request about your records. The answer shows above the box, with the records it used under Sources.</p>
            <AskBase ask={ask} />
          </section>

          <section id="batches" className="bcp-section">
            <h2>Batches</h2>
            <p className="bcp-copy">
              Each list is a batch. Open a batch to see the fields, their sources and check dates, or the reasons a field was not found. Download those results as CSV.
            </p>
            <BatchWorkflow signedIn={signedIn} selected={selectedBatch} />
            {model.loadError ? null : model.empty ? (
              <div className="bcp-state">
                <h3>No batches yet</h3>
                <p>When a list is uploaded, its batch shows here with its counts.</p>
              </div>
            ) : (
              <div className="bcp-table" role="table" aria-label="Batches">
                <div className="bcp-tr bcp-th bcp-batch-cols" role="row">
                  <span role="columnheader">Batch</span>
                  <span role="columnheader">Records</span>
                  <span role="columnheader">Finished</span>
                  <span role="columnheader">With a website</span>
                  <span role="columnheader">With an email</span>
                  <span role="columnheader">Skipped steps</span>
                  <span role="columnheader">Status</span>
                </div>
                {model.batches.map((row) => (
                  <div key={row.name} className="bcp-tr bcp-batch-cols" role="row">
                    <span role="cell" className="bcp-name"><button type="button" className="bcp-secondary" onClick={() => setSelectedBatch(row.name)}>{batchLabel(row.name)}</button></span>
                    <span role="cell" data-label="Records">{formatCount(row.total)}</span>
                    <span role="cell" data-label="Finished">{formatCount(row.finished)}</span>
                    <span role="cell" data-label="With a website">{formatCount(row.withSite)}</span>
                    <span role="cell" data-label="With an email">{formatCount(row.withEmail)}</span>
                    <span role="cell" data-label="Skipped steps">{skippedText(row.skipped)}</span>
                    <span role="cell" data-label="Status">
                      <Status row={row} />
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>

          {canRunTrial && <ContextComparison report={model.contextTrial} canRunTrial={canRunTrial} />}

          {canRunTrial && <section id="skills" className="bcp-section">
            <h2>Skills</h2>
            <p className="bcp-copy">
              Base runs only approved skill versions. A new version waits in Skala until it is approved, and an approved version is
              never edited in place.
            </p>
            {model.skillsError ? (
              <div className="bcp-state">
                <h3>Skills could not load</h3>
                <p>We could not read the approved versions from Skala. Nothing has changed.</p>
              </div>
            ) : model.skills.length === 0 ? (
              <div className="bcp-state">
                <h3>No approved skills</h3>
                <p>Base has no approved skill versions to run yet.</p>
              </div>
            ) : (
              <div className="bcp-table" role="table" aria-label="Approved skills">
                <div className="bcp-tr bcp-th bcp-skill-cols" role="row">
                  <span role="columnheader">What it does</span>
                  <span role="columnheader">Skill</span>
                  <span role="columnheader">Version</span>
                  <span role="columnheader">State</span>
                  <span role="columnheader">Approved</span>
                </div>
                {model.skills.map((skill) => (
                  <div key={`${skill.id}@${skill.version}`} className="bcp-tr bcp-skill-cols" role="row">
                    <span role="cell" className="bcp-name">{skill.label}</span>
                    <span role="cell" data-label="Skill" className="bcp-mono">{skill.id}</span>
                    <span role="cell" data-label="Version">{skill.version}</span>
                    <span role="cell" data-label="State">
                      <span className="bcp-pill on">
                        <i aria-hidden="true" />
                        Approved
                      </span>
                    </span>
                    <span role="cell" data-label="Approved">{skill.approvedOn ?? "—"}</span>
                  </div>
                ))}
              </div>
            )}
          </section>}

          <p className="bcp-footer">Your lists and results belong to your workspace.</p>
        </div>
      </div>

      {plugins ? <PluginsDialog tab={plugins} plugins={vault} onTab={setPlugins} onClose={() => setPlugins(null)} /> : null}
    </div>
  );
}
