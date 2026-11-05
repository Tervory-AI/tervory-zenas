"use client";
import { useEffect, useRef, useState } from "react";
import { OrganisationSwitcher } from "../trigger-signin-provider";
import { NAV } from "../trigger/copy";

/** Reuse the shared menu destinations; no new Market Lab-specific menu claims. */
export function H7Shell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    const original = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const elements = () => [...(panel.current?.querySelectorAll<HTMLElement>('a[href],button') ?? [])];
    elements()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); toggle.current?.focus(); }
      if (event.key === "Tab") {
        const list = elements(), first = list[0], last = list[list.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", key);
    return () => { document.body.style.overflow = original; document.removeEventListener("keydown", key); };
  }, [open]);
  return <div className="ml-h7">
    <header className="h7-mobile"><img src="/market-lab/h7/mobile-wordmark.png" alt="Tervory" width="186" height="34" />
      <button ref={toggle} onClick={() => setOpen(!open)} aria-expanded={open} aria-controls="h7-menu">Menu</button></header>
    {open ? <button className="h7-scrim" aria-label="Close" onClick={() => setOpen(false)} /> : null}
    <aside id="h7-menu" ref={panel} className={`h7-menu${open ? " open" : ""}`}>
      <div className="h7-identity"><img src="/market-lab/h7/desktop-wordmark.png" alt="Tervory" width="208" height="38" />
        <button className="h7-close" onClick={() => setOpen(false)}>Close</button>
        <div className="h7-business"><span>Business</span><OrganisationSwitcher /></div><p>Market Lab</p></div>
      <nav>{NAV.map(([title, links]) => <section key={title}><h2>{title}</h2>{links.map(([label, href]) => href
        ? <a key={label} href={href} onClick={() => setOpen(false)}>{label}</a>
        : <span key={label} aria-disabled="true">{label}</span>)}</section>)}
        <section className="h7-account"><a href="/">All products</a><a href="/ledger">Receipts</a><a href="/billing">Billing</a><a href="/onboarding">Account settings</a></section>
      </nav>
    </aside>
    <main className="h7-content"><h1>Market Lab</h1><nav className="h7-tabs"><a href="/market-lab/briefs">Monthly briefs</a><a href="/market-lab/calibration">Calibration</a></nav>{children}</main>
  </div>;
}
