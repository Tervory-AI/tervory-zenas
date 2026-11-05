"use client";

import Link from "next/link";
import { useState } from "react";
import type { ReviewItem } from "@/app/api/zenas/rules";
import { ReviewDesk } from "./desk";
import { WORKED_EXAMPLE } from "./example";

const TOP_LINKS = [
  { href: "https://app.tervory.com/", label: "Dashboard" },
  { href: "/matters", label: "Projects" },
] as const;

const PAGE_LINKS = [
  { href: "/matters", label: "Matters" },
  { href: "/review", label: "Review" },
] as const;

/** Labels from the Zenas panel. They are not routes on this page. */
const PANEL_LABELS = ["Plugins", "Draft", "Court bundles", "Deadlines", "Library", "Moot Court"] as const;

const BOTTOM_LINKS = [
  { href: "/onboarding", label: "Settings" },
  { href: "https://tervory.com", label: "Support" },
] as const;

export function ReviewConsole({
  items,
  note,
  reviewer,
  account,
}: {
  items: ReviewItem[];
  note: string;
  reviewer: string;
  account: string;
}) {
  const [menu, setMenu] = useState(false);
  const name = account.trim() || "Your account";
  const initial = name.slice(0, 1).toUpperCase();
  const example = WORKED_EXAMPLE;

  return (
    <div className="zr-app">
      <button className="zr-menu" type="button" onClick={() => setMenu(true)}>
        Menu
      </button>
      <button className="zr-dim" type="button" hidden={!menu} aria-label="Close menu" onClick={() => setMenu(false)} />
      <aside className={menu ? "zr-panel open" : "zr-panel"}>
        <div className="zr-panel-main">
          <div className="zr-brand">
            <img className="zr-zenas-logo" src="/brand/zenas-on-dark.png" alt="Zenas" width={150} height={53} />
          </div>
          <button className="zr-close" type="button" onClick={() => setMenu(false)}>
            Close
          </button>
          <nav className="zr-nav" aria-label="Account">
            <p className="zr-account">
              <span className="zr-avatar" aria-hidden="true">{initial}</span>
              {name}
            </p>
            {TOP_LINKS.map((link) => (
              <Link key={link.href} href={link.href} onClick={() => setMenu(false)}>
                {link.label}
              </Link>
            ))}
            <Link href="/chats" onClick={() => setMenu(false)}>Chats</Link>
          </nav>
          <div className="zr-rule" />
          <nav className="zr-nav" aria-label="Zenas">
            <p className="zr-kicker">ZENAS</p>
            {PAGE_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                aria-current={link.href === "/review" ? "page" : undefined}
                className={link.href === "/review" ? "active" : undefined}
                onClick={() => setMenu(false)}
              >
                {link.label}
              </Link>
            ))}
            {PANEL_LABELS.map((label) => (
              <span className="zr-item" key={label}>{label}</span>
            ))}
          </nav>
          <div className="zr-side">
            <div className="zr-side-head">
              <p className="zr-kicker">AREAS OF LAW</p>
            </div>
            <p className="zr-item">No areas of law yet.</p>
            <div className="zr-side-head">
              <p className="zr-kicker">DIARY</p>
            </div>
            <p className="zr-item">No diary entries yet.</p>
            <div className="zr-side-head">
              <p className="zr-kicker">UPCOMING COURT DATES</p>
            </div>
            <p className="zr-item">No court dates yet.</p>
          </div>
        </div>
        <div className="zr-panel-foot">
          <div className="zr-rule" />
          <img className="zr-foot-logo" src="/brand/tervory-logo-reversed.png" alt="Tervory" width={186} height={34} />
          <nav className="zr-nav" aria-label="Settings">
            {BOTTOM_LINKS.map((link) => (
              <Link key={link.label} href={link.href} onClick={() => setMenu(false)}>
                {link.label}
              </Link>
            ))}
          </nav>
        </div>
      </aside>
      <div className="zr-body">
        <header className="zr-head">
          <h1>Review</h1>
          <p className="lede">Read a draft beside its sources. Approve it or send it back. Your name, admission number, and changes are recorded.</p>
          <p className="meta">Signed in as {reviewer}</p>
        </header>
        <article className="zr-card" aria-labelledby="worked-example-title">
          <p className="zr-chip">{example.label}</p>
          <h2 id="worked-example-title">{example.title}</h2>
          <p className="meta">{example.jurisdictionLabel} · trigger {example.trigger} · {example.days} days</p>
          <p className="zr-kicker-date">Deadline</p>
          <p className="zr-date">{example.deadline}</p>
          <p>{example.reading}</p>
          <p className="note">{example.note}</p>
          <ol className="zr-steps">
            {example.steps.map((step) => (
              <li key={step.rule}>
                <span className="meta">{step.rule}</span>
                <span>{step.text}</span>
              </li>
            ))}
          </ol>
        </article>
        <ReviewDesk items={items} note={note} />
        <p className="fine">Zenas prepares drafts and research for review by a licensed lawyer.</p>
      </div>
    </div>
  );
}
