/** Public bot transport only. No page, widget, SigninDirectory role or Workspace key. */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const base = process.env.TANK_URL ?? "http://tank:8000";
const empty = (status: number) => new Response(null, { status, headers: { "Cache-Control": "no-store" } });
function originOf(request: Request): string | null {
  const value = request.headers.get("origin") ?? "";
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.origin === value ? value : null;
  } catch { return null; }
}
export async function OPTIONS(request: Request) {
  const origin = originOf(request);
  if (!origin) return empty(403);
  return new Response(null, { status: 204, headers: {
    "Access-Control-Allow-Origin": origin, "Vary": "Origin", "Access-Control-Allow-Methods": "POST",
    "Access-Control-Allow-Headers": "content-type,x-bot-key", "Access-Control-Max-Age": "600",
    "Cache-Control": "no-store",
  } });
}
export async function POST(request: Request) {
  const origin = originOf(request), key = request.headers.get("x-bot-key") ?? "";
  if (!origin || key.length > 160 || !/^tb1\.(us-east|za)\.[a-f0-9]{32}\.rk_pub_[a-f0-9]{48}$/.test(key)) return empty(403);
  if (!request.headers.get("content-type")?.startsWith("application/json")) return empty(415);
  const reader = request.body?.getReader();
  if (!reader) return empty(422);
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > 16000) { void reader.cancel(); return empty(413); }
      chunks.push(part.value);
    }
    const body = new Uint8Array(size);
    let offset = 0;
    for (const part of chunks) { body.set(part, offset); offset += part.byteLength; }
    const target = key.split(".")[1] === "za" ? process.env.TANK_URL_ZA : base;
    if (!target) return empty(503);
    const response = await fetch(new URL("/v1/bot/messages", target), {
      method: "POST", body, redirect: "error", cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(90000)]),
      headers: { "content-type": "application/json", "origin": origin, "x-bot-key": key,
        "x-forwarded-for": request.headers.get("x-forwarded-for") ?? "" },
    });
    if (!response.ok || response.headers.get("Access-Control-Allow-Origin") !== origin) return empty(response.ok ? 502 : response.status);
    if (response.status === 204) return new Response(null, { status: 204, headers: { "Access-Control-Allow-Origin": origin, "Vary": "Origin", "Cache-Control": "no-store" } });
    const result: unknown = await response.json();
    if (!result || typeof result !== "object" || Array.isArray(result)) return empty(502);
    return Response.json(result, { headers: { "Access-Control-Allow-Origin": origin, "Vary": "Origin", "Cache-Control": "no-store" } });
  } catch { return empty(503); }
}
