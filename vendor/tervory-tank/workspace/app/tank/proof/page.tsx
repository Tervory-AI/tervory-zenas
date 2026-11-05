import { goldIdentity } from "../../skala/gold/access";
import { notFound } from "next/navigation";
import { readProof, type ProofFilters } from "./server";
import { ProofView } from "./view";
import "./proof.css";
import { FounderSavingsPanel } from "./savings-founder";
import "../moa/moa.css";
import { moaEvidence } from "@/lib/tank";
import { ShapeChart } from "../moa/view";

export const dynamic = "force-dynamic";
export const metadata = { title: "Tank proof · Tervory" };

export default async function ProofPage({ searchParams }: { searchParams: Promise<ProofFilters> }) {
  const identity = await goldIdentity("platform");
  if (identity.role !== "founder") notFound();
  const filters = await searchParams;
  try {
    const savings = await FounderSavingsPanel({tenant:filters.tenant_id});
    const mixtures = await moaEvidence(identity.userId, filters.tenant_id && filters.tenant_id !== "all" ? filters.tenant_id : "00000000-0000-0000-0000-000000000001", filters.task).catch(() => null);
    return <>{savings}<ProofView data={await readProof(filters)} task={filters.task} /><section className="moa"><h2>Mixture policies</h2><p><a href="/tank/moa">View seats, receipts and promotions</a></p>
      {!mixtures && <p role="alert">Mixture evidence is unavailable.</p>}
      {mixtures?.tasks.filter(t => t.config.seats.length).map(t => <section key={t.task_type}><h3>{t.task_type}</h3><ShapeChart rows={t.shapes}/></section>)}
    </section></>;
  } catch {
    return <section className="proof"><h1>Tank proof</h1><p role="alert">Proof evidence is unavailable. Try again when Tank is reachable.</p><a href="/tank">Back to Tank</a></section>;
  }
}
