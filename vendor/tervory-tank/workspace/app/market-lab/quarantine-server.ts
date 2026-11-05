import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";
import "server-only";
import { auth } from "@/lib/trigger-signin";
import { isListedFounder } from "../review/founder";
import { quarantineFromResponse, type QuarantineLoad } from "./quarantine";

const PLATFORM = "00000000-0000-0000-0000-000000000001";

export async function loadQuarantine(): Promise<QuarantineLoad | null> {
  let userId: string | null = null;
  try {
    userId = (await auth()).userId;
  } catch {
    return null;
  }
  if (!isListedFounder(userId, process.env.TERVORY_ADMIN_USER_IDS) || !userId) return null;
  const key = process.env.TANK_API_KEY ?? "";
  if (!key) return quarantineFromResponse(null, null);
  const host = process.env.TANK_URL ?? "http://tank:8000";
  try {
    const response = await fetch(`${host}/v1/market/quarantine`, {
      headers: {
        "x-tank-key": key,
        "x-tervory-user": userId,
        "x-tervory-tenant": PLATFORM,
        "x-tervory-role": "founder",
      },
      cache: "no-store",
    });
    const body = response.ok ? await response.json().catch(() => null) : null;
    return quarantineFromResponse(response.status, body);
  } catch {
    return quarantineFromResponse(null, null);
  }
}
