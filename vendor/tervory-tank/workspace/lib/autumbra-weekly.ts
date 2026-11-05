const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

export type WeeklyReceipt = {
  id?: string;
  timestamp?: string;
  outcome?: string;
  severity?: string;
  advisory_url?: string;
  site?: string;
  address?: string;
  url?: string;
};

export async function loadWeeklyReceipts(
  tenant: string,
  token?: string | null,
): Promise<{
  receipts: WeeklyReceipt[];
  note?: string;
  unauthorized?: boolean;
  timeZone?: string;
}> {
  if (!TANK_API_KEY) {
    return { receipts: [], note: "Receipt list is not configured on this host yet.", timeZone: "UTC" };
  }
  try {
    const upstream = new URL(
      `${TANK_URL}/v1/ghost/autumbra/receipts/weekly/${encodeURIComponent(tenant)}`,
    );
    if (token) upstream.searchParams.set("token", token);
    const res = await fetch(upstream.toString(), {
      method: "GET",
      cache: "no-store",
      headers: { "x-tank-key": TANK_API_KEY },
    });
    if (res.status === 401) {
      return { receipts: [], unauthorized: true, note: "Unauthorized", timeZone: "UTC" };
    }
    if (!res.ok) {
      return { receipts: [], note: "Could not load this week's receipts right now.", timeZone: "UTC" };
    }
    const data = (await res.json()) as { receipts?: WeeklyReceipt[]; time_zone?: string };
    const timeZone = (data.time_zone || "").trim() || "UTC";
    return { receipts: Array.isArray(data.receipts) ? data.receipts : [], timeZone };
  } catch {
    return { receipts: [], note: "Could not reach Autumbra for this week's receipts.", timeZone: "UTC" };
  }
}
