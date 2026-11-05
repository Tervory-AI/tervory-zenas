import { currentTenant } from "@/lib/tenant";
import { presentMatters, type MatterRow } from "@/app/api/zenas/rules";
import { homeRegion, zenas } from "@/app/api/zenas/tank";
import { MatterBoard } from "./board";

export const dynamic = "force-dynamic";

export default async function MattersPage() {
  let matters: MatterRow[] = [];
  let note = "";
  try {
    const { tenantId } = await currentTenant();
    const res = await zenas(`matters?tenant_id=${tenantId}&home_region=${homeRegion()}`, { method: "GET" });
    if (res.ok) matters = presentMatters(await res.json());
    else if (res.status === 404) note = "The matters list is not on this server yet. You can still open one.";
    else note = "Matters could not be loaded.";
  } catch {
    note = "Matters could not be loaded.";
  }

  return (
    <>
      <picture>
        <source srcSet="/brand/tervory-logo-reversed.png" media="(prefers-color-scheme: dark)" />
        <img className="logo" src="/brand/tervory-logo.png" alt="Tervory" />
      </picture>
      <h1>Matters</h1>
      <p className="lede">Zenas prepares drafts and research for a licensed lawyer to review. You see a draft only after that lawyer releases it.</p>
      <MatterBoard initial={matters} note={note} />
    </>
  );
}
