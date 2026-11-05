"use server";

import { revalidatePath } from "next/cache";
import { activeTenant } from "@/lib/tenant";
import {
  addVaultConnection,
  addVaultLink,
  removeVaultConnection,
  removeVaultFile,
  removeVaultLink,
  removeVaultSecret,
  saveVaultSecret,
  uploadVaultFiles,
  VAULT_OFF_MESSAGE,
  vaultPlugins,
} from "@/lib/tank";

import { mapVaultPayload, offPanel, type PluginPanel } from "./plugins-model";

const VAULT = "radix.vault";
const SQUAD = "radix";

export type PluginResult = { ok: boolean; message: string };

async function actor() {
  try { return await activeTenant(); } catch { return null; }
}

function safe(error: unknown, fallback: string): string {
  const text = error instanceof Error ? error.message : "";
  if (text.includes(VAULT_OFF_MESSAGE)) return VAULT_OFF_MESSAGE;
  if (text.includes("Only CSV, XLSX, and PDF")) return "Only CSV, XLSX, and PDF files can be uploaded.";
  if (text.includes("Sign in")) return "Sign in to change Plugins.";
  return fallback;
}

function done(message: string): PluginResult {
  revalidatePath("/radix");
  return { ok: true, message };
}

export async function saveRadixKey(name: string, value: string, purpose: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (!value.trim()) return { ok: false, message: "Add a key first. Nothing was stored." };
  try {
    await saveVaultSecret(VAULT, SQUAD, tenant.tenantId, tenant.userId, name, purpose, value);
    return done("Saved. The key is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safe(error, "The key was not saved.") };
  }
}

export async function removeRadixSecret(name: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultSecret(VAULT, SQUAD, tenant.tenantId, tenant.userId, name);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The key was not removed.") };
  }
}

export async function uploadRadixFiles(formData: FormData): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (formData.getAll("file").length === 0) return { ok: false, message: "Choose a file first. Nothing was saved." };
  try {
    await uploadVaultFiles(VAULT, SQUAD, tenant.tenantId, tenant.userId, formData);
    return done("Saved.");
  } catch (error) {
    return { ok: false, message: safe(error, "The file was not saved.") };
  }
}

export async function removeRadixFile(fileId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultFile(VAULT, SQUAD, tenant.tenantId, tenant.userId, fileId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The file was not removed.") };
  }
}

export async function addRadixLink(url: string, label: string, frequency: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (!url.trim()) return { ok: false, message: "Add a link first. Nothing was saved." };
  try {
    await addVaultLink(VAULT, SQUAD, tenant.tenantId, tenant.userId, url, label, frequency);
    return done("Saved.");
  } catch (error) {
    return { ok: false, message: safe(error, "The link was not saved.") };
  }
}

export async function removeRadixLink(linkId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultLink(VAULT, SQUAD, tenant.tenantId, tenant.userId, linkId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The link was not removed.") };
  }
}

export async function addRadixConnection(name: string, serverUrl: string, authType: string, token: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (!name.trim() || !serverUrl.trim()) return { ok: false, message: "Add a name and a server URL first. Nothing was saved." };
  try {
    await addVaultConnection(VAULT, SQUAD, tenant.tenantId, tenant.userId, name, serverUrl, authType, token);
    return done("Saved. A token is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safe(error, "The connection was not saved.") };
  }
}

export async function removeRadixConnection(connectionId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultConnection(VAULT, SQUAD, tenant.tenantId, tenant.userId, connectionId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The connection was not removed.") };
  }
}

/** Metadata only. There is no secret resolver or switch action on this page. */
export async function readRadixPlugins(): Promise<PluginPanel> {
  const tenant = await actor();
  if (!tenant) return offPanel("Choose an account to use Plugins.");
  try {
    const raw = await vaultPlugins(VAULT, SQUAD, tenant.tenantId, tenant.userId);
    if (raw.vault !== VAULT) return offPanel("Plugins could not be loaded from Tank. No vault data is shown.");
    return mapVaultPayload(raw);
  } catch {
    return offPanel("Plugins could not be loaded from Tank. No vault data is shown.");
  }
}
