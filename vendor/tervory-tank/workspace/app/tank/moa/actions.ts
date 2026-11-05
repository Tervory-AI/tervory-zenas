"use server";
import { goldIdentity } from "../../skala/gold/access";
import { notFound, redirect } from "next/navigation";
import { moaAction } from "@/lib/tank";

export async function actOnMixture(form: FormData) {
  const identity = await goldIdentity("platform");
  if (identity.role !== "founder") notFound();
  const action = String(form.get("action"));
  if (action !== "bench" && action !== "promote") notFound();
  const task = String(form.get("task"));
  let status = "saved";
  try {
    await moaAction(identity.userId, action, {
      tenant_id: String(form.get("tenant_id")), task_type: task,
      config_hash: String(form.get("config_hash")),
      ...(form.get("seat") ? { seat: String(form.get("seat")) } : {}),
    });
  } catch { status = "unavailable"; }
  redirect(`/tank/moa?${new URLSearchParams({ task, status })}`);
}
