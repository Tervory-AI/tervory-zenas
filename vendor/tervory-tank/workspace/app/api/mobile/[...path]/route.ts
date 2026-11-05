import { cookies } from "next/headers";
import { mobileIdentity, mobileMutation, MOBILE_COOKIE } from "@/lib/mobile";
import { mobileRequest } from "@/lib/tank";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
type Context = { params: Promise<{ path: string[] }> };
const json = (data: unknown, status = 200) => Response.json(data, { status, headers: { "Cache-Control": "no-store" } });

async function limited(request: Request, limit: number): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  const chunks: Uint8Array[] = []; let length = 0;
  if (reader) try {
    for (;;) {
      const item = await reader.read();
      if (item.done) break;
      length += item.value.length;
      if (length > limit) { await reader.cancel(); throw new RangeError(); }
      chunks.push(item.value);
    }
  } finally { reader.releaseLock(); }
  return new Uint8Array(Buffer.concat(chunks));
}

async function handle(request: Request, context: Context) {
  const path = (await context.params).path.join("/");
  if (request.method !== "GET" && !mobileMutation(request)) return json({ error: "Request refused." }, 403);
  try {
    if (path === "session" && request.method === "POST") {
      const input = JSON.parse(new TextDecoder().decode(await limited(request, 2048)));
      const response = await mobileRequest("session/exchange", input);
      if (!response.ok) return json({ error: "Trigger sign-in could not be verified." }, response.status);
      const result = await response.json();
      (await cookies()).set(MOBILE_COOKIE, result.token, { secure: true, httpOnly: true, sameSite: "strict", path: "/", maxAge: 7 * 86400 });
      return json({ signed_in: true });
    }
    const identity = await mobileIdentity();
    if (!identity) return json({ error: "Trigger sign-in required." }, 401);
    const token = (await cookies()).get(MOBILE_COOKIE)!.value;
    if (path === "session" && request.method === "DELETE") {
      const response = await mobileRequest("session/revoke", { token });
      if (response.ok) (await cookies()).delete(MOBILE_COOKIE);
      return response;
    }
    if (path === "bootstrap" && request.method === "GET") return mobileRequest("bootstrap", undefined, identity);
    if (path === "devices" && request.method === "POST") {
      const input = JSON.parse(new TextDecoder().decode(await limited(request, 8192)));
      return mobileRequest("devices", input, identity, token);
    }
    if (path === "shares" && request.method === "POST") {
      const bytes = await limited(request, 6 * 1024 * 1024);
      const form = await new Response(bytes as BodyInit, { headers: { "Content-Type": request.headers.get("content-type") ?? "" } }).formData();
      const files = form.getAll("files");
      if (files.length > 5) return json({ error: "Share up to five files." }, 413);
      const encoded = [];
      for (const file of files) {
        if (!(file instanceof File)) return json({ error: "Invalid file." }, 422);
        encoded.push({ name: file.name, data: Buffer.from(await file.arrayBuffer()).toString("base64") });
      }
      return mobileRequest("shares", { share_id: form.get("share_id"), text: form.get("text"), files: encoded }, identity);
    }
    if (/^shares\/[a-f0-9-]{36}$/.test(path) && request.method === "GET") return mobileRequest(path, undefined, identity);
    return json({ error: "Unknown request." }, 404);
  } catch (error) {
    if (error instanceof RangeError) return json({ error: "Share up to five files totaling 5 MB." }, 413);
    if (error instanceof SyntaxError || error instanceof TypeError) return json({ error: "Invalid request." }, 400);
    return json({ error: "Your workspace is unavailable. Please try again." }, 503);
  }
}

export { handle as GET, handle as POST, handle as DELETE };
