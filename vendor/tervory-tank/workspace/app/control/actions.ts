"use server";
import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { revalidatePath } from "next/cache";
import { isFounder } from "@/lib/admin";
import { setPaidPause, switchProvenModel } from "@/lib/tank";
import { setWorkersPause } from "./workers";

async function founder(): Promise<string> {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) notFound();
  return userId;
}
export async function pauseAction(paused: boolean): Promise<{ error?: string }> {
  const userId = await founder();
  try { await setPaidPause(userId, paused); revalidatePath("/control"); return {}; }
  catch { return { error: "The pause could not be changed. Refresh to check its current state." }; }
}
export async function switchAction(taskType: string): Promise<{ error?: string }> {
  const userId = await founder();
  try { await switchProvenModel(userId, taskType); revalidatePath("/control"); return {}; }
  catch { return { error: "The switch was refused or could not be confirmed. Refresh to check the current model." }; }
}
export async function workersPauseAction(paused: boolean): Promise<{ error?: string }> {
  const userId = await founder();
  try { await setWorkersPause(userId, paused); revalidatePath("/control"); return {}; }
  catch { return { error: "The workers pause could not be changed. Refresh to check its current state." }; }
}
