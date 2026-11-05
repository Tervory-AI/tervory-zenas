import { NextResponse } from "next/server";
import { verifyUndoToken } from "@/lib/autumbra-undo-token";

/**
 * Owner-facing undo door. Accepts ONLY a valid signed token for that tenant + shield.
 * Body tenant/receipt/rule fields are ignored. Public: the token is the credential.
 * If AUTUMBRA_UNDO_TOKEN_SECRET is unset, verification fails closed (401).
 * Second tap on a used link: already_undone, no second queue.
 */
const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

export async function POST(request: Request) {
  let body: { token?: string };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ ok: false, detail: "Could not read the undo request." }, { status: 400 });
  }

  const token = String(body.token || "").trim();
  if (!token) {
    return NextResponse.json(
      { ok: false, detail: "This undo link is invalid or has expired." },
      { status: 401 },
    );
  }

  let tenantId = "";
  let receiptId = "";
  let site = "";
  let ruleId = "";
  try {
    const payload = verifyUndoToken(token);
    tenantId = payload.tenant_id;
    receiptId = payload.receipt_id;
    site = payload.site || "coolify.tervory.com";
    ruleId = payload.rule_id;
  } catch {
    return NextResponse.json(
      { ok: false, detail: "This undo link is invalid or has expired." },
      { status: 401 },
    );
  }

  if (!receiptId && !ruleId) {
    return NextResponse.json({ ok: false, detail: "Missing receipt to undo." }, { status: 400 });
  }
  if (!tenantId) {
    return NextResponse.json({ ok: false, detail: "Missing client id." }, { status: 400 });
  }
  if (!TANK_API_KEY) {
    return NextResponse.json(
      { ok: false, detail: "Undo is not configured on this host yet." },
      { status: 503 },
    );
  }

  const payload = {
    tenant_id: tenantId,
    site,
    prior_receipt_id: receiptId || undefined,
    rule_id: ruleId || undefined,
  };
  try {
    const res = await fetch(`${TANK_URL}/v1/ghost/autumbra/queue/undo`, {
      method: "POST",
      cache: "no-store",
      headers: {
        "content-type": "application/json",
        "x-tank-key": TANK_API_KEY,
      },
      body: JSON.stringify(payload),
    });
    const text = await res.text();
    let data: {
      outcome?: string;
      already_undone?: boolean;
      queued?: boolean;
      detail?: string;
      undone_at?: string;
    } | null = null;
    try {
      data = JSON.parse(text) as typeof data;
    } catch {
      data = { detail: text.slice(0, 200) };
    }
    if (!res.ok) {
      return NextResponse.json(
        { ok: false, detail: "Could not undo that shield right now.", upstream: data },
        { status: res.status >= 500 ? 502 : res.status },
      );
    }
    if (data && (data.already_undone || data.outcome === "already_undone")) {
      return NextResponse.json({
        ok: true,
        queued: false,
        already_undone: true,
        undone_at: data.undone_at,
        detail:
          data.detail ||
          "This block was already undone on an earlier time.",
      });
    }
    return NextResponse.json({ ok: true, queued: true, result: data });
  } catch {
    return NextResponse.json(
      { ok: false, detail: "Could not reach Autumbra to undo." },
      { status: 502 },
    );
  }
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const token = String(url.searchParams.get("token") || "").trim();
  if (!token) {
    return NextResponse.json(
      { ok: false, detail: "This undo link is invalid or has expired." },
      { status: 401 },
    );
  }
  let tenantId = "";
  let receiptId = "";
  try {
    const payload = verifyUndoToken(token);
    tenantId = payload.tenant_id;
    receiptId = payload.receipt_id;
  } catch {
    return NextResponse.json(
      { ok: false, detail: "This undo link is invalid or has expired." },
      { status: 401 },
    );
  }
  if (!TANK_API_KEY) {
    return NextResponse.json({ ok: true, already_undone: false });
  }
  try {
    const upstream = new URL(`${TANK_URL}/v1/ghost/autumbra/undo/status`);
    upstream.searchParams.set("tenant_id", tenantId);
    upstream.searchParams.set("prior_receipt_id", receiptId);
    const res = await fetch(upstream.toString(), {
      method: "GET",
      cache: "no-store",
      headers: { "x-tank-key": TANK_API_KEY },
    });
    const data = (await res.json()) as {
      already_undone?: boolean;
      detail?: string;
      undone_at?: string;
    };
    if (!res.ok) {
      return NextResponse.json({ ok: true, already_undone: false });
    }
    return NextResponse.json({
      ok: true,
      already_undone: Boolean(data.already_undone),
      detail: data.detail,
      undone_at: data.undone_at,
    });
  } catch {
    return NextResponse.json({ ok: true, already_undone: false });
  }
}

