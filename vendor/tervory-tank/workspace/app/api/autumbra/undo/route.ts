import { fetchWithDeadline as fetch } from "../../../../lib/request";
import { NextResponse } from "next/server";
import { verifyUndoToken } from "@/lib/autumbra-undo-token";
import { autumbraRequest } from "@/lib/autumbra-request";

const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";
const INVALID = "This undo link is invalid or has expired.";
const UNAVAILABLE = "Could not undo that shield right now.";
const json = (body: object, status = 200) => NextResponse.json(body, {
  status, headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" },
});

async function bodyToken(request: Request): Promise<string> {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Missing body");
  let timer: ReturnType<typeof setTimeout> | undefined;
  const work = (async () => {
    const chunks: Uint8Array[] = [];
    let size = 0;
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 20000) throw new Error("Invalid body");
      chunks.push(value);
    }
    const raw = Buffer.concat(chunks);
    const body: unknown = JSON.parse(raw.toString("utf8"));
    if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Invalid body");
    if (!("token" in body)) return "";
    if (typeof body.token !== "string") throw new Error("Invalid body");
    return body.token.trim();
  })();
  try {
    return await Promise.race([work, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("Invalid body")), 10000);
    })]);
  } finally {
    clearTimeout(timer);
    reader.cancel().catch(() => undefined);
  }
}

export async function POST(request: Request) {
  let token: string;
  try { token = await bodyToken(request); }
  catch { return json({ok: false, detail: "Could not read the undo request."}, 400); }
  let payload;
  try { payload = verifyUndoToken(token); }
  catch { return json({ok: false, detail: INVALID}, 401); }
  if (!TANK_API_KEY.trim()) return json({ok: false, detail: "Undo is not configured on this host yet."}, 503);
  try {
    return await autumbraRequest(`${TANK_URL}/v1/ghost/autumbra/queue/undo`, {
      method: "POST", cache: "no-store",
      headers: {"content-type": "application/json", "x-tank-key": TANK_API_KEY},
      body: JSON.stringify({tenant_id: payload.tenant_id, site: payload.site,
        prior_receipt_id: payload.receipt_id, rule_id: payload.rule_id || undefined}),
    }, async res => {
      if (!res.ok) return json({ok: false, detail: UNAVAILABLE}, res.status >= 500 ? 502 : res.status);
      const data = await res.json();
      if (data?.already_undone === true || data?.outcome === "already_undone") {
        return json({ok: true, queued: false, already_undone: true,
          detail: "This block was already undone on an earlier time."});
      }
      if (data?.queued !== true) return json({ok: false, detail: UNAVAILABLE}, 502);
      return json({ok: true, queued: true});
    });
  } catch { return json({ok: false, detail: "Could not reach Autumbra to undo."}, 502); }
}

export async function GET(request: Request) {
  let payload;
  try { payload = verifyUndoToken(new URL(request.url).searchParams.get("token") || ""); }
  catch { return json({ok: false, detail: INVALID}, 401); }
  if (!TANK_API_KEY.trim()) return json({ok: false, detail: "Undo is not configured on this host yet."}, 503);
  try {
    const url = new URL(`${TANK_URL}/v1/ghost/autumbra/undo/status`);
    url.searchParams.set("tenant_id", payload.tenant_id);
    url.searchParams.set("prior_receipt_id", payload.receipt_id);
    return await autumbraRequest(url, {method: "GET", cache: "no-store", headers: {"x-tank-key": TANK_API_KEY}}, async res => {
      if (!res.ok) return json({ok: false, detail: UNAVAILABLE}, 502);
      const data = await res.json();
      if (typeof data?.already_undone !== "boolean") return json({ok: false, detail: UNAVAILABLE}, 502);
      return json({ok: true, already_undone: data.already_undone,
        ...(data.already_undone ? {detail: "This block was already undone on an earlier time."} : {})});
    });
  } catch { return json({ok: false, detail: "Could not reach Autumbra to undo."}, 502); }
}
