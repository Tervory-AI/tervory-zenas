import { baseRecords, type BaseRecord } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

// GET /api/base/export?batch=<name>  →  CSV of every enriched record in the batch, one row each.
// Flat columns a person can audit in a spreadsheet; the full profile JSON is the last column.
const COLUMNS = [
  "name", "street", "city", "state", "zip", "phone", "website", "emails", "decision_maker", "description", "categories",
  "services", "socials_profiles", "size_band", "revenue_band", "business_model", "naics",
  "ffl_licence_number", "ffl_licence_type", "ffl_licence_type_label", "ffl_licence_expires", "ffl_renewal_window", "ffl_lapsed",
  "ffl_segment", "ffl_inventory_signal", "registry_match", "tier", "flags",
];

function cell(f: { value: unknown } | undefined): string {
  if (!f || f.value == null || f.value === "") return "";
  const v = f.value;
  if (Array.isArray(v)) return v.map((x) => (typeof x === "object" && x ? JSON.stringify(x) : String(x))).join("; ");
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if ("profiles" in o && o.profiles && typeof o.profiles === "object") {
      return Object.values(o.profiles as Record<string, { url?: string }>).map((p) => p.url ?? "").filter(Boolean).join("; ");
    }
    return JSON.stringify(v);
  }
  return String(v);
}

function esc(s: string): string {
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export async function GET(req: Request) {
  const batch = new URL(req.url).searchParams.get("batch")?.trim();
  if (!batch) return new Response("batch required", { status: 400 });
  try {
    const { tenantId } = await currentTenant();
    const all: BaseRecord[] = [];
    for (let offset = 0; offset < 20000; offset += 2000) {
      const page = await baseRecords(tenantId, { batch, limit: 2000, offset });
      all.push(...page);
      if (page.length < 2000) break;
    }
    const header = ["status", "confidence", ...COLUMNS, "profile_json"].join(",");
    const lines = all.map((r) =>
      [r.status, String(r.confidence ?? ""), ...COLUMNS.map((c) => esc(cell(r.profile[c]))), esc(JSON.stringify(r.profile))].join(","),
    );
    const csv = [header, ...lines].join("\n");
    const safe = batch.replace(/[^a-z0-9-_ ]/gi, "_").slice(0, 60);
    return new Response(csv, {
      headers: {
        "content-type": "text/csv; charset=utf-8",
        "content-disposition": `attachment; filename="tervory-${safe}.csv"`,
        "cache-control": "no-store",
      },
    });
  } catch (err) {
    return new Response(err instanceof Error ? err.message : "Tank unavailable", { status: 502 });
  }
}
