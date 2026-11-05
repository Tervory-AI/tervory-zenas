"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { currentTenant } from "@/lib/tenant";
import { createCheckoutUrl, resolveOrigin } from "./billing";

export async function startCheckout(formData: FormData): Promise<void> {
  const priceId = String(formData.get("priceId") ?? "");
  const headerList = await headers();
  let tenantId = "";
  try {
    tenantId = (await currentTenant()).tenantId;
  } catch {
    redirect("/billing?tab=plans&checkout=unavailable");
  }
  const origin = resolveOrigin(
    process.env.BILLING_PUBLIC_ORIGIN,
    headerList.get("x-forwarded-proto"),
    headerList.get("x-forwarded-host") ?? headerList.get("host"),
  );
  if (!origin) redirect("/billing?tab=plans&checkout=unavailable");
  const url = await createCheckoutUrl(tenantId, priceId, origin);
  if (!url) redirect("/billing?tab=plans&checkout=unavailable");
  redirect(url);
}
