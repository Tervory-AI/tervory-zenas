import { goldIdentity } from "../../skala/gold/access";
import { notFound } from "next/navigation";
import { moaEvidence } from "@/lib/tank";
import { MoaView } from "./view";
import "./moa.css";
export const dynamic = "force-dynamic";
export const metadata = { title: "Mixture evidence · Tervory" };
export default async function MoaPage({ searchParams }: { searchParams: Promise<{ task?: string; status?: string }> }) {
  const identity = await goldIdentity("platform");
  if (identity.role !== "founder") notFound();
  const filters = await searchParams;
  try {
    const data = await moaEvidence(identity.userId, "00000000-0000-0000-0000-000000000001", filters.task);
    return <MoaView data={data} status={filters.status}/>;
  } catch {
    return <article className="moa"><h1>Mixture evidence</h1><p role="alert">Evidence is unavailable. Try again when Tank is reachable.</p><a href="/tank/proof">Back to Tank proof</a></article>;
  }
}
