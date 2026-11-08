import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";
import { regionTank, type Region } from "@/app/api/zenas/tank";

// The only way the Workspace talks to a model: through Tank.
// Server-side only (uses TANK_API_KEY). Never import from a client component.

const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

export async function mobileRequest(path: string, body?: unknown, identity?: { tenantId: string; userId: string; tenantAdmin?: boolean }, session?: string): Promise<Response> {
  const headers = new Headers({ "x-tank-key": TANK_API_KEY, "Content-Type": "application/json" });
  if (identity) {
    headers.set("x-tervory-tenant", identity.tenantId);
    headers.set("x-tervory-user", identity.userId);
    headers.set("x-tervory-role", identity.tenantAdmin ? "org:admin" : "");
  }
  if (session) headers.set("x-mobile-session", session);
  return boundedFetch(`${TANK_URL}/v1/mobile/${path}`, { method: body === undefined ? "GET" : "POST", headers,
    body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store" }, 30_000, async response =>
    new Response([204, 205, 304].includes(response.status) ? null : await response.arrayBuffer(), { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } }));
}

// Bounded server-side fetches for the CLI corridors (review of #506, B1). Kept in this
// import-free module because several test harnesses load lib/tank.ts on its own.
//
// Node 22 / undici 6.29.1: a fetch made with redirect:"error" whose body stalls after
// the headers never aborts once a GC has run, whatever the signal. So these helpers:
//  - use redirect:"manual" and refuse any 3xx / opaqueredirect (still no redirects), and
//  - race the whole call, body read included, against a hard deadline that aborts the
//    request and cancels the body when it fires, without relying on undici honouring it.
// A signal is always set, which also keeps Next's render-time dedupe fetch (tee'd GET
// bodies whose cancel() never settles) out of the way.

export class DeadlineError extends Error { constructor() { super("deadline"); this.name = "DeadlineError"; } }
export class RedirectError extends Error {
  status: number;
  constructor(status: number) { super(`redirect ${status}`); this.name = "RedirectError"; this.status = status; }
}

export const isRedirect = (response: Response) =>
  response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400);

/** fetch(url, init) then read(response), all within timeoutMs. init.signal still aborts early. */
export async function boundedFetch<T>(url: URL | string, init: RequestInit, timeoutMs: number,
                                      read: (response: Response) => Promise<T>): Promise<T> {
  if (init.signal?.aborted) throw init.signal.reason;
  const controller = new AbortController();
  const signal = init.signal ? AbortSignal.any([init.signal, controller.signal]) : controller.signal;
  let response: Response | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new DeadlineError());
      controller.abort();
      try { response?.body?.cancel().catch(() => undefined); } catch { /* locked by the reader */ }
    }, timeoutMs);
  });
  let callerAbort: (() => void) | undefined;
  const cancelled = new Promise<never>((_, reject) => {
    if (!init.signal) return;
    const caller = init.signal;
    callerAbort = () => {
      reject(caller.reason);
      controller.abort();
      try { response?.body?.cancel().catch(() => undefined); } catch { /* locked reader */ }
    };
    caller.addEventListener("abort", callerAbort, { once: true });
    if (caller.aborted) callerAbort();
  });
  const work = (async () => {
    if (signal.aborted) throw signal.reason;
    response = await fetch(url, { ...init, redirect: "manual", signal });
    if (isRedirect(response)) {
      await response.body?.cancel().catch(() => undefined);
      throw new RedirectError(response.status);
    }
    return read(response);
  })();
  work.catch(() => undefined);
  try { return await Promise.race([work, deadline, cancelled]); }
  finally {
    clearTimeout(timer);
    if (callerAbort) init.signal?.removeEventListener("abort", callerAbort);
  }
}

/**
 * Pass an upstream body through, failing it if no bytes arrive for idleMs (Tank sends a
 * heartbeat every 0.25 s while a turn runs), if it is not finished within totalMs (when
 * given), or if the downstream cancels. Each read is raced against a timer, so a stalled
 * upstream cannot pin the request forever even when undici ignores its abort signal.
 * onStop is required: it must abort the upstream fetch (its AbortController), so a stop
 * closes the connection to Tank instead of only cancelling the reader.
 */
export function boundedBody(body: ReadableStream<Uint8Array>, limits: { idleMs: number; totalMs?: number },
                            onStop: () => void): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  const end = limits.totalMs === undefined ? Infinity : Date.now() + limits.totalMs;
  let stopped = false;
  const stop = () => { if (stopped) return; stopped = true; onStop(); reader.cancel().catch(() => undefined); };
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const wait = Math.max(0, Math.min(limits.idleMs, end - Date.now()));
        const item = await Promise.race([
          reader.read(),
          new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new DeadlineError()), wait); }),
        ]);
        if (item.done) controller.close(); else controller.enqueue(item.value);
      } catch (error) {
        stop(); controller.error(error);
      } finally { clearTimeout(timer); }
    },
    cancel() { stop(); },
  });
}

export const DEVICE_PROXY_TIMEOUT_MS = 10_000;
export const PROXY_HEADERS_TIMEOUT_MS = 300_000;
export const PROXY_IDLE_MS = 120_000;
/** Total for a non-stream Tank answer body once its headers arrived (JSON, files up to 5 MB). */
export const COMMAND_BODY_TIMEOUT_MS = 60_000;
/**
 * Bounds for commandRequest (the /chats loop). Headers: 300 s, because card confirmations
 * answer only once the action ran. Event streams: 120 s idle (Tank heartbeats every
 * 0.25 s). Other bodies: 60 s total. Exported as an object so tests can shorten them.
 */
export const COMMAND_LIMITS = { headersMs: PROXY_HEADERS_TIMEOUT_MS, idleMs: PROXY_IDLE_MS, bodyMs: COMMAND_BODY_TIMEOUT_MS };

const DEVICE_ERRORS = new Set(["authorization_pending", "slow_down", "access_denied", "expired_token", "invalid_grant", "invalid_request"]);

/**
 * CLI device sign-in corridor (unauthenticated: create a code, poll, refresh,
 * revoke). This Workspace fronts only its own us-east box; ZA CLIs call the ZA
 * Tank directly. Approval is never reachable here: it needs a SigninDirectory session.
 */
async function personalDeviceRequest(request: Request, path: string[], timeoutMs = DEVICE_PROXY_TIMEOUT_MS): Promise<Response> {
  const headers = { "Cache-Control": "no-store" };
  const refused = (status: number) => Response.json({}, { status, headers });
  const host = (request.headers.get("host") ?? new URL(request.url).host).toLowerCase();
  if (host !== "app.tervory.com") return refused(404);
  const action = path.length === 2 ? path[1] : "";
  if (request.method !== "POST" || !["code", "token", "refresh", "revoke"].includes(action)) return refused(404);
  if (new URL(request.url).search) return refused(400);
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) return refused(415);
  const text = await request.text().catch(() => null);
  if (text === null) return refused(400);
  if (text.length > 4096) return refused(413);
  const forward: Record<string, string> = { "Content-Type": "application/json" };
  if (action === "revoke") {
    const bearer = /^Bearer (tv1\.us-east\.[0-9a-f]{32}\.[A-Za-z0-9_-]{43})$/i.exec(request.headers.get("authorization") ?? "");
    if (bearer) forward.Authorization = `Bearer ${bearer[1]}`;
  }
  try {
    // Never select TANK_URL_ZA or a caller URL; this Workspace talks to its own box.
    // Hard deadline over the whole call, body included (boundedFetch above).
    const { response, payload } = await boundedFetch(new URL(`/v1/command/device/${action}`, TANK_URL), {
      method: "POST", body: text, headers: forward, signal: request.signal, cache: "no-store",
    }, timeoutMs, async response => {
      const type = response.headers.get("content-type") ?? "";
      const payload: unknown = type.startsWith("application/json") ? await response.json().catch(() => null)
        : (await response.body?.cancel().catch(() => undefined), null);
      return { response, payload };
    });
    if (response.ok && payload && typeof payload === "object") return Response.json(payload, { status: response.status, headers });
    const error = payload && typeof payload === "object" ? (payload as { error?: unknown }).error : undefined;
    if (typeof error === "string" && DEVICE_ERRORS.has(error)) {
      return Response.json({ error }, { status: response.status === 429 ? 429 : 400, headers });
    }
    return refused(response.ok ? 502 : response.status);
  } catch { return refused(502); }
}

