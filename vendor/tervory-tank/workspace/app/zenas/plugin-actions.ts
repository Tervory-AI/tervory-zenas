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
} from "@/lib/tank";

const VAULT = "zenas.vault";
const SQUAD = "zenas";

export type PluginResult = { ok: boolean; message: string };

async function actor() {
  const tenant = await activeTenant();
  if (!tenant) return null;
  return tenant;
}

function safe(error: unknown, fallback: string): string {
  const text = error instanceof Error ? error.message : "";
  if (text.includes(VAULT_OFF_MESSAGE)) return VAULT_OFF_MESSAGE;
  if (text.includes("Only CSV, XLSX, and PDF")) return "Only CSV, XLSX, and PDF files can be uploaded.";
  if (text.includes("Sign in")) return "Sign in to change Plugins.";
  return fallback;
}

function done(message: string): PluginResult {
  revalidatePath("/zenas");
  return { ok: true, message };
}

export async function saveZenasKey(name: string, value: string, purpose: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (!value.trim() || !name.trim()) return { ok: false, message: "Add a key first. Nothing was stored." };
  try {
    await saveVaultSecret(VAULT, SQUAD, tenant.tenantId, tenant.userId, name.trim(), purpose, value);
    return done("Saved. The key is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safe(error, "The key was not saved.") };
  }
}

export async function removeZenasSecret(name: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultSecret(VAULT, SQUAD, tenant.tenantId, tenant.userId, name);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The key was not removed.") };
  }
}

export async function uploadZenasFiles(formData: FormData): Promise<PluginResult> {
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

export async function removeZenasFile(fileId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultFile(VAULT, SQUAD, tenant.tenantId, tenant.userId, fileId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The file was not removed.") };
  }
}

export async function addZenasLink(url: string, label: string, frequency: string): Promise<PluginResult> {
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

export async function removeZenasLink(linkId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultLink(VAULT, SQUAD, tenant.tenantId, tenant.userId, linkId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The link was not removed.") };
  }
}

export async function addZenasConnection(name: string, serverUrl: string, token: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (!name.trim() || !serverUrl.trim()) return { ok: false, message: "Add a name and a server URL first. Nothing was saved." };
  try {
    await addVaultConnection(VAULT, SQUAD, tenant.tenantId, tenant.userId, name, serverUrl, "token", token);
    return done("Saved. A token is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safe(error, "The connection was not saved.") };
  }
}

export async function removeZenasConnection(connectionId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultConnection(VAULT, SQUAD, tenant.tenantId, tenant.userId, connectionId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The connection was not removed.") };
  }
}
