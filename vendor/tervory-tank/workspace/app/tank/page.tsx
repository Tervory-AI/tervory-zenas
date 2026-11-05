import type { Metadata } from "next";
import { goldIdentity } from "../skala/gold/access";
import { rejectUnlessFounder } from "@/app/review/access";
import { notFound } from "next/navigation";
import { GraduationPanel } from "./graduation-panel";
import { trainingGraduation, listRoster } from "@/lib/tank";
import { activeTenant } from "@/lib/tenant";
import type { ModelRow } from "./squads";
import { TankView } from "./view";
import type { WorkersFeed } from "./workers";
import { listWorkers } from "./workers-server";
import "./tank.css";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const metadata: Metadata = {
  title: "Tank · Tervory",
};

export default async function TankPage() {
  // Gold also admits reviewers; the global Tank console is founder-only.
  // Admit before reading the workspace, model roster or worker feed.
  if (await rejectUnlessFounder()) notFound();
  const identity = await goldIdentity("platform");
  let workspaceName: string | null = null;
  try {
    const tenant = await activeTenant();
    workspaceName = tenant?.orgName ?? null;
  } catch {
    workspaceName = null;
  }
  let models: ModelRow[] = [];
  let modelsUnavailable = false;
  try {
    models = await listRoster();
  } catch {
    modelsUnavailable = true;
  }
  let workers: WorkersFeed = { available: false, tasks: [] };
  try {
    workers = await listWorkers();
  } catch {
    workers = { available: false, tasks: [] };
  }
  const graduation = identity.role === "founder" ? await trainingGraduation(identity).catch(() => null) : null;
  return <TankView graduation={<GraduationPanel rows={graduation}/>} workspaceName={workspaceName} models={models} modelsUnavailable={modelsUnavailable} workers={workers} />;
}
