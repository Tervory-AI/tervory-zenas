import { auth, signinDirectory } from "@/lib/trigger-signin";
import { currentTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { isWorkspaceOrigin } from "@/lib/workspace-origin";
import { zenas } from "../../tank";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SEATS = new Set(["applicant", "respondent", "judge"]);
const ACTIONS = new Set(["edit", "lock", "start", "handover", "issue", "turn", "ai", "abandon", "publish", "pair"]);
const reply = (body: unknown, status = 200) => Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
type Context = { params: Promise<{ segments?: string[] }> };
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);

async function jsonBody(req: Request): Promise<Record<string, unknown> | null> {
  const reader = req.body?.getReader();
  if (!reader) return null;
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > 262144) { await reader.cancel(); return null; }
      chunks.push(next.value);
    }
    const data = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength; }
    const body: unknown = JSON.parse(new TextDecoder().decode(data));
    return object(body) ? body : null;
  } catch { return null; }
}

async function serve(req: Request, context: Context) {
  if (req.method === "POST" && !isWorkspaceOrigin(req)) return reply({ error: "origin_required" }, 403);
  const segments = (await context.params).segments ?? [];
  const isCreate = req.method === "POST" && segments.length === 1 && segments[0] === "hearings";
  const isCommand = req.method === "POST" && segments.length === 3 && segments[0] === "hearings"
    && UUID.test(segments[1]) && ACTIONS.has(segments[2]);
  const isRead = req.method === "GET" && (segments.length === 1 && ["hearings", "sources", "participants"].includes(segments[0])
    || segments.length === 2 && segments[0] === "hearings" && UUID.test(segments[1])
    || segments.length === 3 && segments[0] === "hearings" && UUID.test(segments[1]) && segments[2] === "fairness");
  if (!isCreate && !isCommand && !isRead) return reply({ error: "not_found" }, 404);
  try {
    const identity = await auth();
    if (!identity.userId) return reply({ error: "not_signed_in" }, 401);
    const tenant = await currentTenant();
    if (tenant.userId !== identity.userId) return reply({ error: "not_found" }, 404);
    const client = await signinDirectory();
    const ownMembership = await client.organizations.getOrganizationMembershipList({ organizationId: tenant.orgId, userId: [tenant.userId], limit: 1 });
    if (!ownMembership.data.some(m => m.publicUserData?.userId === tenant.userId)) return reply({ error: "not_found" }, 404);
    const founder = isFounder(tenant.userId);
    if (segments[0] === "participants") {
      if (!founder) return reply({ error: "not_found" }, 404);
      const members = await client.organizations.getOrganizationMembershipList({ organizationId: tenant.orgId, limit: 100 });
      return reply({ participants: members.data.flatMap(m => m.publicUserData ? [{
        user_id: m.publicUserData.userId,
        name: [m.publicUserData.firstName, m.publicUserData.lastName].filter(Boolean).join(" ") || m.publicUserData.identifier,
      }] : []) });
    }
    const home = process.env.TANK_REGION;
    if (home !== "us-east" && home !== "za") return reply({ error: "room_unavailable" }, 503);
    const params = new URLSearchParams({ home_region: home });
    const seat = new URL(req.url).searchParams.get("seat");
    if (seat) {
      if (!SEATS.has(seat)) return reply({ error: "invalid_seat" }, 400);
      params.set("seat", seat);
    }
    let body: Record<string, unknown> | null = null;
    if (req.method === "POST") {
      body = await jsonBody(req);
      if (!body || typeof body.request_id !== "string" || !UUID.test(body.request_id)) return reply({ error: "invalid_request" }, 400);
      const keys = isCreate ? ["request_id", "worked_id", "seats"] : ["request_id", "revision", "payload"];
      if (Object.keys(body).some(k => !keys.includes(k))) return reply({ error: "invalid_request" }, 400);
      if (isCreate && (typeof body.worked_id !== "string" || !UUID.test(body.worked_id))) return reply({ error: "invalid_source" }, 400);
      if (isCommand && (!Number.isSafeInteger(body.revision) || Number(body.revision) < 0 || !object(body.payload))) return reply({ error: "invalid_request" }, 400);
      if ((isCreate || !["turn", "ai"].includes(segments[2])) && !founder) return reply({ error: "operator_required" }, 403);
      let controllers: unknown[] = [];
      if (isCreate) {
        if (!object(body.seats) || Object.keys(body.seats).length !== 3 || Object.keys(body.seats).some(s => !SEATS.has(s))) return reply({ error: "invalid_seats" }, 400);
        controllers = Object.values(body.seats);
      } else if (segments[2] === "handover") {
        const payload = body.payload as Record<string, unknown>;
        if (typeof payload.seat !== "string" || !SEATS.has(payload.seat)) return reply({ error: "invalid_seat" }, 400);
        controllers = [payload.controller];
      }
      for (const controller of controllers) {
        if (!object(controller) || !["ai", "human"].includes(String(controller.kind))) return reply({ error: "invalid_controller" }, 400);
        if (controller.kind === "human") {
          if (typeof controller.user_id !== "string" || !controller.user_id || controller.user_id.length > 200) return reply({ error: "invalid_controller" }, 400);
          const membership = await client.organizations.getOrganizationMembershipList({ organizationId: tenant.orgId, userId: [controller.user_id], limit: 1 });
          if (!membership.data.some(m => m.publicUserData?.userId === controller.user_id)) return reply({ error: "participant_not_in_workspace" }, 403);
        } else if (controller.user_id != null) return reply({ error: "invalid_controller" }, 400);
      }
    } else if (segments[0] === "sources" && !founder) return reply({ error: "not_found" }, 404);
    const path = segments[0] === "sources" ? "moot/hearing-sources" : "moot/" + segments.join("/");
    const response = await zenas(`${path}?${params}`, { method: req.method, signal: req.signal,
      headers: { "X-Moot-Actor": tenant.userId, "X-Moot-Tenant": tenant.tenantId },
      ...(body ? { body: JSON.stringify(body) } : {}) });
    // Only the room's structured code/projection is returned, never transport diagnostics.
    if (!response.ok) {
      const data = await response.json().catch(() => null) as { detail?: unknown } | null;
      const code = typeof data?.detail === "string" && /^[a-z_]{1,80}$/.test(data.detail) ? data.detail : "room_unavailable";
      return reply({ error: code }, response.status >= 500 ? 503 : response.status);
    }
    return reply(await response.json(), response.status);
  } catch { return reply({ error: "room_unavailable" }, 503); }
}

export const GET = serve;
export const POST = serve;
