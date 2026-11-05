import { rigCatalogueCounts } from "@/lib/tank";
import { activeTenant } from "@/lib/tenant";
import { Lab } from "./lab";
import { loadRigPlugins } from "./vault-server";

export const dynamic = "force-dynamic";

export default async function RigPage() {
  let accountName = "Your account";
  let products: number | null = null;
  let fits: number | null = null;
  try {
    const tenant = await activeTenant();
    if (tenant?.orgName.trim()) accountName = tenant.orgName.trim();
    if (tenant) {
      const counts = await rigCatalogueCounts(tenant.tenantId);
      if (counts) {
        products = counts.products;
        fits = counts.fits;
      }
    }
  } catch {
    accountName = "Your account";
    products = null;
    fits = null;
  }
  const plugins = await loadRigPlugins();
  return <Lab accountName={accountName} products={products} fits={fits} plugins={plugins} />;
}
