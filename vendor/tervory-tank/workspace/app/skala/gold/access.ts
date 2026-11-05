import "server-only";
import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { rejectUnlessFounder, requireReviewer } from "@/app/review/access";
import { currentTenant } from "@/lib/tenant";

export async function goldIdentity(scope: string) {
  const { userId } = await auth();
  if (!userId) notFound();
  const founder = !(await rejectUnlessFounder());
  if (!founder) await requireReviewer();
  if (scope !== "platform" && scope !== "tenant") notFound();
  const tenantId = scope === "platform" ? "00000000-0000-0000-0000-000000000001" : (await currentTenant()).tenantId;
  return { userId, tenantId, role: founder ? "founder" : "reviewer" };
}
