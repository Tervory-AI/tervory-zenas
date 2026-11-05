import { redirect } from "next/navigation";
import { activeTenant, currentTenant } from "@/lib/tenant";
import { triggerIdentity } from "@/lib/trigger";
import { mobileRequest } from "@/lib/tank";

export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const state = params.get("state");
  const challenge = params.get("code_challenge");
  const callback = "https://app.tervory.com/mobile/callback";
  if (!state || !/^[A-Za-z0-9_-]{43}$/.test(state) || !challenge || !/^[A-Za-z0-9_-]{43}$/.test(challenge)
      || params.get("code_challenge_method") !== "S256" || params.get("redirect_uri") !== callback) return new Response(null, { status: 400 });
  let tenant = await activeTenant();
  if (!tenant) {
    try { tenant = await currentTenant(); }
    catch { redirect(`/sign-in?callbackUrl=${encodeURIComponent(`https://app.tervory.com/mobile/sign-in?${params}`)}`); }
  }
  const identity = await triggerIdentity(tenant);
  if (!identity) redirect(`/sign-in?callbackUrl=${encodeURIComponent(`https://app.tervory.com/mobile/sign-in?${params}`)}`);
  const response = await mobileRequest("handshake", { challenge, org_id: identity.orgId, org_name: identity.orgName }, identity);
  if (!response.ok) return new Response("Trigger sign-in is unavailable. Please try again.", { status: 503 });
  const result = await response.json();
  return new Response(null, { status: 303, headers: { Location: `${callback}?${new URLSearchParams({ state, code: result.code })}`, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" } });
}
