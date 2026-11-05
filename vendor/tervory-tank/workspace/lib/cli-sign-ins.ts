import type { Region } from "@/app/api/zenas/tank";
import { signinTank, type SigninIdentity } from "@/lib/signin-regions";
import { boundedFetch } from "@/lib/tank";

export const SIGN_IN_REGIONS: readonly Region[] = ["us-east", "za"];
export const SIGN_IN_TIMEOUT_MS = 10_000;
const TOKEN_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export type SignIn = {
  token_id: string; region: Region; device_name: string; created_at: string; last_used_at: string | null;
};
export type RegionSignIns = { region: Region; ok: true; items: SignIn[] } | { region: Region; ok: false; message: string };

const MESSAGES: Record<number, string> = {
  401: "CLI sign-ins for this region cannot be shown right now. Your Workspace session is fine.",
  403: "CLI sign-ins for this region are managed by Tervory support for this account.",
};

function headers(key: string, identity: SigninIdentity): Record<string, string> {
  // No role header: listing and revoking are scoped to this user and org only.
  return { "x-tank-key": key, "x-tervory-user": identity.userId, "x-tervory-tenant": identity.tenantId };
}

function text(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

/** Active sign-ins only: not revoked, and the refresh token (or a legacy token without expiry) still valid. */
export function activeSignIns(rows: unknown, region: Region, now = Date.now()): SignIn[] {
  if (!Array.isArray(rows)) return [];
  const items: SignIn[] = [];
  for (const row of rows as Record<string, unknown>[]) {
    const id = text(row?.token_id);
    const created = text(row?.created_at);
    if (!id || !TOKEN_ID.test(id) || !created || row.revoked_at) continue;
    const until = text(row.refresh_expires_at) ?? text(row.expires_at);
    if (until && Date.parse(until) <= now) continue;
    items.push({ token_id: id, region, device_name: text(row.device_name) ?? text(row.name) ?? "Unnamed device",
                 created_at: created, last_used_at: text(row.last_used_at) });
  }
  return items;
}

export async function regionSignIns(identity: SigninIdentity, region: Region): Promise<RegionSignIns> {
  const tank = signinTank(region);
  if (!tank || !tank.key) return { region, ok: false, message: "CLI sign-ins for this region are not connected here." };
  try {
    const { status, body } = await boundedFetch(new URL("/v1/personal-tokens", tank.url), {
      method: "GET", headers: headers(tank.key, identity), cache: "no-store", signal: AbortSignal.timeout(SIGN_IN_TIMEOUT_MS),
    }, SIGN_IN_TIMEOUT_MS, async response => {
      if (!response.ok) {
        await response.arrayBuffer().catch(() => undefined);
        return { status: response.status, body: null };
      }
      return { status: response.status, body: await response.json().catch(() => null) as unknown };
    });
    if (status !== 200) return { region, ok: false, message: MESSAGES[status] ?? "CLI sign-ins for this region could not be loaded. Try again." };
    if (!Array.isArray(body)) return { region, ok: false, message: "CLI sign-ins for this region could not be loaded. Try again." };
    return { region, ok: true, items: activeSignIns(body, region) };
  } catch {
    return { region, ok: false, message: "CLI sign-ins for this region could not be reached. Try again." };
  }
}

export function listSignIns(identity: SigninIdentity): Promise<RegionSignIns[]> {
  return Promise.all(SIGN_IN_REGIONS.map(region => regionSignIns(identity, region)));
}

/** Revoke one of the signed-in user's CLI sign-ins. Tank ends its access and refresh tokens at once. */
export async function revokeSignIn(identity: SigninIdentity, region: Region, tokenId: string): Promise<boolean> {
  if (!TOKEN_ID.test(tokenId)) return false;
  const tank = signinTank(region);
  if (!tank || !tank.key) return false;
  try {
    return await boundedFetch(new URL(`/v1/personal-tokens/${tokenId}`, tank.url), {
      method: "DELETE", headers: headers(tank.key, identity), cache: "no-store", signal: AbortSignal.timeout(SIGN_IN_TIMEOUT_MS),
    }, SIGN_IN_TIMEOUT_MS, async response => {
      await response.arrayBuffer().catch(() => undefined);
      return response.status === 204;
    });
  } catch {
    return false;
  }
}
