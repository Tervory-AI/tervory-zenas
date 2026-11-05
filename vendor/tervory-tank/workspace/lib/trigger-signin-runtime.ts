import "server-only";

type Runtime = { issuer: string; client_id: string; workspace_client_id: string; project_id: string; organisations: Record<string, string>; founders: string[] };
let cached: Runtime | undefined;
let expires = 0;

export async function signinRuntime(): Promise<Runtime> {
  if (cached && expires > Date.now()) return cached;
  const response = await fetch(`${process.env.TANK_URL ?? "http://tank:8000"}/v1/trigger-signin/config`, {
    headers: { "x-tank-key": process.env.TANK_API_KEY ?? "" }, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Trigger sign-in setup is incomplete");
  const record = (await response.json() as { regions?: Record<string, Runtime> }).regions?.["us-east"];
  if (!record || record.issuer !== "https://auth.tervory.com" || !record.workspace_client_id || !record.project_id
    || !record.organisations || !Array.isArray(record.founders)) throw new Error("Trigger sign-in setup refused");
  process.env.TRIGGER_SIGNIN_ISSUER = record.issuer;
  process.env.TRIGGER_SIGNIN_CLIENT_ID = record.client_id;
  process.env.TRIGGER_SIGNIN_WORKSPACE_CLIENT_ID = record.workspace_client_id;
  process.env.TRIGGER_SIGNIN_PROJECT_ID = record.project_id;
  process.env.TRIGGER_SIGNIN_ORG_TENANTS = JSON.stringify(record.organisations);
  process.env.TERVORY_ADMIN_USER_IDS = record.founders.join(",");
  cached = record;
  expires = Date.now() + 30000;
  return record;
}
