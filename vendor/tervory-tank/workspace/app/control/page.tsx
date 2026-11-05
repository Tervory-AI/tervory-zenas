import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { isFounder } from "@/lib/admin";
import { goldSummary, readFounderDashboard, readPaidPause, readDashboardCandidates } from "@/lib/tank";
import { signupSource, payingSource } from "./sources";
import { readWorkers } from "./workers";
import { FounderDashboard } from "./view";
import { activity, candidates, wholeNumber } from "./values";
import "./dashboard.css";
import "./control.css";

export const dynamic = "force-dynamic";
export default async function ControlPage() {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) notFound();
  const now = Date.now();
  const [data, signups, paying, pause, models, workers, gold] = await Promise.all([
    readFounderDashboard(userId), signupSource(now), payingSource(now), readPaidPause(userId),
    readDashboardCandidates(userId), readWorkers(userId),
    goldSummary({ userId, tenantId: "00000000-0000-0000-0000-000000000001", role: "founder" }),
  ]).catch(() => { throw new Error("Control panel source could not be read."); });
  if (typeof pause.paid_paused !== "boolean" || typeof workers.workers_paused !== "boolean") {
    throw new Error("Control panel pause state is incomplete.");
  }
  return <FounderDashboard view={{ data, signups, paying, activity: activity(data, now),
    paused: pause.paid_paused, workersPaused: workers.workers_paused, workers, models: candidates(models),
    checks: gold.skills.reduce((total, skill) => total + wholeNumber(skill.candidates), 0), now }} />;
}
