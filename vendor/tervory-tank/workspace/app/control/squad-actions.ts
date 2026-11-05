"use server";
import { auth, signinDirectory } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { isFounder } from "@/lib/admin";
import { tenantIdFor } from "@/lib/tenant";
import { readCommandSquads, setCommandSquad } from "@/lib/tank";
import type { CommandSquadGrant, CommandSquadChange } from "@/lib/tank";

export type SquadClients = { clients: { id: string; name: string }[]; page: number; more: boolean; error?: string };
export type SquadState = { grants: CommandSquadGrant[]; log: CommandSquadChange[]; error?: string };
async function founder() {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) notFound();
  return userId;
}
export async function squadClients(page: number): Promise<SquadClients> {
  await founder();
  if (!Number.isSafeInteger(page) || page < 0 || page > 10000) return { clients: [], page: 0, more: false, error: "Invalid client page." };
  try {
    const directory = await signinDirectory();
    const result = await directory.organizations.getOrganizationList({ limit: 25, offset: page * 25, orderBy: "name" });
    return { clients: result.data.map(org => ({ id: org.id, name: org.name })), page, more: (page + 1) * 25 < result.totalCount };
  } catch { return { clients: [], page, more: false, error: "Clients could not be loaded. Try again." }; }
}
export async function squadState(orgId: string): Promise<SquadState> {
  const userId = await founder();
  try {
    const directory = await signinDirectory();
    const org = await directory.organizations.getOrganization({ organizationId: orgId });
    return await readCommandSquads(userId, tenantIdFor(org.id));
  } catch { return { grants: [], log: [], error: "Client access could not be loaded. Try again." }; }
}
export async function changeSquad(orgId: string, agent: string, enabled: boolean): Promise<SquadState> {
  const userId = await founder();
  try {
    if (typeof enabled !== "boolean" || !["base", "tiger", "market_lab", "rig", "tisan", "radix", "zenas"].includes(agent)) throw Error("Invalid change");
    const directory = await signinDirectory();
    const org = await directory.organizations.getOrganization({ organizationId: orgId });
    const tenantId = tenantIdFor(org.id);
    await setCommandSquad(userId, tenantId, agent, enabled);
    return await readCommandSquads(userId, tenantId);
  } catch { return { grants: [], log: [], error: "The change could not be confirmed. Reload access before trying again." }; }
}
