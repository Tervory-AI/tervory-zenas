/** Public Meta transport. Tank verifies the untouched payload before parsing it. */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };
const empty = (status: number) => new Response(null, { status, headers });
function target(request: Request): URL | null {
  const regions = new URL(request.url).searchParams.getAll("region");
  if (regions.length !== 1) return null;
  const base = regions[0] === "za" ? process.env.TANK_URL_ZA
    : regions[0] === "us-east" ? process.env.TANK_URL : undefined;
  return base ? new URL("/v1/bot/whatsapp", base) : null;
}
export async function GET(request: Request) {
  const url = target(request);
  if (!url) return empty(503);
  const query = new URL(request.url).searchParams;
  const names = ["hub.mode", "hub.verify_token", "hub.challenge"];
  if (names.some(name => query.getAll(name).length !== 1 || (query.get(name)?.length ?? 0) > 512)) return empty(403);
  if (query.get("hub.mode") !== "subscribe" || !/^[0-9]{1,128}$/.test(query.get("hub.challenge") ?? "")) return empty(403);
  try {
    // Keep the verify token out of the internal request URL and its access log.
    const response = await fetch(url, { redirect: "error", cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(15000)]), headers: {
        "x-whatsapp-mode": query.get("hub.mode")!, "x-whatsapp-verify-token": query.get("hub.verify_token")!,
        "x-whatsapp-challenge": query.get("hub.challenge")!,
      } });
    if (!response.ok) return empty(response.status);
    const challenge = await response.text();
    if (challenge !== query.get("hub.challenge")) return empty(502);
    return new Response(challenge, { headers: { ...headers, "Content-Type": "text/plain" } });
  } catch { return empty(502); }
}
export async function POST(request: Request) {
  const url = target(request), signature = request.headers.get("x-hub-signature-256") ?? "";
  if (!url) return empty(503);
  if (!/^sha256=[a-fA-F0-9]{64}$/.test(signature)) return empty(403);
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
      if (size > 262144) { void reader.cancel(); return empty(413); }
      chunks.push(part.value);
    }
    const body = new Uint8Array(size);
    let offset = 0;
    for (const part of chunks) { body.set(part, offset); offset += part.byteLength; }
    const response = await fetch(url, { method: "POST", body, redirect: "error", cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(90000)]),
      headers: { "Content-Type": "application/json", "x-hub-signature-256": signature } });
    return empty(response.ok ? 200 : response.status);
  } catch { return empty(502); }
}
