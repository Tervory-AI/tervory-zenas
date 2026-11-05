import { NextResponse } from "next/server";
import { currentTenant } from "@/lib/tenant";
import { homeRegion, zenas } from "../../../tank";

export const runtime = "nodejs";

export async function POST(req: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const body = (await req.json().catch(() => null)) as { kind?: string; text?: string } | null;
  const kind = body?.kind?.trim() ?? "";
  const text = body?.text ?? "";
  if (!kind || !text.trim()) {
    return NextResponse.json({ error: "A document needs a kind and some text." }, { status: 400 });
  }
  try {
    const { tenantId } = await currentTenant();
    const res = await zenas(`matters/${id}/documents`, {
      method: "POST",
      body: JSON.stringify({
        tenant_id: tenantId,
        home_region: homeRegion(),
        kind,
        body: text,
        sources: [],
      }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof payload?.detail === "string" ? payload.detail : "The document was not saved.";
      return NextResponse.json({ error: detail }, { status: res.status });
    }
    return NextResponse.json(
      { ok: true, id: payload.document_id ?? payload.document?.id ?? null, state: "draft" },
      { status: 201 },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "The document was not saved.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
