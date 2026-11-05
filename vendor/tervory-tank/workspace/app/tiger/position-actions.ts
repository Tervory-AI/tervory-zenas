"use server";

import { revalidatePath } from "next/cache";
import { decideTigerMatch } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export async function decideMatch(id: string, decision: "confirm" | "reject" | "undo") {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) || !["confirm", "reject", "undo"].includes(decision)) return { ok: false as const };
  try {
    const match = await decideTigerMatch(await currentTenant(), id, decision);
    revalidatePath("/tiger");
    return { ok: true as const, match };
  } catch { return { ok: false as const }; }
}
