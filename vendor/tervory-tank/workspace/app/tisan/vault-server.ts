// Server-only. Not a server-action module, so the browser cannot call into the vault.

import { activeTenant } from "@/lib/tenant";
import { vaultPlugins } from "@/lib/tank";
import { mapVaultPayload, offPanel, TISAN_SQUAD, TISAN_VAULT, VAULT_OFF_MESSAGE, type PluginPanel } from "./vault-model";

export async function loadTisanPlugins(): Promise<PluginPanel> {
  let tenant: Awaited<ReturnType<typeof activeTenant>> = null;
  try {
    tenant = await activeTenant();
  } catch {
    return offPanel("Sign in to use Plugins.");
  }
  if (!tenant) return offPanel("Sign in to use Plugins.");
  try {
    const raw = await vaultPlugins(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId);
    return mapVaultPayload(raw);
  } catch (error) {
    const text = error instanceof Error ? error.message : "";
    if (text.includes(VAULT_OFF_MESSAGE)) return offPanel();
    return offPanel("Plugins could not be loaded from Tank. No vault data is shown.");
  }
}
