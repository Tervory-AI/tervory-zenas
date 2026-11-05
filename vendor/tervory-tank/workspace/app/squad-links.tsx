"use client";

import { usePathname } from "next/navigation";
import { createContext, useContext, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

// shell-1 logic
export type SquadMember = {
  href: string;
  label: string;
  job: string;
  logo: string;
  lightLogo?: string;
  width: number;
  height: number;
  founderOnly: boolean;
};

/** Same order as the old sidebar list. Use official originals; on-dark assets get a navy tile on the light panel. */
export const SQUAD_MEMBERS: readonly SquadMember[] = [
  { href: "/base", label: "Base", job: "Verified business lists", logo: "/brand/base-on-dark.png", width: 2322, height: 601, founderOnly: false },
  { href: "/tiger", label: "Tiger", job: "Prices and rival moves", logo: "/brand/tiger-on-dark.png", width: 2607, height: 801, founderOnly: false },
  { href: "/market-lab", label: "Market Lab", job: "Sourced market research", logo: "/brand/market-lab-on-dark.png", width: 3685, height: 608, founderOnly: false },
  { href: "/rig", label: "Rig", job: "Products and matching parts", logo: "/brand/rig-on-dark.png", width: 1173, height: 651, founderOnly: false },
  { href: "/tisan", label: "Tisan", job: "Sites and apps", logo: "/brand/tisan-on-dark.png", width: 2806, height: 701, founderOnly: false },
  { href: "/radix", label: "Radix", job: "System and site exposure", logo: "/brand/radix-on-dark.png", width: 1893, height: 651, founderOnly: false },
  { href: "/zenas", label: "Zenas", job: "US & SA legal research", logo: "/brand/zenas-on-dark.png", width: 1970, height: 701, founderOnly: false },
  { href: "/skala", label: "Skala", job: "Skill testing", logo: "/brand/skala-on-dark.png", lightLogo: "/brand/skala-on-light.png", width: 3796, height: 901, founderOnly: true },
  { href: "/tank", label: "Tank", job: "Task routing and receipts", logo: "/brand/tank-on-dark.png", width: 2466, height: 601, founderOnly: true },
];

export type SquadPanelState = {
  open: boolean;
  focus: string;
  sidebarHeight: number;
};

export type SquadAction =
  | { type: "toggle" }
  | { type: "escape" }
  | { type: "outside" }
  | { type: "choose" }
  | { type: "close" }
  | { type: "arrow"; key: string; from?: string };

export function visibleMembers(founder: boolean): SquadMember[] {
  return SQUAD_MEMBERS.filter((member) => founder || !member.founderOnly);
}

export function isCurrent(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function memberToFocus(pathname: string, members: readonly SquadMember[]): string {
  const current = members.find((member) => isCurrent(pathname, member.href));
  return current?.href ?? members[0]?.href ?? "squad";
}

export function isArrowKey(key: string): boolean {
  return key === "ArrowDown" || key === "ArrowUp" || key === "ArrowLeft" || key === "ArrowRight" || key === "Home" || key === "End";
}

export function focusAfterArrow(hrefs: readonly string[], focused: string, key: string): string {
  if (hrefs.length === 0) return focused;
  const index = hrefs.indexOf(focused);
  if (key === "Home") return hrefs[0] ?? focused;
  if (key === "End") return hrefs[hrefs.length - 1] ?? focused;
  if (key === "ArrowDown" || key === "ArrowRight") {
    if (index < 0) return hrefs[0] ?? focused;
    return hrefs[Math.min(hrefs.length - 1, index + 1)] ?? focused;
  }
  if (key === "ArrowUp" || key === "ArrowLeft") {
    if (index < 0) return hrefs[hrefs.length - 1] ?? focused;
    return hrefs[Math.max(0, index - 1)] ?? focused;
  }
  return focused;
}

/** Opening, closing, Escape, an outside click, a choice, and arrow keys. Sidebar height is never changed. */
export function applySquadAction(
  state: SquadPanelState,
  action: SquadAction,
  members: readonly SquadMember[],
  pathname: string,
): SquadPanelState {
  const sidebarHeight = state.sidebarHeight;
  if (action.type === "arrow") {
    if (!state.open) return { ...state, sidebarHeight };
    const from = action.from && members.some((member) => member.href === action.from) ? action.from : state.focus;
    return { ...state, sidebarHeight, focus: focusAfterArrow(members.map((member) => member.href), from, action.key) };
  }
  if (action.type === "toggle") {
    if (state.open) return { open: false, focus: "squad", sidebarHeight };
    return { open: true, focus: memberToFocus(pathname, members), sidebarHeight };
  }
  if (!state.open) return { ...state, sidebarHeight };
  return { open: false, focus: "squad", sidebarHeight };
}
// shell-1 view

/** True inside the shared shell, so a page's own copy of this control stays out of the list. */
export const InAppShell = createContext(false);

const SquadFounderContext = createContext(false);

/** Shell passes the same founder flag the rest of the sidebar uses. */
export function SquadFounderProvider({ founder, children }: { founder: boolean; children: React.ReactNode }) {
  return <SquadFounderContext.Provider value={founder}>{children}</SquadFounderContext.Provider>;
}

/**
 * One Squad button. The member list is a pop-out beside the sidebar.
 * Pages still render this; inside the shell only the active copy is shown.
 */
export function SquadLinks({
  active = false,
  founder: founderProp,
  className,
  linkClassName,
}: {
  active?: boolean;
  founder?: boolean;
  className?: string;
  linkClassName?: string;
}) {
  const nested = useContext(InAppShell);
  const fromShell = useContext(SquadFounderContext);
  if (nested && !active) return null;
  return (
    <SquadControl
      founder={founderProp ?? fromShell}
      className={active ? undefined : className}
      linkClassName={active ? undefined : linkClassName}
    />
  );
}

function SquadControl({
  founder,
  className,
  linkClassName,
}: {
  founder: boolean;
  className?: string;
  linkClassName?: string;
}) {
  const pathname = usePathname() ?? "";
  const members = visibleMembers(founder);
  const panelId = "squad-popout";
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const membersRef = useRef(members);
  const pathRef = useRef(pathname);
  membersRef.current = members;
  pathRef.current = pathname;
  const [ready, setReady] = useState(false);
  const [panel, setPanel] = useState<SquadPanelState>({ open: false, focus: "squad", sidebarHeight: 0 });
  const landed = useRef(false);

  function dispatch(action: SquadAction) {
    setPanel((current) => applySquadAction(current, action, membersRef.current, pathRef.current));
  }

  useEffect(() => setReady(true), []);

  useEffect(() => {
    if (!landed.current) {
      landed.current = true;
      return;
    }
    if (panel.open) {
      const links = [...(panelRef.current?.querySelectorAll<HTMLAnchorElement>("[data-squad-member]") ?? [])];
      const target = links.find((link) => link.dataset.squadHref === panel.focus) ?? links[0];
      (target ?? buttonRef.current)?.focus();
      return;
    }
    buttonRef.current?.focus();
  }, [panel.open, panel.focus]);

  useEffect(() => {
    if (!panel.open) return;
    function onKey(event: globalThis.KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      dispatch({ type: "escape" });
    }
    function onPointer(event: PointerEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (buttonRef.current?.contains(target) || panelRef.current?.contains(target)) return;
      dispatch({ type: "outside" });
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [panel.open]);

  function onPanelKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      dispatch({ type: "escape" });
      return;
    }
    if (event.key === "Tab") {
      const items = [...(panelRef.current?.querySelectorAll<HTMLElement>("a, button") ?? [])];
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    if (!isArrowKey(event.key)) return;
    event.preventDefault();
    const from = (event.target as HTMLElement).dataset.squadHref;
    dispatch({ type: "arrow", key: event.key, from });
  }

  const popout = (
    <>
    {panel.open ? <div className="squad-dim" aria-hidden="true" onPointerDown={(event) => { event.preventDefault(); dispatch({ type: "outside" }); }} /> : null}
    <div
      ref={panelRef}
      id={panelId}
      className="squad-pop shell-pop"
      role="dialog"
      aria-modal="true"
      aria-label="Squad"
      hidden={!panel.open}
      onKeyDown={onPanelKeyDown}
    >
      <div className="squad-pop-bar shell-pop-head">
        <p className="shell-kicker">Squad</p>
        <button type="button" className="squad-pop-close shell-pop-close" aria-label="Close" onClick={() => dispatch({ type: "close" })}>
          <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
        </button>
      </div>
      <ul className="squad-list shell-members">
        {members.map((member, index) => {
          const here = isCurrent(pathname, member.href);
          const width = (member.width / member.height) * 22;
          return (
            <li key={member.href}>
              {member.founderOnly && !members[index - 1]?.founderOnly ? <p className="squad-platform">Platform</p> : null}
              <a
                className={linkClassName ? `squad-row ${linkClassName}` : "squad-row"}
                href={member.href}
                data-squad-member=""
                data-squad-href={member.href}
                aria-current={here ? "page" : undefined}
                onClick={() => dispatch({ type: "choose" })}
              >
                <span className={member.lightLogo ? "squad-logo" : "squad-logo squad-logo-tile"}>
                  <img src={member.lightLogo ?? member.logo} alt="" width={width} height={22} />
                </span>
                <span className="squad-name shell-member-name shell-sr">{member.label}</span>
                <span className="squad-job shell-member-job">{member.job}</span>
              </a>
            </li>
          );
        })}
      </ul>
    </div>
    </>
  );

  return (
    <nav className={className ? `shell-squad ${className}` : "shell-squad"} aria-label="Squad">
      <button
        ref={buttonRef}
        id="squad-button"
        type="button"
        className="shell-squad-button squad-trigger"
        aria-expanded={panel.open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        onClick={() => dispatch({ type: "toggle" })}
      >
        Squad
      </button>
      {ready ? createPortal(popout, document.body) : null}
    </nav>
  );
}
