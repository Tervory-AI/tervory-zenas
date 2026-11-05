// Server-only. Reads base.vault through Tank. Not a server-action module, so the browser cannot call it.
// Base reads only base.vault. It never reads tank.vault or another squad's vault, and it never turns a vault on.

import { activeTenant } from "@/lib/tenant";
import { vaultPlugins } from "@/lib/tank";
import { BASE_SQUAD, BASE_VAULT, LOAD_ERROR_MESSAGE, mapVaultPayload, offPanel, SIGN_IN_MESSAGE, VAULT_OFF_MESSAGE, type PluginPanel } from "./plugin-panel";

export async function loadBasePlugins(): Promise<PluginPanel> {
  const tenant = await activeTenant();
  if (!tenant) return offPanel(SIGN_IN_MESSAGE);
  try {
    const raw = await vaultPlugins(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId);
    return mapVaultPayload(raw);
  } catch (error) {
    const text = error instanceof Error ? error.message : "";
    if (text.includes(VAULT_OFF_MESSAGE)) return offPanel();
    return offPanel(LOAD_ERROR_MESSAGE);
  }
}
