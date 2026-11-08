"use client";

import { useEffect, useRef, useState, type ReactNode, type KeyboardEvent } from "react";
import { Plugins } from "./plugins";
import { checks } from "./content";
import { RadixSummary } from "./summary";
import { SquadLinks } from "../squad-links";
import { RadixDesk } from "./conversation";

type Section = "Overview" | "Findings" | "Reports";
const sections: Section[] = ["Overview", "Findings", "Reports"];

function keepDialogFocus(event: KeyboardEvent<HTMLDialogElement>) {
  if (event.key !== "Tab") return;
  const nodes = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
    'button, a[href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
  )).filter((node) => node.tabIndex >= 0 && !node.matches(":disabled") && node.getClientRects().length > 0);
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (!first) return;
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function TervoryLogo() {
  return <img src="/brand/tervory-logo-reversed.png" width={186} height={34} alt="Tervory" className="rx-logo" />;
}

function RadixLogo() {
  return <img src="/brand/radix-on-dark.png" width={186} height={64} alt="Radix" className="rx-radix-mark" />;
}

export function RadixPage({ accountName, settings }: { accountName: string; settings: ReactNode }) {
  const [pluginsOpen, setPluginsOpen] = useState(false);
  const [vaultEnabled, setVaultEnabled] = useState(false);
  const [section, setSection] = useState<Section>("Overview");
  const plugins = useRef<HTMLDialogElement>(null);
  const menu = useRef<HTMLDialogElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const pluginsTrigger = useRef<HTMLButtonElement | null>(null);
  const menuTrigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const media = window.matchMedia("(min-width: 801px)");
    function closeMenu() { if (media.matches) menu.current?.close(); }
    media.addEventListener("change", closeMenu);
    return () => media.removeEventListener("change", closeMenu);
  }, []);

  function navigate(next: Section) {
    menu.current?.close();
    setSection(next);
    body.current?.scrollTo({ top: 0 });
    requestAnimationFrame(() => heading.current?.focus());
  }
  function openPlugins(trigger: HTMLButtonElement) {
    const fromMenu = Boolean(menu.current?.open);
    menu.current?.close();
    pluginsTrigger.current = fromMenu ? menuTrigger.current : trigger;
    setPluginsOpen(true);
    plugins.current?.showModal();
  }
  function navigation(variant: "panel" | "menu" = "menu") {
    const workspace = <nav className="rx-bottom" aria-label="Workspace">
      <span onClickCapture={() => menu.current?.close()}>{settings}</span>
      <a href="https://tervory.com" target="_blank" rel="noreferrer">Support<span className="rx-sr"> (opens a new tab)</span></a>
    </nav>;
    return <>
      {variant === "panel"
        ? <div className="rx-brand"><RadixLogo /></div>
        : <a href="/" className="rx-brand" aria-label="Tervory workspace"><TervoryLogo /></a>}
      <nav className="rx-standard" aria-label="Standard navigation">
        <p className="rx-account">{accountName}</p>
        <a href="https://app.tervory.com/">Dashboard</a>
        <a href="/matters">Projects</a>
        <a href="/chats">Chats</a>
      </nav>
      <SquadLinks className="rx-standard squad-nav" />
      <div className="rx-page-nav">
        <p className="rx-kicker">Radix</p>
        <nav aria-label="Radix">
          {sections.map((item) => <button key={item} type="button" aria-current={section === item ? "page" : undefined}
            onClick={() => navigate(item)}>{item}</button>)}
          <button type="button" onClick={(event) => openPlugins(event.currentTarget)} aria-haspopup="dialog">Plugins</button>
        </nav>
      </div>
      {variant === "panel"
        ? <div className="rx-panel-foot"><a href="/" className="rx-brand" aria-label="Tervory workspace"><TervoryLogo /></a>{workspace}</div>
        : workspace}
    </>;
  }
  return <div className="rx">
    <a className="rx-skip" href="#rx-heading">Skip to page content</a>
    <header className="rx-mobile-bar">
      <TervoryLogo />
      <button ref={menuTrigger} type="button" aria-haspopup="dialog" onClick={() => menu.current?.showModal()}>Menu</button>
    </header>
    <aside className="rx-panel" data-panel-anchor="fixed">{navigation("panel")}</aside>
    <dialog className="rx-menu-dialog" ref={menu} aria-label="Navigation" onKeyDown={keepDialogFocus}>
      <button className="rx-menu-close" type="button" onClick={() => menu.current?.close()}>Close menu</button>
      {navigation("panel")}
    </dialog>
    <div className="rx-body" ref={body} data-scroll-root>
      <p className="rx-crumb"><a href="/">Workspace</a> / Radix</p>
      <header className="rx-intro">
        <div>
          <h1 id="rx-heading" ref={heading} tabIndex={-1}>{section === "Overview" ? "Radix" : section}</h1>
          <p className="rx-lead">{section === "Overview"
            ? "Check code before it ships. Review exposed secrets, risky dependencies, and unsafe code."
            : "Review the checks made on code supplied to Radix."}</p>
        </div>
        <a href="/chats" className="rx-secondary">Chats</a>
      </header>

      <RadixSummary section={section} />
      <RadixDesk />

      {section === "Overview" ? <>
        <section aria-labelledby="rx-checks">
          <h2 id="rx-checks" className="rx-section-heading">Code checks</h2>
          <div className="rx-checks">{checks.map((check) => <article key={check.title} className="rx-card">
            <h3>{check.title}</h3><p>{check.copy}</p>
          </article>)}</div>
        </section>
        <section aria-labelledby="rx-flow">
          <h2 id="rx-flow" className="rx-section-heading">From finding to fix</h2>
          <ol className="rx-steps"><li>Review the file and rule.</li><li>Make the change.</li><li>Run a fresh check.</li></ol>
        </section>
      </> : null}
      <footer className="rx-footnote">Code checks use supplied files. No website is scanned from this page.</footer>
    </div>
    <dialog ref={plugins} className="rx-plugins-dialog" aria-labelledby="rx-plugins-title" onKeyDown={keepDialogFocus}
      onClose={() => { setPluginsOpen(false); pluginsTrigger.current?.focus(); }}>
      <div className="rx-modal-header">
        <div><h2 id="rx-plugins-title">Plugins</h2><p>The files and connections Radix can use.</p></div>
        <button type="button" className="rx-text-button" onClick={() => plugins.current?.close()}>Close</button>
      </div>
      <Plugins open={pluginsOpen} onEnabled={setVaultEnabled} />
      <button type="button" className={vaultEnabled ? "rx-secondary" : "rx-primary"} onClick={() => plugins.current?.close()}>Done</button>
    </dialog>
  </div>;
}
