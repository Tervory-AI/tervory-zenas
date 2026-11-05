// Public MCP/OAuth transport and signed-in approval share Tank, never client
// identity headers. No provider calls or client-selected upstream addresses.
import { boundedBody, boundedFetch } from "@/lib/tank";
import { homeRegion } from "@/app/api/zenas/tank";
import { signinTank, type SigninIdentity } from "@/lib/signin-regions";
import { isIP } from "node:net";

const PATHS = {
  mcp: "/mcp", token: "/oauth/token", register: "/oauth/register", revoke: "/oauth/revoke",
  resource: "/.well-known/oauth-protected-resource",
  resourceMcp: "/.well-known/oauth-protected-resource/mcp",
  authorization: "/.well-known/oauth-authorization-server",
} as const;
const FORWARD = ["authorization", "mcp-session-id", "mcp-protocol-version", "accept", "content-type", "origin"];
const RETURN = ["content-type", "mcp-session-id", "mcp-protocol-version", "www-authenticate", "allow", "retry-after"];
const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
// Tank waits 60 seconds before returning the durable thread link. Leave time
// for authentication, database work and delivery of that response.
const DEADLINE = 90_000;

async function body(request: Request): Promise<Uint8Array<ArrayBuffer> | undefined> {
  if (!request.body) return undefined;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  const read = async () => {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > 65_536) throw new RangeError("body limit");
      chunks.push(part.value);
    }
    const joined = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.byteLength; }
    return joined;
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([read(), new Promise<never>((_, reject) => {
      timer = setTimeout(() => { void reader.cancel().catch(() => {}); reject(new Error("deadline")); }, 10_000);
    })]);
  } finally {
    clearTimeout(timer);
    void reader.cancel().catch(() => {});
  }
}

/** No caller-supplied identity crosses here. Registration alone authenticates
 * Workspace's assertion of the proxy-derived source address, never a user.
 */
export async function mcpPublicRequest(request: Request, operation: keyof typeof PATHS): Promise<Response> {
  const refused = (status: number) => new Response(null, { status, headers: { "Cache-Control": "no-store" } });
  const host = (request.headers.get("host") ?? new URL(request.url).host).toLowerCase();
  if (host !== "app.tervory.com" || homeRegion() !== "us-east") return refused(404);
  const discovery = operation === "resource" || operation === "resourceMcp" || operation === "authorization";
  if (!(operation === "mcp" ? ["POST", "GET", "DELETE"] : discovery ? ["GET"] : ["POST"]).includes(request.method)) return refused(405);
  const headers = new Headers();
  if (operation === "mcp") {
    for (const key of FORWARD) { const value = request.headers.get(key); if (value !== null) headers.set(key, value); }
  } else if (!discovery) {
    // OAuth token/revocation/registration forwards only the body and media type.
    headers.set("content-type", request.headers.get("content-type") ?? "");
    if (operation === "register") {
      // Coolify's Traefik strips untrusted X-Real-IP and sets it from the peer.
      // Do not read caller X-Forwarded-For or copy an incoming assertion/key.
      const source = request.headers.get("x-real-ip") ?? "";
      const tank = signinTank("us-east");
      if (!isIP(source)) return refused(400);
      if (!tank?.key) return refused(503);
      headers.set("x-tervory-oauth-source", source);
      headers.set("x-tank-key", tank.key);
    }
  }
  const stop = new AbortController();
  try {
    const payload = request.method === "POST" ? await body(request) : undefined;
    const response = await boundedFetch<Response>(new URL(PATHS[operation], TANK_URL), {
      method: request.method, headers, ...(payload ? { body: payload } : {}), cache: "no-store",
      signal: AbortSignal.any([request.signal, stop.signal]),
    }, DEADLINE, async value => value);
    const outgoing = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Accel-Buffering": "no" });
    for (const key of RETURN) { const value = response.headers.get(key); if (value !== null) outgoing.set(key, value); }
    const empty = [204, 205, 304].includes(response.status);
    return new Response(empty || !response.body ? null : boundedBody(response.body, { idleMs: 90_000, totalMs: DEADLINE }, () => stop.abort()),
      { status: response.status, headers: outgoing });
  } catch (error) { stop.abort(); return refused(error instanceof RangeError ? 413 : 502); }
}

export type AuthorizationInput = {
  response_type: string; client_id: string; redirect_uri: string; state: string;
  code_challenge: string; code_challenge_method: string; resource: string;
};

/** Same authenticated us-east approval path as /cli/device, server-side only.
 * UI wiring waits for approved Figma frame 1; this never accepts a browser identity.
 */
export async function mcpApproval(identity: SigninIdentity, input: AuthorizationInput,
                                  action?: "approve" | "deny"): Promise<{ status: number; body: unknown }> {
  const tank = signinTank("us-east");
  if (!tank || homeRegion() !== "us-east") return { status: 404, body: null };
  const url = new URL("/oauth/authorize", tank.url);
  if (!action) for (const [key, value] of Object.entries(input)) url.searchParams.set(key, value);
  try {
    return await boundedFetch(url, {
      method: action ? "POST" : "GET", headers: {
        "content-type": "application/json", "x-tank-key": tank.key,
        "x-tervory-user": identity.userId, "x-tervory-tenant": identity.tenantId,
        "x-tervory-role": identity.tenantAdmin === true ? "org:admin" : "", "x-tervory-home-region": "us-east",
      }, ...(action ? { body: JSON.stringify({ ...input, action }) } : {}),
      cache: "no-store", signal: AbortSignal.timeout(30_000),
    }, 30_000, async response => ({ status: response.status, body: await response.json().catch(() => null) }));
  } catch { return { status: 502, body: null }; }
}
