import "server-only";

// Server-side only. The Workspace key stays here; the browser never sees it.
// GET /v1/workers on Tank is read only.

import { readWorkers, type WorkersFeed } from "./workers";

const TANK_URL = process.env["TANK_URL"] ?? "http://tank:8000";
const TANK_API_KEY = process.env["TANK_API_KEY"] ?? "";

export async function listWorkers(): Promise<WorkersFeed> {
  const res = await fetch(`${TANK_URL}/v1/workers`, {
    method: "GET",
    headers: { "x-tank-key": TANK_API_KEY },
    cache: "no-store",
  });
  if (!res.ok) return { available: false, tasks: [] };
  return readWorkers(await res.json().catch(() => null));
}
