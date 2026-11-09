"use client";
import { fetchWithDeadline as fetch } from "../../../lib/request";
import { request } from "../request";

import { useEffect, useRef, useState, type FormEvent } from "react";
import type { AdminOperation, AdminResult, CartOrder, CommerceResult } from "./types";

const platforms = { shopify: "Shopify", woocommerce: "WooCommerce", bigcommerce: "BigCommerce" } as const;
type Platform = keyof typeof platforms;
type Act = (operation: AdminOperation, data: Record<string, unknown>) => Promise<AdminResult>;
export type CommerceSection = "connection" | "orders" | "delivery";

async function send(operation: AdminOperation, data: Record<string, unknown>): Promise<AdminResult> {
  return request("/rig/admin/api", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ operation, data }) }, async response => {
    if (!response.ok) throw new Error("The retailer request was not confirmed.");
    return response.json() as Promise<AdminResult>;
  }, 120000);
}

export function orderStatus(order: CartOrder): string {
  if (order.state === "needs_person" || order.state === "create_unknown" || order.state === "cancel_unknown") return "Needs a person";
  if (order.payment_confirmed) {
    if (order.late_payment) return "Paid after window";
    if (order.lines.every(line => line.kind === "back_order")) return "Back ordered";
    return order.lines.some(line => line.kind === "back_order") ? "Paid · Includes back order" : "Paid";
  }
  return order.state === "expired" ? "Unpaid order cancelled" : "Waiting for payment";
}

