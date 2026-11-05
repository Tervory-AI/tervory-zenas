"use server";

import { redirect } from "next/navigation";
import { activeTenant } from "@/lib/tenant";
import { triggerIdentity } from "@/lib/trigger";
import { signinRegion } from "@/lib/signin-regions";
import { revokeSignIn } from "@/lib/cli-sign-ins";

/** Revoke one of the signed-in user's own CLI sign-ins (Tank scopes it to this user and org). */
export async function revokeCliSignIn(form: FormData): Promise<void> {
  const tenant = await triggerIdentity(await activeTenant());
  if (!tenant) redirect("/onboarding");
  const region = signinRegion(form.get("region"));
  const id = form.get("token_id");
  const ok = region !== null && typeof id === "string" && await revokeSignIn(tenant, region, id);
  redirect(`/cli/sign-ins?result=${ok ? "revoked" : "failed"}`);
}
