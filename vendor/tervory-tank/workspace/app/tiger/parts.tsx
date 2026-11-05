import type { ReactNode } from "react";

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
  if (rows.length === 0) return empty ? <p className="fine">{empty}</p> : null;
  return (
    <div className={`table-wrap ${className}`}>
      <table>
        <thead><tr>{heads.map((head) => <th key={head}>{head}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {row.map((cell, cellIndex) => <td key={cellIndex} className={cellIndex === priceCol ? "price" : cellIndex === 0 && priceCol === undefined ? "name" : ""}>{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
