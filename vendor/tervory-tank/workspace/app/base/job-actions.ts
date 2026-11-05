"use server";

import { randomUUID } from "node:crypto";
import { revalidatePath } from "next/cache";
import { currentTenant } from "@/lib/tenant";
import { baseRun } from "@/lib/tank";
import { MAX_CSV_BYTES, parseBusinessCsv } from "./csv";
import { batchResult, readBatch, type BatchResult } from "./batch-data";

export type UploadResult = { ok: true; batch: string; rows: number } | { ok: false; message: string };

export async function uploadBaseCsv(form: FormData): Promise<UploadResult> {
  let tenant;
  try { tenant = await currentTenant(); }
  catch { return { ok: false, message: "Sign in to your workspace before uploading a list." }; }
  const file = form.get("file");
  if (!(file instanceof File) || !file.name.toLowerCase().endsWith(".csv")) return { ok: false, message: "Choose a CSV file." };
  if (file.size > MAX_CSV_BYTES) return { ok: false, message: "Use a CSV smaller than 500 KB, with up to 1,000 businesses." };
  let records;
  try { records = parseBusinessCsv(await file.text()); }
  catch (error) { return { ok: false, message: error instanceof Error ? error.message : "The CSV could not be read." }; }
  const name = String(form.get("name") || file.name.replace(/\.csv$/i, "")).trim().slice(0, 65);
  const batch = `${name || "Business list"} · ${randomUUID()}`;
  try {
    await baseRun(tenant.tenantId, tenant.userId, batch, records, false, null, false);
  } catch {
    return { ok: false, message: "We could not confirm that the list started. Refresh the batches before uploading again." };
  }
  revalidatePath("/base");
  return { ok: true, batch, rows: records.length };
}

export async function baseBatchStatus(batch: string): Promise<{ ok: true; result: BatchResult } | { ok: false; message: string }> {
  if (!batch || batch.length > 120) return { ok: false, message: "Choose a batch from your list." };
  try {
    const { tenantId } = await currentTenant();
    const rows = await readBatch(tenantId, batch);
    if (!rows.length) return { ok: false, message: "This batch is not available in your workspace." };
    return { ok: true, result: batchResult(batch, rows) };
  } catch { return { ok: false, message: "The results could not be loaded. Try again shortly." }; }
}
