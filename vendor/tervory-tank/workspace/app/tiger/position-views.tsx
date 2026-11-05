"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { TigerMatch } from "@/lib/tank";
import { Plugins } from "./plugins";
import type { PluginPanel } from "./plugins-data";
import { decideMatch } from "./position-actions";
import { AttemptTime, PageLink, Pill, SimpleTable } from "./parts";
import { WATCH_VIEWS, pagePath, publicLink, siteHost, type ComparisonData, type CompetitorRow, type PositionRow, type SiteRow, type WatchView } from "./position-model";

const MATCH_REASON = "Same brand and maker part number. No barcode match. Confirm the product before it affects price position.";
const MATCH_NOTE = "Suggested matches do not affect price position. Confirm only when the competitor page is the same product. Reject a suggestion when it is a different product.";
const POSITION_RULE = "Proposed rule: compare your price with current readings for confirmed matches in the same currency. Include your store in the rank; equal prices share a rank. Difference is your price minus the cheapest price, divided by that price for %. Exclude missing and overdue readings; show no position when no current competitor reading is available. Stock is shown separately.";
const SITES_NOTE = "Pages checked and matches found refer to the latest site check. Skipped pages are not counted as checked. A human check can stop a run after some pages have been read; robots restrictions can leave other permitted pages readable. Only saved Source links are checked.";

function Note({ title, children }: { title: string; children: string }) {
  return <aside className="note-card"><strong>{title}</strong><p>{children}</p></aside>;
}

export function PositionViews({ data, initialView, query, plugins }: { data: ComparisonData; initialView: WatchView; query: string; plugins: PluginPanel | null }) {
  const [view, setView] = useState(initialView);
  const [matches, setMatches] = useState(data.matches);
  const [pluginsOpen, setPluginsOpen] = useState(false);
  const router = useRouter();
  useEffect(() => setMatches(data.matches), [data.matches]);
  const q = query.trim().toLowerCase();
  const includes = (...values: (string | null | undefined)[]) => !q || values.some(v => v?.toLowerCase().includes(q));
  const positions = data.positions?.filter(row => includes(row.name, row.sku, ...row.competitors.map(c => c.label))) ?? data.positions;
  const reviews = matches?.filter(row => includes(row.sku, row.product?.name, row.url, row.identifiers.seen.brand, row.identifiers.seen.mpn)) ?? matches;
  const sites = data.sites?.filter(row => includes(row.label, row.url)) ?? data.sites;
  return <section className="block position-block">
    <div className="watch-views" role="tablist" aria-label="Watch list">
      {WATCH_VIEWS.map(item => <button key={item.id} type="button" role="tab" aria-selected={view === item.id} className={view === item.id ? "on" : ""} onClick={() => setView(item.id)}>{item.label}</button>)}
    </div>
    <div role="tabpanel" className="watch-content">
      {view === "position" ? <PricePosition rows={positions} /> : view === "matches" ? <Matches rows={reviews} onChange={match => {
        setMatches(current => current?.flatMap(row => row.id === match.id ? match.state === "confirmed" ? [] : [match] : [row]) ?? null);
        router.refresh();
      }} /> : <Sites rows={sites} onPlugins={() => setPluginsOpen(true)} pluginsEnabled={plugins !== null} />}
    </div>
    {pluginsOpen && plugins ? <Plugins plugins={plugins} initialTab="Source links" onClose={() => setPluginsOpen(false)} /> : null}
  </section>;
}

function Competitor({ row }: { row: CompetitorRow }) {
  return <div className="position-competitor">
    <PageLink row={{ url: row.url, store: `${row.label} ↗` }} />
    {row.price ? <>
      <strong>{row.price}{row.stock ? ` · ${row.stock}` : ""}</strong>
      {row.checkedAt && row.checked ? <p className="fine">Checked <AttemptTime row={{ attemptedAt: row.checkedAt, checked: row.checked }} />{row.every ? ` · ${row.every}` : ""}</p> : null}
      {row.stale ? <><Pill status="Older than watch interval" warning image /><p className="fine">Last reading retained; excluded from position.</p></> : null}
    </> : <><Pill status="No reading yet" image /><p className="fine">Price, stock and check time unavailable.</p></>}
  </div>;
}

