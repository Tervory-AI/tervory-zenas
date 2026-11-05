"use server";

import { redirect } from "next/navigation";
import { createTigerWatch, deleteTigerWatch } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { EVERY_OPTIONS } from "./format";

const EVERY = new Set<string>(EVERY_OPTIONS.map((item) => item.value));

function notice(message: string): string {
  return `/prices?notice=${encodeURIComponent(message)}`;
}

function plainError(err: unknown): string {
  const text = err instanceof Error ? err.message : "";
  const detail = /"detail"\s*:\s*"([^"]+)"/.exec(text)?.[1] ?? text;
  if (detail.includes("not signed in")) return "Sign in to watch prices.";
  if (detail.includes("already watched") || text.includes(" 409")) return "That page is already on the list.";
  if (detail.includes("url not allowed") || detail.includes("url is too long")) return "That address cannot be checked. Use a public page.";
  if (detail.includes("every must")) return "Choose how often to check.";
  if (detail.includes("threshold")) return "The alert percent has to be zero or more.";
  if (text.includes(" 503") || detail.toLowerCase().includes("unreachable")) return "Price checks are not reachable right now.";
  if (text.includes(" 404")) return "That watch is already gone.";
  return "That could not be saved. Try again.";
}

function safeBack(value: string): string {
  if (value === "/prices") return value;
  if (value.startsWith("/prices?") && !value.includes("://") && !value.startsWith("/prices?//")) return value.slice(0, 500);
  return "/prices";
}

export async function addWatch(formData: FormData): Promise<void> {
  const url = String(formData.get("url") ?? "").trim();
  const label = String(formData.get("label") ?? "").trim();
  const every = String(formData.get("every") ?? "").trim();
  const thresholdRaw = String(formData.get("threshold") ?? "").trim();
  if (!url) redirect(notice("Enter the page address."));
  if (!/^https?:\/\//i.test(url) || url.length > 2000) redirect(notice("The address needs to be a public http or https page."));
  if (label.length > 200) redirect(notice("The label is too long."));
  if (!EVERY.has(every)) redirect(notice("Choose how often to check."));
  const percent = Number(thresholdRaw === "" ? "0" : thresholdRaw);
  if (!Number.isFinite(percent) || percent < 0 || percent > 10000) redirect(notice("The alert percent has to be zero or more."));
  const threshold = Math.round(percent * 1000) / 100000;
  try {
    const tenant = await currentTenant();
    await createTigerWatch(tenant.tenantId, { url, label, every, threshold });
  } catch (err) {
    redirect(notice(plainError(err)));
  }
  redirect("/prices");
}

export async function stopWatch(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "").trim();
  const back = safeBack(String(formData.get("back") ?? ""));
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) redirect(notice("That watch is already gone."));
  try {
    const tenant = await currentTenant();
    await deleteTigerWatch(tenant.tenantId, id);
  } catch (err) {
    redirect(notice(plainError(err)));
  }
  redirect(back);
}
