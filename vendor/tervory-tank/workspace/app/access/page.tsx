import { accessRequest, type AccessPanel, type AccessQueueItem } from "@/lib/tank";
import { accessIdentity } from "./identity";
import { AccessView } from "./view";
import "./access.css";
export const dynamic = "force-dynamic";
export default async function AccessPage({ searchParams }: { searchParams: Promise<{ account?: string; section?: string }> }) {
  const { account, section } = await searchParams;
  const actor = await accessIdentity(account);
  const result = await Promise.allSettled([
    accessRequest<AccessPanel>(actor, "connections"), accessRequest<AccessQueueItem[]>(actor, "queue"),
  ]);
  return <AccessView founder={actor.founder} account={actor.tenantId} selected={account ?? ""} name={actor.orgName}
    initialSection={section === "checks" ? "checks" : "connections"}
    panel={result[0].status === "fulfilled" ? result[0].value : null}
    queue={result[1].status === "fulfilled" ? result[1].value : []}
    error={result.some(r => r.status === "rejected") ? "Connections could not be loaded. Please try again." : ""} />;
}
