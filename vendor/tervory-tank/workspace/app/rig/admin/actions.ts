"use server";

import { auth } from "@/lib/trigger-signin";
import { activeTenant } from "@/lib/tenant";
import { rigAdminAction, rigAdminPanel } from "@/lib/tank";
import { isListedFounder } from "../../review/founder";
import type { AdminOperation, AdminResult, RetailerPanel, MappingPreview, CommerceResult } from "./types";

export async function retailerIdentity() {
  const { userId, orgRole } = await auth();
  const founder = isListedFounder(userId, process.env.TERVORY_ADMIN_USER_IDS);
  if (!founder && orgRole !== "org:admin") throw new Error("Retailer administrator access is required.");
  const tenant = await activeTenant();
  if (!tenant) throw new Error("Choose your retailer account first.");
  return { tenantId: tenant.tenantId, userId: tenant.userId };
}

export async function changeRetailer(operation: AdminOperation, data: Record<string, unknown>): Promise<AdminResult> {
  try {
    const actor = await retailerIdentity();
    if (["commerce-connect", "commerce-orders", "store-delivery"].includes(operation)) {
      const commerce = await rigAdminAction<CommerceResult>(actor, operation, data);
      return { ok: true, message: "", commerce };
    }
    if (operation === "delete-products") {
      const result = await rigAdminAction<{ deleted: number }>(actor, operation, data);
      return { ok: true, message: `${result.deleted} deleted.`, deleted: result.deleted, panel: await rigAdminPanel<RetailerPanel>(actor) };
    }
    if (operation === "mapping") {
      const mapping = await rigAdminAction<MappingPreview>(actor, operation, data);
      return { ok: true, message: "Check each field and confirm the mapping before saving.", mapping };
    }
    if (operation === "embed-keys") {
      const embed = await rigAdminAction<NonNullable<AdminResult["embed"]>>(actor, operation, data);
      return { ok: true, message: "Copy your snippet now. The key is only shown once.", embed };
    }
    const result = await rigAdminAction<{ ok?: boolean; suggestions?: string; receipt?: { added: number; updated: number; rejected: number } }>(actor, operation, data);
    if (result.ok === false) return { ok: false, message: "Nothing was imported. Check the file, private price rule and founder-set exchange rate.", panel: await rigAdminPanel<RetailerPanel>(actor) };
    const message = result.receipt
      ? `${result.receipt.added} added, ${result.receipt.updated} updated, ${result.receipt.rejected} rejected.`
      : operation === "connect" ? "Feed connected. Nightly refresh is scheduled for 03:00 UTC."
      : operation === "suggest" ? "Suggestions are ready for review. Nothing was approved automatically." : "Saved.";
    return { ok: true, message: message + (result.suggestions === "unavailable" ? " Fit suggestions are unavailable; no combinations were approved." : ""), panel: await rigAdminPanel<RetailerPanel>(actor) };
  } catch {
    return { ok: false, message: "The operation was not confirmed. Check the settings and import history before trying again." };
  }
}
