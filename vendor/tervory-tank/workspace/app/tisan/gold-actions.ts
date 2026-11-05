"use server";

import { activeTenant } from "@/lib/tenant";
import { approveTisanGold, tisanGoldPages } from "@/lib/tank";

const PLATFORM_TENANT = "00000000-0000-0000-0000-000000000001";

async function founder() {
  const tenant = await activeTenant();
  const allowed = (process.env.TERVORY_ADMIN_USER_IDS ?? "").split(",").map((id) => id.trim()).filter(Boolean);
  if (!tenant || !allowed.includes(tenant.userId)) throw new Error("Not available");
  return tenant;
}

export async function loadGoldPages() {
  const tenant = await founder();
  const [platform, workspace] = await Promise.all([
    tisanGoldPages(PLATFORM_TENANT, tenant.userId),
    tisanGoldPages(tenant.tenantId, tenant.userId),
  ]);
  return [...platform, ...workspace];
}

export async function approveGoldPage(pageId: string, frameHash: string, pageHash: string, scope: "platform" | "workspace" = "workspace") {
  const tenant = await founder();
  if (!["platform", "workspace"].includes(scope)) throw new Error("Invalid page scope");
  if (!/^[a-z0-9-]{1,100}$/.test(pageId) || !/^[a-f0-9]{64}$/.test(frameHash) || !/^[a-f0-9]{64}$/.test(pageHash))
    throw new Error("Invalid page reference");
  await approveTisanGold(scope === "platform" ? PLATFORM_TENANT : tenant.tenantId, tenant.userId, pageId, frameHash, pageHash);
  return { approved: true };
}
