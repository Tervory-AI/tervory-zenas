import type { Metadata } from "next";
import { auth } from "@/lib/trigger-signin";
import { isFounder } from "@/lib/admin";
import { activeTenant } from "@/lib/tenant";
import { refreshBase } from "./actions";
import { loadBase } from "./load";
import { offPanel, SIGN_IN_MESSAGE } from "./plugin-panel";
import { askBase, searchBase } from "./search";
import { loadBasePlugins } from "./vault-server";
import { BaseView } from "./view";
import "./base.css";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export const metadata: Metadata = {
  title: "Base · Tervory",
};

export default async function BasePage() {
  const { userId } = await auth();
  let accountName: string | null = null;
  let signedIn = false;
  try {
    const tenant = await activeTenant();
    signedIn = Boolean(tenant);
    accountName = tenant?.orgName ?? null;
  } catch {
    signedIn = false;
    accountName = null;
  }
  // Plugins read base.vault through Tank. While the vault is off, Tank returns no items and the tab says so.
  const plugins = signedIn ? await loadBasePlugins() : offPanel(SIGN_IN_MESSAGE);
  const model = await loadBase();
  return <BaseView model={model} plugins={plugins} accountName={accountName} refresh={refreshBase} search={searchBase} ask={askBase} signedIn={Boolean(userId)} canRunTrial={isFounder(userId)} />;
}