/** CLI-only public corridor. No SigninDirectory cookies or service-key identity assertions. */
export async function personalCommandRequest(request: Request, path: string[],
                                             limits: { headersMs: number; idleMs: number; deviceMs: number; bodyMs?: number } = { headersMs: PROXY_HEADERS_TIMEOUT_MS, idleMs: PROXY_IDLE_MS, deviceMs: DEVICE_PROXY_TIMEOUT_MS, bodyMs: COMMAND_BODY_TIMEOUT_MS }): Promise<Response> {
  const headers = { "Cache-Control": "no-store" };
  const refused = (status: number) => Response.json({}, { status, headers });
  if (path[0] === "device") return personalDeviceRequest(request, path, limits.deviceMs);
  const authorization = request.headers.get("authorization") ?? "";
  const token = /^Bearer (tv1\.(us-east|za)\.[0-9a-f]{32}\.[A-Za-z0-9_-]{43})$/i.exec(authorization);
  if (!token) return refused(401);
  const host = (request.headers.get("host") ?? new URL(request.url).host).toLowerCase();
  const region = host === "app.tervory.com" ? "us-east" : host === "za.tervory.com" ? "za" : null;
  if (!region || token[2] !== region) return refused(403);
  const id = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
  const target = path.join("/");
  const routes: Record<string, RegExp> = {
    GET: new RegExp(`^(tools|code-mcp-policy|code-models|code-sessions|code-sessions/${id}|threads|threads/${id}(/events)?|tasks/${id}/receipts)$`),
    POST: new RegExp(`^(code-attachments|complete|threads|threads/${id}/(turn|actions|code-turn|code-stream)|cards/${id}|build)$`),
    PATCH: new RegExp(`^threads/${id}$`),
    PUT: /^code-mcp-policy$/,
  };
  const agentRoutes: Record<string, RegExp[]> = {
    GET: [/^window-agents\/jobs$/, new RegExp(`^window-agents/jobs/${id}/receipt$`), /^window-agents\/vault\/github$/],
    POST: [/^window-agents\/jobs$/, new RegExp(`^window-agents/jobs/${id}/cancel$`),
      /^window-agents\/reviews$/, /^window-agents\/webhooks$/, /^window-agents\/vault\/github$/],
  };
  if (path.some(segment => !/^[a-z0-9-]+$/.test(segment)) ||
      !(routes[request.method]?.test(target) || agentRoutes[request.method]?.some(route => route.test(target)))) return refused(404);
  const query = new URL(request.url).searchParams;
  for (const key of query.keys()) if (key !== "after" || !target.endsWith("/events")) return refused(400);
  const after = query.getAll("after");
  if (after.length > 1 || (after.length && !/^\d{1,15}$/.test(after[0]))) return refused(400);
  // Never select TANK_URL_ZA or a caller URL; this Workspace talks to its own box.
  const url = new URL(target.startsWith("window-agents/") ? `/v1/${target}` : `/v1/command/${target}`, TANK_URL);
  if (after.length) url.searchParams.set("after", after[0]);
  let body: Uint8Array | undefined;
  if (request.method !== "GET") {
    if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) return refused(415);
    const reader = request.body?.getReader();
    if (reader) {
      const chunks: Uint8Array[] = []; let size = 0;
      try {
        while (true) {
          const item = await reader.read(); if (item.done) break;
          size += item.value.byteLength;
          if (size > (target === "code-attachments" ? 7_100_000 : 2_000_000)) return refused(413);
          chunks.push(item.value);
        }
      } catch { return refused(400); }
      finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      body = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    }
  }
  // Aborted when the body is stopped (idle, total deadline, or the client went away), so
  // the connection to Tank is closed, not just the reader cancelled.
  const stop = new AbortController();
  const signal = AbortSignal.any([request.signal, stop.signal]);
  try {
    // The deadline covers the headers (card confirmations answer only once the action ran);
    // the body is then passed through with an idle bound so a stalled Tank cannot pin it.
    const response = await boundedFetch(url, {
      method: request.method, body: body as BodyInit | undefined, signal,
      headers: { Authorization: `Bearer ${token[1]}`, "Content-Type": "application/json" },
      cache: "no-store",
    }, limits.headersMs, async response => response);
    if (!response.ok) { stop.abort(); await response.body?.cancel().catch(() => undefined); return refused(response.status); }
    const type = response.headers.get("content-type") ?? "";
    if (!type.startsWith("application/json") && !type.startsWith("text/event-stream")) {
      stop.abort(); await response.body?.cancel().catch(() => undefined); return refused(502);
    }
    // Event streams: idle bound (Tank heartbeats). JSON: idle bound and a total deadline.
    const bodyLimits = type.startsWith("text/event-stream") ? { idleMs: limits.idleMs }
      : { idleMs: limits.idleMs, totalMs: limits.bodyMs ?? COMMAND_BODY_TIMEOUT_MS };
    return new Response(response.body && boundedBody(response.body, bodyLimits, () => stop.abort()), { status: response.status, headers: {
      ...headers, "Content-Type": type, "X-Accel-Buffering": "no", "X-Content-Type-Options": "nosniff",
    } });
  } catch { stop.abort(); return refused(502); }
}

/** Founder memory inspection; identity comes from the server's goldIdentity check. */
export function memoryRecord(identity: { tenantId: string; userId: string; role: string }, id: string): Promise<Response> {
  return tenantFetch(identity.tenantId, new URL(`/v1/memory/records/${encodeURIComponent(id)}`, TANK_URL), {
    method: "GET", cache: "no-store", redirect: "error",
    headers: {
      "X-Tank-Key": TANK_API_KEY, "X-Tervory-User": identity.userId,
      "X-Tervory-Tenant": identity.tenantId, "X-Tervory-Role": identity.role,
    },
  });
}

/** Founder JSON inspection only; caller identity is resolved by Workspace. */
export function rigPriceHistory(identity: { tenantId: string; userId: string }, query: URLSearchParams): Promise<Response> {
  const url = new URL("/v1/agents/rig/price-history", TANK_URL);
  for (const key of ["tenant_id", "product_id", "offset", "limit"]) {
    const value = query.get(key);
    if (value !== null) url.searchParams.set(key, value);
  }
  if (!url.searchParams.has("tenant_id")) url.searchParams.set("tenant_id", identity.tenantId);
  return tenantFetch(query.get("tenant_id") ?? identity.tenantId, url, { cache: "no-store", redirect: "error", headers: {
    "X-Tank-Key": TANK_API_KEY, "X-Tervory-User": identity.userId, "X-Tervory-Tenant": identity.tenantId,
  } });
}

/** API-only reports. Identity is supplied by SigninDirectory on the server, never the browser. */
export async function caseStudyRequest(identity: { tenantId: string; userId: string }, path = "", query = new URLSearchParams(), signal?: AbortSignal): Promise<Response> {
  const allowed = ["", "ensure", "clients", "base", "tiger", "market_lab", "rig", "zenas", "trigger", "tisan", "radix", "autumbra"];
  if (!allowed.includes(path)) throw new Error("Unknown request.");
  const url = new URL(`/v1/case-studies${path ? `/${path}` : ""}`, TANK_URL);
  for (const key of ["tenant_id", "start", "end", "offset", "limit"]) {
    const value = query.get(key);
    if (value !== null) url.searchParams.set(key, value);
  }
  const tenant = query.get("tenant_id") ?? identity.tenantId;
  const init: RequestInit = {
    method: path === "ensure" ? "POST" : "GET", cache: "no-store",
    // Observe ensure redirects as non-retryable HTTP failures; never follow them.
    redirect: path === "ensure" ? "manual" : "error", signal,
    headers: { "X-Tank-Key": TANK_API_KEY, "X-Tervory-User": identity.userId, "X-Tervory-Tenant": identity.tenantId },
  };
  if (path === "radix") return tenantBoundedFetch(tenant, url, init, 30_000,
    readRadixResponse);
  return tenantFetch(tenant, url, init);
}

// Optional bookkeeping, never an authorization or squad grant. Durable SigninDirectory
// identity + Tank grants/ledger are the retry source on later requests, including
// after process restarts. This bounded per-process guard only limits load.
const reportRegistrations = new Map<string, { failures: number; next: number; pending?: Promise<void> }>();

export async function ensureCaseStudyReports(identity: { tenantId: string; userId: string }): Promise<void> {
  // Reject malformed runtime configuration before classifying fetch failures.
  const endpoint = new URL("/v1/case-studies/ensure", TANK_URL);
  if (!["http:", "https:"].includes(endpoint.protocol)) throw new Error("Invalid Tank protocol");
  const key = JSON.stringify([identity.tenantId, identity.userId]);
  const previous = reportRegistrations.get(key);
  if (previous?.pending) return previous.pending;
  if (previous && previous.next > Date.now()) return;
  if (!previous && reportRegistrations.size >= 1000) {
    for (const [oldKey, entry] of reportRegistrations) {
      if (!entry.pending && entry.next <= Date.now()) reportRegistrations.delete(oldKey);
    }
    // Defer bookkeeping rather than evict in-flight requests or grow without bound.
    if (reportRegistrations.size >= 1000) return;
  }
  const state = { failures: previous?.failures ?? 0, next: 0, pending: undefined as Promise<void> | undefined };
  reportRegistrations.set(key, state);
  state.pending = (async () => {
    let status: number | undefined;
    try {
      const response = await caseStudyRequest(identity, "ensure", new URLSearchParams(), AbortSignal.timeout(1500));
      status = response.status;
      if (!response.ok) throw new Error(`Case-study registration rejected (${status})`);
      if (state.failures) console.info("case_study_registration_recovered", { tenantId: identity.tenantId });
      state.failures = 0;
      state.next = Date.now() + 30_000;
    } catch (error) {
      // Never contain security, configuration, programming or contract failures.
      const transport = error instanceof TypeError || (error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name));
      if (!(status === 408 || status === 429 || (status !== undefined && status >= 500) || (status === undefined && transport))) {
        reportRegistrations.delete(key);
        throw error;
      }
      state.failures += 1;
      const retryMs = Math.min(60_000, 1000 * 2 ** Math.min(state.failures - 1, 6));
      state.next = Date.now() + retryMs;
      // Never log response bodies, credentials, user data or transport messages.
      console.warn("case_study_registration_deferred", { tenantId: identity.tenantId, status: status ?? "transport", retryMs });
    } finally {
      state.pending = undefined;
    }
  })();
  return state.pending;
}

/** Base product only. Identity and allowed operations are resolved server-side. */
export async function baseProductRequest(tenantId: string, path: string, query: URLSearchParams = new URLSearchParams(), body?: Record<string, unknown>): Promise<Response> {
  const parameters = new URLSearchParams(query);
  parameters.set("tenant_id", tenantId);
  return tenantBoundedFetch(tenantId, `${TANK_URL}/v1/base-product/${path}${body ? "" : `?${parameters}`}`, {
    method: body ? "POST" : "GET", cache: "no-store", redirect: "error",
    headers: { "Content-Type": "application/json", "X-Tank-Key": TANK_API_KEY },
    ...(body ? { body: JSON.stringify({ ...body, tenant_id: tenantId }) } : {}),
  }, 30_000, async response => new Response([204, 205, 304].includes(response.status) ? null : await response.arrayBuffer(), { status: response.status, headers: response.headers }));
}

// RIG-5: all 3D Workspace traffic, including private previews, stays server-side.
export type RigThreeDOperation = "capabilities" | "dashboard" | "submit" | "approve" | "mounts" | "reset" | "preview" | "runtime";
export async function rigThreeDRequest(
  tenantId: string, operation: RigThreeDOperation, payload: Record<string, unknown> = {}, actor = "",
): Promise<Response> {
  const response = await tenantFetch(tenantId, `${TANK_URL}/v1/rig/3d/workspace/${operation}`, {
    method: "POST", cache: "no-store", redirect: "error",
    headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY, "x-tervory-user": actor },
    body: JSON.stringify({ tenant_id: tenantId, payload }),
  });
  if (!response.ok) throw new Error("The 3D request could not be completed. Check the details and try again.");
  return response;
}

// Founder-only Ghost corridor. Identity is resolved by the server action, never the browser.
export type GhostIncident = {
  id: string; target: string; signal: string; origin: string; state: string;
  summary?: string; created_at: string; cost_usd: number;
  steps: { step: string; ledger_id: string; cost_usd: number }[];
  previous?: { cause: string; fix: string }[];
  brief?: { title: string; cause: string; status: string; instructions: string[] };
};
export type GhostPanel = {
  incidents: GhostIncident[];
  monitor: { running: boolean; unavailable_checks: number };
  integrations: Record<string, string>;
};
export function ghostPanel(userId: string): Promise<GhostPanel> {
  return tank<GhostPanel>("/v1/ghost/incidents", { method: "GET", headers: { "X-Tervory-Admin": userId } });
}
export function ghostLook(userId: string, target: string, signal: string): Promise<GhostIncident> {
  return tank<GhostIncident>("/v1/ghost/look", {
    method: "POST", headers: { "X-Tervory-Admin": userId }, body: JSON.stringify({ target, signal }),
  });
}
export function ghostResolve(userId: string, id: string, cause: string, fix: string): Promise<GhostIncident> {
  return tank<GhostIncident>(`/v1/ghost/incidents/${encodeURIComponent(id)}/resolve`, {
    method: "POST", headers: { "X-Tervory-Admin": userId }, body: JSON.stringify({ cause, fix }),
  });
}

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type RouteResult = {
  ledger_id: string;
  model: string;
  rung: number;
  content: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  latency_ms: number;
};

