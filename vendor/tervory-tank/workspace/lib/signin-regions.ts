import { homeRegion, regionTank, type Region } from "@/app/api/zenas/tank";

export type SigninIdentity = { tenantId: string; userId: string; tenantAdmin?: boolean };

export function signinRegion(value: unknown): Region | null {
  return value === "us-east" || value === "za" ? value : null;
}

export function signinTank(region: Region): { url: string; key: string } | null {
  if (region === homeRegion()) return regionTank(region);
  const url = ((region === "za" ? process.env.TANK_URL_ZA : process.env.TANK_URL_US) ?? "").trim().replace(/\/+$/, "");
  const key = (process.env.TRIGGER_SIGNIN_SESSIONS_KEY ?? "").trim();
  if (!url || !key) return null;
  let parsed: URL;
  try { parsed = new URL(url); } catch { return null; }
  if (parsed.protocol !== "https:" || parsed.username || parsed.password) return null;
  return { url, key };
}
