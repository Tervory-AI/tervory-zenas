// Existing SigninDirectory session authority only; no browser body/header supplies a role.
import { auth } from "@/lib/trigger-signin";
import { tenantIdFor, type Tenant } from "./tenant";
import { mobileIdentity } from "./mobile";

export async function triggerIdentity(tenant: Tenant | null): Promise<(Tenant & { tenantAdmin: boolean }) | null> {
  if (!tenant) return null;
  const mobile = await mobileIdentity();
  if (mobile && mobile.tenantId === tenant.tenantId && mobile.userId === tenant.userId) return mobile;
  const session = await auth();
  const tenantAdmin = session.userId === tenant.userId && session.orgId === tenant.orgId
    && tenantIdFor(tenant.orgId) === tenant.tenantId && session.orgRole === "org:admin";
  return { ...tenant, tenantAdmin };
}
