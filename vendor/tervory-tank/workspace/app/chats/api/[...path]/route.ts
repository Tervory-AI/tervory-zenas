import { triggerIdentity } from "@/lib/trigger";
import { activeTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { commandRequest, commandTools, CommandRequestError, listRoster } from "@/lib/tank";
import { modelValue } from "../../models";
import { clientLabel, presentCard, presentReceipt, presentSnapshot, presentThread, visibleCard } from "../../presentation";
import { normalizeSnapshot, readEvents, type Card, type Receipt, type Snapshot, type Thread } from "../../contract";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
// Coolify ends HTTPS before Next, so request.url is http:// while the browser's Origin is https://.
// Compare hosts, not full origins; a browser cannot forge the Host header.
const sameHost = (request: Request) => { try { return new URL(request.headers.get("origin") ?? "").host === (request.headers.get("host") ?? new URL(request.url).host); } catch { return false; } };
type Context = { params: Promise<{ path: string[] }> };
const json = (data: unknown, status = 200) => Response.json(data, { status, headers: { "Cache-Control": "no-store" } });
const attachmentId = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const filename = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 200 && !/[\\/\x00-\x1f\x7f]/.test(value) && ![".", ".."].includes(value);
const disposition = (name: string) => `attachment; filename*=UTF-8''${encodeURIComponent(name).replace(/['()*]/g, char => `%${char.charCodeAt(0).toString(16)}`)}`;
function download(upstream: Response, name?: string) {
  // Forward no cookies, redirects, inline media types or unvalidated headers.
  const header = upstream.headers.get("content-disposition") ?? "";
  const safe = /^attachment; filename\*=UTF-8''[a-zA-Z0-9!#$&+.^_`|~%\-]*$/.test(header) ? header : disposition("download");
  return new Response(upstream.body, { headers: { "Content-Type": "application/octet-stream", "Content-Disposition": name ? disposition(name) : safe, "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" } });
}
async function boundedBody(request: Request, limit: number): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) { const { value, done } = await reader.read(); if (done) break; size += value.length; if (size > limit) throw new CommandRequestError(413, "attachment_limit", "Attach files no larger than 5 MB."); chunks.push(value); }
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return bytes;
}
async function handle(request: Request, context: Context): Promise<Response> {
  try {
    if (request.method !== "GET" && !sameHost(request)) return json({ error: "This request must come from your workspace." }, 403);
    const identity = await triggerIdentity(await activeTenant());
    if (!identity) return json({ error: "Please sign in and choose your workspace." }, 401);
    const founder = isFounder(identity.userId);
    const { path } = await context.params;
    if (path.some(part => !/^[a-zA-Z0-9_-]+$/.test(part))) return json({ error: "Unknown request." }, 404);
    const target = path.join("/");
    const method = request.method;
    if (target === "tools" && method === "GET") return json(await commandTools(identity));
    const radixReport = path.length === 4 && path[0] === "radix" && path[1] === "reports" && attachmentId(path[2]) && ["html", "pdf"].includes(path[3]);
    const radixEvidence = path.length === 5 && path[0] === "radix" && path[1] === "reports" && attachmentId(path[2]) && path[3] === "evidence" && /^\d+$/.test(path[4]);
    if (method === "GET" && radixReport) return download(await commandRequest(identity, target, { signal: request.signal }));
    if (method === "GET" && radixEvidence) return json(await (await commandRequest(identity, target, { signal: request.signal })).json());
    const fileRequest = /^threads\/[\w-]+\/files\/[\w-]+\/\d+$/.test(target) && method === "GET";
    if (fileRequest) {
      const snapshot: Snapshot = await (await commandRequest(identity, `threads/${path[1]}`, { signal: request.signal })).json();
      const file = snapshot.messages.find(message => message.message_id === path[3])?.attachments?.[Number(path[4])];
      if (!file) return json({ error: "This file is unavailable." }, 404);
      if (file.attachment_id && attachmentId(file.attachment_id)) return download(await commandRequest(identity, `attachments/${file.attachment_id}`, { signal: request.signal }), file.name);
      if (typeof file.text !== "string") return json({ error: "This file is unavailable." }, 404);
      return new Response(file.text, { headers: { "Content-Type": "text/plain; charset=utf-8", "Content-Disposition": disposition(file.name), "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" } });
    }
    if (path.length === 2 && path[0] === "attachments" && attachmentId(path[1]) && method === "GET") return download(await commandRequest(identity, target, { signal: request.signal }));
    if (/^threads\/[\w-]+\/attachments$/.test(target) && method === "POST") {
      const name = new URL(request.url).searchParams.get("name");
      if (!filename(name)) return json({ error: "Use a filename of 1 to 200 characters without slashes or control characters." }, 400);
      const bytes = await boundedBody(request, 5 * 1024 * 1024);
      const result = await (await commandRequest(identity, `${target}?${new URLSearchParams({ name })}`, { method, body: bytes as BodyInit, headers: { "content-type": "application/octet-stream" }, signal: request.signal })).json();
      return json({ attachment_id: result.attachment_id, name, size: result.size, model_readable: result.model_readable === true });
    }
    const valid = (target === "threads" && ["GET", "POST"].includes(method))
      || (/^threads\/[\w-]+$/.test(target) && ["GET", "PATCH"].includes(method))
      || (/^threads\/[\w-]+\/turn$/.test(target) && method === "POST")
      || (/^threads\/[\w-]+\/events$/.test(target) && method === "GET")
      || (/^cards\/[\w-]+$/.test(target) && method === "POST");
    if (!valid) return json({ error: "Unknown request." }, 404);
    const roster = await listRoster();
    const names = roster.flatMap(model => [model.name, model.providerModel]).filter(Boolean);
    let body: Record<string, unknown> | undefined;
    if (method !== "GET") {
      const raw = await request.text();
      if (raw.length > 8_000_000) return json({ error: "Files must total less than 5 MB." }, 413);
      const input = JSON.parse(raw);
      if (target.startsWith("cards/")) {
        if (!["confirm", "cancel"].includes(input.action)) return json({ error: "Choose Confirm or Cancel." }, 400);
        body = { action: input.action };
      } else if (target.endsWith("/turn")) {
        if (typeof input.text !== "string" || !input.text.trim() || input.text.length > 20000) return json({ error: "Write a message of up to 20,000 characters." }, 400);
        if (!founder && input.model && input.model !== "auto") return json({ error: "Your account uses Auto." }, 403);
        const model = !input.model || input.model === "auto" ? "auto" : roster.find(row => modelValue(row.name) === input.model)?.name;
        if (!model) return json({ error: "That option is no longer available. Choose another." }, 400);
        const attachments = input.attachments ?? [];
        if (!Array.isArray(attachments) || attachments.length > 5 || attachments.some(file => !file || (file.attachment_id !== undefined ? !attachmentId(file.attachment_id) : !filename(file.name) || typeof file.text !== "string" || file.text.length > 200000 || file.text.includes("\0")))) return json({ error: "Attach up to five stored files or text files of no more than 200,000 characters each." }, 400);
        body = { text: input.text, model, ...(input.priority === undefined ? {} : { priority: input.priority }), attachments: attachments.map(file => file.attachment_id ? { attachment_id: file.attachment_id } : { name: file.name, text: file.text }) };
      } else {
        if (input.title !== undefined && (typeof input.title !== "string" || !input.title.trim() || input.title.length > 160)) return json({ error: "Use a chat name of 1 to 160 characters." }, 400);
        body = input.title ? { title: input.title.trim() } : {};
      }
    }
    let query = "";
    if (target.endsWith("/events")) {
      const after = new URL(request.url).searchParams.get("after") ?? "0";
      if (!/^\d+$/.test(after)) return json({ error: "Invalid update position." }, 400);
      query = `?after=${after}`;
    }
    const upstream = await commandRequest(identity, target + query, { method, body: body ? JSON.stringify(body) : undefined, signal: request.signal });
    if (upstream.headers.get("content-type")?.includes("text/event-stream")) {
      if (!upstream.body) throw new Error("Missing stream");
      const encoder = new TextEncoder();
      const cancelled = new AbortController();
      let closed = false;
      const abort = () => cancelled.abort();
      request.signal.addEventListener("abort", abort, { once: true });
      if (request.signal.aborted) abort();
      const stream = new ReadableStream<Uint8Array>({
        async start(controller) {
          const emit = (type: string, data: unknown, seq?: number) => { if (!closed && !cancelled.signal.aborted) controller.enqueue(encoder.encode(`${seq === undefined ? "" : `id: ${seq}\n`}event: ${type}\ndata: ${JSON.stringify(data)}\n\n`)); };
          try {
            await readEvents(upstream.body!, event => {
              let data: unknown = event.data;
              if (event.type === "error" && !founder) data = { message: "The response was interrupted. Reopen this chat before sending again." };
              if (!founder) {
                if (event.type === "receipt") data = presentReceipt(event.data as unknown as Receipt, false);
                if (event.type === "step") data = { ...event.data, label: clientLabel(String(event.data.label), names), actor: clientLabel(String(event.data.actor), names) };
                if (event.type === "card") { if (!visibleCard(event.data as unknown as Card, false)) return; data = presentCard(event.data as unknown as Card, false, names); }
              }
              if (founder && event.type === "card") data = presentCard(event.data as unknown as Card, true, names);
              if (["token", "step", "card", "receipt", "done", "error"].includes(event.type)) emit(event.type, data, event.seq);
            }, cancelled.signal);
          } catch { emit("error", { message: "The response was interrupted. Reopen this chat before sending again." }); }
          finally { request.signal.removeEventListener("abort", abort); if (!closed) { closed = true; controller.close(); } }
        },
        cancel() { closed = true; cancelled.abort(); },
      });
      return new Response(stream, { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no" } });
    }
    if (upstream.status === 204) return json({ ok: true });
    const result = await upstream.json();
    if (method === "GET" && target === "threads") return json((result as Thread[]).map(row => presentThread(row)));
    if (method === "GET" && path.length === 2 && path[0] === "threads") return json(presentSnapshot(normalizeSnapshot(result as Snapshot), founder, names));
    if (target.startsWith("cards/")) return json(visibleCard(result as Card, founder) ? presentCard(result as Card, founder, names) : { ok: true });
    // Writes only acknowledge success; authoritative state is refreshed from the thread.
    return json(target === "threads" && method === "POST" ? { thread_id: result.thread_id } : { ok: true });
  } catch (error) { if (error instanceof CommandRequestError) return json({ error: error.message, code: error.code }, error.status); return json({ error: "This request could not be completed. Reload the chat before trying again." }, 502); }
}
export const GET = handle;
export const POST = handle;
export const PATCH = handle;
