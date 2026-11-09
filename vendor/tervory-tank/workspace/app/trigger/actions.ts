"use server";

import { triggerIdentity } from "@/lib/trigger";
import { activeTenant } from "@/lib/tenant";
import { commandRequest } from "@/lib/tank";
import { waitForAction } from "./request";

async function identity() {
  const tenant = await activeTenant();
  if (!tenant) throw new Error("This item is unavailable.");
  const actor = await triggerIdentity(tenant);
  if (!actor) throw new Error("This item is unavailable.");
  return actor;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// Select constant routes; request strings never become an upstream path prefix.
const READ_ROUTES = {
  "crm/leads": { path: "trigger/console/crm/leads", detail: "path" },
  "crm/contacts": { path: "trigger/console/crm/contacts", detail: "path" },
  "crm/estimates": { path: "trigger/console/crm/estimates", detail: "path" },
  "crm/invoices": { path: "trigger/console/crm/invoices", detail: "path" },
  "marketing/channels": {
    path: "trigger/console/marketing/channels",
    detail: "none",
  },
  "marketing/posts": {
    path: "trigger/console/marketing/posts",
    detail: "query",
  },
  "marketing/composer": {
    path: "trigger/console/marketing/composer",
    detail: "query",
  },
  "marketing/calendar": {
    path: "trigger/console/marketing/calendar",
    detail: "query",
  },
  "marketing/reviews": {
    path: "trigger/console/marketing/reviews",
    detail: "query",
  },
  "bot/settings": { path: "trigger/console/bot/settings", detail: "none" },
  "bot/inbox": { path: "trigger/console/bot/inbox", detail: "query" },
} as const;

export async function readTrigger(
  area: string,
  section: string,
  recordId?: string,
) {
  if (
    typeof area !== "string" ||
    area.length > 16 ||
    typeof section !== "string" ||
    section.length > 16
  )
    throw new Error("This item is unavailable.");
  const key = `${area}/${section}`;
  if (!Object.hasOwn(READ_ROUTES, key))
    throw new Error("This item is unavailable.");
  const route = READ_ROUTES[key as keyof typeof READ_ROUTES];
  if (
    recordId !== undefined &&
    (typeof recordId !== "string" ||
      recordId.length !== 36 ||
      !UUID.test(recordId) ||
      route.detail === "none")
  )
    throw new Error("This item is unavailable.");
  const path =
    recordId === undefined
      ? route.path
      : route.detail === "path"
        ? `${route.path}/${encodeURIComponent(recordId)}`
        : `${route.path}?record_id=${encodeURIComponent(recordId)}`;
  const actor = await identity();
  const result = await waitForAction((async () => (await commandRequest(actor, path, {
    signal: AbortSignal.timeout(10_000),
  })).json())(), 10_000);
  if (route.path === "trigger/console/bot/settings")
    result.settings = { name: actor.orgName, ...result.settings };
  return result;
}
export async function proposeTrigger(
  tool: string,
  arguments_: Record<string, unknown>,
) {
  return (
    await commandRequest(await identity(), "trigger/console/propose", {
      method: "POST",
      body: JSON.stringify({ tool, arguments: arguments_ }),
    })
  ).json();
}
export async function confirmTrigger(
  thread: string,
  card: string,
  action: "confirm" | "cancel" = "confirm",
) {
  if (action !== "confirm" && action !== "cancel") throw new Error("This item is unavailable.");
  if (
    typeof thread !== "string" ||
    thread.length !== 36 ||
    !UUID.test(thread) ||
    typeof card !== "string" ||
    card.length !== 36 ||
    !UUID.test(card)
  )
    throw new Error("This item is unavailable.");
  return (
    await commandRequest(
      await identity(),
      `cards/${encodeURIComponent(card)}`,
      {
        method: "POST",
        body: JSON.stringify({ action }),
      },
    )
  ).json();
}
