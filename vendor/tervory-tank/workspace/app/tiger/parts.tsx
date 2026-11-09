"use client";
import { useEffect, useRef, type ReactNode } from "react";

export function useDialogFocus(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    if (!node) return;
    const controls = () => Array.from(node.querySelectorAll<HTMLElement>(
      'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]'
    )).filter(item => item.getClientRects().length > 0);
    (controls()[0] ?? node).focus();
    function key(event: KeyboardEvent) {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close.current(); }
      if (event.key !== "Tab") return;
      const items = controls();
      if (!items.length) { event.preventDefault(); node?.focus(); return; }
      const first = items[0], last = items[items.length - 1];
      if (!node?.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first).focus(); }
      else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    function focus(event: FocusEvent) {
      if (!node?.contains(event.target as Node)) (controls()[0] ?? node)?.focus();
    }
    document.addEventListener("keydown", key, true);
    document.addEventListener("focusin", focus);
    return () => {
      document.removeEventListener("keydown", key, true);
      document.removeEventListener("focusin", focus);
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return ref;
}

export function Pill({ status, warning = false, image = false }: { status: string; warning?: boolean; image?: boolean }) {
  const solid = status === "Price changed" || status === "Stock changed";
  const hollow = status === "Not checked";
  return <span className={warning ? "pill warning" : solid ? "pill solid" : hollow ? "pill hollow" : "pill"}>
    {image ? <img src={warning ? "/tiger/warning-dot.svg" : status === "Checked" ? "/tiger/checked-dot.svg" : "/tiger/neutral-dot.svg"} alt="" /> : <i />}{status}
  </span>;
}

export function PageLink({ row }: { row: { url: string; store: string; wrapPath?: boolean } }) {
  const pieces = row.store.split("/");
  return row.url && /^https?:\/\//i.test(row.url) ? <a href={row.url} title={row.url} target="_blank" rel="noreferrer">{row.wrapPath
    ? <span>{pieces.map((piece, index) => <span key={index}>{index ? <wbr /> : null}{piece}{index < pieces.length - 1 ? "/" : ""}</span>)}</span>
    : row.store}</a> : null;
}

export function AttemptTime({ row }: { row: { attemptedAt: string | null; checked: string | null; reason?: string | null } }) {
  return row.attemptedAt ? <time dateTime={row.attemptedAt}>{row.checked}</time> : row.reason ? null : <>Not checked yet</>;
}

export function SimpleTable({ heads, rows, empty, priceCol, className = "" }: { heads: string[]; rows: ReactNode[][]; empty: string; priceCol?: number; className?: string }) {
  return (
    <div className={`table-wrap ${className}`}>
      <table>
        <thead><tr>{heads.map((head) => <th key={head}>{head}</th>)}</tr></thead>
        <tbody>
          {rows.length === 0 ? <tr data-empty="true">{heads.map((head, index) => <td key={index}>—</td>)}</tr> : null}
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) => <td key={cellIndex} className={cellIndex === priceCol ? "price" : cellIndex === 0 && priceCol === undefined ? "name" : ""}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && empty ? <p className="fine">{empty}</p> : null}
    </div>
  );
}
