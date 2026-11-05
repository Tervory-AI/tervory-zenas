/** Load whether an Undo link was already used (owner page). */

const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

export type UndoStatus = {
  already_undone: boolean;
  detail?: string;
  undone_at?: string;
  timeZone?: string;
};

export async function loadUndoStatus(
  tenantId: string,
  priorReceiptId: string,
): Promise<UndoStatus> {
  if (!TANK_API_KEY || !tenantId || !priorReceiptId) {
    return { already_undone: false };
  }
  try {
    const upstream = new URL(`${TANK_URL}/v1/ghost/autumbra/undo/status`);
    upstream.searchParams.set("tenant_id", tenantId);
    upstream.searchParams.set("prior_receipt_id", priorReceiptId);
    const res = await fetch(upstream.toString(), {
      method: "GET",
      cache: "no-store",
      headers: { "x-tank-key": TANK_API_KEY },
    });
    if (!res.ok) return { already_undone: false };
    const data = (await res.json()) as {
      already_undone?: boolean;
      detail?: string;
      undone_at?: string;
      time_zone?: string;
    };
    return {
      already_undone: Boolean(data.already_undone),
      detail: data.detail,
      undone_at: data.undone_at,
      timeZone: (data.time_zone || "").trim() || "UTC",
    };
  } catch {
    return { already_undone: false };
  }
}