export function timeLeft(expires: string, now: number): string {
  const seconds = Math.max(0, Math.ceil((Date.parse(expires) - now) / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function amount(order: CartOrder): string {
  if (order.total === undefined || !order.currency) return "";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: order.currency }).format(Number(order.total));
}

export function OrderRows({ orders, now }: { orders: CartOrder[]; now: number }) {
  return <div className="rig-commerce-card rig-commerce-orders">{!orders.length && <div><strong>0</strong><p><a href="/rig/admin?cart=connection">Shop connection</a></p></div>}{orders.map(order => {
    const status = orderStatus(order);
    const back = order.lines.filter(line => line.kind === "back_order").map(line => line.title).join(", ");
    const placementFailure = order.state === "needs_person" && order.payment_confirmed;
    const needsPerson = status === "Needs a person";
    return <article key={order.id} data-order-id={order.id}>
      <strong>#{order.number || order.id} · <span className="rig-commerce-status">{status}</span></strong>
      {!placementFailure && <p>{needsPerson ? "Confirmation needed" : order.payment_confirmed ? order.late_payment ? "Payment confirmed after the 5-minute window" : "Payment confirmed" : order.state === "expired" ? "Payment window ended" : `${timeLeft(order.expires_at, now)} left`}</p>}
      {!placementFailure && <p className="rig-commerce-muted">{amount(order)}{needsPerson ? " · Payment status could not be confirmed" : order.state === "expired" ? " · The unpaid order was cancelled. The next checkout re-checks price and stock." : !order.payment_confirmed ? " · Store checkout is open" : order.late_payment ? ` · Order is going through${back ? ` · ${back} back ordered` : ""}` : status === "Back ordered" ? " · Awaiting stock · Delivery date not confirmed" : back ? ` · ${back} back ordered · Delivery date not confirmed` : ""}</p>}
      {needsPerson && <a className="rig-commerce-button" href={`/rig/admin?cart=orders&order=${encodeURIComponent(order.id)}`}>Review order</a>}
    </article>;
  })}</div>;
}

export function CommerceAdmin({ section, initial = {}, initialOrders = [], act = send, embedded = false }: { section: CommerceSection; initial?: CommerceResult; initialOrders?: CartOrder[]; act?: Act; embedded?: boolean }) {
  const inFlight = useRef(false);
  const [readError, setReadError] = useState(false);
  const [open, setOpen] = useState(false);
  const [config, setConfig] = useState(initial);
  const [platform, setPlatform] = useState<Platform | "">(initial.platform && initial.platform in platforms ? initial.platform as Platform : "");
  const [status, setStatus] = useState<"edit" | "testing" | "connected" | "failed">(initial.platform ? "connected" : "edit");
  const [orders, setOrders] = useState(initialOrders);
  const [now, setNow] = useState(0);
  const [min, setMin] = useState(initial.delivery?.min_days?.toString() ?? "");
  const [max, setMax] = useState(initial.delivery?.max_days?.toString() ?? "");
  const [saving, setSaving] = useState(false);
  const [invalid, setInvalid] = useState(false);
  useEffect(() => { setNow(Date.now()); const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    let active = true, reading = false, failures = 0;
    if (section === "orders") {
      const read = async () => {
        // Reuse quote delivery's six-attempt ceiling; reload starts a new read.
        if (!active || reading || failures >= 6) return;
        reading = true;
        try { const result = await act("commerce-orders", {});
          if (!result.ok || !result.commerce?.orders) throw new Error();
          if (active) { setOrders(result.commerce.orders); setReadError(false); failures = 0; }
        } catch { failures++; if (active) setReadError(true); }
        finally { reading = false; }
      };
      read(); const timer = setInterval(read, 5000); return () => { active = false; clearInterval(timer); };
    }
    void act("commerce-connect", {}).then(result => {
      if (!active || !result.ok || !result.commerce) return;
      const value = result.commerce;
      setConfig(value);
      if (value.platform && value.platform in platforms) { setPlatform(value.platform as Platform); setStatus("connected"); }
      setMin(value.delivery?.min_days?.toString() ?? ""); setMax(value.delivery?.max_days?.toString() ?? "");
    }).catch(() => {});
    return () => { active = false; };
  }, [act, section]);
  async function connect(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!platform || inFlight.current) return;
    inFlight.current = true;
    const form = event?.currentTarget;
    const fields = form ? new FormData(form) : null;
    const data: Record<string, unknown> = { platform, vault_name: `rig-commerce-${platform}` };
    if (platform === "bigcommerce") data.store_hash = fields?.get("store_hash") || config.store_hash;
    else data.site = fields?.get("site") || config.site;
    if (fields) {
      const credential: Record<string, string> = {};
      for (const key of platform === "woocommerce" ? ["consumer_key", "consumer_secret"] : ["access_token"]) credential[key] = String(fields.get(key) || "");
      data.credential = credential;
      // Clear inputs immediately. Secret values never become component state.
      form?.reset();
    }
    setStatus("testing");
    try { const result = await act("commerce-connect", data); if (result.ok) setConfig(old => ({ ...old, platform, site: data.site as string, store_hash: data.store_hash as string })); setStatus(result.ok ? "connected" : "failed"); }
    catch { setStatus("failed"); }
    finally { inFlight.current = false; }
  }
  async function delivery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) return;
    const good = min !== "" && max !== "" && Number.isInteger(Number(min)) && Number.isInteger(Number(max)) && 0 <= Number(min) && Number(min) <= Number(max) && Number(max) <= 60;
    setInvalid(!good);
    if (!good) { event.currentTarget.querySelector<HTMLInputElement>("input")?.focus(); return; }
    inFlight.current = true;
    setSaving(true);
    try { const result = await act("store-delivery", { min_days: Number(min), max_days: Number(max) }); setInvalid(!result.ok); }
    catch { setInvalid(true); }
    finally { inFlight.current = false; setSaving(false); }
  }
  const name = platform ? platforms[platform] : "";
  const title = section === "orders" ? "Orders" : "Retailer settings";
  const body = section === "orders" ? <>{readError ? <p role="alert">Rig did not answer. Try again.</p> : <OrderRows orders={orders} now={now} />}</> : section === "delivery" ? <form className="rig-commerce-card" onSubmit={delivery}>
    <h2>Delivery from your own stock</h2>
    <label className="rig-commerce-field">Minimum business days<input type="number" min="0" max="60" step="1" required value={min} onChange={event => setMin(event.target.value)} aria-invalid={invalid} /></label>
    <label className="rig-commerce-field">Maximum business days<input type="number" min="0" max="60" step="1" required value={max} onChange={event => setMax(event.target.value)} aria-invalid={invalid} /></label>
    <p>Minimum and maximum business days for this store’s own stock. Items sourced from another supplier show that supplier’s longer delivery time.</p>
    {min !== "" && max !== "" && <p className="rig-commerce-preview">Shopper preview: Delivery: {min}–{max} business days</p>}
    <div className="rig-commerce-actions"><button className="rig-commerce-primary" disabled={saving}>Save delivery time</button></div>
  </form> : <form className="rig-commerce-card" onSubmit={connect}>
    <h2>Shop connection</h2>
    <div className="rig-commerce-field rig-commerce-platform"><span id="rig-platform-label">Shop platform</span><button className="rig-platform-select" type="button" role="combobox" aria-labelledby="rig-platform-label" aria-controls="rig-platform-options" aria-expanded={open} aria-haspopup="listbox" onClick={() => setOpen(!open)} onKeyDown={event => { if (event.key === "Escape") setOpen(false); if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) { event.preventDefault(); setOpen(true); requestAnimationFrame(() => document.querySelector<HTMLButtonElement>("#rig-platform-options button")?.focus()); } }}><span>{name || "Choose a platform"}</span><img src="/rig-cart/select-chevron.svg" alt="" width="16" height="16" /></button>
      {open && <div id="rig-platform-options" role="listbox" aria-labelledby="rig-platform-label">{Object.entries(platforms).map(([key, label], index) => <button key={key} type="button" role="option" aria-selected={platform === key} onClick={() => { setPlatform(key as Platform); setStatus("edit"); setOpen(false); document.querySelector<HTMLButtonElement>(".rig-platform-select")?.focus(); }} onKeyDown={event => { if (event.key === "Escape") { setOpen(false); document.querySelector<HTMLButtonElement>(".rig-platform-select")?.focus(); } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) { event.preventDefault(); const options = document.querySelectorAll<HTMLButtonElement>("#rig-platform-options button"); options[event.key === "Home" ? 0 : event.key === "End" ? 2 : (index + (event.key === "ArrowDown" ? 1 : 2)) % 3]?.focus(); } }}>{label}</button>)}</div>}
    </div>
    {platform && status === "edit" && <>
      <label className="rig-commerce-field">{platform === "bigcommerce" ? "Store hash" : "Store address"}<input name={platform === "bigcommerce" ? "store_hash" : "site"} type={platform === "bigcommerce" ? "text" : "url"} defaultValue={platform === "bigcommerce" ? config.store_hash : config.site} required autoComplete="off" /></label>
      {(platform === "woocommerce" ? [["consumer_key", "Consumer key"], ["consumer_secret", "Consumer secret"]] : [["access_token", "Access token"]]).map(([key, label]) => <label className="rig-commerce-field" key={key}>{label}<input name={key} type="password" required autoComplete="new-password" defaultValue="" /></label>)}
      <p>Get this store’s credentials from its {name} admin, then enter them here in Rig admin. Saved credentials stay hidden.</p>
      <div className="rig-commerce-actions"><button className="rig-commerce-primary">Save and test connection</button></div>
    </>}
    {platform && status !== "edit" && <>
      <div className="rig-commerce-notice" role="status"><strong>{status === "testing" ? "Testing connection…" : status === "connected" ? `Connected to ${name}` : "Connection failed"}</strong><p>{status === "testing" ? "Checking access to the store’s shop platform." : status === "connected" ? `Checkout sends shoppers to this store’s own ${name} checkout.` : "Check the store address and credentials, then test again."}</p></div>
      <p>{status === "failed" ? "Saved credentials remain hidden." : "Credentials saved and hidden."}</p>
      <div className="rig-commerce-actions"><button className="rig-commerce-primary" type="button" disabled={status === "testing"} onClick={() => void connect()}>{status === "testing" ? "Testing connection…" : status === "failed" ? "Test again" : "Test connection"}</button>{status !== "testing" && <button type="button" onClick={() => setStatus("edit")}>Replace credentials</button>}</div>
    </>}
  </form>;
  return <section className={`rig-commerce${embedded ? " rig-commerce-embedded" : ""}`} data-section={section}>
    {!embedded && <><a href="/rig">← Back to Rig</a><p className="rig-commerce-eyebrow">YOUR STORE</p><h1>{title}</h1></>}{body}
  </section>;
}
