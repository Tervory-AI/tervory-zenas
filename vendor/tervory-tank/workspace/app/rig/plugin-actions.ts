"use server";

import { revalidatePath } from "next/cache";
import { VAULT_OFF_MESSAGE } from "./vault-model";
import {
  addRigConnection,
  addRigLink,
  removeRigConnection,
  removeRigFile,
  removeRigLink,
  removeRigSecret,
  saveRigSecret,
  uploadRigFiles,
} from "./vault-server";

export type PluginResult = { ok: boolean; message: string };

function safe(error: unknown, fallback: string): string {
  const text = error instanceof Error ? error.message : "";
  if (text.includes(VAULT_OFF_MESSAGE)) return VAULT_OFF_MESSAGE;
  if (text.includes("Only CSV, XLSX, and PDF")) return "Only CSV, XLSX, and PDF files can be uploaded.";
  if (text.includes("Sign in")) return "Sign in to change Plugins.";
  return fallback;
}

function done(message: string): PluginResult {
  revalidatePath("/rig");
  return { ok: true, message };
}

export async function saveRigKey(name: string, secret: string, purpose: string): Promise<PluginResult> {
  if (!name.trim() || !secret.trim()) return { ok: false, message: "Add a key first. Nothing was stored." };
  try {
    await saveRigSecret(name.trim(), purpose.trim(), secret);
    return done("Saved. The key is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safe(error, "The key was not saved.") };
  }
}

export async function removeRigKey(name: string): Promise<PluginResult> {
  try {
    await removeRigSecret(name);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The key was not removed.") };
  }
}

export async function uploadRigFileForm(formData: FormData): Promise<PluginResult> {
  if (formData.getAll("file").length === 0) return { ok: false, message: "Choose a file first. Nothing was saved." };
  try {
    await uploadRigFiles(formData);
    return done("Saved.");
  } catch (error) {
    return { ok: false, message: safe(error, "The file was not saved.") };
  }
}

export async function removeRigFileAction(fileId: string): Promise<PluginResult> {
  try {
    await removeRigFile(fileId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The file was not removed.") };
  }
}

export async function addRigLinkAction(url: string, label: string, frequency: string): Promise<PluginResult> {
  if (!url.trim()) return { ok: false, message: "Add a link first. Nothing was saved." };
  try {
    await addRigLink(url.trim(), label.trim(), frequency);
    return done("Saved.");
  } catch (error) {
    return { ok: false, message: safe(error, "The link was not saved.") };
  }
}

export async function removeRigLinkAction(linkId: string): Promise<PluginResult> {
  try {
    await removeRigLink(linkId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The link was not removed.") };
  }
}

export async function addRigConnectionAction(name: string, serverUrl: string, authType: string, token: string): Promise<PluginResult> {
  if (!name.trim() || !serverUrl.trim()) return { ok: false, message: "Add a name and a server URL first. Nothing was saved." };
  try {
    await addRigConnection(name.trim(), serverUrl.trim(), authType, token);
    return done("Saved. A token is hidden and will not be shown again.");
  } catch (error) {
    return { ok: false, message: safe(error, "The connection was not saved.") };
  }
}

export async function removeRigConnectionAction(connectionId: string): Promise<PluginResult> {
  try {
    await removeRigConnection(connectionId);
    return done("Removed.");
  } catch (error) {
    return { ok: false, message: safe(error, "The connection was not removed.") };
  }
}
