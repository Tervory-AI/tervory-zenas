import { redirect } from "next/navigation";
import { activeTenant } from "@/lib/tenant";
import { triggerIdentity } from "@/lib/trigger";
import { listSignIns } from "@/lib/cli-sign-ins";
import { revokeCliSignIn } from "./actions";
import { SignInList } from "./list";
import "../../access/access.css";

export const dynamic = "force-dynamic";
export const metadata = { title: "CLI sign-ins", referrer: "no-referrer" };

type Params = Promise<Record<string, string | string[] | undefined>>;
const NOTICES: Record<string, string> = {
  revoked: "Revoked. That sign-in stopped working at once, including its refresh token.",
  failed: "That sign-in could not be revoked. Try again; if it persists, contact Tervory support.",
};

export default async function CliSignInsPage({ searchParams }: { searchParams: Params }) {
  const tenant = await triggerIdentity(await activeTenant());
  if (!tenant) redirect("/onboarding");
  const result = (await searchParams).result;
  const notice = typeof result === "string" ? NOTICES[result] : undefined;
  return <SignInList regions={await listSignIns(tenant)} action={revokeCliSignIn} notice={notice} />;
}
