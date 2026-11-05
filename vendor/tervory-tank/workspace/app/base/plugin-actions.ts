"use server";

// Plugins writes for Base. Each one goes to Tank, server-side, for base.vault only.
// Tank refuses every write while the vault is off. Nothing here can turn a vault on.

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
import { BASE_SQUAD, BASE_VAULT, safeMessage, NEED_FILE, NEED_KEY, NEED_LINK, NEED_MCP } from "./plugin-panel";

export type PluginResult = { ok: boolean; message: string };

const SIGN_IN = "Sign in to change Plugins.";

function done(message: string): PluginResult {
  revalidatePath("/base");
  return { ok: true, message };
}

export async function saveBaseKey(name: string, value: string, purpose: string): Promise<PluginResult> {
  const tenant = await activeTenant();
  if (!tenant) return { ok: false, message: SIGN_IN };
  if (!name.trim() || !value.trim()) return { ok: false, message: NEED_KEY };
  try {
    await saveVaultSecret(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId, name.trim(), purpose, value);
    return done("Saved. The key is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safeMessage(error, "The key was not saved.") };
  }
}

export async function removeBaseSecret(name: string): Promise<PluginResult> {
  const tenant = await activeTenant();
  if (!tenant) return { ok: false, message: SIGN_IN };
  try {
    await removeVaultSecret(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId, name);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safeMessage(error, "The key was not removed.") };
  }
}

export async function uploadBaseFiles(formData: FormData): Promise<PluginResult> {
  const tenant = await activeTenant();
  if (!tenant) return { ok: false, message: SIGN_IN };
  if (formData.getAll("file").length === 0) return { ok: false, message: NEED_FILE };
  try {
    await uploadVaultFiles(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId, formData);
    return done("Saved.");
  } catch (error) {
    return { ok: false, message: safeMessage(error, "The file was not saved.") };
  }
}

export async function removeBaseFile(fileId: string): Promise<PluginResult> {
  const tenant = await activeTenant();
  if (!tenant) return { ok: false, message: SIGN_IN };
  try {
    await removeVaultFile(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId, fileId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safeMessage(error, "The file was not removed.") };
  }
}

export async function addBaseLink(url: string, label: string, frequency: string): Promise<PluginResult> {
  const tenant = await activeTenant();
  if (!tenant) return { ok: false, message: SIGN_IN };
  if (!url.trim()) return { ok: false, message: NEED_LINK };
  try {
    await addVaultLink(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId, url, label, frequency);
    return done("Saved.");
  } catch (error) {
    return { ok: false, message: safeMessage(error, "The link was not saved.") };
  }
}

export async function removeBaseLink(linkId: string): Promise<PluginResult> {
  const tenant = await activeTenant();
  if (!tenant) return { ok: false, message: SIGN_IN };
  try {
    await removeVaultLink(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId, linkId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safeMessage(error, "The link was not removed.") };
  }
}

export async function addBaseConnection(name: string, serverUrl: string, authType: string, token: string): Promise<PluginResult> {
  const tenant = await activeTenant();
  if (!tenant) return { ok: false, message: SIGN_IN };
  if (!name.trim() || !serverUrl.trim()) return { ok: false, message: NEED_MCP };
  try {
    await addVaultConnection(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId, name, serverUrl, authType, token);
    return done("Saved. A token is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safeMessage(error, "The connection was not saved.") };
  }
}

export async function removeBaseConnection(connectionId: string): Promise<PluginResult> {
  const tenant = await activeTenant();
  if (!tenant) return { ok: false, message: SIGN_IN };
  try {
    await removeVaultConnection(BASE_VAULT, BASE_SQUAD, tenant.tenantId, tenant.userId, connectionId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safeMessage(error, "The connection was not removed.") };
  }
}
