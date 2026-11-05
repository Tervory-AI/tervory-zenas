import { currentTenant } from "@/lib/tenant";
import { checkoutNotice, loadBilling } from "./billing";
import { BillingScreen, billingTab } from "./screen";
import "./billing.css";
import { loadRigThreeDBilling, RigThreeDAddon } from "./rig-three-d";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Billing · Tervory",
  description: "Plans, usage, and invoices for this organisation.",
};

export default async function BillingPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string; checkout?: string }>;
}) {
  const params = await searchParams;
  const tab = billingTab(params.tab);
  const notice = checkoutNotice(typeof params.checkout === "string" ? params.checkout : undefined);
  try {
    const tenant = await currentTenant();
    const view = await loadBilling(tenant.tenantId);
    const threeD = tab === "plans" ? await loadRigThreeDBilling(tenant.tenantId) : null;
    return <BillingScreen tab={tab} view={view} notice={notice}
      addon={threeD ? <RigThreeDAddon view={threeD} /> : null} />;
  } catch (err) {
    const message = err instanceof Error && err.message.includes("not signed in")
      ? "Sign in to see billing."
      : "Billing could not be loaded. Try again in a moment.";
    return (
      <div className="billing">
        <p className="kicker">Billing</p>
        <h1>Billing</h1>
        <p className="problem">{message}</p>
      </div>
    );
  }
}
