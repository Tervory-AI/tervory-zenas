import { activeTenant } from "@/lib/tenant";
import { redirect } from "next/navigation";
import { loadCalibration } from "../h7-load";
import { H7Shell } from "../h7-shell";
import { CalibrationView } from "../h7-view";
import "../h7.css";
export const dynamic = "force-dynamic";
export const metadata = { title: "Market Lab · Tervory" };
export default async function CalibrationPage() {
  const tenant = await activeTenant();
  if (!tenant) redirect("/onboarding");
  return <H7Shell><CalibrationView value={await loadCalibration(tenant)} /></H7Shell>;
}
