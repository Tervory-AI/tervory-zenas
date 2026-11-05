"use server";
import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { auth } from "@/lib/trigger-signin";
import { isListedFounder } from "../review/founder";

const PLATFORM = "00000000-0000-0000-0000-000000000001";
const BATCH = /^[A-Za-z0-9_.:-]{1,80}$/;

async function founderId(): Promise<string | null> {
  try {
    const { userId } = await auth();
    if (!isListedFounder(userId, process.env.TERVORY_ADMIN_USER_IDS)) return null;
    return userId ?? null;
  } catch {
    return null;
  }
}

async function tellTank(batchId: string, action: "release" | "reject" | "remove", userId: string): Promise<boolean> {
  const key = process.env.TANK_API_KEY ?? "";
  if (!key) return false;
  const host = process.env.TANK_URL ?? "http://tank:8000";
  try {
    const response = await fetch(`${host}/v1/market/quarantine/${encodeURIComponent(batchId)}/${action}`, {
      method: "POST",
      headers: {
        "x-tank-key": key,
        "x-tervory-user": userId,
        "x-tervory-tenant": PLATFORM,
        "x-tervory-role": "founder",
      },
      cache: "no-store",
    });
    return response.ok;
  } catch {
    return false;
  }
}

async function act(form: FormData, action: "release" | "reject" | "remove"): Promise<void> {
  const userId = await founderId();
  if (!userId) throw new Error("Not found");
  const batchId = String(form.get("batch") ?? "").trim();
  if (!BATCH.test(batchId)) throw new Error("Not found");
  const changed = await tellTank(batchId, action, userId);
  if (!changed) redirect("/market-lab?quarantine_error=1");
  revalidatePath("/market-lab");
}

export async function releaseBatch(form: FormData): Promise<void> {
  await act(form, "release");
}

export async function rejectBatch(form: FormData): Promise<void> {
  await act(form, "reject");
}

export async function removeBatch(form: FormData): Promise<void> {
  await act(form, "remove");
}
