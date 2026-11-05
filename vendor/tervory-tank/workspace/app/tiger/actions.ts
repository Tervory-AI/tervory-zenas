"use server";

import { redirect } from "next/navigation";
import { createTigerWatch, deleteTigerWatch } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { EVERY_OPTIONS } from "./copy";

const EVERY = new Set<string>(EVERY_OPTIONS.map((item) => item.value));

function back(message: string): string {
  return `/tiger?tab=watch&notice=${encodeURIComponent(message)}`;
}

function plainError(err: unknown): string {
  const text = err instanceof Error ? err.message : "";
  const detail = /"detail"\s*:\s*"([^"]+)"/.exec(text)?.[1] ?? text;
  if (detail.includes("not signed in")) return "Sign in to add a product.";
  if (detail.includes("already watched") || text.includes(" 409")) return "That page is already on the watch list.";
  if (detail.includes("url not allowed") || detail.includes("url is too long")) return "That address cannot be checked. Use a public page.";
  if (detail.includes("every must")) return "Choose how often to check.";
  if (text.includes(" 503") || detail.toLowerCase().includes("unreachable")) return "Tiger is not reachable right now.";
  if (text.includes(" 404")) return "That product is already gone.";
  return "That could not be saved. Try again.";
}

export async function addProduct(formData: FormData): Promise<void> {
  const url = String(formData.get("url") ?? "").trim();
  const label = String(formData.get("label") ?? "").trim();
  const every = String(formData.get("every") ?? "").trim();
  const priceOn = formData.get("price") === "on";
  const stockOn = formData.get("stock") === "on";
  if (!url) redirect(back("Enter the product page link."));
  if (!/^https?:\/\//i.test(url) || url.length > 2000) redirect(back("The address needs to be a public http or https page."));
  if (label.length > 200) redirect(back("The name is too long."));
  if (!EVERY.has(every)) redirect(back("Choose how often to check."));
  try {
    const tenant = await currentTenant();
    await createTigerWatch(tenant.tenantId, { url, label, every, threshold: 0, alert_price: priceOn, alert_stock: stockOn });
  } catch (err) {
    redirect(back(plainError(err)));
  }
  redirect("/tiger?tab=watch");
}

export async function removeProduct(formData: FormData): Promise<void> {
  const id = String(formData.get("id") ?? "").trim();
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    redirect(back("That product is already gone."));
  }
  try {
    const tenant = await currentTenant();
    await deleteTigerWatch(tenant.tenantId, id);
  } catch (err) {
    redirect(back(plainError(err)));
  }
  redirect("/tiger?tab=watch");
}
