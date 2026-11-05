import { activeTenant } from "@/lib/tenant";
import { oneParam } from "../prices/format";
import { loadTiger } from "./load";
import { sectionFrom } from "./model";
import { TigerApp } from "./view";
import { loadTigerComparison } from "./position-load";
import { watchView } from "./position-model";

export const dynamic = "force-dynamic";

export default async function TigerPage({
  searchParams,
}: {
  searchParams: Promise<{ notice?: string | string[]; tab?: string | string[]; view?: string | string[] }>;
}) {
  const params = await searchParams;
  const [loaded, comparison] = await Promise.all([loadTiger(), loadTigerComparison()]);
  let accountName: string | null = null;
  try {
    accountName = (await activeTenant())?.orgName ?? null;
  } catch {
    accountName = null;
  }
  const notice = oneParam(params.notice) || loaded.notice || "";
  return (
    <TigerApp
      data={loaded.data}
      notice={notice}
      initialTab={sectionFrom(oneParam(params.tab))}
      accountName={accountName}
      comparison={comparison}
      initialView={watchView(oneParam(params.view))}
    />
  );
}
