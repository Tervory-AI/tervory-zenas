import { NextResponse } from "next/server";
import { receiveStripeWebhook } from "../../../billing/webhook";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const result = await receiveStripeWebhook({
    rawBody: await request.text(),
    signature: request.headers.get("stripe-signature"),
    secret: process.env.STRIPE_WEBHOOK_SECRET,
  });
  return NextResponse.json(result.body, { status: result.status });
}
