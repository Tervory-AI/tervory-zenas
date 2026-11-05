import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";
// Server-side only (uses TANK_API_KEY). WORKERS-2: the host workers' check-in and the founder's workers pause.
export type WorkerBeat = {
  worker: string; state: "running" | "paused"; issue: number | null; commit: string;
  last_error: string | null; seen_at: string; age_s: number; checking_in: boolean;
};
export type WorkersRead = { workers_paused: boolean; worker: WorkerBeat | null; stale_after_s: number };
export const STALE_AFTER_S = 180;

async function founderTank<T>(userId: string, path: string, init: RequestInit = {}): Promise<T> {
  const url = process.env.TANK_URL ?? "http://tank:8000";
  const key = process.env.TANK_API_KEY ?? "";
  if (!key) throw new Error("Tank is not connected");
  const res = await fetch(`${url}${path}`, {
    ...init, cache: "no-store",
    headers: { "content-type": "application/json", "x-tank-key": key, "x-tervory-admin": userId, ...(init.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`Tank ${res.status}`);
  return res.json() as Promise<T>;
}
export function readWorkers(userId: string): Promise<WorkersRead> {
  return founderTank(userId, "/v1/controls/workers", { method: "GET" });
}
export function setWorkersPause(userId: string, paused: boolean): Promise<{ workers_paused: boolean }> {
  return founderTank(userId, "/v1/controls/workers-paused", { method: "PUT", body: JSON.stringify({ paused }) });
}
/** "not checking in" when the last heartbeat is older than 3 minutes (or there is none). */
export function checkingIn(beat: WorkerBeat | null, now: number = Date.now()): boolean {
  if (!beat) return false;
  const seen = Date.parse(beat.seen_at);
  if (Number.isNaN(seen)) return false;
  return (now - seen) / 1000 <= STALE_AFTER_S && beat.checking_in !== false;
}
