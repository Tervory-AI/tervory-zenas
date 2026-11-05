import "server-only";
import { currentTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
export async function accessIdentity(selected?: string | null) {
  const tenant = await currentTenant();
  const founder = isFounder(tenant.userId);
  if (selected && (!founder || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(selected))) throw new Error("Account selection refused.");
  return { tenantId: selected || tenant.tenantId, userId: tenant.userId, founder, orgName: tenant.orgName };
}
