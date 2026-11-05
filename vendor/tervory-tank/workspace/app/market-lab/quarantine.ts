export type BatchStatus = "held" | "pooled" | "rejected" | "removed";

export type QuarantineBatch = {
  id: string;
  seriesName: string;
  source: string;
  region: string;
  currency: string;
  unit: string;
  fetchedAt: string;
  status: BatchStatus;
  reason: string;
};

const STATUSES: BatchStatus[] = ["held", "pooled", "rejected", "removed"];

export function quarantineActions(status: BatchStatus): Array<"Release" | "Reject" | "Remove"> {
  if (status === "held") return ["Release", "Reject"];
  if (status === "pooled") return ["Remove"];
  return [];
}

export function nextStatus(status: BatchStatus, action: "release" | "reject" | "remove"): BatchStatus | null {
  if (action === "release" && status === "held") return "pooled";
  if (action === "reject" && status === "held") return "rejected";
  if (action === "remove" && (status === "held" || status === "pooled")) return "removed";
  return null;
}

export function entersPool(status: BatchStatus): boolean {
  return status === "pooled";
}

export const QUARANTINE_ERROR = "Market Lab did not answer. Nothing was changed.";

export type QuarantineLoad = {
  batches: QuarantineBatch[];
  error: string | null;
};

function failedLoad(): QuarantineLoad {
  return { batches: [], error: QUARANTINE_ERROR };
}

/** A missing reply, a bad status, or a body that is not a batch list leaves the page unchanged. */
export function quarantineFromResponse(status: number | null, body: unknown): QuarantineLoad {
  if (status === null || status < 200 || status >= 300 || !body || typeof body !== "object" || Array.isArray(body)) {
    return failedLoad();
  }
  const rows = (body as { batches?: unknown }).batches;
  if (!Array.isArray(rows)) return failedLoad();
  const batches: QuarantineBatch[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object" || Array.isArray(row)) return failedLoad();
    const batch = asBatch(row as Record<string, unknown>);
    if (!batch) return failedLoad();
    batches.push(batch);
  }
  return { batches, error: null };
}

/** A failed action keeps the batches that were already on the page. */
export function quarantineActionResult(status: number | null, batches: QuarantineBatch[]): QuarantineLoad {
  if (status === null || status < 200 || status >= 300) {
    return { batches, error: QUARANTINE_ERROR };
  }
  return { batches, error: null };
}

export function quarantinePageError(loadError: string | null, flag: string | string[] | undefined): string | null {
  if (loadError) return loadError;
  const values = Array.isArray(flag) ? flag : flag ? [flag] : [];
  return values.includes("1") ? QUARANTINE_ERROR : null;
}

export function asBatch(row: Record<string, unknown>): QuarantineBatch | null {
  const status = String(row.status ?? "");
  if (!STATUSES.includes(status as BatchStatus)) return null;
  const id = String(row.id ?? "").trim();
  const seriesName = typeof row.series_name === "string" ? row.series_name.trim() : "";
  if (!id || !seriesName || seriesName.includes("00000000-0000-0000-0000")) return null;
  return {
    id,
    seriesName,
    source: String(row.source ?? ""),
    region: String(row.region ?? ""),
    currency: String(row.currency ?? ""),
    unit: String(row.unit ?? ""),
    fetchedAt: String(row.fetched_at ?? row.fetchedAt ?? ""),
    status: status as BatchStatus,
    reason: String(row.reason ?? ""),
  };
}