function Competitors({ row }: { row: PositionRow }) {
  return row.competitors.length ? <div className="position-competitors">{row.competitors.map(c => <Competitor key={c.url} row={c} />)}</div> : <p className="no-matches">No matches yet</p>;
}

function PricePosition({ rows }: { rows: PositionRow[] | null }) {
  return <>
    <p className="intro">Your products against confirmed matches on the competitor sites you saved.</p>
    {rows ? <>
      <SimpleTable className="position-table desktop-comparison" heads={["Product", "Your price", "Matched competitor pages", "Cheapest", "Your rank", "Difference"]} empty="" rows={rows.map(row => [
        <div key="product" className="position-product"><strong>{row.name}</strong><span className="fine">{row.sku}</span></div>,
        <span key="own" className="price">{row.ownPrice}</span>, <Competitors key="competitors" row={row} />,
        <span key="cheapest" className={row.cheapest === "—" ? "missing" : "price"}>{row.cheapest}</span>,
        <strong key="rank" className={row.rank === "—" ? "missing" : ""}>{row.rank}</strong>,
        <strong key="difference" className={row.difference === "—" ? "missing" : ""}>{row.difference}</strong>,
      ])} />
      <ul className="cards mobile-comparison price-cards">{rows.map(row => <li key={row.sku}>
        <strong className="card-name">{row.name}</strong><p className="fine">{row.sku}</p>
        <div className="position-summary"><span>Your price</span><span className="price">{row.ownPrice}</span></div>
        <Competitors row={row} />
        <dl className="position-totals">
          <div><dt>Cheapest</dt><dd className={row.cheapest === "—" ? "" : "price"}>{row.cheapest}</dd></div>
          <div><dt>Your rank</dt><dd>{row.rank}</dd></div>
          <div><dt>Difference</dt><dd>{row.difference}</dd></div>
        </dl>
      </li>)}</ul>
    </> : null}
    <Note title="How position is calculated">{POSITION_RULE}</Note>
  </>;
}

function Evidence({ row }: { row: TigerMatch }) {
  return <div className="match-evidence">
    <p>Brand: {row.identifiers.seen.brand}</p><p>Maker part number: {row.identifiers.seen.mpn}</p>
    {row.method === "brand_mpn" ? <p>Barcode match: none</p> : null}
    <PageLink row={{ url: publicLink(row.seen_on) ?? "", store: `${row.site_label || siteHost(row.url)} · page evidence ↗` }} />
  </div>;
}

function Decision({ row, onChange }: { row: TigerMatch; onChange: (match: TigerMatch) => void }) {
  const [state, setState] = useState<"ready" | "busy" | "failed">("ready");
  const running = useRef(false);
  async function run(decision: "confirm" | "reject" | "undo") {
    if (running.current) return;
    running.current = true;
    setState("busy");
    try {
      const result = await decideMatch(row.id, decision);
      if (result.ok) { onChange(result.match); setState("ready"); }
      else setState("failed");
    } catch { setState("failed"); }
    finally { running.current = false; }
  }
  return <div className="row-actions match-actions" aria-busy={state === "busy"} data-action-state={state}>
    {row.state === "rejected" ? <><span>Rejected</span><button type="button" className="text" disabled={state !== "ready"} onClick={() => void run("undo")}>Undo</button></> : <>
      <button type="button" className="line small" disabled={state !== "ready"} onClick={() => void run("confirm")}>Confirm</button>
      <button type="button" className="text" disabled={state !== "ready"} onClick={() => void run("reject")}>Reject</button>
    </>}
  </div>;
}

