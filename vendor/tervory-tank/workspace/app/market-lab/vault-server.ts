// Server-only. Not a server-action module, so the browser cannot call the secret reader.

import { activeTenant } from "@/lib/tenant";
import { resolveVaultSecret, vaultPlugins, VAULT_OFF_MESSAGE } from "@/lib/tank";
import type { KeyName } from "./catalog";
import { mapVaultPayload, offPanel, type PluginPanel } from "./plugins";

const VAULT = "lab.vault";
const SQUAD = "market_lab";

export async function loadLabPlugins(): Promise<PluginPanel> {
  const tenant = await activeTenant();
  if (!tenant) return offPanel("Sign in to use Plugins.");
  try {
    const raw = await vaultPlugins(VAULT, SQUAD, tenant.tenantId, tenant.userId);
    return mapVaultPayload(raw);
  } catch (error) {
    const text = error instanceof Error ? error.message : "";
    if (text.includes(VAULT_OFF_MESSAGE)) return offPanel();
    return offPanel("Plugins could not be loaded. Your saved items are not shown.");
  }
}

/** Used by the page while it calls a feed. The return value is not passed to the browser. */
export async function resolveLabFeedKey(name: KeyName): Promise<string> {
  const tenant = await activeTenant();
  if (!tenant) return "";
  try {
    return (await resolveVaultSecret(VAULT, SQUAD, tenant.tenantId, tenant.userId, name)) ?? "";
  } catch {
    return "";
  }
}
