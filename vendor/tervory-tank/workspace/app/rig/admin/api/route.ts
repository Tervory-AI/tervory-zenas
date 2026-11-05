import { NextRequest, NextResponse } from "next/server";
import { changeRetailer, retailerIdentity } from "../actions";
import type { AdminOperation } from "../types";

const operations = new Set(["commerce-connect", "commerce-orders", "store-delivery", "connect", "import", "delete-products", "refresh", "mapping", "confirm-mapping", "settings", "embed-keys", "price-rule", "exchange-rate", "templates", "approve", "reject", "suggest"]);

// Feed files use a bounded route body instead of Next's 1 MB server-action limit.
export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin");
  let sameOrigin = false;
  try { sameOrigin = Boolean(origin && new URL(origin).host === request.headers.get("host")); } catch { /* Refuse invalid origins. */ }
  if (!sameOrigin) {
    return NextResponse.json({ ok: false }, { status: 403 });
  }
  try { await retailerIdentity(); }
  catch { return NextResponse.json({ ok: false }, { status: 403 }); }
  const reader = request.body?.getReader();
  if (!reader) return NextResponse.json({ ok: false }, { status: 400 });
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 12_000_000) {
      await reader.cancel();
      return NextResponse.json({ ok: false }, { status: 413 });
    }
    chunks.push(value);
  }
  try {
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const body = JSON.parse(new TextDecoder().decode(bytes));
    if (!operations.has(body.operation) || !body.data || typeof body.data !== "object" || Array.isArray(body.data)) {
      return NextResponse.json({ ok: false }, { status: 422 });
    }
    return NextResponse.json(await changeRetailer(body.operation as AdminOperation, body.data), { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ ok: false }, { status: 422 }); }
}
