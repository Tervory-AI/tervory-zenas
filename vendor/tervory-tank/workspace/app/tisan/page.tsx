import { activeTenant } from "@/lib/tenant";
import { offPanel, type PluginPanel } from "./vault-model";
import { loadTisanPlugins } from "./vault-server";
import { Tisan } from "./tisan";

export const dynamic = "force-dynamic";

export default async function TisanPage() {
  let plugins: PluginPanel = offPanel();
  let accountName: string | null = null;
  let canApproveGold = false;
  try {
    plugins = await loadTisanPlugins();
  } catch {
    plugins = offPanel("Plugins could not be loaded from Tank. No vault data is shown.");
  }
  try {
    const tenant = await activeTenant();
    accountName = tenant?.orgName ?? null;
    canApproveGold = !!tenant && (process.env.TERVORY_ADMIN_USER_IDS ?? "").split(",").map((id) => id.trim()).includes(tenant.userId);
  } catch {
    accountName = null;
  }
  return <Tisan plugins={plugins} accountName={accountName} canApproveGold={canApproveGold} />;
}