export const SKALA_SERVER_TIMEOUT_MS = { value: 25_000 };
async function tank<T>(path: string, init: RequestInit): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json");
  if (path.startsWith("/v1/base/")) {
    const tenant = requestTenant(path, init);
    const read = async (response: Response) => {
      if (!response.ok) throw new Error("Base could not complete this request.");
      return await response.json() as T;
    };
    if (tenant) return tenantBoundedFetch(tenant, new URL(path, TANK_URL), {...init, headers}, 30_000, read);
    if (!["/v1/base/engines", "/v1/base/context-dev/trial"].includes(path)) throw new Error("Sign in to your workspace.");
    headers.set("x-tank-key", TANK_API_KEY);
    return boundedFetch(new URL(path, TANK_URL), {...init, headers, redirect: "manual"},
      path.endsWith("/trial") ? 300_000 : 30_000, read);
  }
  const goldTenant = path.startsWith("/v1/gold/") ? headers.get("x-tervory-tenant") : null;
  const tenant = requestTenant(path, init) || (goldTenant !== "00000000-0000-0000-0000-000000000001" ? goldTenant : null);
  const read = async (res: Response): Promise<T> => {
    if (!res.ok) {
      throw new Error("Your request could not be completed. Please try again.");
    }
    return await res.json() as T;
  };
  if (/^\/v1\/(skala|gold)(?:\/|$)/.test(path)) {
    if (!TANK_API_KEY.trim()) throw new Error("Missing setting: TANK_API_KEY.");
    // One attempt for both reads and writes; the client may explicitly retry.
    if (tenant) return tenantBoundedFetch(tenant, new URL(path, TANK_URL), {...init, headers}, SKALA_SERVER_TIMEOUT_MS.value, read);
    headers.set("x-tank-key", TANK_API_KEY);
    return boundedFetch(new URL(path, TANK_URL), {...init, headers, cache: "no-store"}, SKALA_SERVER_TIMEOUT_MS.value, read);
  }
  return read(await tenantFetch(tenant, new URL(path, TANK_URL), {...init, headers}));
}

export type RosterModel = {
  name: string;
  providerModel: string;
  inputPerM: number | null;
  outputPerM: number | null;
};

function priceOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** The roster LiteLLM serves, cheapest first, with USD per million tokens. No key and no missing-key flag. */
/** The /chats loop reads the roster on every request, so it gets a hard deadline, body included. */
export const ROSTER_TIMEOUT_MS = { value: 15_000 };
export async function listRoster(): Promise<RosterModel[]> {
  const rows = await boundedFetch(`${TANK_URL}/v1/models`, {
    method: "GET", cache: "no-store", headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY },
  }, ROSTER_TIMEOUT_MS.value, async res => {
    if (!res.ok) { const text = await res.text(); throw new Error("Your request could not be completed. Please try again."); }
    return (await res.json()) as unknown;
  });
  if (!Array.isArray(rows)) return [];
  const models: RosterModel[] = [];
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const record = row as Record<string, unknown>;
    const name = typeof record.name === "string" ? record.name.trim() : "";
    if (!name) continue;
    models.push({
      name,
      providerModel: typeof record.provider_model === "string" ? record.provider_model : "",
      inputPerM: priceOrNull(record.input_usd_per_m),
      outputPerM: priceOrNull(record.output_usd_per_m),
    });
  }
  return models;
}

export function route(tenantId: string, taskType: string, messages: ChatMessage[], agent = "tier0"): Promise<RouteResult> {
  return tank<RouteResult>("/v1/route", {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, agent, task_type: taskType, messages }),
  });
}

// ---- tasks: the board ----

export type TaskStatus = "queued" | "running" | "done" | "failed";

export type Task = {
  id: string;
  tenant_id: string;
  created_by: string | null;
  agent: string;
  task_type: string;
  title: string;
  status: TaskStatus;
  result: string | null;
  error: string | null;
  ledger_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  // joined from the ledger row once the task has run
  model?: string | null;
  rung?: number | null;
  cost_usd?: number | string | null;
  baseline_cost_usd?: number | string | null;
  baseline_status?: string | null;
  latency_ms?: number | null;
  feedback?: "accepted" | "rejected" | null;
};

export function createTask(
  tenantId: string,
  createdBy: string,
  title: string,
  taskType: string,
  messages: ChatMessage[],
  agent = "tier0",
): Promise<Task> {
  return tank<Task>("/v1/tasks", {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, created_by: createdBy, title, task_type: taskType, messages, agent }),
  });
}

async function readRadixResponse(response: Response): Promise<Response> {
  const bytes = await response.arrayBuffer();
  return new Response(response.status === 204 || response.status === 205 ? null : bytes,
    {status: response.status, headers: response.headers});
}

// Radix interactive reads/writes use the existing regional, whole-body deadline.
// No automatic write retry: the caller checks its records after uncertainty.
export const radixTaskStore = {
  list: (tenant: string, limit: number) => radixTaskRequest<Task[]>(tenant, `/v1/tasks?tenant_id=${encodeURIComponent(tenant)}&limit=${limit}`, {method: "GET"}),
  get: (tenant: string, id: string) => radixTaskRequest<Task>(tenant, `/v1/tasks/${encodeURIComponent(id)}?tenant_id=${encodeURIComponent(tenant)}`, {method: "GET"}),
  create: (tenant: string, user: string, title: string, taskType: string, messages: ChatMessage[], agent: string) =>
    radixTaskRequest<Task>(tenant, "/v1/tasks", {method: "POST", body: JSON.stringify({tenant_id: tenant, created_by: user, title, task_type: taskType, messages, agent})}),
};
function radixTaskRequest<T>(tenant: string, path: string, init: RequestInit): Promise<T> {
  return tenantBoundedFetch(tenant, new URL(path, TANK_URL), {...init, headers: {"content-type": "application/json"}}, 30_000, async response => {
    if (!response.ok) {
      await response.body?.cancel().catch(() => {});
      throw new Error("Radix could not reach your records. Please try again.");
    }
    return await response.json() as T;
  });
}

export function listTasks(tenantId: string, limit = 50): Promise<Task[]> {
  return tank<Task[]>(`/v1/tasks?tenant_id=${tenantId}&limit=${limit}`, { method: "GET" });
}

export function getTask(tenantId: string, taskId: string): Promise<Task> {
  const query = new URLSearchParams({ tenant_id: tenantId });
  return tank<Task>(`/v1/tasks/${encodeURIComponent(taskId)}?${query}`, { method: "GET" });
}

// ---- feedback: the outcome column ----

export function sendFeedback(
  tenantId: string,
  ledgerId: string,
  feedback: "accepted" | "rejected",
  by: string,
  note?: string,
  verdict?: "useful" | "not_useful" | "wrong",
): Promise<{ ok: true }> {
  return tank<{ ok: true }>(`/v1/ledger/${ledgerId}/feedback`, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, feedback, by, note: note ?? null, verdict }),
  });
}

// ---- ledger ----

export type LedgerSummary = {
  unpriced: number;
  tenant_id: string;
  days: number;
  calls: number;
  delivered: number;
  accepted: number;
  rejected: number;
  acceptance_rate: number | null;
  cost_usd: number;
  cost_per_delivered_usd: number | null;
  cost_per_accepted_usd: number | null;
  by_model: {
    unpriced: number;
    model: string;
    rung: number;
    calls: number;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number | null;
    delivered: number;
    accepted: number;
    rejected: number;
  }[];
};

export type LedgerFailure = { day: string; task_type: string; skill: string; reason: string; calls: number; avg_ms: number | string | null; example: string | null };
export function ledgerFailures(tenantId: string, days = 30): Promise<LedgerFailure[]> {
  return tank<LedgerFailure[]>(`/v1/ledger/failures?tenant_id=${tenantId}&days=${days}`, { method: "GET" });
}

export function ledgerSummary(tenantId: string, days = 30): Promise<LedgerSummary> {
  return tank<LedgerSummary>(`/v1/ledger/summary?tenant_id=${tenantId}&days=${days}`, { method: "GET" });
}

// ---- Skala: skills ----

export type SkillState = "candidate" | "staging" | "approved" | "retired";
export type Skill = {
  id: string;
  version: string;
  agent: string;
  rung: number;
  task_type: string | null;
  description: string;
  state: SkillState;
  eval_threshold: number | string;
  cases: number;
  last_eval_score: number | string | null;
  last_eval_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  present: boolean;
  calls_30d?: number;
  avg_cost_usd?: number | string | null;
};

export type EvalResult = {
  skill: string;
  passed: number;
  total: number;
  score: number;
  threshold: number;
  cost_usd: number;
  state: SkillState;
  gate: "passed" | "failed";
  details: { case: number; name: string | null; passed: boolean; why: string; goal: unknown; result: unknown }[];
};

export function listSkills(bounded = false): Promise<Skill[]> {
  if (bounded) return boundedFetch(new URL("/v1/skala", TANK_URL), { method: "GET", cache: "no-store", redirect: "manual",
    headers: { "x-tank-key": TANK_API_KEY } }, 5_000, async response => {
      if (!response.ok) throw new Error("Base could not complete this request.");
      return await response.json() as Skill[];
    });
  return tank<Skill[]>("/v1/skala", { method: "GET" });
}
export function runSkillEvals(id: string, version: string, by: string, requestId?: string): Promise<EvalResult> {
  return tank<EvalResult>(`/v1/skala/${encodeURIComponent(id)}/${encodeURIComponent(version)}/evals`, {
    method: "POST", headers: requestId ? {"idempotency-key": requestId} : undefined,
    body: JSON.stringify({ by }),
  });
}
// Owner testing: run one skill version on one input, without touching any records.
export function trySkill(tenantId: string, id: string, version: string, input: Record<string, unknown>) {
  return tank<Record<string, unknown>>(`/v1/skala/${encodeURIComponent(id)}/run`, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, version, input, preview: true }),
  });
}
export function approveSkill(id: string, version: string, by: string): Promise<{ ok: true }> {
  return tank<{ ok: true }>(`/v1/skala/${encodeURIComponent(id)}/${encodeURIComponent(version)}/approve`, {
    method: "POST",
    body: JSON.stringify({ by }),
  });
}
export function retireSkill(id: string, version: string, by: string): Promise<{ ok: true }> {
  return tank<{ ok: true }>(`/v1/skala/${encodeURIComponent(id)}/${encodeURIComponent(version)}/retire`, {
    method: "POST",
    body: JSON.stringify({ by }),
  });
}

