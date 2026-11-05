import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";
import "server-only";

import type { Shield, ShieldBoard, ShieldTile } from "./shields-fixtures";
export type { Shield, ShieldBoard, ShieldReceipt, ShieldTile } from "./shields-fixtures";

// Autumbra Enforce shields: founder-only reads and taps through Tank (`/v1/ghost/shields/*`).
// Server-side only (uses TANK_API_KEY).
const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

async function call<T>(path: string, userId: string, method: "GET" | "POST"): Promise<T> {
  const res = await fetch(`${TANK_URL}/v1/ghost/shields/${path}`, {
    method, cache: "no-store",
    headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY, "x-tervory-admin": userId },
  });
  if (!res.ok) throw new Error(`Autumbra shields ${res.status}`);
  return (await res.json()) as T;
}

/** Read failures reach the console's existing unavailable state. */
export async function shieldBoard(userId: string): Promise<ShieldBoard> {
  const [tile, list] = await Promise.all([
    call<ShieldTile>("tile", userId, "GET"),
    call<{ shields: Shield[] }>("list", userId, "GET"),
  ]);
  if (!tile || tile.fixture || !Array.isArray(list?.shields)
    || !tile.open_by_severity || typeof tile.open_total !== "number"
    || !tile.time_to_know_seconds || !tile.time_to_shield_seconds
    || typeof tile.sites_broken_by_a_shield !== "number"
    || typeof tile.cost_this_month_usd !== "number" || typeof tile.receipts_this_month !== "number") {
    throw new Error("Autumbra shields unavailable");
  }
  return { tile, shields: list.shields };
}

export async function shieldTap(userId: string, id: string, tap: "raise" | "undo"): Promise<Shield> {
  if (!/^[0-9a-f-]{36}$/.test(id)) throw new Error("Invalid shield");
  return call<Shield>(`${id}/${tap}`, userId, "POST");
}
