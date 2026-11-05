import type { Metadata } from "next";
import { goldIdentity } from "./access";
import { GoldView } from "./view";
import "./gold.css";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const metadata: Metadata = { title: "Squad training · Skala · Tervory" };

export default async function GoldPage() {
  const identity = await goldIdentity("platform");
  return <GoldView founder={identity.role === "founder"} />;
}
