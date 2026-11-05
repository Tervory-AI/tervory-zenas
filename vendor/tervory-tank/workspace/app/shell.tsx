"use client";

import { OrganisationSwitcher, SignoutButton, useSignin } from "@/app/trigger-signin-provider";
import Link from "next/link";
import { usePathname, useSearchParams, useSelectedLayoutSegments } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import {
  FOUNDER_LINKS,
  REST_LINKS,
  REVIEW_LINK,
  TOP_LINKS,
  linkIsCurrent,
  pageSection,
  usesOwnShell,
  type NavLink,
} from "./product-nav";
import { InAppShell, SquadFounderProvider, SquadLinks } from "./squad-links";

export type ShellAccess = { founder: boolean; reviewer: boolean };

export function Shell({
  founder: initialFounder,
  reviewer: initialReviewer,
  readAccess,
  children,
}: {
  founder: boolean;
  reviewer: boolean;
  readAccess: () => Promise<ShellAccess>;
  children: React.ReactNode;
}) {
  const browserPathname = usePathname() ?? "";
  const [routeRoot] = useSelectedLayoutSegments();
  // Product hosts rewrite / to a room without changing the browser pathname.
  const pathname = browserPathname === "/" && routeRoot ? `/${routeRoot}` : browserPathname;
  const { isLoaded, userId } = useSignin();
  const [founder, setFounder] = useState(initialFounder);
  const [reviewer, setReviewer] = useState(initialReviewer);
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    if (!isLoaded) return;
    let live = true;
    readAccess()
      .then((value) => {
        if (!live) return;
        setFounder(value.founder);
        setReviewer(value.reviewer);
      })
      .catch(() => {
        if (!live) return;
        setFounder(false);
        setReviewer(false);
      });
    return () => {
      live = false;
    };
  }, [isLoaded, userId, readAccess]);

  useEffect(() => {
    setMenu(false);
  }, [pathname]);

  const menuButton = useRef<HTMLButtonElement>(null);
  const closeMenu = () => {
    setMenu(false);
    menuButton.current?.focus();
  };

  useEffect(() => {
    if (!menu) return;
    const media = window.matchMedia("(min-width: 801px)");
    const resize = () => { if (media.matches) setMenu(false); };
    document.getElementById("shell-side-close")?.focus();
    const escape = (event: KeyboardEvent) => {
      // Squad's portal handles its own Escape and focus return first.
      if (event.defaultPrevented || event.key !== "Escape" || document.querySelector(".squad-pop:not([hidden])")) return;
      event.preventDefault();
      setMenu(false);
      menuButton.current?.focus();
    };
    document.addEventListener("keydown", escape);
    media.addEventListener("change", resize);
    return () => {
      document.removeEventListener("keydown", escape);
      media.removeEventListener("change", resize);
    };
  }, [menu]);

  if (usesOwnShell(pathname)) return (
    <SquadFounderProvider founder={founder}>
      <main>{children}</main>
    </SquadFounderProvider>
  );

  return (
    <SquadFounderProvider founder={founder}>
      <InAppShell.Provider value={true}>
        <div className={menu ? "app-shell product menu-open" : "app-shell product"}>
          <a className="shell-skip" href="#shell-content">Skip to content</a>
          <header className="shell-mobile">
            <img src="/brand/tervory-logo-reversed.png" alt="Tervory" width={218} height={40} />
            <button type="button" className="shell-menu" id="shell-menu" ref={menuButton} aria-expanded={menu} aria-controls="app-sidebar" onClick={() => setMenu((open) => !open)}>Menu</button>
          </header>
          {menu ? (
            <button type="button" className="shell-dim" aria-label="Close menu" onClick={closeMenu} />
          ) : null}
          <Suspense fallback={<ShellBar pathname={pathname} search="" hash="" founder={founder} reviewer={reviewer} menu={menu} onClose={closeMenu} />}>
            <ShellBarLive pathname={pathname} founder={founder} reviewer={reviewer} menu={menu} onClose={closeMenu} />
          </Suspense>
          <div id="shell-content" className="shell-main">
            <main>{children}</main>
          </div>
        </div>
      </InAppShell.Provider>
    </SquadFounderProvider>
  );
}

function ShellBarLive({
  pathname,
  founder,
  reviewer,
  menu,
  onClose,
}: {
  founder: boolean;
  reviewer: boolean;
  menu: boolean;
  onClose: () => void;
  pathname: string;
}) {
  const search = useSearchParams();
  const [hash, setHash] = useState("");

  useEffect(() => {
    const read = () => setHash(window.location.hash);
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, [pathname]);

  return (
    <ShellBar
      pathname={pathname}
      search={search.toString()}
      hash={hash}
      founder={founder}
      reviewer={reviewer}
      menu={menu}
      onClose={onClose}
    />
  );
}

function ShellBar({
  pathname,
  search,
  hash,
  founder,
  reviewer,
  menu,
  onClose,
}: {
  pathname: string;
  search: string;
  hash: string;
  founder: boolean;
  reviewer: boolean;
  menu: boolean;
  onClose: () => void;
}) {
  const section = pageSection(pathname, founder, reviewer);
  const current = (href: string) => linkIsCurrent(href, pathname, search, hash);
  // SigninDirectory 7 (Core 3) removed <SignedIn>; check the session with useAuth instead.
  const { isSignedIn } = useSignin();

  return (
    <aside id="app-sidebar" className={menu ? "shell-side open" : "shell-side"} aria-label="Sidebar">
      <div className="shell-brand">
        <img src="/brand/tervory-logo-reversed.png" alt="Tervory" width={218} height={40} />
        <button type="button" className="shell-side-close" id="shell-side-close" onClick={onClose}>Close</button>
      </div>
      {isSignedIn ? (
        <div className="shell-account">
          <OrganisationSwitcher />
          <SignoutButton />
        </div>
      ) : null}
      <nav className="shell-nav" aria-label="Workspace">
        {TOP_LINKS.map((link) => (
          <ShellLink key={link.label} link={link} current={current(link.href)} onClose={onClose} />
        ))}
      </nav>
      <SquadLinks active founder={founder} />
      {section ? (
        <nav className="shell-nav shell-own" aria-label={section.title}>
          <p className="shell-kicker">{section.title}</p>
          {section.links.map((link) => (
            <ShellLink key={link.href + link.label} link={link} current={current(link.href)} onClose={onClose} />
          ))}
        </nav>
      ) : null}
      <div className="shell-spacer" />
      <nav className="shell-nav" aria-label="Account">
        {REST_LINKS.map((link) => (
          <ShellLink key={link.label} link={link} current={current(link.href)} onClose={onClose} />
        ))}
        {reviewer ? <ShellLink link={REVIEW_LINK} current={current(REVIEW_LINK.href)} onClose={onClose} /> : null}
        {founder ? FOUNDER_LINKS.map((link) => (
          <ShellLink key={link.href} link={link} current={current(link.href)} onClose={onClose} />
        )) : null}
      </nav>
    </aside>
  );
}

function ShellLink({ link, current, onClose }: { link: NavLink; current: boolean; onClose: () => void }) {
  const external = link.href.startsWith("http") && !link.href.startsWith("https://app.tervory.com/");
  if (external) {
    return (
      <a href={link.href} target="_blank" rel="noreferrer">
        {link.label}
        <span className="shell-sr"> (opens a new tab)</span>
      </a>
    );
  }
  return (
    <Link href={link.href} aria-current={current ? "page" : undefined} onClick={onClose}>
      {link.label}
    </Link>
  );
}
