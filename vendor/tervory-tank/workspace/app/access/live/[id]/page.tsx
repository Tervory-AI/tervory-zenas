import { notFound } from "next/navigation";
import { accessIdentity } from "../../identity";
import { LiveView } from "./view";
import "../../access.css";
export const dynamic = "force-dynamic";
export default async function LivePage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ account?: string }> }) {
  const { id } = await params; const { account } = await searchParams;
  const actor = await accessIdentity(account);
  if (!actor.founder || !/^[0-9a-f-]{36}$/.test(id)) notFound();
  return <LiveView id={id} account={actor.tenantId} />;
}
