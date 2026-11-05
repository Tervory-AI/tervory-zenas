import { NextResponse } from "next/server";
import { activeTenant } from "@/lib/tenant";
import { uploadRadixFiles } from "../../plugin-actions";

export const runtime = "nodejs";
// Coolify ends HTTPS before Next, so request.url is http:// while the browser's Origin is https://.
// Compare hosts, not full origins; a browser cannot forge the Host header.
const sameHost = (request: Request) => { try { return new URL(request.headers.get("origin") ?? "").host === (request.headers.get("host") ?? new URL(request.url).host); } catch { return false; } };
const MAX_REQUEST_BYTES = 8 * 1024 * 1024 + 64 * 1024;

function reply(message: string, status: number) {
  return NextResponse.json({ ok: false, message }, { status, headers: { "Cache-Control": "no-store" } });
}

/** A bounded multipart route avoids changing Next's shared server-action limit. */
export async function POST(request: Request) {
  if (!sameHost(request)) return reply("This request was refused.", 403);
  const tenant = await activeTenant().catch(() => null);
  if (!tenant) return reply("Choose an account to use Plugins.", 401);
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.startsWith("multipart/form-data;")) return reply("Choose a file first.", 400);
  const reader = request.body?.getReader();
  if (!reader) return reply("Choose a file first.", 400);
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > MAX_REQUEST_BYTES) {
        await reader.cancel();
        return reply("Each file must be 8 MB or smaller.", 413);
      }
      chunks.push(chunk.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const form = await new Request(request.url, { method: "POST", headers: { "content-type": contentType }, body: bytes }).formData();
    const files = form.getAll("file");
    if (files.length !== 1 || !(files[0] instanceof File) || files[0].size > 8 * 1024 * 1024) {
      return reply("Choose one file, 8 MB or smaller.", 400);
    }
    // Drop caller-supplied fields. Identity and vault are chosen on the server.
    const clean = new FormData(); clean.append("file", files[0]);
    const result = await uploadRadixFiles(clean);
    return NextResponse.json(result, { status: result.ok ? 200 : 400, headers: { "Cache-Control": "no-store" } });
  } catch { return reply("The file was not saved.", 400); }
}
