// Server-only. Not a server-action module, so the browser cannot call the secret reader.

import { activeTenant } from "@/lib/tenant";
import { vaultPlugins, VAULT_OFF_MESSAGE } from "@/lib/tank";
import { mapVaultPayload, offPanel, type PluginPanel } from "./plugins";

const VAULT = "zenas.vault";
const SQUAD = "zenas";

export async function loadZenasPlugins(): Promise<PluginPanel> {
  const tenant = await activeTenant();
  if (!tenant) return offPanel("Sign in to use Plugins.");
  try {
    const raw = await vaultPlugins(VAULT, SQUAD, tenant.tenantId, tenant.userId);
    return mapVaultPayload(raw);
  } catch (error) {
    const text = error instanceof Error ? error.message : "";
    if (text.includes(VAULT_OFF_MESSAGE)) return offPanel();
    return offPanel("Plugins could not be loaded from Tank. No vault data is shown.");
  }
}
