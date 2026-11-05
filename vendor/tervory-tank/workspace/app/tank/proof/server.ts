import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";
import "server-only";
import { signinDirectory } from "@/lib/trigger-signin";
import { tenantIdFor, uuidFrom } from "@/lib/tenant";
import { resolveTenantNames } from "./tenant-names";
import { goldIdentity } from "../../skala/gold/access";
import { notFound } from "next/navigation";
import type { Proof } from "./view";

export type ProofFilters = { tenant_id?: string; task?: string; start?: string; end?: string; page?: string };

export async function proofResponse(filters: ProofFilters & { until?: string; member?: string; population?: string; free_only?: string } = {}, format?: "csv" | "pdf", endpoint: "" | "/savings" | "/savings/rows" | "/savings/export/csv" | "/savings/export/pdf" | "/savings/client-copy/csv" | "/savings/client-copy/pdf" | "/baselines" = "", init: RequestInit = {}) {
  const identity = await goldIdentity("platform");
  if (identity.role !== "founder") notFound();
  const query = new URLSearchParams();
  for (const key of ["tenant_id", "task", "start", "end", "page", "until", "member", "population", "free_only"] as const) {
    const value = filters[key];
    if (!value || (key === "tenant_id" && value === "all")) continue;
    if (key === "tenant_id" && value === "clients") { query.set("clients_only","true"); continue; }
    query.set(key, (key === "start" || key === "end") && !/(Z|[+-]\d\d:\d\d)$/.test(value) ? `${value}Z` : value);
  }
  if (format === "pdf" || endpoint.endsWith("/pdf")) {
    const tenant = filters.tenant_id;
    let name = tenant === "clients" ? "All clients" : "All accounts";
    if (tenant && !["all", "clients"].includes(tenant)) {
      const names = await resolveTenantNames([tenant], async params => {
        const client = await signinDirectory();
        return client.organizations.getOrganizationList(params);
      }, tenantIdFor, uuidFrom);
      name = names.tenant_names[tenant];
    }
    query.set("client_name", name);
  }
  const response = await fetch(`${process.env.TANK_URL ?? "http://tank:8000"}/v1/proof${endpoint}${format ? `/export/${format}` : ""}?${query}`, {
    ...init, cache: "no-store",
    headers: { "content-type": "application/json", "x-tank-key": process.env.TANK_API_KEY ?? "", "x-tervory-admin": identity.userId },
  });
  if (!response.ok) throw new Error("Proof evidence is unavailable. Try again when Tank is reachable.");
  return response;
}

export async function readProof(filters: ProofFilters = {}): Promise<Proof> {
  const data: Proof = await (await proofResponse(filters)).json();
  const names = await resolveTenantNames(data.tenants, async params => {
    const client = await signinDirectory();
    return client.organizations.getOrganizationList(params);
  }, tenantIdFor, uuidFrom);
  return { ...data, ...names };
}
