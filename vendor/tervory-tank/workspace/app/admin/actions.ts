"use server";

import { revalidatePath } from "next/cache";
import { auth } from "@/lib/trigger-signin";
import { isFounder, saveAutoPromote, savePrice as putPrice } from "@/lib/admin";

export type SaveResult = { ok: true } | { ok: false; error: string };

export async function savePrice(_prev: SaveResult | null, formData: FormData): Promise<SaveResult> {
  const { userId } = await auth();
  if (!isFounder(userId) || !userId) return { ok: false, error: "Not found" };
  const outcomeType = String(formData.get("outcome_type") ?? "").trim();
  const unitPrice = Number(formData.get("unit_price"));
  const currency = String(formData.get("currency") ?? "USD").trim().toUpperCase();
  const note = String(formData.get("note") ?? "").trim();
  const effectiveFrom = String(formData.get("effective_from") ?? "").trim();
  if (!outcomeType) return { ok: false, error: "Enter an outcome type." };
  if (!Number.isFinite(unitPrice) || unitPrice < 0) return { ok: false, error: "Enter a price that is zero or more." };
  if (!/^[A-Z]{3}$/.test(currency)) return { ok: false, error: "Currency is a three-letter code." };
  if (effectiveFrom && !isIsoDate(effectiveFrom)) return { ok: false, error: "Effective from is a date like 2026-10-01." };
  if (effectiveFrom && effectiveFrom < "2020-01-01") return { ok: false, error: "Effective from cannot be before 2020-01-01." };
  try {
    await putPrice(userId, {
      outcome_type: outcomeType,
      unit_price: unitPrice,
      currency,
      note: note || null,
      effective_from: effectiveFrom || null,
    });
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "The price could not be saved." };
  }
  revalidatePath("/admin");
  return { ok: true };
}

export async function setAutoPromote(formData: FormData): Promise<void> {
  const { userId } = await auth();
  if (!isFounder(userId) || !userId) return;
  const taskType = String(formData.get("task_type") ?? "").trim();
  const auto = String(formData.get("auto_promote") ?? "") === "true";
  if (!taskType) return;
  await saveAutoPromote(userId, taskType, auto);
  revalidatePath("/admin");
}

function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}
