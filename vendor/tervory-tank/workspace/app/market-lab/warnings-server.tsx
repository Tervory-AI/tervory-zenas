import { marketWarningRequest, type MarketWarnings } from "@/lib/tank";
import { Warnings } from "./warnings";

export async function WarningSection({ tenantId }: { tenantId: string }) {
  try {
    const data = await marketWarningRequest<MarketWarnings>(tenantId, "list");
    return <Warnings data={data} />;
  } catch {
    return <Warnings data={null} failed />;
  }
}
