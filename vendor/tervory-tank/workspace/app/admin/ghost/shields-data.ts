import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";
import "server-only";

// GHOST-2 Shields Up: founder-only reads and taps, through Tank. Server-side only (uses TANK_API_KEY).
const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

export type ShieldReceipt = { step: string; at: string; ledger_id: string; status: string };
export type Shield = {
  id: string; site: string; kind: string; path: string; state: string; reason: string; ref: string;
  proposed_at: string; up_at: string | null; ended_at: string | null; note: string | null;
  plan: { plain: string; phase: string; rule: { action: string; expression: string } };
  brief: { title: string; steps: string[] } | null;
  receipts: ShieldReceipt[];
};
export type ShieldTile = {
  mode: "dry_run" | "live";
  open_total: number;
  open_by_severity: Record<string, number>;
  time_to_know_seconds: { median: number | null; worst: number | null; target: number };
  time_to_shield_seconds: { median: number | null; worst: number | null; target: number };
  sites_broken_by_a_shield: number;
  cost_this_month_usd: number;
  receipts_this_month: number;
};
export type ShieldBoard = { tile: ShieldTile; shields: Shield[] };

async function call<T>(path: string, userId: string, method: "GET" | "POST"): Promise<T> {
  const res = await fetch(`${TANK_URL}/v1/ghost/shields/${path}`, {
    method, cache: "no-store",
    headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY, "x-tervory-admin": userId },
  });
  if (!res.ok) throw new Error(`Autumbra Watch shields ${res.status}`);
  return (await res.json()) as T;
}

export async function shieldBoard(userId: string): Promise<ShieldBoard> {
  const [tile, list] = await Promise.all([
    call<ShieldTile>("tile", userId, "GET"),
    call<{ shields: Shield[] }>("list", userId, "GET"),
  ]);
  return { tile, shields: list.shields };
}

export function shieldTap(userId: string, id: string, tap: "raise" | "undo"): Promise<Shield> {
  if (!/^[0-9a-f-]{36}$/.test(id)) throw new Error("Invalid shield");
  return call<Shield>(`${id}/${tap}`, userId, "POST");
}
