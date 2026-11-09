import { baseBatches, baseRecords, listSkills } from "@/lib/tank";
import { loadContextReport, type ContextReport } from "./context-report";
import { currentTenant } from "@/lib/tenant";
import { buildModel, type BatchIn, type PageModel, type SkillIn } from "./model";

/** Reads batch counts and approved skills from Tank. Read only: nothing is queued, re-run or saved. */
export async function loadBase(): Promise<PageModel> {
  let tenantId: string | null = null;
  try { tenantId = (await currentTenant()).tenantId; } catch { /* Signed-out reads contain no tenant data. */ }
  const [batches, context, skills] = await Promise.allSettled([
    tenantId ? baseBatches(tenantId) : Promise.reject(new Error("Sign in to your workspace.")),
    tenantId ? loadContextReport(tenantId, baseRecords) : Promise.reject(new Error("Sign in to your workspace.")),
    listSkills(true),
  ]);
  return { ...buildModel({ batches: batches.status === "fulfilled" ? batches.value : null,
    skills: skills.status === "fulfilled" ? skills.value : null, now: new Date() }),
    contextTrial: context.status === "fulfilled" ? context.value : null };
}