function Matches({ rows, onChange }: { rows: TigerMatch[] | null; onChange: (match: TigerMatch) => void }) {
  if (rows === null) return null;
  if (!rows.length) return <p className="empty comparison-empty">No matches to review.</p>;
  // The approved review frame describes brand/part-number suggestions only.
  rows = rows.filter(row => row.method === "brand_mpn");
  if (!rows.length) return null;
  return <>
    <SimpleTable className="matches-table desktop-comparison" heads={["Your product", "Evidence seen on the page", "Competitor page", "Review"]} empty="" rows={rows.map(row => [
      <div key="own" className="match-product"><strong>{row.product?.name || row.sku}</strong><p>Brand: {row.product?.brand ?? row.identifiers.own.brand}</p><p>Maker part number: {row.product?.mpn ?? row.identifiers.own.mpn}</p></div>,
      <div key="evidence" className="desktop-evidence"><p className="intro">{MATCH_REASON}</p><Evidence row={row} /></div>,
      <div key="page" className="match-page"><PageLink row={{ url: publicLink(row.url) ?? "", store: `${row.site_label || siteHost(row.url)} ↗` }} /><p className="fine">{pagePath(row.url).slice(siteHost(row.url).length)}</p></div>,
      <Decision key="review" row={row} onChange={onChange} />,
    ])} />
    <ul className="cards mobile-comparison matches-cards">{rows.map(row => <li key={row.id}>
      <strong className="card-name">{row.product?.name || row.sku}</strong>
      <p className="intro">{MATCH_REASON}</p>
      <p className="your-product">Your product: {row.product?.brand ?? row.identifiers.own.brand} · {row.product?.mpn ?? row.identifiers.own.mpn}</p>
      <div className="evidence-heading">Evidence seen on the page</div><Evidence row={row} />
      <PageLink row={{ url: publicLink(row.url) ?? "", store: `Competitor page: ${pagePath(row.url)} ↗`, wrapPath: true }} />
      <Decision row={row} onChange={onChange} />
    </li>)}</ul>
    <Note title="Your confirmation decides the match">{MATCH_NOTE}</Note>
  </>;
}

function SiteStatus({ row }: { row: SiteRow }) {
  if (!row.status) return null;
  return <div className="site-status">
    <Pill status={row.status === "human_check" ? "Blocked by a human check" : row.status === "robots" ? "Some pages closed to robots" : "Checked"} warning={row.status !== "checked"} image />
    {row.status === "checked" ? <p className="fine">Latest site check completed.</p> : row.status === "human_check" ? <p className="fine">Stopped at a human check. Tiger never solves or bypasses it.</p> : row.skipped !== null ? <p className="fine">{row.skipped} pages skipped: this site asks robots not to read them.</p> : null}
  </div>;
}

function SiteTime({ row, mobile = false }: { row: SiteRow; mobile?: boolean }) {
  if (!row.status || !row.checkedAt || !row.checked) return null;
  return <>{row.status && row.status !== "checked" ? "Last attempt: " : mobile ? "Last checked: " : ""}<AttemptTime row={{ attemptedAt: row.checkedAt, checked: row.checked }} /></>;
}

function Sites({ rows, onPlugins, pluginsEnabled }: { rows: SiteRow[] | null; onPlugins: () => void; pluginsEnabled: boolean }) {
  return <>
    <p className="intro">Sites saved under Source links in Tiger’s Plugins.</p>
    <button type="button" className="text plugins-link" onClick={onPlugins} aria-disabled={!pluginsEnabled}>Tiger Plugins → Source links</button>
    {rows === null ? null : !rows.length ? <p className="empty comparison-empty">No saved sites.</p> : <>
      <SimpleTable className="sites-table desktop-comparison" heads={["Saved site", "Pages checked", "Matches found", "Last checked", "Check status"]} empty="" rows={rows.map(row => [
        <div key="site" className="saved-site"><PageLink row={{ url: row.url, store: `${row.label} ↗` }} /><span className="fine">{row.host}</span></div>,
        row.pages, row.matches, <SiteTime key="time" row={row} />, <SiteStatus key="status" row={row} />,
      ])} />
      <ul className="cards mobile-comparison sites-cards">{rows.map(row => <li key={row.id}>
        <PageLink row={{ url: row.url, store: `${row.label} ↗` }} /><p className="fine">{row.host}</p>
        <dl className="site-counts"><div><dt>Pages checked</dt><dd>{row.pages}</dd></div><div><dt>Matches found</dt><dd>{row.matches}</dd></div></dl>
        <p className="site-time"><SiteTime row={row} mobile /></p><SiteStatus row={row} />
      </li>)}</ul>
      <Note title="What these counts cover">{SITES_NOTE}</Note>
    </>}
  </>;
}
