"use server";

import { activeTenant } from "@/lib/tenant";
import { readClientSavings as readSavings } from "@/lib/tank";
import type { RowFilter, Savings, SavingsRows } from "./savings-types";

// Tenant and transport credentials come only from the server session.
async function read<T>(path: "" | "/rows" | "/revision", query: Record<string, string> = {}): Promise<T> {
  const tenant = await activeTenant();
  if (!tenant) throw new Error("Please sign in to see your savings.");
  try { return await readSavings<T>(tenant.tenantId,path,query); }
  catch { throw new Error("Your savings could not be loaded. Please try again."); }
}
export async function readClientSavings(): Promise<Savings> { return read(""); }
export async function readSavingsRevision(): Promise<{ revision: string }> { return read("/revision"); }
export async function readClientSavingsRows(filters: RowFilter): Promise<SavingsRows> {
  const query: Record<string, string> = { end: filters.end, page: String(filters.page ?? 0) };
  for (const name of ["start", "until", "member"] as const) if (filters[name]) query[name] = String(filters[name]);
  if (filters.free_only) query.free_only = "true";
  // Do not forward population, tenant, credentials, or arbitrary browser fields.
  return read("/rows", query);
}