// ---- Base: records ----

export type BaseRecord = {
  id: string;
  task_id: string | null;
  batch: string | null;
  raw: Record<string, unknown>;
  profile: Record<string, { value: unknown; source: string; confidence: number }>;
  passes: Record<string, Record<string, unknown>>;
  confidence: number;
  status: "queued" | "running" | "done" | "failed";
  error: string | null;
  created_at: string;
};

export function baseRun(
  tenantId: string, createdBy: string, batch: string, records: Record<string, unknown>[], preview: boolean,
  engine?: string | null, splitByState = false,
) {
  return tank<{ task: Task; tasks: Task[]; records: number; batches: number; skills: Record<string, string> }>("/v1/base/run", {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, created_by: createdBy, batch, records, preview, engine: engine || null, split_by_state: splitByState }),
  });
}
export function baseEngines(): Promise<string[]> {
  return tank<string[]>("/v1/base/engines", { method: "GET" });
}
export function runContextTrial(userId: string): Promise<{
  records_sent: number; message?: string; comparison_records?: Pick<BaseRecord, "profile" | "passes">[];
}> {
  return tank("/v1/base/context-dev/trial", {
    method: "POST", headers: { "x-tervory-admin": userId },
  });
}
export function baseRecords(tenantId: string, opts: { taskId?: string; batch?: string; limit?: number; offset?: number } = {}): Promise<BaseRecord[]> {
  const q = new URLSearchParams({ tenant_id: tenantId, limit: String(opts.limit ?? 200), offset: String(opts.offset ?? 0) });
  if (opts.taskId) q.set("task_id", opts.taskId);
  if (opts.batch) q.set("batch", opts.batch);
  return tank<BaseRecord[]>(`/v1/base/records?${q}`, { method: "GET" });
}

export type BaseBatch = {
  batch: string; started: string; total: number; done: number; failed: number; pending: number;
  ready: number; aware: number; review: number; requeue: number; with_site: number; with_email: number;
  model_skipped?: number;
};
export function baseRetry(tenantId: string, batch: string): Promise<{ requeued: number }> {
  return tank<{ requeued: number }>("/v1/base/retry", { method: "POST", body: JSON.stringify({ tenant_id: tenantId, batch }) });
}
export function baseRerun(
  tenantId: string, batch: string, flag = "no_website", preview?: boolean, skill?: string,
): Promise<{ requeued: number }> {
  return tank<{ requeued: number }>("/v1/base/rerun", {
    method: "POST", body: JSON.stringify({ tenant_id: tenantId, batch, flag, preview: preview ?? null, skill: skill || null }),
  });
}
export function baseBatches(tenantId: string): Promise<BaseBatch[]> {
  return tank<BaseBatch[]>(`/v1/base/batches?tenant_id=${tenantId}`, { method: "GET" });
}

// ---- Tank: the ladder (graduation) ----

export type Ladder = {
  task_type: string; rung: number; model: string; candidate_model: string | null; shadow_rate: number | string;
  previous_model: string | null; eval_threshold: number | string; cache_scope: string; priority: string;
  n: number; agreement: number | string | null; cand_cost: number | string | null; prim_cost: number | string | null;
  calls_30d: number; cost_30d: number | string; cache_hits: number; eligible: boolean; saving_pct: number | null;
};
export function ladder(): Promise<Ladder[]> {
  return tank<Ladder[]>("/v1/graduation", { method: "GET" });
}
export function setCandidate(taskType: string, candidateModel: string | null, shadowRate: number, by: string) {
  return tank<{ ok: true }>(`/v1/graduation/${encodeURIComponent(taskType)}/candidate`, {
    method: "PUT", body: JSON.stringify({ candidate_model: candidateModel, shadow_rate: shadowRate, by }),
  });
}
export function promoteCandidate(taskType: string, by: string) {
  return tank<{ ok: true; model: string }>(`/v1/graduation/${encodeURIComponent(taskType)}/promote`, {
    method: "POST", headers: { "X-Tervory-Admin": by }, body: JSON.stringify({ by }),
  });
}
export function rollbackModel(taskType: string, by: string) {
  return tank<{ ok: true; model: string }>(`/v1/graduation/${encodeURIComponent(taskType)}/rollback`, { method: "POST", body: JSON.stringify({ by }) });
}
export type EvalsSummary = { task_type: string; cases: number; accepted: number; rejected: number; manual: number };
export function evalsSummary(): Promise<EvalsSummary[]> {
  return tank<EvalsSummary[]>("/v1/evals/summary", { method: "GET" });
}

// ---- Tiger: watches, prices, alerts (server-side; Tank proxies to the Tiger room) ----

export type TigerWatch = {
  id: string;
  tenant_id: string;
  url: string;
  label: string;
  engine: string;
  every: string;
  enabled: boolean;
  threshold: number | string;
  alert_price?: boolean;
  alert_stock?: boolean;
};

export type TigerMatch = {
  site_label?: string;
  id: string;
  tenant_id: string;
  sku: string;
  url: string;
  seen_on: string;
  checked_at: string;
  state: "suggested" | "rejected" | "confirmed";
  method: string;
  product?: { name: string | null; brand: string | null; mpn: string | null };
  identifiers: {
    own: { gtin?: string | null; brand?: string | null; mpn?: string | null };
    seen: { gtins?: string[]; brand?: string | null; mpn?: string | null };
  };
};
export type TigerPosition = {
  sku: string;
  name?: string | null;
  own_price: string | number | null;
  currency: string | null;
  competitors: { url: string; price: string | number | null; currency: string | null; stock: string | null; checked_at: string | null }[];
};

export type TigerSiteCheck = { url: string; pages: number | null; matches: number; checked_at: string | null; status: "checked" | "human_check" | "robots" | null; skipped: number | null };

export function listTigerMatches(actor: AccessIdentity): Promise<{ tenant_id: string; matches: TigerMatch[]; site_checks?: TigerSiteCheck[] }> {
  return accessRequest(actor, "tiger/matches");
}

export function listTigerPosition(actor: AccessIdentity): Promise<{ tenant_id: string; products: TigerPosition[] }> {
  return accessRequest(actor, "tiger/position");
}

/** A button click uses Tank's existing durable card confirmation and dispatch.
 * No model planner runs; dispatch calls the same /access/tiger/match/{decision} route.
 */
export async function decideTigerMatch(actor: AccessIdentity, matchId: string, decision: "confirm" | "reject" | "undo"): Promise<TigerMatch> {
  const post = async (path: string, body: unknown) => (await commandRequest(actor, path, { method: "POST", body: JSON.stringify(body) })).json();
  const thread = await post("threads", { title: "Tiger" });
  const card = await post(`threads/${encodeURIComponent(thread.thread_id)}/actions`, {
    tool: `tiger.match.${decision}`, arguments: { match_id: matchId },
  });
  const result = await post(`cards/${encodeURIComponent(card.card_id)}`, { action: "confirm" });
  if (result.status !== "confirmed" || result.result?.match?.id !== matchId || result.result.match.tenant_id !== actor.tenantId) throw new Error("Tiger decision was not confirmed");
  return result.result.match as TigerMatch;
}

export type TigerObservation = {
  id: string;
  tenant_id: string;
  url: string;
  ts: string;
  price: string | number | null;
  currency: string | null;
  availability: string | null;
  method: string | null;
  evidence: string | null;
  product_name?: string | null;
  last_product_name?: string | null;
  issue?: string | null;
};

export type TigerAlert = {
  id: string;
  tenant_id: string;
  url: string;
  ts: string;
  kind: string;
  before: string | null;
  after: string | null;
};

async function tigerCall<T>(path: string, init: RequestInit): Promise<T> {
  const tenant = requestTenant(path, init);
  if (!tenant || !TANK_API_KEY) throw new Error("Tiger is unreachable");
  return tenantBoundedFetch(tenant, `${TANK_URL}${path}`, {
    ...init, headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY, ...(init.headers ?? {}) },
    cache: "no-store",
  }, 10_000, async res => {
    const text = await res.text();
    if (!res.ok) {
      let detail = "";
      try { detail = JSON.parse(text).detail; } catch { /* No raw response in errors. */ }
      const safe = ["url not allowed", "url is too long", "that url is already watched"].includes(detail) ? detail : "";
      throw new Error(`Tiger ${res.status}: ${safe}`);
    }
    return (text ? JSON.parse(text) : null) as T;
  });
}

export function listTigerWatches(tenantId: string): Promise<{ watches: TigerWatch[] }> {
  return tigerCall<{ watches: TigerWatch[] }>(`/v1/tiger/watchlist?tenant_id=${encodeURIComponent(tenantId)}`, { method: "GET" });
}

export function createTigerWatch(
  tenantId: string,
  input: { url: string; label: string; every: string; threshold: number; alert_price?: boolean; alert_stock?: boolean },
): Promise<TigerWatch> {
  return tigerCall<TigerWatch>("/v1/tiger/watchlist", {
    method: "POST",
    body: JSON.stringify({
      tenant_id: tenantId,
      url: input.url,
      label: input.label,
      every: input.every,
      threshold: input.threshold,
      alert_price: input.alert_price ?? true,
      alert_stock: input.alert_stock ?? true,
      enabled: true,
      engine: "",
    }),
  });
}

export function deleteTigerWatch(tenantId: string, watchId: string): Promise<null> {
  const q = new URLSearchParams({ tenant_id: tenantId });
  return tigerCall<null>(`/v1/tiger/watchlist/${encodeURIComponent(watchId)}?${q}`, { method: "DELETE" });
}

export function listTigerObservations(tenantId: string, url: string): Promise<{ observations: TigerObservation[] }> {
  const q = new URLSearchParams({ tenant_id: tenantId, url });
  return tigerCall<{ observations: TigerObservation[] }>(`/v1/tiger/observations?${q}`, { method: "GET" });
}

export function listTigerAlerts(tenantId: string, url?: string): Promise<{ alerts: TigerAlert[] }> {
  const q = new URLSearchParams({ tenant_id: tenantId });
  if (url) q.set("url", url);
  return tigerCall<{ alerts: TigerAlert[] }>(`/v1/tiger/alerts?${q}`, { method: "GET" });
}

// ---- Plugins vaults. Server-side only. The browser never receives a secret value. ----

export const VAULT_OFF_MESSAGE = "This vault is off. Turn it on in Tank.";

export type VaultPlugins = {
  vault: string;
  enabled: boolean;
  message: string | null;
  secrets: { name: string; purpose: string; mask: string; saved_at: string }[];
  files: { id: string; name: string; type: string; size: number; rows: number | null; pages: number | null; uploaded_at: string; status: string; reason?: string }[];
  links: { id: string; url: string; label: string; frequency: string; last_checked: string | null; status: string }[];
  connections: { id: string; name: string; server_url: string; auth_type: string; status: string; last_used: string | null; has_token: boolean }[];
};

