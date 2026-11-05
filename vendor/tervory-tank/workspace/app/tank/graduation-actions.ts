"use server";
import { revalidatePath } from "next/cache";
import { goldIdentity } from "../skala/gold/access";
import { promoteCandidate, trainingGraduation } from "@/lib/tank";

export async function promoteTraining(form: FormData) {
  const identity = await goldIdentity("platform");
  if (identity.role !== "founder") throw new Error("Not found");
  const task = String(form.get("task") ?? "");
  const rows = await trainingGraduation(identity);
  const ready = rows.some((row) => {
    const mark = row as typeof row & { trained_on_removed?: boolean; blocked_reason?: string | null };
    return mark.task_type === task && mark.can_promote && !mark.trained_on_removed
      && mark.blocked_reason !== "trained on removed data" && mark.blocked_reason !== "dropped";
  });
  if (!ready) throw new Error("Candidate is not ready");
  // Existing Tank endpoint independently enforces the live shadow bar.
  await promoteCandidate(task, identity.userId);
  revalidatePath("/tank");
}
