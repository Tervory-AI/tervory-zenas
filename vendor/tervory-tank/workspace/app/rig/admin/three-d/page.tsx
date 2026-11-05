import { randomUUID } from "node:crypto";
import { rigThreeDRequest } from "@/lib/tank";
import { administrator } from "./server";
import { changeThreeD } from "./actions";
import { ThreeDAdminScreen, type ProductJob } from "./screen";
import "./three-d.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "3D product views · Tervory" };

export default async function Page({ searchParams }: { searchParams: Promise<{ notice?: string; job?: string }> }) {
  const params = await searchParams;
  let jobs: ProductJob[] = [], enabled = false, fewPhotos = false, error = "";
  try {
    const identity = await administrator();
    const caps = await (await rigThreeDRequest(identity.tenantId, "capabilities")).json();
    enabled = caps.rig_3d === true;
    fewPhotos = Array.isArray(caps.photo_sources) && caps.photo_sources.includes("ai");
    if (enabled) {
      const dashboard = await (await rigThreeDRequest(identity.tenantId, "dashboard", {}, identity.userId)).json();
      jobs = dashboard.jobs;
    }
  } catch { error = "This page could not be loaded. Choose an organisation where you are an administrator, then try again."; }
  const jobId = /^[0-9a-f-]{36}$/.test(params.job ?? "") ? params.job! : randomUUID();
  return <ThreeDAdminScreen jobs={jobs} enabled={enabled} fewPhotos={fewPhotos} error={error} notice={params.notice} jobId={jobId} action={changeThreeD} />;
}
