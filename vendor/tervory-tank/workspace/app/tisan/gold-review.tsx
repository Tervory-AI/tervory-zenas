"use client";

import { useState } from "react";
import type { TisanGoldPage } from "@/lib/tank";
import { approveGoldPage, loadGoldPages } from "./gold-actions";

function preview(html: string, css: string) {
  // Opaque sandbox and CSP prevent scripts, navigation, forms and external requests.
  const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'none'; base-uri 'none'">`;
  return policy + `<style>${css.replace(/<\/style/gi, "")}</style>` + html;
}

function SavedFrame({ frame }: { frame: Record<string, unknown> }) {
  const previewImage = typeof frame.preview_png === "string" && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(frame.preview_png) ? frame.preview_png : null;
  return <>
    {previewImage ? <img src={previewImage} alt="Saved Figma frame" style={{ maxWidth: "100%" }} /> : <p>Frame image not saved. Review the recorded frame data below.</p>}
    <details open={!previewImage}><summary>Frame measurements and text</summary><pre>{JSON.stringify(frame, null, 2)}</pre></details>
  </>;
}

export function GoldReview({ initialPages = [] }: { initialPages?: TisanGoldPage[] }) {
  const [pages, setPages] = useState<TisanGoldPage[]>(initialPages);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function load() {
    setBusy(true);
    try { const rows = await loadGoldPages(); setPages(rows); setMessage(rows.length ? "Review both versions before approving." : "No built pages with saved frames yet."); }
    catch { setMessage("Gold review is available to founders only, with an active organization."); }
    finally { setBusy(false); }
  }
  async function approve(page: TisanGoldPage) {
    setBusy(true);
    try { await approveGoldPage(page.page_id, page.frame_hash, page.page_hash, page.tenant_id === "00000000-0000-0000-0000-000000000001" ? "platform" : "workspace"); setMessage("Approved as gold. A changed page or frame will need approval again."); }
    catch { setMessage("Approval failed. Reload the pages and review again."); }
    finally { setBusy(false); }
  }
  return <section className="gold-review" aria-label="Gold page review">
    <h2>Approve built pages</h2>
    <button type="button" disabled={busy} onClick={load}>Load pages for review</button>
    <p role="status">{message}</p>
    {pages.map((page) => <article key={`${page.tenant_id}:${page.page_id}`}>
      <h3>{page.page_id}</h3>
      <p>{page.tenant_id === "00000000-0000-0000-0000-000000000001" ? "Platform page" : "Workspace page"}</p>
      <p>Frame {page.figma_frame_id} · {page.built_page_path}</p>
      <div className="gold-comparison">
        <div><h4>Saved frame</h4><SavedFrame frame={page.frame} /></div>
        <div><h4>Built page</h4><iframe title={`Built page ${page.page_id}`} sandbox="" srcDoc={preview(page.html, page.css)} /></div>
      </div>
      <p>External assets are blocked in this preview. Frame evidence is shown exactly as saved.</p>
      <button type="button" disabled={busy} onClick={() => approve(page)}>Approve as gold</button>
    </article>)}
  </section>;
}