async function vaultFetch(path: string, userId: string, squad: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("x-tank-key", TANK_API_KEY);
  headers.set("x-tervory-user", userId);
  headers.set("x-tervory-squad", squad);
  if (typeof init.body === "string" && !headers.has("content-type")) headers.set("content-type", "application/json");
  const tenant = requestTenant(path, init);
  if (squad === "radix" && tenant) {
    return tenantBoundedFetch(tenant, `${TANK_URL}${path}`, { ...init, headers, cache: "no-store" }, 30_000,
      readRadixResponse);
  }
  if (squad === "base" || squad === "tiger") {
    const tenant = requestTenant(path, init);
    if (!tenant || (squad === "tiger" && !TANK_API_KEY)) throw new Error(squad === "tiger" ? "Tiger is unreachable" : "Sign in to change Plugins.");
    return tenantBoundedFetch(tenant, `${TANK_URL}${path}`, { ...init, headers, cache: "no-store" }, squad === "tiger" ? 10_000 : 30_000,
      async response => new Response([204, 205, 304].includes(response.status) ? null : await response.arrayBuffer(), { status: response.status, headers: response.headers }));
  }
  return tenantFetch(requestTenant(path, init), `${TANK_URL}${path}`, { ...init, headers, cache: "no-store" });
}

async function vaultJson<T>(path: string, userId: string, squad: string, init: RequestInit): Promise<T> {
  const res = await vaultFetch(path, userId, squad, init);
  const text = await res.text();
  if (!res.ok) throw new Error(squad === "tiger" ? (text.includes(VAULT_OFF_MESSAGE) ? VAULT_OFF_MESSAGE : `Tiger ${res.status}`) : "Your request could not be completed. Please try again.");
  return (text ? JSON.parse(text) : {}) as T;
}

export function vaultPlugins(vault: string, squad: string, tenantId: string, userId: string): Promise<VaultPlugins> {
  const q = new URLSearchParams({ tenant_id: tenantId });
  return vaultJson<VaultPlugins>(`/v1/vaults/${encodeURIComponent(vault)}/plugins?${q}`, userId, squad, { method: "GET" });
}

export function saveVaultSecret(
  vault: string,
  squad: string,
  tenantId: string,
  userId: string,
  name: string,
  purpose: string,
  value: string,
): Promise<{ ok: boolean }> {
  return vaultJson(`/v1/vaults/${encodeURIComponent(vault)}/secrets`, userId, squad, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, name, purpose, value }),
  });
}

export function removeVaultSecret(vault: string, squad: string, tenantId: string, userId: string, name: string): Promise<{ ok: boolean }> {
  const q = new URLSearchParams({ tenant_id: tenantId, name });
  return vaultJson(`/v1/vaults/${encodeURIComponent(vault)}/secrets?${q}`, userId, squad, { method: "DELETE" });
}

/** Server-only. Do not pass the result to a client component. */
export async function resolveVaultSecret(
  vault: string,
  squad: string,
  tenantId: string,
  userId: string,
  name: string,
): Promise<string | null> {
  const body = await vaultJson<{ found?: boolean; value?: string }>(
    `/v1/vaults/${encodeURIComponent(vault)}/secrets/resolve`,
    userId,
    squad,
    { method: "POST", body: JSON.stringify({ tenant_id: tenantId, name }) },
  );
  return body.found && typeof body.value === "string" ? body.value : null;
}

export async function uploadVaultFiles(
  vault: string,
  squad: string,
  tenantId: string,
  userId: string,
  files: FormData,
): Promise<{ ok: boolean }> {
  const q = new URLSearchParams({ tenant_id: tenantId });
  const res = await vaultFetch(`/v1/vaults/${encodeURIComponent(vault)}/files?${q}`, userId, squad, {
    method: "POST",
    body: files,
  });
  const text = await res.text();
  if (!res.ok) throw new Error("Your request could not be completed. Please try again.");
  return { ok: true };
}

export function removeVaultFile(vault: string, squad: string, tenantId: string, userId: string, fileId: string): Promise<{ ok: boolean }> {
  const q = new URLSearchParams({ tenant_id: tenantId });
  return vaultJson(`/v1/vaults/${encodeURIComponent(vault)}/files/${encodeURIComponent(fileId)}?${q}`, userId, squad, {
    method: "DELETE",
  });
}

export function addVaultLink(
  vault: string,
  squad: string,
  tenantId: string,
  userId: string,
  url: string,
  label: string,
  frequency: string,
): Promise<{ ok: boolean }> {
  return vaultJson(`/v1/vaults/${encodeURIComponent(vault)}/links`, userId, squad, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, url, label, frequency }),
  });
}

export function removeVaultLink(vault: string, squad: string, tenantId: string, userId: string, linkId: string): Promise<{ ok: boolean }> {
  const q = new URLSearchParams({ tenant_id: tenantId });
  return vaultJson(`/v1/vaults/${encodeURIComponent(vault)}/links/${encodeURIComponent(linkId)}?${q}`, userId, squad, {
    method: "DELETE",
  });
}

export function addVaultConnection(
  vault: string,
  squad: string,
  tenantId: string,
  userId: string,
  name: string,
  serverUrl: string,
  authType: string,
  token: string,
): Promise<{ ok: boolean }> {
  return vaultJson(`/v1/vaults/${encodeURIComponent(vault)}/connections`, userId, squad, {
    method: "POST",
    body: JSON.stringify({ tenant_id: tenantId, name, server_url: serverUrl, auth_type: authType, token }),
  });
}

export function removeVaultConnection(
  vault: string,
  squad: string,
  tenantId: string,
  userId: string,
  connectionId: string,
): Promise<{ ok: boolean }> {
  const q = new URLSearchParams({ tenant_id: tenantId });
  return vaultJson(
    `/v1/vaults/${encodeURIComponent(vault)}/connections/${encodeURIComponent(connectionId)}?${q}`,
    userId,
    squad,
    { method: "DELETE" },
  );
}

// Skills library. library.vault is not skala.vault. Server-side only.
// A squad may list, link, or transfer. It cannot rewrite the library from here.

export type LibrarySkill = {
  skill_id: string;
  version: string;
  agent: string;
  description: string;
  published_at: string | null;
};

export type LibraryLink = {
  id: string;
  squad: string;
  vault: string;
  skill_id: string;
  version: string;
  created_at: string | null;
};

export type LibraryView = {
  vault: string;
  enabled: boolean;
  message: string | null;
  skills: LibrarySkill[];
  links: LibraryLink[];
};

export function librarySkills(squad: string, tenantId: string, userId: string): Promise<LibraryView> {
  const q = new URLSearchParams({ tenant_id: tenantId });
  return vaultJson<LibraryView>(`/v1/vaults/library.vault/skills?${q}`, userId, squad, { method: "GET" });
}

export function linkLibrarySkill(
  squad: string,
  tenantId: string,
  userId: string,
  skillId: string,
  version: string,
): Promise<{ id: string; vault: string }> {
  return vaultJson(
    `/v1/vaults/library.vault/skills/${encodeURIComponent(skillId)}/${encodeURIComponent(version)}/link`,
    userId,
    squad,
    { method: "POST", body: JSON.stringify({ tenant_id: tenantId }) },
  );
}

export function transferLibrarySkill(
  squad: string,
  tenantId: string,
  userId: string,
  skillId: string,
  version: string,
): Promise<{ ok: boolean; vault: string }> {
  return vaultJson(
    `/v1/vaults/library.vault/skills/${encodeURIComponent(skillId)}/${encodeURIComponent(version)}/transfer`,
    userId,
    squad,
    { method: "POST", body: JSON.stringify({ tenant_id: tenantId }) },
  );
}

// Gold review: identity and tenant come only from the authenticated server route.
export function goldSummary(identity: { userId: string; tenantId: string; role: string }): Promise<{
  region: string;
  skills: Array<{ agent: string; skill_id: string; checked: number; target: number; holdout: number; candidates: number; last_score: number | null; trend: number | null; ran_at: string | null }>;
  pairs: Record<string, number>;
}> {
  return tank("/v1/gold/summary", { method: "GET", headers: {
    "x-tervory-user": identity.userId, "x-tervory-tenant": identity.tenantId, "x-tervory-role": identity.role,
  } });
}

export function goldNext(identity: { userId: string; tenantId: string; role: string }, agent: string, skill: string, after?: string): Promise<{
  id: string; input: unknown; expected: Record<string, unknown>; source: string;
} | null> {
  const query = new URLSearchParams({ agent, skill });
  if (after) query.set("after", after);
  return tank(`/v1/gold/next?${query}`, { method: "GET", headers: {
    "x-tervory-user": identity.userId, "x-tervory-tenant": identity.tenantId, "x-tervory-role": identity.role,
  } });
}

export function checkGold(identity: { userId: string; tenantId: string; role: string }, id: string,
  body: { action: "right" | "wrong" | "skip"; correction?: Record<string, unknown>; note?: string }): Promise<{ ok: boolean }> {
  return tank(`/v1/gold/${encodeURIComponent(id)}/check`, { method: "POST", body: JSON.stringify(body), headers: {
    "x-tervory-user": identity.userId, "x-tervory-tenant": identity.tenantId, "x-tervory-role": identity.role,
  } });
}

export function confirmTrainingRead(identity: { userId: string; tenantId: string; role: string }, observationId: string): Promise<{ ok: boolean }> {
  return tank(`/v1/training/tiger/${encodeURIComponent(observationId)}/confirm`, { method: "POST", headers: {
    "x-tervory-user": identity.userId, "x-tervory-tenant": identity.tenantId, "x-tervory-role": identity.role,
  } });
}

export function finalGoldCheck(identity: { userId: string; tenantId: string; role: string }, skill: string, version: string): Promise<{
  results: Array<{ skill_id: string; version: string; score: number; n: number }>;
}> {
  return tank(`/v1/gold/final/${encodeURIComponent(skill)}/${encodeURIComponent(version)}`, { method: "POST", headers: {
    "x-tervory-user": identity.userId, "x-tervory-tenant": identity.tenantId, "x-tervory-role": identity.role,
  } });
}

// Stripe event ids. The Workspace key is the only caller. A repeat claim is first: false.
export function claimBillingEvent(eventId: string, type: string): Promise<{ first: boolean }> {
  return tank<{ first: boolean }>("/v1/billing/events/claim", {
    method: "POST",
    body: JSON.stringify({ event_id: eventId, type }),
  });
}

export function releaseBillingEvent(eventId: string): Promise<{ released: boolean }> {
  return tank<{ released: boolean }>("/v1/billing/events/release", {
    method: "POST",
    body: JSON.stringify({ event_id: eventId }),
  });
}

