import { rigThreeDRequest } from "@/lib/tank";

export type RigThreeDBilling = { configured: boolean; enabled: boolean | null; amount: string | null };

export async function loadRigThreeDBilling(tenantId: string): Promise<RigThreeDBilling> {
  const configured = /^price_[A-Za-z0-9]+$/.test(process.env.RIG_3D_PRICE_ID?.trim() ?? "");
  try {
    const value = await (await rigThreeDRequest(tenantId, "capabilities")).json();
    const price = value.billing?.price;
    const amount = typeof price?.amount === "number" && Number.isFinite(price.amount)
      && /^[A-Z]{3}$/.test(price.currency)
      ? new Intl.NumberFormat("en-US", { style: "currency", currency: price.currency }).format(price.amount) : null;
    return { configured, enabled: value.rig_3d === true, amount };
  } catch { return { configured, enabled: null, amount: null }; }
}

export function RigThreeDAddon({ view }: { view: RigThreeDBilling }) {
  return <section className="panel" aria-labelledby="rig-three-d-billing" style={{ marginTop: 24 }}>
    <p className="kicker">Optional add-on</p>
    <h2 id="rig-three-d-billing">3D product views</h2>
    <p>Let shoppers turn products, explore parts that fit and request a quote. Includes showroom terminals.</p>
    {view.enabled === true ? <p className="chip">Included for this organisation</p> : null}
    {view.enabled === null ? <p role="status">We could not check whether this is included. Try again in a moment.</p> : null}
    {view.amount ? <p className="amount">{view.amount}</p> : null}
    {view.enabled ? <a href="/rig/admin/three-d/">Manage 3D product views</a>
      : view.enabled === null ? null
      : !view.configured ? <p><strong>Not available yet</strong></p>
      : <p>Contact your account owner to confirm the price and add 3D product views.</p>}
    <p className="caption">Your existing plan stays the same. Nothing is charged from this page.</p>
  </section>;
}
