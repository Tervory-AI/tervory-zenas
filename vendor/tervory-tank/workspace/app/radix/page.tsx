import type { Metadata } from "next";
import { activeTenant } from "@/lib/tenant";
import { RadixSettings } from "./settings";
import { RadixPage } from "./view";
import "./radix.css";

export const metadata: Metadata = {
  title: "Radix | Tervory",
  description: "Review code checks, findings, and reports.",
};

export default async function Page() {
  let accountName = "Your account";
  try {
    const tenant = await activeTenant();
    if (tenant?.orgName.trim()) accountName = tenant.orgName.trim();
  } catch {
    accountName = "Your account";
  }
  return <RadixPage accountName={accountName} settings={<RadixSettings />} />;
}