export function markBillingEventHandled(eventId: string): Promise<{ handled: boolean }> {
  return tank<{ handled: boolean }>("/v1/billing/events/handled", {
    method: "POST",
    body: JSON.stringify({ event_id: eventId }),
  });
}

export type TrainingGraduation = {
  task_type: string; model: string; gold_count: number; candidate: string | null;
  heldout_score: number | null; current_cost: number | null; candidate_cost: number | null; can_promote: boolean;
};
export function trainingGraduation(identity: {userId: string; tenantId: string; role: string}) {
  return tank<TrainingGraduation[]>("/v1/training/graduation", {method: "GET", headers: {
    "x-tervory-user": identity.userId, "x-tervory-tenant": identity.tenantId, "x-tervory-role": identity.role,
  }});
}

export type TisanGoldPage = {
  page_id: string; tenant_id: string; figma_file_id: string; figma_frame_id: string;
  built_page_path: string; frame_hash: string; page_hash: string;
  frame: Record<string, unknown>; page_structure: Record<string, unknown>; html: string; css: string;
};

export function tisanGoldPages(tenantId: string, userId: string): Promise<TisanGoldPage[]> {
  return tank<TisanGoldPage[]>(`/v1/tisan/gold/pages?tenant_id=${encodeURIComponent(tenantId)}`, {
    method: "GET", headers: { "X-Tervory-Admin": userId },
  });
}

export function approveTisanGold(tenantId: string, userId: string, pageId: string, frameHash: string, pageHash: string) {
  return tank<{ approved: boolean }>("/v1/tisan/gold/approve", {
    method: "POST", headers: { "X-Tervory-Admin": userId },
    body: JSON.stringify({ tenant_id: tenantId, page_id: pageId, frame_hash: frameHash, page_hash: pageHash }),
  });
}

export type RigCatalogueCounts = { products: number; fits: number };

function wholeCount(value: unknown): number | null {
  const count = typeof value === "number" ? value : Number(value);
  if (!Number.isInteger(count) || count < 0) return null;
  return count;
}

/** Product and fit totals for this tenant, from the Rig room through Tank. Null when Rig does not answer. */
export async function rigCatalogueCounts(tenantId: string): Promise<RigCatalogueCounts | null> {
  if (!tenantId) return null;
  try {
    const row = await tank<{ products?: unknown; fits?: unknown }>(
      `/v1/rig/catalogues?tenant_id=${encodeURIComponent(tenantId)}`,
      { method: "GET" },
    );
    const products = wholeCount(row.products);
    const fits = wholeCount(row.fits);
    if (products === null || fits === null) return null;
    return { products, fits };
  } catch {
    return null;
  }
}

/** Read-only Rig room calls. The Workspace key stays here; a shopper key never does. */
export function rigProxy(
  pathname: string,
  tenantId: string,
  init: { method?: "GET" | "POST"; query?: Record<string, string>; body?: unknown } = {},
): Promise<Record<string, unknown>> {
  const params = new URLSearchParams({ tenant_id: tenantId });
  for (const [key, value] of Object.entries(init.query ?? {})) params.set(key, value);
  const path = `${pathname}?${params.toString()}`;
  if ((init.method ?? "GET") === "POST") {
    return tank<Record<string, unknown>>(path, { method: "POST", body: JSON.stringify(init.body ?? {}) });
  }
  return tank<Record<string, unknown>>(path, { method: "GET" });
}

// DASH-1: server-only dashboard reads and founder control calls.
export type DashboardDay = { day: string; calls: number; code: number; delivered: number; spent: number | null; unpriced?: number };
export type DashboardRead = {
  daily: DashboardDay[];
  jobs_done: number;
  saved_usd: number | null;
  credits_left: number | null;
  granted_squads?: string[];
  recent?: { id: string; title: string; status: string }[];
  skills?: { id: string; version: string; last_eval_score: number | null; eval_threshold: number }[];
  squad?: { agent: string; status: string; jobs: number }[];
  shadow_policies?: number;
  batch_policies?: number;
};
export function readClientDashboard(tenantId: string): Promise<DashboardRead> {
  return tank(`/v1/dashboards/client?${new URLSearchParams({ tenant_id: tenantId })}`, { method: "GET" });
}
export function readFounderDashboard(userId: string): Promise<DashboardRead> {
  return tank("/v1/dashboards/founder", { method: "GET", headers: { "x-tervory-admin": userId } });
}
export function readPaidPause(userId: string): Promise<{ paid_paused: boolean; refused: { task: string; path: string; at: string }[] }> {
  return tank("/v1/controls", { method: "GET", headers: { "x-tervory-admin": userId } });
}
export function setPaidPause(userId: string, paused: boolean): Promise<{ paid_paused: boolean }> {
  return tank("/v1/controls/paid-paused", {
    method: "PUT", headers: { "x-tervory-admin": userId }, body: JSON.stringify({ paused }),
  });
}
export function switchProvenModel(userId: string, taskType: string): Promise<{ ok: boolean }> {
  return tank(`/v1/graduation/${encodeURIComponent(taskType)}/promote`, {
    method: "POST", headers: { "x-tervory-admin": userId }, body: JSON.stringify({ by: userId }),
  });
}

export function readDashboardCandidates(userId: string): Promise<{ task_type: string; candidate_model: string | null; n: number; agreement: number | null; min_shadow_samples?: number; eligible: boolean; prim_cost?: number | null; cand_cost?: number | null }[]> {
  return tank("/v1/graduation", { method: "GET", headers: { "x-tervory-admin": userId } });
}

// SKALA-2 founder approval safety. Credentials remain in this server-only client.
export type SkillApprovalStatus = {
  id: string; version: string; reason: string | null; score: number | null; runs: number; model: boolean; room_authored: boolean;
};
export type SkillApprovalPreview = { token: string; items: SkillApprovalStatus[] };
export function skillApprovalStatus(): Promise<SkillApprovalStatus[]> {
  return tank<SkillApprovalStatus[]>("/v1/skala/approvals/status", { method: "GET" });
}
export function previewSkillApprovals(by: string): Promise<SkillApprovalPreview> {
  return tank<SkillApprovalPreview>("/v1/skala/approvals/preview", { method: "POST", body: JSON.stringify({ by }) });
}
export function confirmSkillApprovals(by: string, token: string, confirmed: boolean) {
  return tank<{ ok: boolean; approved: { id: string; version: string }[] }>("/v1/skala/approvals/confirm", {
    method: "POST", body: JSON.stringify({ by, token, confirmed }),
  });
}
export function reviewSkillSource(id: string, version: string, by: string) {
  return tank<{ source: string; files: { path: string; source: string }[]; review_token: string }>(`/v1/skala/${encodeURIComponent(id)}/${encodeURIComponent(version)}/source-review`, {
    method: "POST", body: JSON.stringify({ by }),
  });
}
export function approveReviewedSkill(id: string, version: string, by: string, review_token: string, confirmed: boolean) {
  return tank<{ ok: boolean }>(`/v1/skala/${encodeURIComponent(id)}/${encodeURIComponent(version)}/approve`, {
    method: "POST", body: JSON.stringify({ by, review_token, confirmed }),
  });
}
export function rollbackSkill(id: string, version: string, by: string, confirmed: boolean) {
  return tank<{ ok: boolean }>(`/v1/skala/${encodeURIComponent(id)}/${encodeURIComponent(version)}/rollback`, {
    method: "POST", body: JSON.stringify({ by, confirmed }),
  });
}

// COMMAND-1A: founder-managed client squad grants on this box.
export type CommandSquadGrant = { agent: string; enabled: boolean; enabled_by: string | null; enabled_at: string | null };
export type CommandSquadChange = { id: number; agent: string; enabled: boolean; changed_by: string; changed_at: string };
function commandSquadHeaders(userId: string, tenantId: string) {
  return { "X-Tervory-User": userId, "X-Tervory-Tenant": tenantId };
}
export async function readCommandSquads(userId: string, tenantId: string) {
  const headers = commandSquadHeaders(userId, tenantId);
  const [grants, log] = await Promise.all([
    tank<CommandSquadGrant[]>("/v1/command/squads", { method: "GET", headers }),
    tank<CommandSquadChange[]>("/v1/command/squads/log", { method: "GET", headers }),
  ]);
  return { grants, log };
}
export async function setCommandSquad(userId: string, tenantId: string, agent: string, enabled: boolean) {
  return tank<{ agent: string; enabled: boolean; changed: boolean }>(`/v1/command/squads/${encodeURIComponent(agent)}`, {
    method: "PUT", headers: commandSquadHeaders(userId, tenantId), body: JSON.stringify({ enabled }),
  });
}

// Client-roster startup only: keep request-time squad behavior unchanged. A
// single deadline covers both fetch and JSON, even if upstream ignores abort.
export const CLIENT_STARTUP_LIMITS = { requestMs: 1500 };
async function startupSquadRequest<T>(userId: string, tenantId: string, path: string, init: RequestInit): Promise<T> {
  return tenantBoundedFetch(tenantId, new URL(`/v1/command/squads${path}`, TANK_URL), {
    ...init, cache: "no-store",
    headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY,
      ...commandSquadHeaders(userId, tenantId) },
  }, CLIENT_STARTUP_LIMITS.requestMs, async response => {
    // Never consume an error body (or put it in an exception/log).
    if (!response.ok) {
      void response.body?.cancel().catch(() => undefined);
      throw new Error("Client startup squad request failed");
    }
    return await response.json() as T;
  });
}
export async function readStartupCommandSquads(userId: string, tenantId: string) {
  const [grants, log] = await Promise.all([
    startupSquadRequest<CommandSquadGrant[]>(userId, tenantId, "", { method: "GET" }),
    startupSquadRequest<CommandSquadChange[]>(userId, tenantId, "/log", { method: "GET" }),
  ]);
  return { grants, log };
}
export async function setStartupCommandSquad(userId: string, tenantId: string, agent: string) {
  return startupSquadRequest<{ agent: string; enabled: boolean; changed: boolean }>(
    userId, tenantId, `/${encodeURIComponent(agent)}`, { method: "PUT", body: JSON.stringify({ enabled: true }) });
}

