import { rigAdminPanel } from "@/lib/tank";
import { retailerIdentity } from "./actions";
import { CommerceAdmin, type CommerceSection } from "./commerce-admin";
import { RetailerAdmin } from "./retailer-admin";
import type { RetailerPanel } from "./types";
import "./retailer-admin.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "Retailer settings · Rig" };

export default async function RetailerAdminPage({ searchParams }: { searchParams: Promise<{ cart?: string }> }) {
  const section = (await searchParams).cart;
  let panel: RetailerPanel;
  try {
    panel = await rigAdminPanel<RetailerPanel>(await retailerIdentity());
  } catch {
    return <main className="retailer-admin"><a href="/rig">← Back to Rig</a><h1>Retailer settings</h1><p role="alert">Retailer settings are unavailable. Sign in as your retailer’s administrator and check that Rig is connected.</p></main>;
  }
  if (section && ["connection", "orders", "delivery"].includes(section)) return <CommerceAdmin section={section as CommerceSection} />;
  return <><RetailerAdmin initial={panel} /><div className="rig-commerce-index">{(["connection", "orders", "delivery"] as const).map(key => <div key={key}><a href={`/rig/admin?cart=${key}`}>{key === "connection" ? "Shop connection" : key === "orders" ? "Orders" : "Delivery from your own stock"}</a><CommerceAdmin section={key} embedded /></div>)}</div></>;
}
