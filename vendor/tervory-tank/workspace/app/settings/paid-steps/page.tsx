import { notFound } from "next/navigation";
import { triggerIdentity } from "@/lib/trigger";
import { activeTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { readPaidPause } from "@/lib/tank";
import TriggerShell from "../../trigger/shell";
import { PaidSettings } from "./settings";
import "../../trigger/trigger.css";
import "./settings.css";

export const dynamic = "force-dynamic";

export default async function PaidStepsPage() {
  const identity = await triggerIdentity(await activeTenant());
  if (!identity) notFound();
  const founder = isFounder(identity.userId);
  if (!founder && !identity.tenantAdmin) notFound();
  // The existing platform stop remains founder-only. A missing read must not
  // render a false running state or make a resume control available.
  const paused = founder ? await readPaidPause(identity.userId).then(value => value.paid_paused).catch(() => null) : null;
  return <TriggerShell title="Workspace"><PaidSettings founder={founder} paused={paused} /></TriggerShell>;
}