// ---- COMMAND-1: same-box conversations; caller identity is server-owned. ----
export class CommandRequestError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; }
}
export async function commandRequest(identity: { tenantId: string; userId: string; tenantAdmin?: boolean }, path: string, init: RequestInit = {}, options: { passThroughErrors?: boolean } = {}): Promise<Response> {
  if (!TANK_API_KEY) throw new Error("The conversation service is not configured.");
  const headersUntil = Date.now() + COMMAND_LIMITS.headersMs;
  const stop = new AbortController();
  const signal = init.signal ? AbortSignal.any([init.signal, stop.signal]) : stop.signal;
  let target;
  try { target = await tenantDestination(identity.tenantId, Math.min(COMMAND_LIMITS.headersMs, 10_000), signal); }
  catch (error) {
    if (error instanceof DeadlineError) throw new CommandRequestError(504, "timeout", "The conversation service did not answer in time. Reload the chat before trying again.");
    throw new CommandRequestError(503, "home_unavailable", "This workspace home is unavailable.");
  }
  const url = new URL(`/v1/command/${path}`, target.url);
  url.searchParams.delete("tenant_id");
  url.searchParams.delete("user_id");
  const headers = new Headers(init.headers);
  if (!headers.has("content-type")) headers.set("content-type", "application/json");
  headers.set("x-tank-key", target.key);
  headers.set("x-tervory-home-region", target.home ?? (process.env.TANK_REGION === "za" ? "za" : "us-east"));
  headers.set("x-tervory-user", identity.userId);
  headers.set("x-tervory-tenant", identity.tenantId);
  headers.delete("x-tervory-admin");
  // Lane A resolves tenant_squad from the database on each authenticated call.
  // Never turn a caller header into tool authority.
  headers.delete("x-tervory-squad");
  headers.set("x-tervory-role", identity.tenantAdmin === true ? "org:admin" : "");
  // Review of #506, B1: hard deadline on the headers (boundedFetch, redirect:"manual",
  // 3xx refused) and a bound on the body that cancels it: idle for event streams, idle
  // plus total for everything else, error bodies included. Works under undici 6's
  // redirect:"error" + GC hang because every wait is raced against a timer.
  let upstream: Response;
  try {
    upstream = await boundedFetch(url, { ...init, headers, cache: "no-store", signal }, Math.max(1, headersUntil - Date.now()), async answer => answer);
  } catch (error) {
    if (error instanceof DeadlineError) throw new CommandRequestError(504, "timeout", "The conversation service did not answer in time. Reload the chat before trying again.");
    throw new CommandRequestError(502, "request_failed", "This request could not be completed. Reload the chat before trying again.");
  }
  const stream = upstream.ok && (upstream.headers.get("content-type") ?? "").includes("text/event-stream");
  const limits = stream ? { idleMs: COMMAND_LIMITS.idleMs } : { idleMs: COMMAND_LIMITS.bodyMs, totalMs: COMMAND_LIMITS.bodyMs };
  const empty = [204, 205, 304].includes(upstream.status);
  if (empty) upstream.body?.cancel().catch(() => undefined);
  const response = new Response(empty || !upstream.body ? null : boundedBody(upstream.body, limits, () => stop.abort()),
    { status: upstream.status, statusText: upstream.statusText, headers: upstream.headers });
  if (!response.ok && !options.passThroughErrors) {
    const detail = await response.json().then(body => body.detail).catch(() => undefined);
    if (response.status === 409 && detail === "Pinned models await a router capability; choose Auto") throw new CommandRequestError(409, "pin_pending", "Choosing a specific answer option is not supported yet. Choose Auto to continue.");
    if (response.status === 422 && detail === "Model is not on the roster") throw new CommandRequestError(422, "pin_unavailable", "That answer option is no longer available. Choose Auto to continue.");
    if (response.status === 409 && detail === "An answer is already running in this thread") throw new CommandRequestError(409, "answer_running", "An answer is already running. Wait for it to finish before sending again.");
    if (response.status === 403) throw new CommandRequestError(403, "access_denied", "Access to this action has not been verified for your account. No new permission has been granted.");
    if (response.status === 404) throw new CommandRequestError(404, "not_found", "This item is unavailable.");
    if (response.status === 413) throw new CommandRequestError(413, "attachment_limit", "The file or this chat's storage limit has been reached (5 MB per file, 20 files and 20 MB per chat).");
    throw new CommandRequestError(502, "request_failed", "This request could not be completed. Reload the chat before trying again.");
  }
  return response;
}

/** Lane A derives this catalogue from the founder-controlled tenant_squad list. */
export async function commandTools(identity: { tenantId: string; userId: string }): Promise<string[]> {
  const result: unknown = await (await commandRequest(identity, "tools")).json();
  if (!Array.isArray(result) || !result.every(tool => {
    if (typeof tool !== "string") return false;
    const segments = tool.split(".");
    return segments.length >= 2 && segments.every(segment => segment.length > 0
      && !/[^a-z_]/.test(segment) && !["__proto__", "prototype", "constructor"].includes(segment));
  })) throw new Error("Tool access could not be loaded.");
  return result;
}

// ACCESS-1: identity is always supplied by the authenticated Workspace server.
export type AccessIdentity = { tenantId: string; userId: string };
export type AccessConnection = { room: string; site: string; terms: string; revoked: boolean; clear_pending: boolean; overdue: boolean; rotate_by: string; rotation_days: number };
export type AccessQueueItem = { id: string; tenant_id: string; room: string; site: string; reason: string; status: string; created_at: string; live_available: boolean };
export type AccessPanel = { connections: AccessConnection[]; policies: { site: string; terms: string; rotation_days: number }[]; feeds: { distributor: string; site: string; source: string; fetched_at: string; row_count: number }[]; meta_enabled: boolean };
export async function accessRequest<T>(actor: AccessIdentity, path: string, body?: unknown): Promise<T> {
  const send = path.startsWith("tiger/")
    ? (tenant: string, url: string, init: RequestInit) => tenantBoundedFetch(tenant, url, init, 10_000,
        async response => new Response([204, 205, 304].includes(response.status) ? null : await response.arrayBuffer(), {status: response.status, headers: response.headers}))
    : tenantFetch;
  const response = await send(actor.tenantId, `${TANK_URL}/v1/access/${path}`, {
    method: body === undefined ? "GET" : "POST", cache: "no-store",
    headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY,
      "x-tervory-user": actor.userId, "x-tervory-tenant": actor.tenantId },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw new Error("The connection request could not be completed. Check the settings or try again.");
  return response.json() as Promise<T>;
}

// RIG-3 retailer calls reuse ACCESS-1. Identity comes only from the server session.
export function rigAdminPanel<T>(actor: AccessIdentity): Promise<T> {
  return accessRequest<T>(actor, "rig/admin");
}

export function rigAdminAction<T>(actor: AccessIdentity, operation: string, data: Record<string, unknown>): Promise<T> {
  if (!["connect", "import", "delete-products", "refresh", "mapping", "confirm-mapping", "settings", "embed-keys", "price-rule", "exchange-rate", "templates", "approve", "reject", "suggest"].includes(operation)) {
    throw new Error("Unknown retailer operation.");
  }
  return accessRequest<T>(actor, `rig/admin/${operation}`, { data });
}

/** CORE-2 only: server-resolved identity wins over every caller query field. */
export function readClientSavings<T>(tenantId: string, endpoint: "" | "/rows" | "/revision" = "", query: Record<string,string> = {}): Promise<T> {
  const params = new URLSearchParams();
  for (const name of ["end","start","until","member","free_only","page"]) if(query[name]) params.set(name,query[name]);
  params.set("tenant_id",tenantId);
  return tank<T>(`/v1/client-savings${endpoint}?${params}`,{method:"GET"});
}

// MOA-1 founder evidence. Only server components/actions call these helpers.
export type MoaShape = { shape: string; mode: string; status: string; score: number | null; cost_per_call: number | null; samples: number };
export type MoaSeat = { name: string; model: string; candidate: string | null; critical: boolean; timeout_s: number };
export type MoaAttempt = { model: string; tokens_in: number; tokens_out: number; cost_usd: number | null; ledger_id: string | null };
export type MoaReceiptSeat = { name: string; model: string; status: string; error: string | null; attempts: MoaAttempt[] };
export type MoaEvidence = {
  tenant_id: string; region: string;
  tasks: { task_type: string; enabled: boolean; config_hash: string;
    config: { seats: MoaSeat[]; shape: string; mode: string; min_samples: number; bench_enabled: boolean; cheap_aggregator: string; frontier_aggregator: string; cheap_candidate: string | null; frontier_candidate: string | null };
    shapes: MoaShape[]; proposals: { seat: string; candidate_model: string; samples: number; agreement: number }[] }[];
  runs: { id: string; task_type: string; kind: string; status: string; created_at: string;
    receipt: { baseline_model?: string; seats?: MoaReceiptSeat[]; cost_usd?: number | null; all_frontier_usd?: number | null;
      single_top_usd?: number | null; saved_vs_frontier_usd?: number | null; saved_vs_single_usd?: number | null;
      error?: string; shadow_error?: string } }[];
  shadows: { id: string; seat: string; status: string; current_model: string; candidate_model: string;
    against_current: number | null; against_final: number | null; candidate_cost: number | null }[];
};
export function moaEvidence(userId: string, tenantId: string, task?: string): Promise<MoaEvidence> {
  const query = new URLSearchParams({ tenant_id: tenantId });
  if (task) query.set("task", task);
  return tank(`/v1/moa?${query}`, { method: "GET", headers: { "x-tervory-admin": userId } });
}
export function moaAction(userId: string, action: "bench" | "promote", body: { tenant_id: string; task_type: string; config_hash: string; seat?: string }): Promise<unknown> {
  return tank(`/v1/moa/${action}`, { method: "POST", headers: { "x-tervory-admin": userId }, body: JSON.stringify(body) });
}

/** Published matches are read on demand from their owning region, never persisted here. */
export async function mootPublishedMatches(matchId?: string, signal?: AbortSignal, region: Region = "us-east"): Promise<unknown> {
  if (region !== "us-east" && region !== "za") return { matches: [] };
  const query = new URLSearchParams({ home_region: region });
  if (matchId) query.set("match_id", matchId);
  const timeout = AbortSignal.timeout(10000);
  const init: RequestInit = {
    method: "GET", signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  };
  if (region === "us-east") return tank(`/v1/zenas/moot/published?${query}`, init);
  const owner = regionTank("za");
  if (!owner) return { matches: [] };
  const response = await fetch(`${owner.url}/v1/zenas/moot/published?${query}`, {
    ...init, cache: "no-store", redirect: "error",
    headers: { "content-type": "application/json", "x-tank-key": owner.key },
  });
  if (!response.ok) throw new Error("Published matches unavailable");
  return response.json();
}

// Market Lab: the server derives tenantId from SigninDirectory before calling this corridor.
export type MarketFeed = {
  id: string; name: string; kind: "copy" | "linked"; rows: number;
  url?: string; cadence?: "daily" | "weekly" | "monthly" | "annual";
  state: "on" | "off"; note: string; checked_at: string | null; document_id: string | null;
};
export type MarketOverview = { feeds: MarketFeed[]; on_count: number; refresh_status: string | null };
export type MarketFact = { text: string; href: string; name: string };
export type MarketAnswer = { content: string; facts: MarketFact[]; sources: { name: string; href: string }[] };
export type MarketDocument = {
  kind?: string;
  passage_count?: number;
  passages?: { index: number; text: string; label: string; page: number | null; usable: boolean }[];
  reason?: string;
  calculation?: WarningCalculation;
  offset: number; page_size: number; point_count: number; event_count: number;
  id: string; name: string; fetched_at: string; licence: string; terms_checked_at: string;
  url: string; presentation?: { publisher?: string; publisher_url?: string; notice?: string; explanation?: string };
  downloads: { url: string; sha256: string }[];
  points: { index: number; title: string; display_title: string; date_label: string; geo: string; geo_name: string; date: string; value: string; display_value: string; unit: string; unit_label: string; revision: string; revision_label: string; withdrawn?: boolean }[];
  events: { index: number; title: string; display_title: string; date_label: string; display_url: string; link_label: string; body: string; date: string; url: string; kind: string }[];
};
export class MarketLabRequestError extends Error {
  readonly status: number;
  constructor(status: number) {
    super("Market Lab could not read its stored sources.");
    this.name = "MarketLabRequestError";
    this.status = status;
  }
}
export async function marketLabRequest<T>(
  tenantId: string, operation: "overview" | "ask" | "document" | "refresh",
  payload: { question?: string; document_id?: string; offset?: number; row?: number; row_kind?: "point" | "event" } = {},
): Promise<T> {
  const response = await tenantFetch(tenantId, `${TANK_URL}/v1/market-lab/${operation}`, {
    method: "POST", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(operation === "ask" ? 190000 : 35000),
    headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY },
    body: JSON.stringify({ ...payload, tenant_id: tenantId }),
  });
  if (!response.ok) throw new MarketLabRequestError(response.status);
  return await response.json() as T;
}

