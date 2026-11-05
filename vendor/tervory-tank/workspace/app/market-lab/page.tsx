import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { activeTenant } from "@/lib/tenant";
import { refreshFeeds } from "./actions";
import { readFlags, loadMarketLab } from "./load";
import { quarantinePageError } from "./quarantine";
import { loadQuarantine } from "./quarantine-server";
import { QuarantinePanel } from "./quarantine-view";
import { loadLabPlugins } from "./vault-server";
import { WarningSection } from "./warnings-server";
import { MarketLabView } from "./view";
import "./market-lab.css";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const metadata: Metadata = {
  title: "Market Lab · Tervory",
};

export default async function MarketLabPage({
  searchParams,
}: {
  searchParams: Promise<{ quarantine_error?: string | string[] }>;
}) {
  const params = await searchParams;
  const tenant = await activeTenant();
  if (!tenant) redirect("/onboarding");
  const workspaceName = tenant.orgName;
  const plugins = await loadLabPlugins();
  const envKeys = readFlags();
  const model = await loadMarketLab(tenant.tenantId);
  const quarantine = await loadQuarantine();
  return (
    <MarketLabView
      warnings={<WarningSection tenantId={tenant.tenantId} />}
      model={model}
      plugins={plugins}
      envKeys={envKeys}
      refresh={refreshFeeds}
      workspaceName={workspaceName}
      quarantine={
        quarantine ? (
          <QuarantinePanel
            batches={quarantine.batches}
            error={quarantinePageError(quarantine.error, params.quarantine_error)}
            listFailed={quarantine.error !== null}
          />
        ) : null
      }
    />
  );
}
