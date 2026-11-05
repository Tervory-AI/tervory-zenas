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
} from "@/lib/tank";
import { TISAN_SQUAD, TISAN_VAULT, VAULT_OFF_MESSAGE } from "./vault-model";

export type PluginResult = { ok: boolean; message: string };

async function actor() {
  try {
    return await activeTenant();
  } catch {
    return null;
  }
}

function safe(error: unknown, fallback: string): string {
  const text = error instanceof Error ? error.message : "";
  if (text.includes(VAULT_OFF_MESSAGE)) return VAULT_OFF_MESSAGE;
  if (text.includes("Only CSV, XLSX, and PDF")) return "Only CSV, XLSX, and PDF files can be uploaded.";
  if (text.includes("Sign in")) return "Sign in to change Plugins.";
  return fallback;
}

function done(message: string): PluginResult {
  revalidatePath("/tisan");
  return { ok: true, message };
}

export async function saveTisanKey(name: string, value: string, purpose: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (!value.trim()) return { ok: false, message: "Add a key first. Nothing was stored." };
  try {
    await saveVaultSecret(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId, name, purpose, value);
    return done("Saved. The key is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safe(error, "The key was not saved.") };
  }
}

export async function removeTisanSecret(name: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultSecret(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId, name);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The key was not removed.") };
  }
}

export async function uploadTisanFiles(formData: FormData): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (formData.getAll("file").length === 0) return { ok: false, message: "Choose a file first. Nothing was saved." };
  try {
    await uploadVaultFiles(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId, formData);
    return done("Saved.");
  } catch (error) {
    return { ok: false, message: safe(error, "The file was not saved.") };
  }
}

export async function removeTisanFile(fileId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultFile(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId, fileId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The file was not removed.") };
  }
}

export async function addTisanLink(url: string, label: string, frequency: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (!url.trim()) return { ok: false, message: "Add a link first. Nothing was saved." };
  try {
    await addVaultLink(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId, url, label, frequency);
    return done("Saved.");
  } catch (error) {
    return { ok: false, message: safe(error, "The link was not saved.") };
  }
}

export async function removeTisanLink(linkId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultLink(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId, linkId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The link was not removed.") };
  }
}

export async function addTisanConnection(name: string, serverUrl: string, authType: string, token: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  if (!name.trim() || !serverUrl.trim()) return { ok: false, message: "Add a name and a server URL first. Nothing was saved." };
  try {
    await addVaultConnection(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId, name, serverUrl, authType, token);
    return done("Saved. A token is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safe(error, "The connection was not saved.") };
  }
}

export async function removeTisanConnection(connectionId: string): Promise<PluginResult> {
  const tenant = await actor();
  if (!tenant) return { ok: false, message: "Sign in to change Plugins." };
  try {
    await removeVaultConnection(TISAN_VAULT, TISAN_SQUAD, tenant.tenantId, tenant.userId, connectionId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The connection was not removed.") };
  }
}