// Market Lab warnings: tenant identity is supplied by the authenticated server.
export type MarketWarning = { id: string; title: string; facts: Pick<MarketFact, "text" | "href">[]; status: string; resolution_href: string | null; feedback: boolean | null };
export type MarketWarnings = { warnings: MarketWarning[]; history: { name: string; text: string; href: string }[]; problem?: string | null; training_pending: boolean };
export type WarningPoint = { display_value?: string | null; date: string; value: string; unit: string; title: string; href: string };
export type WarningCalculation = {
  kind: "history" | "resolution";
  upstream_name?: string; downstream_name?: string;
  episode_count?: number; hits?: number; hit_rate?: number | null; lag_min?: number | null; lag_max?: number | null;
  config?: { min_episodes: number; min_lag_days: number; max_lag_days: number; max_gap_days: number; outcome_pct: number; pct_threshold: number };
  current?: { start: string; point: WarningPoint; previous: WarningPoint } | null;
  episodes?: { upstream: WarningPoint; previous: WarningPoint; baseline: WarningPoint; outcome: WarningPoint; observations: WarningPoint[]; hit: boolean; lag_days: number | null; start: string; end: string }[];
  baseline?: WarningPoint; observations?: WarningPoint[]; selected?: WarningPoint | null; coverage_complete?: boolean; coverage_end?: WarningPoint | null;
  outcome?: "hit" | "miss" | "not_measurable"; score?: number | null; window_start?: string; window_end?: string; original?: string;
};
export async function marketWarningRequest<T>(tenantId: string, operation: "list" | "feedback", payload: { warning_id?: string; event_id?: string; useful?: boolean; actor?: string } = {}): Promise<T> {
  const response = await tenantFetch(tenantId, `${TANK_URL}/v1/market-lab/warnings/${operation}`, {
    method: "POST", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(120000),
    headers: { "content-type": "application/json", "x-tank-key": TANK_API_KEY },
    body: JSON.stringify({ ...payload, tenant_id: tenantId }),
  });
  if (!response.ok) throw new MarketLabRequestError(response.status);
  return await response.json() as T;
}
/** Founder CRM reads: fixed same-box Tank only; no CRM credential reaches Workspace callers. */
export async function triggerCrmRead(
  identity: { tenantId: string; userId: string }, path: readonly string[], query: URLSearchParams,
  signal?: AbortSignal,
): Promise<Response> {
  const { crmReadPath, crmReadQuery } = await import("./trigger-crm");
  const target = crmReadPath(path);
  const parameters = crmReadQuery(query, path.length === 2);
  if (!target || !parameters) throw new Error("invalid_crm_request");
  if (!TANK_API_KEY) throw new Error("crm_unavailable");
  const url = new URL(`/v1/trigger/crm/${target}`, TANK_URL);
  url.search = parameters.toString();
  return tenantFetch(identity.tenantId, url, {
    method: "GET", cache: "no-store", redirect: "error", signal,
    headers: {
      "accept": "application/json", "x-tank-key": TANK_API_KEY,
      "x-tervory-user": identity.userId,
      "x-tervory-tenant": identity.tenantId,
    },
  });
}


/** Private content uses a separately enabled full Workspace key, never a homes key. */
function contentTank(region: Region): { url: string; key: string } | null {
  if (region === (process.env.TANK_REGION === "za" ? "za" : "us-east")) return TANK_API_KEY ? { url: TANK_URL, key: TANK_API_KEY } : null;
  const suffix = region === "za" ? "ZA" : "US";
  const key = (process.env["TANK_CONTENT_API_KEY_" + suffix] ?? "").trim();
  const url = (process.env["TANK_URL_" + suffix] ?? "").trim().replace(/\/+$/, "");
  if (!key || !url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password ? { url, key } : null;
  } catch { return null; }
}

/** The US directory receives a tenant UUID, never the content being routed. */
export async function tenantDestination(tenantId: string, timeoutMs = 10_000, signal?: AbortSignal): Promise<{ url: string; key: string; home: Region | null }> {
  const directory = contentTank("us-east");
  if (!directory) throw new Error("The tenant region directory is not configured.");
  const response = await boundedFetch(
    new URL("/v1/tenants/" + encodeURIComponent(tenantId) + "/home", directory.url),
    { method: "GET", headers: { "x-tank-key": directory.key }, cache: "no-store", signal }, timeoutMs,
    async response => ({ status: response.status, body: await response.json().catch(() => null) }),
  );
  const body = response.body;
  const object = body !== null && typeof body === "object" && !Array.isArray(body);
  if (response.status === 404 && object && Object.hasOwn(body, "detail") &&
      body.detail !== null && typeof body.detail === "object" && Object.hasOwn(body.detail, "code") &&
      body.detail.code === "tenant_home_missing") {
    return { url: TANK_URL, key: TANK_API_KEY, home: null };
  }
  if (response.status !== 200 || !object || !Object.hasOwn(body, "tenant_id") ||
      !Object.hasOwn(body, "home_region") || body.tenant_id !== tenantId ||
      !["us-east", "za"].includes(body.home_region)) {
    throw new Error("The tenant home region is unavailable.");
  }
  const home = body.home_region as Region;
  const target = contentTank(home);
  if (!target) throw new Error("The tenant home region is not connected for content.");
  return { ...target, home };
}

export function requestTenant(path: string, init: RequestInit): string | null {
  const query = new URL(path, TANK_URL).searchParams.get("tenant_id");
  let bodyTenant: string | null = null;
  if (typeof init.body === "string") {
    try {
      const value = JSON.parse(init.body);
      bodyTenant = value && typeof value === "object" && !Array.isArray(value) &&
        Object.hasOwn(value, "tenant_id") && typeof value.tenant_id === "string" ? value.tenant_id : null;
    } catch { /* No JSON tenant on this request. */ }
  }
  if (query && bodyTenant && query !== bodyTenant) throw new Error("Conflicting tenant identities.");
  return query || bodyTenant;
}


export async function workspaceRequest<T>(identity: {tenantId: string; userId: string},
                                         path: string, init: RequestInit = {}): Promise<T> {
  const target = await tenantDestination(identity.tenantId);
  const headers = new Headers(init.headers);
  headers.set("content-type", "application/json");
  headers.set("x-tank-key", target.key);
  headers.set("x-tervory-user", identity.userId);
  headers.set("x-tervory-tenant", identity.tenantId);
  headers.set("x-tervory-home-region", target.home ?? (process.env.TANK_REGION === "za" ? "za" : "us-east"));
  headers.delete("x-tervory-squad");
  headers.delete("x-tervory-role");
  return boundedFetch(new URL("/v1/workspace" + path, target.url),
    {...init, headers, cache: "no-store"}, 30_000, async response => {
      if (!response.ok) throw new CommandRequestError(response.status, "request_failed", "This result is unavailable.");
      return await response.json() as T;
    });
}

export async function onboardTenantHome(userId: string, actingTenant: string, tenantId: string, home: Region) {
  const target = contentTank("us-east");
  if (!target) throw new Error("The tenant region directory is not configured.");
  return boundedFetch(new URL("/v1/tenants/" + encodeURIComponent(tenantId) + "/home", target.url),
    {method: "PUT", cache: "no-store", headers: {"content-type": "application/json",
      "x-tank-key": target.key, "x-tervory-user": userId, "x-tervory-tenant": actingTenant},
      body: JSON.stringify({home_region: home})}, 10_000, async response => {
        if (!response.ok) throw new CommandRequestError(response.status, "onboarding_failed", "The home region was not set.");
        return await response.json();
      });
}

function regionalCall(target: {url: string; key: string; home: Region | null}, raw: URL | string, init: RequestInit) {
  const source = new URL(String(raw), TANK_URL);
  if (source.origin !== new URL(TANK_URL).origin) throw new Error("Unknown workspace endpoint.");
  const url = new URL(source.pathname + source.search, target.url);
  const headers = new Headers(init.headers);
  headers.set("x-tank-key", target.key);
  headers.set("x-tervory-home-region", target.home ?? (process.env.TANK_REGION === "za" ? "za" : "us-east"));
  return {url, init: {...init, headers, cache: "no-store" as RequestCache, redirect: "manual" as RequestRedirect}};
}

/** Resolve authority before transporting private content. Never follow a redirect. */
export async function tenantFetch(tenant: string | null, raw: URL | string, init: RequestInit = {}): Promise<Response> {
  const target = tenant ? await tenantDestination(tenant, 10_000, init.signal ?? undefined) : {url: TANK_URL, key: TANK_API_KEY, home: null};
  const call = regionalCall(target, raw, init);
  const response = await fetch(call.url, call.init);
  if (isRedirect(response)) {
    await response.body?.cancel().catch(() => undefined);
    throw new RedirectError(response.status);
  }
  return response;
}

async function tenantBoundedFetch<T>(tenant: string, raw: URL | string, init: RequestInit, timeoutMs: number,
                                   read: (response: Response) => Promise<T>): Promise<T> {
  const until = Date.now() + timeoutMs;
  const target = await tenantDestination(tenant, Math.min(timeoutMs, 10_000));
  const call = regionalCall(target, raw, init);
  return boundedFetch(call.url, call.init, Math.max(1, until - Date.now()), read);
}
