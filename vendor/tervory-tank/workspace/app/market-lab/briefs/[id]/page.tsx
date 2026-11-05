import { activeTenant } from "@/lib/tenant";
import { notFound, redirect } from "next/navigation";
import { loadBrief } from "../../h7-load";
import { H7Shell } from "../../h7-shell";
import { BriefView } from "../../h7-view";
import "../../h7.css";
export const dynamic = "force-dynamic";
export const metadata = { title: "Market Lab · Tervory" };
export default async function BriefPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) notFound();
  const tenant = await activeTenant();
  if (!tenant) redirect("/onboarding");
  return <H7Shell><BriefView value={await loadBrief(tenant, id)} /></H7Shell>;
}
