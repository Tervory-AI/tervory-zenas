import { auth } from "@/lib/trigger-signin";
import { tenantIdFor } from "@/lib/tenant";

export async function administrator() {
  const { userId, orgId, orgRole } = await auth();
  if (!userId || !orgId || orgRole !== "org:admin") {
    throw new Error("Choose an organisation where you are an administrator to manage 3D product views.");
  }
  return { tenantId: tenantIdFor(orgId), userId };
}
