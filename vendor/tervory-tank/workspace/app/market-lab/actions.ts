"use server";

import { revalidatePath } from "next/cache";
import { activeTenant } from "@/lib/tenant";
import { marketLabRequest } from "@/lib/tank";

export async function refreshFeeds(): Promise<void> {
  const tenant = await activeTenant();
  if (!tenant) throw new Error("Sign in and choose a workspace first.");
  await marketLabRequest(tenant.tenantId, "refresh");
  revalidatePath("/market-lab");
}
