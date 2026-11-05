import { NextResponse } from "next/server";
import { activeTenant } from "@/lib/tenant";
import { uploadVaultFiles } from "@/lib/tank";

const MAX_UPLOAD = 32 * 1024 * 1024 + 256000;
export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  let sameOrigin = false;
  try { sameOrigin = Boolean(origin && new URL(origin).host === request.headers.get("host")); } catch { /* Refuse malformed origins. */ }
  if (!sameOrigin) {
    return NextResponse.json({ ok: false, message: "Upload files from your Market Lab page." }, { status: 403 });
  }
  const tenant = await activeTenant();
  if (!tenant) return NextResponse.json({ ok: false, message: "Sign in to upload files." }, { status: 401 });
  const tooLarge = () => NextResponse.json({ ok: false, message: "Upload up to 32 MB at a time." }, { status: 413 });
  if (Number(request.headers.get("content-length") ?? 0) > MAX_UPLOAD) return tooLarge();
  try {
    // Bound chunked requests too, before parsing their multipart fields.
    const reader = request.body?.getReader();
    if (!reader) return NextResponse.json({ ok: false, message: "Choose a file first." }, { status: 400 });
    const chunks: Uint8Array<ArrayBuffer>[] = [];
    let total = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > MAX_UPLOAD) { await reader.cancel(); return tooLarge(); }
        chunks.push(new Uint8Array(value));
      }
    } finally { reader.releaseLock(); }
    const files = await new Response(new Blob(chunks), { headers: { "Content-Type": request.headers.get("content-type") ?? "" } }).formData();
    await uploadVaultFiles("lab.vault", "market_lab", tenant.tenantId, tenant.userId, files);
    return NextResponse.json({ ok: true, message: "Upload complete. Check each file’s reading status below." });
  } catch {
    return NextResponse.json({ ok: false, message: "The upload could not be completed. Check the file list before trying again." }, { status: 503 });
  }
}
