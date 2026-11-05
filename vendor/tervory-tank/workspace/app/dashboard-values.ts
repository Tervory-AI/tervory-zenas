import type { DashboardRead } from "@/lib/tank";
import type { Savings } from "./tank/proof/savings-types";

export function requiredDashboardText(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new Error("Dashboard source is incomplete.");
  return value;
}

/** Missing measurements are errors, never placeholder counts or sample names. */
export function clientMeasurements(data: DashboardRead, savings: Savings) {
  if (!data || !Number.isSafeInteger(data.jobs_done) || data.jobs_done < 0 ||
      !Array.isArray(data.recent) || !Array.isArray(data.granted_squads) ||
      !data.granted_squads.every(item => typeof item === "string")) {
    throw new Error("Dashboard activity source is incomplete.");
  }
  const amount = savings?.month?.saved;
  if ((typeof amount !== "string" && typeof amount !== "number") ||
      String(amount).trim() === "" || !Number.isFinite(Number(amount))) {
    throw new Error("Dashboard savings source is incomplete.");
  }
  const recent = data.recent.slice(0, 4);
  for (const job of recent) {
    if (!job || !["queued", "running", "done", "failed"].includes(job.status)) {
      throw new Error("Dashboard job status has no approved display state.");
    }
    requiredDashboardText(job.id);
    requiredDashboardText(job.title);
  }
  return { jobsDone: data.jobs_done, saved: Number(amount), recent, grantedSquads: data.granted_squads };
}
