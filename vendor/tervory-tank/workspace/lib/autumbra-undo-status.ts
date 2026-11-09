import { autumbraRequest } from "./autumbra-request";

/** Load whether an Undo link was already used (owner page). */

const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

export type UndoStatus = {
  already_undone: boolean;
  detail?: string;
  undone_at?: string;
  timeZone?: string;
  available?: boolean;
};

export async function loadUndoStatus(
  tenantId: string,
  priorReceiptId: string,
): Promise<UndoStatus> {
  if (!TANK_API_KEY || !tenantId || !priorReceiptId) {
    return { already_undone: false, available: false, detail: "Undo is not configured on this host yet." };
  }
  try {
    const upstream = new URL(`${TANK_URL}/v1/ghost/autumbra/undo/status`);
    upstream.searchParams.set("tenant_id", tenantId);
    upstream.searchParams.set("prior_receipt_id", priorReceiptId);
    return await autumbraRequest(upstream, {
      method: "GET",
      cache: "no-store",
      headers: { "x-tank-key": TANK_API_KEY },
    }, async res => {
    if (!res.ok) throw new Error("Unavailable");
    const data = (await res.json()) as {
      already_undone?: boolean;
      detail?: string;
      undone_at?: string;
      time_zone?: string;
    };
    if (typeof data?.already_undone !== "boolean") throw new Error("Unavailable");
    return {
      available: true,
      already_undone: data.already_undone,
      detail: data.already_undone ? "This block was already undone on an earlier time." : undefined,
      undone_at: data.undone_at,
      timeZone: typeof data.time_zone === "string" ? data.time_zone.trim() || "UTC" : "UTC",
    };
    });
  } catch {
    return { already_undone: false, available: false, detail: "Could not reach Autumbra to undo." };
  }
}
