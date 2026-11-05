import type { BaseRecord } from "@/lib/tank";

export type ContextReport = {
  records: number;
  creditsUsed: number;
  noTruth: number;
  creditsPerRecord: number | null;
  unknownCharges: number;
  creditCapReached: boolean;
  fields: { field: string; checked: number; baseCorrect: number; contextCorrect: number }[];
};

export async function loadContextReport(
  tenantId: string,
  read: (tenantId: string, options: { limit: number }) => Promise<BaseRecord[]>,
): Promise<ContextReport> {
  return contextReport(await read(tenantId, { limit: 200 }));
}

function comparable(value: unknown): string {
  if (Array.isArray(value)) return JSON.stringify(value.map(comparable).sort());
  if (value && typeof value === "object") {
    return JSON.stringify(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, comparable(item)]));
  }
  return String(value ?? "").trim().toLowerCase().replace(/\/$/, "");
}

function socialUrls(value: unknown): string[] {
  if (Array.isArray(value)) return value.flatMap(socialUrls);
  if (value && typeof value === "object") return Object.values(value).flatMap(socialUrls);
  if (typeof value !== "string") return [];
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol)) return [];
    return [`${url.hostname.toLowerCase().replace(/^www\./, "")}${url.pathname.replace(/\/$/, "")}`];
  } catch {
    return [];
  }
}

function fieldValue(field: string, value: unknown): string {
  if (field === "phone") return String(value ?? "").replace(/\D/g, "").replace(/^1(?=\d{10}$)/, "");
  if (field === "address") return comparable(value).replace(/[^\w ]/g, " ").replace(/\s+/g, " ").trim();
  return field === "socials" ? JSON.stringify([...new Set(socialUrls(value))].sort()) : comparable(value);
}

export function contextReport(records: Pick<BaseRecord, "profile" | "passes">[]): ContextReport {
  const report: ContextReport = { records: 0, creditsUsed: 0, noTruth: 0, creditsPerRecord: null, unknownCharges: 0, creditCapReached: false, fields: [] };
  const fields = new Map<string, ContextReport["fields"][number]>();
  let credits = 0;
  for (const record of records) {
    const trial = record.passes.context_dev;
    if (trial?.status === "credit_limit" || trial?.credit_cap_reached === true) report.creditCapReached = true;
    if (!trial || !["candidate", "failed", "transport_error", "unknown"].includes(String(trial.status))) continue;
    report.records++;
    if (typeof trial.credits === "number" && Number.isFinite(trial.credits) && trial.credits >= 0) credits += trial.credits;
    else report.unknownCharges++;
    const review = record.passes.context_dev_checked;
    if (review?.checked !== true || !review.expected || typeof review.expected !== "object" || Array.isArray(review.expected) || !Object.keys(review.expected).length) {
      report.noTruth++;
      continue;
    }
    const candidate = trial.fields && typeof trial.fields === "object" ? trial.fields as Record<string, unknown> : {};
    for (const [field, expected] of Object.entries(review.expected)) {
      if (!["name", "website", "description", "socials", "phone", "address"].includes(field)) continue;
      const row = fields.get(field) ?? { field, checked: 0, baseCorrect: 0, contextCorrect: 0 };
      row.checked++;
      const baseValue = field === "phone"
        ? record.profile.phone?.value ?? (record.profile.phones?.value as unknown[] | undefined)?.[0]
        : field === "address"
          ? ["address", "city", "state", "zip"].map((key) => record.profile[key]?.value ?? "").filter(Boolean).join(", ")
          : record.profile[field]?.value;
      row.baseCorrect += Number(fieldValue(field, baseValue) === fieldValue(field, expected));
      row.contextCorrect += Number(fieldValue(field, candidate[field]) === fieldValue(field, expected));
      fields.set(field, row);
    }
  }
  report.fields = [...fields.values()];
  report.creditsUsed = credits;
  report.creditsPerRecord = report.records && !report.unknownCharges ? credits / report.records : null;
  return report;
}
