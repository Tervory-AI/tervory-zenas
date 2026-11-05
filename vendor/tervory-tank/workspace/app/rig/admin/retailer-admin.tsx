"use client";

import { useState, useRef, type FormEvent } from "react";
import type { AdminOperation, AdminResult, FitReview, RetailerPanel, MappingPreview } from "./types";
import { Products } from "./products";

const PROVIDERS = [["catalogue", "Any catalogue"], ["shopify", "Shopify"], ["woocommerce", "WooCommerce"], ["google", "Google Merchant"], ["zanders", "Zanders"], ["sports_south", "Sports South"], ["rsr", "RSR"], ["jerrys", "Jerry’s"]];
const providerName = (id: string) => id === "delete-products" ? "Products" : PROVIDERS.find(([key]) => key === id)?.[1] ?? id;
const fields = (form: HTMLFormElement) => Object.fromEntries(new FormData(form).entries());

async function sendRetailer(operation: AdminOperation, data: Record<string, unknown>): Promise<AdminResult> {
  const response = await fetch("/rig/admin/api", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ operation, data }) });
  if (!response.ok) throw new Error("The retailer request was not confirmed.");
  return response.json() as Promise<AdminResult>;
}

export function RetailerAdmin({ initial, act = sendRetailer }: { initial: RetailerPanel; act?: (operation: AdminOperation, data: Record<string, unknown>) => Promise<AdminResult> }) {
  const [panel, setPanel] = useState(initial);
  const [embed, setEmbed] = useState<AdminResult["embed"]>();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(false);
  const [provider, setProvider] = useState("catalogue");
  const feedForm = useRef<HTMLFormElement>(null);
  const [mapping, setMapping] = useState<MappingPreview | null>(null);
  const [columns, setColumns] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState(false);
  const [scope, setScope] = useState("default");
  const [mode, setMode] = useState("file");
  const [authMode, setAuthMode] = useState("none");
  const removeProducts = async (skus: string[]) => {
    setBusy(true); setNotice("");
    let deleted = 0;
    try {
      // Each request is an atomic batch within the API's 5,000-product limit.
      for (let offset = 0; offset < skus.length; offset += 5000) {
        const result = await act("delete-products", { skus: skus.slice(offset, offset + 5000) });
        if (result.panel) setPanel(result.panel);
        if (!result.ok) { setNotice(result.message); setError(true); return false; }
        deleted += result.deleted ?? 0;
      }
      setNotice(`${deleted} deleted.`); setError(false); return true;
    } catch { setNotice("The operation was not confirmed. Check the import history before retrying."); setError(true); return false; }
    finally { setBusy(false); }
  };
  const run = async (operation: AdminOperation, data: Record<string, unknown>) => {
    setBusy(true); setNotice("");
    try {
      const result = await act(operation, data);
      setNotice(result.message); setError(!result.ok);
      if (result.panel) setPanel(result.panel);
      if (result.embed) setEmbed(result.embed);
      return result.ok;
    } catch { setNotice("The operation was not confirmed. Check the import history before retrying."); setError(true); return false; }
    finally { setBusy(false); }
  };
  const feedData = async (form: HTMLFormElement): Promise<Record<string, unknown>> => {
    const values = fields(form);
    const format = provider === "google" ? "xml" : ["shopify", "woocommerce"].includes(provider) ? "json" : String(values.format);
    const data: Record<string, unknown> = { distributor: provider, site: String(values.site).trim(),
      feed_id: String(values.feed_id || ""), currency: String(values.currency).toUpperCase(), country: String(values.country).toUpperCase(),
      price_basis: values.price_basis, format, columns, records_path: String(values.records_path || "") };
    if (mode === "file") {
      const file = values.file;
      if (!(file instanceof File) || !file.size || file.size > 8_000_000) throw new Error("Choose a feed file smaller than 8 MB.");
      if (format === "xlsx") {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = "";
        for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        data.contents = btoa(binary); data.encoding = "base64";
      } else data.contents = await file.text();
      data.source = "ftp_export";
    } else {
      data.endpoint = String(values.endpoint).trim(); data.auth_mode = authMode;
      for (const field of ["token", "username", "password"]) data[field] = String(values[field] || "");
      data.official_read_only = values.official === "on";
    }
    return data;
  };
  const preview = async () => {
    const form = feedForm.current;
    if (!form || !form.reportValidity()) return;
    setBusy(true); setConfirmed(false); setMapping(null);
    try {
      const result = await act("mapping", await feedData(form));
      if (result.mapping) { setMapping(result.mapping); setColumns(result.mapping.columns); }
      setNotice(result.message); setError(!result.ok);
    } catch { setNotice("The feed could not be read. Check its file, address and format."); setError(true); }
    finally { setBusy(false); }
  };
  const submitFeed = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!confirmed || !mapping) { setNotice("Suggest the field mapping, review it and confirm it first."); setError(true); return; }
    setBusy(true);
    try {
      const data = await feedData(event.currentTarget);
      if (mapping.preview_id) {
        const result = await act("confirm-mapping", { ...data, preview_id: mapping.preview_id });
        if (!result.ok) { setNotice(result.message); setError(true); return; }
        if (result.panel) setPanel(result.panel);
        setMapping({ ...mapping, preview_id: undefined });
      }
      await run(mode === "file" ? "import" : "connect", data);
    } catch { setNotice("The feed was not saved. Check the file and confirm the mapping again."); setError(true); }
    finally { setBusy(false); }
  };
  const reuseFeed = (feed: RetailerPanel["feeds"][number]) => {
    const form = feedForm.current;
    if (!form || !feed.mapping_confirmed) return;
    setProvider(feed.provider);
    for (const name of ["site", "feed_id", "currency", "country", "price_basis", "records_path"] as const) {
      const input = form.elements.namedItem(name) as HTMLInputElement | HTMLSelectElement | null;
      if (input) input.value = feed[name] || "";
    }
    // Set format after React switches the provider's format control.
    setTimeout(() => { const input = form.elements.namedItem("format") as HTMLSelectElement | null; if (input) input.value = feed.format || "csv"; }, 0);
    setColumns(feed.columns || {}); setMapping({ fields: Object.values(feed.columns || {}), columns: feed.columns || {}, required: ["sku", "name", "brand", "category", "price", "image_url"] }); setConfirmed(true);
  };
  const submitRule = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = event.currentTarget;
    if (await run("price-rule", fields(form))) form.reset();
  };
  const decide = (operation: "approve" | "reject", fit: FitReview) => run(operation, { part_sku: fit.part_sku, fits_sku: fit.fits_sku, fits_family: fit.fits_family });
  return <main className="retailer-admin">
    <header className="retailer-header"><div><a href="/rig">← Back to Rig</a><p className="retailer-eyebrow">YOUR CATALOGUE</p><h1>Retailer settings</h1><p>Keep your catalogue current. Review what belongs together.</p></div><span className="retailer-count">{panel.products} products · {panel.currency}</span></header>
    {notice && <p className={`retailer-notice ${error ? "is-error" : ""}`} role={error ? "alert" : "status"}>{notice}</p>}
    <div className="retailer-grid">
      <Products rows={panel.product_rows ?? []} busy={busy} remove={removeProducts} />
      <section className="retailer-card"><h2>Quote delivery</h2>
        <form onSubmit={async e => { e.preventDefault(); await run("settings", fields(e.currentTarget)); }}><fieldset disabled={busy}>
          <label>Store name<input name="name" required maxLength={120} defaultValue={panel.settings?.name || ""} /></label>
          <label>Quote delivery email<input name="email" type="email" required maxLength={254} defaultValue={panel.settings?.email || ""} /></label>
          <button type="submit">Save delivery settings</button>
        </fieldset></form>
      </section>
      <section className="retailer-card"><h2>Widget for your website</h2>
        <p>Publish your catalogue and save your delivery email, then create a key for your website.</p>
        <form onSubmit={async e => { e.preventDefault(); const data = fields(e.currentTarget); setEmbed(undefined); await run("embed-keys", { origins: [String(data.origin).trim()] }); }}><fieldset disabled={busy || !panel.products || !panel.settings?.email}>
          <label>Website origin<input name="origin" type="url" required placeholder="https://your-store.com" /></label>
          <button type="submit">Create embed key</button>
        </fieldset></form>
        {embed && <><label>Publishable embed key<input readOnly value={embed.publishable_key} onFocus={e => e.currentTarget.select()} /></label>
          <label htmlFor="rig-widget-snippet">Widget snippet</label><textarea id="rig-widget-snippet" readOnly rows={6} value={embed.snippet} onFocus={e => e.currentTarget.select()} />
          <p>Copy this snippet into your website. This key only works on the website origin you entered.</p></>}
      </section>
      <section className="retailer-card"><div className="retailer-section-heading"><span>01</span><h2>Feeds</h2></div><p>Import your catalogue file or connect an official feed. Connected feeds refresh nightly at 03:00 UTC.</p>
        {panel.feeds.map(feed => <div className="retailer-feed" key={feed.provider + feed.site + feed.feed_id}><div><strong>{providerName(feed.provider)}</strong><small>{feed.site} {feed.feed_id}</small><small>{feed.country} · {feed.currency} · {feed.price_basis === "selling" ? "Selling price" : "Supplier cost"}</small></div><div><button disabled={busy || !feed.mapping_confirmed} onClick={() => reuseFeed(feed)}>Use saved mapping</button>{feed.next_refresh && <button disabled={busy} onClick={() => run("refresh", { distributor: feed.provider, site: feed.site, feed_id: feed.feed_id || "" })}>Refresh now</button>}</div></div>)}
        {!panel.feeds.length && <p className="retailer-empty">No feed connected yet. Start with your catalogue file.</p>}
        <form ref={feedForm} onSubmit={submitFeed} onChange={e => {
          const control = e.target;
          const name = control instanceof HTMLInputElement || control instanceof HTMLSelectElement || control instanceof HTMLTextAreaElement ? control.name : "";
          if (name === "confirmed") return;
          setConfirmed(false);
          if (name && (name !== "file" || mapping?.preview_id)) setMapping(null);
          if (!name && !mapping?.preview_id) setMapping(null);
        }}><fieldset disabled={busy}><legend className="retailer-sr">Feed settings</legend>
          <div className="retailer-fields"><label>Source<select name="provider" value={provider} onChange={e => setProvider(e.target.value)}>{PROVIDERS.map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label><label>Add by<select name="delivery" value={mode} onChange={e => setMode(e.target.value)}><option value="file">Import file</option><option value="connection">Connect feed</option></select></label></div>
          <label>Feed name<input name="feed_id" required pattern="[a-z0-9_-]+" maxLength={60} placeholder="main-catalogue" /></label><label>Supplier or store website<input name="site" type="url" required placeholder="https://supplier.example" /></label>
          <div className="retailer-fields"><label>Feed currency<input name="currency" required pattern="[A-Za-z]{3}" maxLength={3} placeholder="USD" /></label>{!["shopify", "woocommerce", "google"].includes(provider) && <label>File format<select name="format"><option value="csv">CSV</option><option value="tsv">TSV</option><option value="json">JSON</option><option value="xml">XML</option><option value="xlsx">Excel (.xlsx)</option></select></label>}</div><div className="retailer-fields"><label>Country code<input name="country" required pattern="[A-Za-z]{2}" maxLength={2} placeholder="NZ" /></label><label>Prices are<select name="price_basis"><option value="cost">Supplier cost</option><option value="selling">Selling price</option></select></label></div>
          {mode === "file" ? <label>Catalogue file<input name="file" type="file" required accept=".json,.xml,.csv,.tsv,.xlsx" /></label> : <><label>Read-only feed address<input name="endpoint" type="url" required /></label><label>Authentication<select name="auth_mode" value={authMode} onChange={e => setAuthMode(e.target.value)}><option value="none">Public feed</option><option value="bearer">Secret key</option><option value="shopify">Shopify secret key</option><option value="basic">Username and password / WooCommerce keys</option></select></label>{authMode === "basic" ? <><label>Username or consumer key<input name="username" type="password" autoComplete="new-password" required /></label><label>Password or consumer secret<input name="password" type="password" autoComplete="new-password" required /></label></> : authMode !== "none" && <label>Secret key<input name="token" type="password" autoComplete="new-password" required /></label>}<label className="retailer-check"><input name="official" type="checkbox" required />This is an official, read-only feed.</label></>}
          <label>Where the products are in the file<input name="records_path" placeholder="catalogue.products" /></label>
          <button type="button" onClick={preview}>Suggest field mapping</button>
          {mapping && <div><p>Column names — check each match. Only names are shown; source prices stay private.</p><div className="retailer-fields">{[...mapping.required, "quantity", "active", "url", "gtin", "family", "fits_sku", "fits_family", "fit_evidence"].map(name => <label key={name}>{({ sku: "Product code", name: "Product name", brand: "Brand", category: "Category", price: "Price", image_url: "Image address", quantity: "Stock quantity", active: "Availability", url: "Product address", gtin: "Barcode", family: "Product family", fits_sku: "Fits product", fits_family: "Fits family", fit_evidence: "Fit evidence" } as Record<string, string>)[name]}<select value={columns[name] || ""} onChange={e => setColumns(previous => { const next = { ...previous }; if (e.target.value) next[name] = e.target.value; else delete next[name]; return next; })}><option value="">Choose a field</option>{mapping.fields.map(field => <option key={field} value={field}>{field}</option>)}</select></label>)}</div><label className="retailer-check"><input name="confirmed" type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />I checked this mapping and the feed's currency, country and price type.</label></div>}
          <button className="retailer-primary" type="submit" disabled={!confirmed || !mapping || mapping.required.some(name => !columns[name])}>{mode === "file" ? "Publish catalogue" : "Connect feed"}</button>
        </fieldset></form>
      </section>
      <section className="retailer-card"><div className="retailer-section-heading"><span>02</span><h2>Indicative prices</h2></div><p>One price in {panel.currency} for shoppers. Selling prices skip freight, duty and margin; only currency conversion applies when needed. Your rule and supplier amounts stay private.</p><span className="retailer-tag">{panel.price_configured ? "Price rule saved" : "Price rule needed"}</span>
        <form onSubmit={submitRule}><fieldset disabled={busy}><legend className="retailer-sr">Replace the private price rule</legend><label>Shop currency<input name="shop_currency" defaultValue={panel.currency} pattern="[A-Za-z]{3}" maxLength={3} required /></label><label>Apply rule to<select name="scope" value={scope} onChange={e => setScope(e.target.value)}><option value="default">All suppliers, unless overridden</option><option value="region">Country or region</option><option value="supplier">One feed</option></select></label>{scope === "region" && <label>Region or country code<input name="scope_id" placeholder="US, EU or NZ" pattern="[A-Z]{2}" required /></label>}{scope === "supplier" && <label>Feed<select name="scope_id" required>{panel.feeds.map(feed => <option key={feed.provider + feed.site + feed.feed_id} value={`${feed.provider}:${feed.site}:${feed.feed_id || ""}`}>{feed.feed_id || providerName(feed.provider)} · {feed.site}</option>)}</select></label>}<label>Freight per item, in supplier currency<input name="freight" type="number" min="0" max="1000000000" step="0.01" required /></label><div className="retailer-fields"><label>Duty (%)<input name="duty_percent" type="number" min="0" max="1000" step="0.01" required /></label><label>Gross margin (%)<input name="margin_percent" type="number" min="0" max="99.99" step="0.01" required /></label></div><p className="retailer-hint">Enter all three fields to replace the rule. Saved values are never sent back to this page. Existing feed products are repriced together.</p><button type="submit">Save price rule</button></fieldset></form>
        {panel.can_set_exchange_rate ? <form onSubmit={async e => { e.preventDefault(); const form = e.currentTarget; if (await run("exchange-rate", fields(form))) form.reset(); }}><fieldset disabled={busy}><legend>Exchange rate · founder only</legend><div className="retailer-fields"><label>Source currency<input name="currency" pattern="[A-Za-z]{3}" maxLength={3} required /></label><label>Shop currency<input name="target_currency" defaultValue={panel.currency} pattern="[A-Za-z]{3}" maxLength={3} required /></label><label>Shop currency per unit<input name="rate" type="number" min="0.000001" max="1000000" step="any" required /></label></div><button type="submit">Set exchange rate</button></fieldset></form> : <p className="retailer-hint">The founder sets each exchange rate to the shop currency. Imports wait until that rate is available.</p>}
      </section>
      <section className="retailer-card retailer-wide"><div className="retailer-section-heading"><span>03</span><h2>Fit approval queue</h2><span className="retailer-tag">{panel.fits.length} to review</span></div><p>Suggested combinations stay hidden from shoppers until you approve them.</p>
        {!panel.fits.length && <p className="retailer-empty">Nothing waiting for review.</p>}
        {panel.fits.map(fit => <article className="retailer-fit" key={`${fit.part_sku}:${fit.fits_sku || fit.fits_family}`}><div><h3>{fit.part_name} <span>with</span> {fit.fits_with}</h3><p>{fit.note || "No supplier evidence was provided. Verify this combination before approving."}</p><small>{fit.part_sku} · {fit.fits_sku || fit.fits_family}</small></div><div className="retailer-actions"><button disabled={busy} onClick={() => decide("reject", fit)}>Reject</button><button disabled={busy} onClick={() => decide("approve", fit)}>Approve</button></div></article>)}
        <button disabled={busy} onClick={() => run("suggest", {})}>Find fit suggestions</button>
      </section>
      <section className="retailer-card"><div className="retailer-section-heading"><span>04</span><h2>Setup templates</h2></div><p>Choose the product categories that make a complete setup. Only approved combinations are shown.</p>
        {Object.entries(panel.templates).map(([category, roles]) => <div className="retailer-template" key={category}><strong>{category}</strong><small>{Object.entries(roles).map(([role, target]) => role === target ? role : `${role} (${target})`).join(" · ")}</small></div>)}
        <form onSubmit={async e => { e.preventDefault(); const form = e.currentTarget; const data = fields(form); const roles = String(data.roles).split(",").map(s => s.trim()).filter(Boolean); if (!roles.length) return; if (await run("templates", { [String(data.category).trim()]: Object.fromEntries(roles.map(role => [role, role])) })) form.reset(); }}><fieldset disabled={busy}><legend className="retailer-sr">Save a category template</legend><label>Product category<input name="category" required maxLength={80} /></label><label>Included categories, separated by commas<input name="roles" required placeholder="shade, stand" /></label><button type="submit">Save template</button></fieldset></form>
      </section>
      <section className="retailer-card"><div className="retailer-section-heading"><span>05</span><h2>What shoppers couldn’t find</h2></div><p>Search counts for your catalogue. No shopper identities.</p>{panel.missed_searches.length ? <ul className="retailer-searches">{panel.missed_searches.map(row => <li key={row.query}><span>{row.query}</span><strong>{row.count}</strong></li>)}</ul> : <p className="retailer-empty">No unmatched searches in the last 90 days.</p>}</section>
      <section className="retailer-card retailer-wide"><div className="retailer-section-heading"><span>06</span><h2>Import history</h2></div><p>Each file is applied in full, or nothing changes.</p>{panel.receipts.length ? <div className="retailer-history">{panel.receipts.map(row => <article key={row.id}><div><strong>{providerName(row.provider)}</strong><small>{row.site} {row.feed_id}</small><small>{new Date(row.created_at).toLocaleString()}</small></div><p>{row.provider === "delete-products" ? `${row.updated} deleted.` : <><b>{row.added}</b> added · <b>{row.updated}</b> updated · <b>{row.rejected}</b> rejected</>}</p></article>)}</div> : <p className="retailer-empty">Import a file to see the result here.</p>}</section>
    </div>
  </main>;
}
