import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";
// Server-only. The browser never imports this file and never calls the vault itself.
// Talks to Tank the same way Market Lab does. Rig's vault only. It is not turned on here.

import { activeTenant } from "@/lib/tenant";
import { mapVaultPayload, offPanel, type PluginPanel } from "./vault-model";

const VAULT = "rig.vault";
const SQUAD = "rig";
const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

function pathFor(suffix: string, query?: URLSearchParams): string {
  const base = `/v1/vaults/${encodeURIComponent(VAULT)}${suffix}`;
  const text = query?.toString();
  return text ? `${base}?${text}` : base;
}

async function tank(pathname: string, userId: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("x-tank-key", TANK_API_KEY);
  headers.set("x-tervory-user", userId);
  headers.set("x-tervory-squad", SQUAD);
  if (typeof init.body === "string" && !headers.has("content-type")) headers.set("content-type", "application/json");
  return fetch(`${TANK_URL}${pathname}`, { ...init, headers, cache: "no-store" });
}

async function tankJson<T>(pathname: string, userId: string, init: RequestInit): Promise<T> {
  const response = await tank(pathname, userId, init);
  const text = await response.text();
  if (!response.ok) throw new Error(text.slice(0, 180));
  return (text ? JSON.parse(text) : {}) as T;
}

/** The vault starts off. Anything other than an on list stays the off panel. */
export async function loadRigPlugins(): Promise<PluginPanel> {
  try {
    const tenant = await activeTenant();
    if (!tenant) return offPanel();
    const query = new URLSearchParams({ tenant_id: tenant.tenantId });
    const raw = await tankJson<Parameters<typeof mapVaultPayload>[0]>(pathFor("/plugins", query), tenant.userId, { method: "GET" });
    return mapVaultPayload(raw);
  } catch {
    return offPanel();
  }
}

async function actor(): Promise<{ tenantId: string; userId: string } | null> {
  try {
    const tenant = await activeTenant();
    if (!tenant) return null;
    return { tenantId: tenant.tenantId, userId: tenant.userId };
  } catch {
    return null;
  }
}

export async function saveRigSecret(name: string, purpose: string, secret: string): Promise<void> {
  const tenant = await actor();
  if (!tenant) throw new Error("Sign in to change Plugins.");
  await tankJson(pathFor("/secrets"), tenant.userId, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenant.tenantId, name, purpose, value: secret }),
  });
}

export async function removeRigSecret(name: string): Promise<void> {
  const tenant = await actor();
  if (!tenant) throw new Error("Sign in to change Plugins.");
  const query = new URLSearchParams({ tenant_id: tenant.tenantId, name });
  await tankJson(pathFor("/secrets", query), tenant.userId, { method: "DELETE" });
}

export async function uploadRigFiles(files: FormData): Promise<void> {
  const tenant = await actor();
  if (!tenant) throw new Error("Sign in to change Plugins.");
  const query = new URLSearchParams({ tenant_id: tenant.tenantId });
  const response = await tank(pathFor("/files", query), tenant.userId, { method: "POST", body: files });
  if (!response.ok) throw new Error((await response.text()).slice(0, 180));
}

export async function removeRigFile(fileId: string): Promise<void> {
  const tenant = await actor();
  if (!tenant) throw new Error("Sign in to change Plugins.");
  const query = new URLSearchParams({ tenant_id: tenant.tenantId });
  await tankJson(pathFor(`/files/${encodeURIComponent(fileId)}`, query), tenant.userId, { method: "DELETE" });
}

export async function addRigLink(url: string, label: string, frequency: string): Promise<void> {
  const tenant = await actor();
  if (!tenant) throw new Error("Sign in to change Plugins.");
  await tankJson(pathFor("/links"), tenant.userId, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenant.tenantId, url, label, frequency }),
  });
}

export async function removeRigLink(linkId: string): Promise<void> {
  const tenant = await actor();
  if (!tenant) throw new Error("Sign in to change Plugins.");
  const query = new URLSearchParams({ tenant_id: tenant.tenantId });
  await tankJson(pathFor(`/links/${encodeURIComponent(linkId)}`, query), tenant.userId, { method: "DELETE" });
}

export async function addRigConnection(name: string, serverUrl: string, authType: string, token: string): Promise<void> {
  const tenant = await actor();
  if (!tenant) throw new Error("Sign in to change Plugins.");
  await tankJson(pathFor("/connections"), tenant.userId, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenant.tenantId, name, server_url: serverUrl, auth_type: authType, token }),
  });
}

export async function removeRigConnection(connectionId: string): Promise<void> {
  const tenant = await actor();
  if (!tenant) throw new Error("Sign in to change Plugins.");
  const query = new URLSearchParams({ tenant_id: tenant.tenantId });
  await tankJson(pathFor(`/connections/${encodeURIComponent(connectionId)}`, query), tenant.userId, { method: "DELETE" });
}
