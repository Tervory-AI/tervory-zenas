import { NextResponse } from "next/server";
import { signinRuntime } from "@/lib/trigger-signin-runtime";

export async function GET() {
  const { issuer, workspace_client_id } = await signinRuntime();
  const target = new URL("/oidc/v1/end_session", issuer);
  target.searchParams.set("client_id", workspace_client_id);
  target.searchParams.set("post_logout_redirect_uri", "https://app.tervory.com/sign-in");
  return NextResponse.redirect(target, { headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" } });
}
