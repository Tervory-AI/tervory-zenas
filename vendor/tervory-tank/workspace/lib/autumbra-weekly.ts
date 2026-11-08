import { autumbraRequest } from "./autumbra-request";
import { verifyUndoToken } from "./autumbra-undo-token";

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
  cost_usd?: number;
  would_have_cost_usd?: number;
};

export async function loadWeeklyReceipts(
  tenant: string,
  token?: string | null,
): Promise<{
  receipts: WeeklyReceipt[];
  note?: string;
  unauthorized?: boolean;
  timeZone?: string;
  available?: boolean;
}> {
  if (!TANK_API_KEY) {
    return { receipts: [], available: false, note: "Missing setting: TANK_API_KEY. Connect Autumbra to load receipts.", timeZone: "UTC" };
  }
  try {
    const upstream = new URL(
      `${TANK_URL}/v1/ghost/autumbra/receipts/weekly/${encodeURIComponent(tenant)}`,
    );
    if (token) upstream.searchParams.set("token", token);
    return await autumbraRequest(upstream, {
      method: "GET",
      cache: "no-store",
      headers: { "x-tank-key": TANK_API_KEY },
    }, async res => {
    if (res.status === 401) {
      return { receipts: [], available: false, unauthorized: true, note: "Unauthorized", timeZone: "UTC" };
    }
    if (!res.ok) {
      return { receipts: [], available: false, note: "Could not load this week's receipts right now.", timeZone: "UTC" };
    }
    const data = (await res.json()) as { receipts?: WeeklyReceipt[]; time_zone?: string };
    if (!Array.isArray(data.receipts) || data.receipts.some(row => !row || typeof row !== "object")) {
      throw new Error("Invalid receipt list");
    }
    let timeZone = typeof data.time_zone === "string" ? data.time_zone.trim() || "UTC" : "UTC";
    try { new Intl.DateTimeFormat("en", {timeZone}); } catch { timeZone = "UTC"; }
    return { receipts: data.receipts.filter(row => {
      type Source = { fixture?: boolean; kind?: string; control?: string };
      const source = row as WeeklyReceipt & Source & { receipt?: Source };
      return [source, source.receipt].every(item => !item || (!item.fixture
        && item.kind !== "exercise" && item.control !== "fixture_edge"));
    }).map(row => {
      const clean: WeeklyReceipt = {};
      for (const key of ["cost_usd", "would_have_cost_usd"] as const) {
        const amount = row[key];
        if (typeof amount === "number" && Number.isFinite(amount) && amount >= 0) clean[key] = amount;
      }
      for (const key of ["id", "timestamp", "outcome", "severity", "site", "address"] as const) {
        if (typeof row[key] === "string") clean[key] = row[key].slice(0, 1000);
      }
      try {
        const advisory = new URL(row.advisory_url || "");
        if (advisory.protocol === "https:" && !advisory.username && !advisory.password) clean.advisory_url = advisory.href;
      } catch { /* Invalid links are not actionable. */ }
      try {
        const undo = new URL(row.url || "");
        const signed = verifyUndoToken(undo.searchParams.get("token") || "");
        if (["https://app.tervory.com", "https://protect.tervory.com"].includes(undo.origin) && !undo.username && !undo.password
            && undo.pathname === `/autumbra/undo/${encodeURIComponent(signed.receipt_id)}`
            && signed.tenant_id === tenant && signed.receipt_id === clean.id) clean.url = undo.href;
      } catch { /* A receipt cannot link to another client's action. */ }
      return clean;
    }), available: true, timeZone };
    });
  } catch {
    return { receipts: [], available: false, note: "Could not reach Autumbra for this week's receipts.", timeZone: "UTC" };
  }
}
