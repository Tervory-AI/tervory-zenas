import { baseRecords, type BaseRecord } from "@/lib/tank";
import { blockedSources, evidence, resultStatus } from "./evidence";
import { csvCell } from "./csv";

export type BatchResultRow = {
  id: string; name: string; website: string; phone: string; emails: string; description: string;
  status: "verified" | "failed" | "pending"; reason: string; sourceUrl: string | null; checkedAt: string | null;
  blockedUrls: string[];
};
export type BatchResult = {
  batch: string; total: number; verified: number; failed: number; pending: number;
  rows: BatchResultRow[]; reasons: { reason: string; count: number }[];
};

function value(record: BaseRecord, key: string): string {
  const item = record.profile[key]?.value;
  return Array.isArray(item) ? item.filter((part) => typeof part === "string").join("; ") : typeof item === "string" ? item : "";
}
export function resultRow(record: BaseRecord): BatchResultRow {
  const checked = evidence(record);
  const phone = record.profile.phone;
  const emails = record.profile.emails;
  const description = record.profile.description;
  // Do not export an uploaded phone, guessed email or model estimate as a
  // website-confirmed field. Keep the pipeline's provenance distinction.
  const sitePhone = phone?.source === "site" || record.passes["7"]?.checks
    && (record.passes["7"].checks as Record<string, unknown>).phone_matches_site === true;
  return { id: record.id, name: value(record, "name") || String(record.raw.name ?? "Unnamed business"),
    website: value(record, "website"), phone: sitePhone ? value(record, "phone") : "",
    emails: emails?.source === "site" ? value(record, "emails") : "",
    description: description?.source === "site:code" ? value(record, "description") : "", ...resultStatus(record),
    sourceUrl: checked?.sourceUrl ?? null, checkedAt: checked?.checkedAt ?? null, blockedUrls: blockedSources(record) };
}

/** Read this tenant's exact batch, without the old export's silent 20,000-row truncation. */
export async function readBatch(tenantId: string, batch: string): Promise<BatchResultRow[]> {
  const all: BatchResultRow[] = [];
  const seen = new Set<string>();
  const pageSize = 1000;
  for (let offset = 0; offset <= 100_000; offset += pageSize) {
    const page = await baseRecords(tenantId, { batch, limit: pageSize, offset });
    if (all.length + page.length > 100_000) throw new Error("Batch exceeds the supported export size.");
    // Legacy batches can span pages with equal creation times. Refuse an
    // unstable page boundary instead of exporting duplicated or missing rows.
    for (const record of page) {
      if (seen.has(record.id)) throw new Error("The batch changed while its results were being read.");
      seen.add(record.id);
    }
    all.push(...page.map(resultRow));
    if (page.length < pageSize) return all;
  }
  throw new Error("Could not read the complete batch.");
}

export function batchResult(batch: string, rows: BatchResultRow[]): BatchResult {
  const reasons = new Map<string, number>();
  for (const row of rows) if (row.status === "failed") reasons.set(row.reason, (reasons.get(row.reason) ?? 0) + 1);
  return { batch, total: rows.length, verified: rows.filter((row) => row.status === "verified").length,
    failed: rows.filter((row) => row.status === "failed").length, pending: rows.filter((row) => row.status === "pending").length,
    rows: rows.slice(0, 100), reasons: [...reasons].map(([reason, count]) => ({ reason, count })) };
}

export function verifiedCsv(rows: BatchResultRow[]): string {
  const headers = ["name", "website", "phone", "emails", "description", "source_url", "checked_at"];
  return "\uFEFF" + [headers.join(","), ...rows.filter((row) => row.status === "verified").map((row) =>
    [row.name, row.website, row.phone, row.emails, row.description, row.sourceUrl, row.checkedAt].map(csvCell).join(","))].join("\r\n") + "\r\n";
}
