import { currentTenant } from "@/lib/tenant";
import { presentReview, reviewRefusal, type ReviewItem } from "@/app/api/zenas/rules";
import { homeRegion, zenas } from "@/app/api/zenas/tank";
import { requireReviewer } from "./access";
import { ReviewConsole } from "./console";

export const dynamic = "force-dynamic";

export default async function ReviewPage() {
  const reviewer = await requireReviewer();
  let items: ReviewItem[] = [];
  let note = "";
  try {
    const { tenantId } = await currentTenant();
    const tags = reviewer.jurisdictions.join(",");
    const res = await zenas(
      `review?tenant_id=${tenantId}&home_region=${homeRegion()}&jurisdiction=${tags}`,
      { method: "GET" },
    );
    if (res.ok) items = presentReview(await res.json(), reviewer.jurisdictions);
    else note = reviewRefusal(res.status, await res.json().catch(() => null));
  } catch {
    note = "The review queue could not be loaded.";
  }

  return (
    <ReviewConsole
      account={reviewer.name}
      items={items}
      note={note}
      reviewer={reviewer.admissionNo ? `${reviewer.name} · ${reviewer.admissionNo}` : reviewer.name}
    />
  );
}
