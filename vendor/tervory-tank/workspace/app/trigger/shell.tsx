"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { OrganisationSwitcher } from "../trigger-signin-provider";
import { NAV } from "./copy";

export default function TriggerShell({
  children,
  title = "Trigger",
}: {
  children: React.ReactNode;
  title?: string;
}) {
  const [open, setOpen] = useState(false),
    path = usePathname();
  const menu = useRef<HTMLElement>(null),
    toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    setOpen(false);
  }, [path]);
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const items = () =>
      Array.from(
        menu.current?.querySelectorAll<HTMLElement>(
          'a[href],button,[tabindex="0"]',
        ) ?? [],
      ).filter(item => !item.hasAttribute("disabled") && item.getClientRects().length > 0);
    items()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        toggle.current?.focus();
      }
      if (event.key === "Tab") {
        const a = items(),
          first = a[0],
          last = a[a.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = previous;
      document.removeEventListener("keydown", key);
      toggle.current?.focus();
    };
  }, [open]);
  return (
    <div className="trigger-console">
      <div className="trigger-mobile-bar">
        <strong>{title}</strong>
        <button
          ref={toggle}
          aria-label="Menu"
          aria-expanded={open}
          aria-controls="trigger-menu"
          onClick={() => setOpen(!open)}
        >
          <span className="trigger-hamburger" aria-hidden="true" />
        </button>
      </div>
      {open && (
        <button
          className="trigger-scrim"
          aria-label="Close"
          onClick={() => setOpen(false)}
        />
      )}
      <aside
        ref={menu}
        id="trigger-menu"
        className={`trigger-sidebar ${open ? "is-open" : ""}`}
        aria-label={title}
        role={open ? "dialog" : undefined}
        aria-modal={open ? true : undefined}
      >
        <div className="trigger-identity">
          <div className="trigger-logo-row">
            <img
              className="trigger-wordmark"
              src="/trigger/wordmark.png"
              alt="Tervory"
            />
            <button
              className="trigger-close-menu"
              onClick={() => setOpen(false)}
            >
              Close
            </button>
          </div>
          <div className="trigger-business">
            <span>Business</span>
            <OrganisationSwitcher />
          </div>
          <span className="trigger-product">{title}</span>
        </div>
        <nav className="trigger-areas">
          {NAV.map(([title, links]) => (
            <section className="trigger-group" key={title}>
              <h2>{title}</h2>
              {links.map(([label, href]) =>
                href ? (
                  <Link
                    key={label}
                    href={href}
                    aria-current={path === href ? "page" : undefined}
                  >
                    {label}
                  </Link>
                ) : (
                  <span key={label} aria-disabled="true">
                    {label}
                  </span>
                ),
              )}
            </section>
          ))}
          <div className="trigger-account">
            <Link href="/">All products</Link>
            <Link href="/ledger">Receipts</Link>
            <Link href="/billing">Billing</Link>
            <Link href="/onboarding">Account settings</Link>
          </div>
        </nav>
      </aside>
      <div className="trigger-content">{children}</div>
    </div>
  );
}
