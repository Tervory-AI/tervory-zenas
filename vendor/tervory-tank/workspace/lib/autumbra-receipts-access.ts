import { auth } from "@/lib/trigger-signin";
import { activeTenant } from "@/lib/tenant";
import { verifyReceiptsToken } from "@/lib/autumbra-undo-token";

/**
 * Weekly receipts: signed-in owner of THAT tenant, or a valid unexpired
 * receipts token signed for THAT tenant. Anything else → deny.
 */
export async function mayReadWeeklyReceipts(
  tenant: string,
  token: string | null | undefined,
): Promise<boolean> {
  const tid = (tenant || "").trim();
  if (!tid) return false;
  const raw = (token || "").trim();
  if (raw) {
    try {
      const payload = verifyReceiptsToken(raw);
      return payload.tenant_id === tid;
    } catch {
      return false;
    }
  }
  try {
    const { userId } = await auth();
    if (!userId) return false;
    const tenantSession = await activeTenant();
    return !!tenantSession && tenantSession.tenantId === tid;
  } catch {
    return false;
  }
}
