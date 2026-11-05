// Stripe webhook for the Workspace. Server-side only.
// Stripe's library checks the signature. The same event id is applied once.
// The claim lives in Tank, so a second Workspace process sees it too.

import Stripe from "stripe";
import { claimBillingEvent, markBillingEventHandled, releaseBillingEvent } from "../../lib/tank";

export type WebhookEffect = (event: { id: string; type: string }) => void | Promise<void>;

export type WebhookStore = {
  /** True when this caller is the first to take the event id. */
  claim(eventId: string, eventType: string): Promise<boolean>;
  /** Drop a claim when handling failed, so Stripe's retry can run once. */
  release(eventId: string): Promise<void>;
  /** Keep a successful claim so a later release cannot drop it. */
  handled(eventId: string): Promise<void>;
};

/** One claim store. State is in Tank, not in this process. */
export function tankWebhookStore(): WebhookStore {
  return {
    async claim(eventId: string, eventType: string) {
      const result = await claimBillingEvent(eventId, eventType);
      if (typeof result?.first !== "boolean") {
        throw new Error("Billing claim was not stored.");
      }
      return result.first;
    },
    async release(eventId: string) {
      await releaseBillingEvent(eventId);
    },
    async handled(eventId: string) {
      await markBillingEventHandled(eventId);
    },
  };
}

export type WebhookResponse = {
  status: number;
  body: { received?: boolean; duplicate?: boolean; error?: string };
};

export async function receiveStripeWebhook(input: {
  rawBody: string;
  signature: string | null;
  secret: string | undefined;
  store?: WebhookStore;
  effect?: WebhookEffect;
}): Promise<WebhookResponse> {
  const secret = input.secret?.trim() ?? "";
  if (!secret) {
    return { status: 500, body: { error: "Billing webhook is not configured." } };
  }
  const signature = input.signature?.trim() ?? "";
  if (!signature) {
    return { status: 400, body: { error: "Missing Stripe signature." } };
  }

  let event: Stripe.Event;
  try {
    event = Stripe.webhooks.constructEvent(input.rawBody, signature, secret);
  } catch {
    return { status: 400, body: { error: "Invalid Stripe signature." } };
  }

  const eventId = typeof event.id === "string" ? event.id.trim() : "";
  const eventType = typeof event.type === "string" ? event.type.trim() : "";
  if (!eventId || !eventType) {
    return { status: 400, body: { error: "Invalid Stripe event." } };
  }

  const store = input.store ?? tankWebhookStore();
  let first: boolean;
  try {
    first = await store.claim(eventId, eventType);
  } catch {
    return { status: 500, body: { error: "Billing webhook could not be recorded." } };
  }
  if (!first) {
    return { status: 200, body: { received: true, duplicate: true } };
  }

  try {
    if (input.effect) await input.effect({ id: eventId, type: eventType });
  } catch {
    try {
      await store.release(eventId);
    } catch {
      return { status: 500, body: { error: "Billing webhook could not be recorded." } };
    }
    return { status: 500, body: { error: "Billing webhook could not be recorded." } };
  }

  try {
    await store.handled(eventId);
  } catch {
    return { status: 500, body: { error: "Billing webhook could not be recorded." } };
  }

  return { status: 200, body: { received: true } };
}
