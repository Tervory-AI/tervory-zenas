"use server";

import { auth } from "@/lib/trigger-signin";
import { revalidatePath } from "next/cache";
import { isFounder } from "@/lib/admin";
import { ghostLook, ghostResolve } from "@/lib/tank";
import { shieldTap } from "./shields-data";

export type GhostResult = { ok: boolean; message: string } | null;

export async function look(_previous: GhostResult, form: FormData): Promise<GhostResult> {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) return { ok: false, message: "Not found." };
  const target = String(form.get("target") ?? "");
  const signal = String(form.get("signal") ?? "");
  try {
    const incident = await ghostLook(userId, target, signal);
    revalidatePath("/admin/ghost");
    return { ok: true, message: incident.summary ?? "The investigation has started." };
  } catch {
    return { ok: false, message: "Autumbra Watch could not finish the investigation. Check its room and Tank connection." };
  }
}

export async function resolve(_previous: GhostResult, form: FormData): Promise<GhostResult> {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) return { ok: false, message: "Not found." };
  const id = String(form.get("id") ?? "");
  const cause = String(form.get("cause") ?? "");
  const fix = String(form.get("fix") ?? "");
  try {
    await ghostResolve(userId, id, cause, fix);
    revalidatePath("/admin/ghost");
    return { ok: true, message: "The check passed. The confirmed cause and fix are saved." };
  } catch {
    return { ok: false, message: "The issue could not be verified. It has not been marked solved." };
  }
}

const tapWords: Record<string, string> = {
  dry_run: "Practice run: nothing was changed. This is exactly what Autumbra Watch would change once it is allowed to.",
  up: "The shield is up and the site passed its health check.",
  auto_undone: "The site failed its health check, so the shield was taken down straight away.",
  refused: "The site was already failing its health check, so the shield was not raised.",
  failed: "The change was refused. Nothing was changed.",
  undone: "The shield is down.",
  withdrawn: "The proposal was withdrawn.",
  undo_failed: "Autumbra Watch could not take the shield down. Remove it by hand.",
  brief: "This site is not behind our edge, so Autumbra Watch wrote it up for a worker instead.",
};

export async function shieldAction(_previous: GhostResult, form: FormData): Promise<GhostResult> {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) return { ok: false, message: "Not found." };
  const id = String(form.get("id") ?? "");
  const tap = form.get("tap") === "undo" ? "undo" : "raise";
  try {
    const shield = await shieldTap(userId, id, tap);
    revalidatePath("/admin/ghost");
    return { ok: !["failed", "auto_undone", "undo_failed", "refused"].includes(shield.state),
             message: tapWords[shield.state] ?? "Done." };
  } catch {
    return { ok: false, message: "Autumbra Watch could not finish. Nothing has been marked as changed." };
  }
}
