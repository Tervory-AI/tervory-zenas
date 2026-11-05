// COMMAND-1 Lane A wire contract; server-free so streaming can be tested offline.
export type Money = number | null;
export type Totals = { cost_usd: Money; baseline_cost_usd: Money; saved_usd: Money; auto_cost_usd?: Money };
export type ReceiptRow = Totals & { paid_step_id?: string; paid_step_mode?: "approved" | "automatic"; paid_step_approval_id?: string; step_id: string; model?: string; baseline_model?: string; tokens_in?: number; tokens_out?: number; ledger_id: string; cost_status?: string; baseline_status?: string; requested_model?: string; auto_model?: string | null; auto_cost_status?: string; auto_priced_at?: string | null; auto_cost_label?: string; auto_cost_basis?: string };
export type Receipt = { rows: ReceiptRow[]; totals: Totals; status?: string; step_id?: string };
export type Attachment = { name: string; content_type: string; text?: string; attachment_id?: string; size?: number; model_readable?: boolean };
export type Message = { message_id: string; role: "user" | "assistant"; content: string; receipt?: Receipt; attachments?: Attachment[] };
export type Step = { phase_finished?: boolean; paused?: boolean; step_id: string; label: string; actor: string; status: "planned" | "running" | "done" | "failed"; link?: string; receipt_status?: string };
export type CardDetail = { label: string; value: string; href?: string };
export type RadixFinding = { tool?: string; severity?: string; rule?: string; file?: string; line?: number; explanation?: string; remediation?: string };
export type RadixView = { kind: "proof" | "scan" | "findings"; domain?: string; name?: string; value?: string; expires_at?: string; scan_id?: string; target?: string; status?: string; completed_at?: string; high?: number; critical?: number; findings?: RadixFinding[] };
export type Card = { radix?: RadixView; card_id: string; kind: "approve_skill" | "paid_run" | "build" | "retire" | "other"; summary: string; cost_estimate_usd?: Money; status?: string; tool?: string; arguments?: Record<string, unknown>; result?: unknown; retry_safe?: boolean; error?: string; receipt_status?: string; targets?: CardDetail[]; results?: CardDetail[] };
export type Thread = { thread_id: string; title: string; updated_at: string; spent_usd: Money; saved_usd: Money; receipt_status?: string };
export type Snapshot = { thread: Thread; messages: Message[]; steps: Step[]; cards: Card[]; seq?: number; receipts?: ReceiptRow[]; job_receipts?: Receipt[] };
export type ModelOption = { value: string; label: string; inputPerM: Money; outputPerM: Money };
export type Event = { type: string; data: Record<string, unknown>; seq?: number };
export function dollars(value: Money | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `$${value.toFixed(value !== 0 && Math.abs(value) < .01 ? 6 : 2)}` : "Unavailable";
}
export function safeLink(value: string | undefined): string | undefined {
  if (!value || /[\\\u0000-\u0020]/.test(value)) return;
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try { const url = new URL(value); if (url.protocol === "https:" && !url.username && !url.password) return url.href; } catch { /* Not a link. */ }
}
/** Decode UTF-8 incrementally; SSE records can be split at any byte boundary. */
export async function readEvents(stream: ReadableStream<Uint8Array>, receive: (event: Event) => void, signal?: AbortSignal) {
  const reader = stream.getReader(); const decoder = new TextDecoder(); let buffer = "";
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  function dispatch(record: string) {
    let type = "message"; let id: number | undefined; const data: string[] = [];
    for (const line of record.split(/\r?\n/)) {
      if (line.startsWith("event:")) type = line.slice(6).trim();
      if (line.startsWith("id:")) { const n = Number(line.slice(3).trim()); if (Number.isSafeInteger(n) && n >= 0) id = n; }
      if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) return;
    const payload = JSON.parse(data.join("\n")) as Record<string, unknown>;
    receive({ type, data: payload, seq: typeof payload.seq === "number" ? payload.seq : id });
  }
  try {
    while (true) {
      const { value, done } = await reader.read(); buffer += decoder.decode(value, { stream: !done });
      if (signal?.aborted) break;
      let boundary: RegExpMatchArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) { dispatch(buffer.slice(0, boundary.index)); buffer = buffer.slice(boundary.index! + boundary[0].length); }
      if (buffer.length > 2_000_000) throw new Error("The response was too large. Reopen the chat to continue.");
      if (done) { if (buffer.trim()) dispatch(buffer); break; }
    }
  } finally { signal?.removeEventListener("abort", cancel); await reader.cancel().catch(() => {}); reader.releaseLock(); }
}
export function upsert<T>(rows: T[], row: T, key: keyof T): T[] {
  return rows.some(item => item[key] === row[key]) ? rows.map(item => item[key] === row[key] ? row : item) : [...rows, row];
}

export function receiptFor(rows: ReceiptRow[], step_id?: string, pending = false): Receipt {
  const sum = (key: "cost_usd" | "baseline_cost_usd" | "saved_usd" | "auto_cost_usd") => !pending && rows.length && rows.every(row => typeof row[key] === "number" && Number.isFinite(row[key])) ? rows.reduce((total, row) => total + row[key]!, 0) : null;
  // Pinned turns carry Tank's "What Auto would have cost" (same recorded tokens at the
  // policy model's price book, frozen at acceptance). Total it only when every row is priced.
  const auto = rows.some(row => row.auto_cost_usd !== undefined) ? { auto_cost_usd: sum("auto_cost_usd") } : {};
  return { step_id, rows, status: pending || !rows.length || rows.some(row => row.cost_usd === null) ? "pending" : "recorded", totals: { cost_usd: sum("cost_usd"), baseline_cost_usd: sum("baseline_cost_usd"), saved_usd: sum("saved_usd"), ...auto } };
}
/** Reconstruct durable empty receipts from the step/card markers, without inventing zero. */
export function normalizeSnapshot(snapshot: Snapshot): Snapshot {
  const groups = new Map<string, Receipt>();
  for (const receipt of snapshot.job_receipts ?? []) if (receipt.step_id) groups.set(receipt.step_id, receipt);
  for (const row of snapshot.receipts ?? []) {
    const rows = groups.get(row.step_id)?.rows ?? [];
    groups.set(row.step_id, receiptFor(upsert(rows, row, "ledger_id"), row.step_id));
  }
  const pending = new Set([
    ...snapshot.steps.filter(step => step.receipt_status === "pending").map(step => step.step_id),
    ...snapshot.cards.filter(card => card.receipt_status === "pending" || ["executing", "unknown"].includes(card.status ?? "")).map(card => card.card_id),
  ]);
  for (const id of pending) groups.set(id, receiptFor(groups.get(id)?.rows ?? [], id, true));
  const messages = snapshot.messages.map(message => {
    const receipt = groups.get(message.message_id) ?? message.receipt;
    groups.delete(message.message_id);
    return { ...message, receipt };
  });
  return { ...snapshot, thread: pending.size ? { ...snapshot.thread, receipt_status: "pending", spent_usd: null, saved_usd: null } : snapshot.thread, messages, job_receipts: [...groups.values(), ...(snapshot.job_receipts ?? []).filter(receipt => !receipt.step_id)] };
}
/** A work receipt must never be attached to whichever answer happens to be streaming. */
export function applyReceipt(snapshot: Snapshot, receipt: Receipt, answerId: string): Snapshot {
  const ids = receipt.step_id ? [receipt.step_id] : [...new Set(receipt.rows.map(row => row.step_id))];
  if (!ids.length) return snapshot;
  let next = snapshot;
  for (const id of ids) {
    const isAnswer = snapshot.steps.some(step => step.step_id === id && step.actor === "Tervory") && !snapshot.cards.some(card => card.card_id === id);
    const messageId = snapshot.messages.some(message => message.message_id === id) ? id : isAnswer ? answerId : undefined;
    const previous = messageId ? next.messages.find(message => message.message_id === messageId)?.receipt : next.job_receipts?.find(item => item.step_id === id);
    const incoming = receipt.rows.filter(row => row.step_id === id);
    const rows = incoming.reduce((all, row) => upsert(all, row, "ledger_id"), previous?.rows ?? []);
    // Receipt events are row deltas, so keep earlier evidence for the same step.
    const part = receiptFor(rows, id, receipt.status === "pending");
    // Aggregate only this step's durable rows; event totals may cover several steps.
    // Auto comparisons arrive on ledger rows and must survive snapshot/replay.
    if (messageId) next = { ...next, messages: next.messages.map(message => message.message_id === messageId ? { ...message, receipt: part } : message) };
    else next = { ...next, job_receipts: upsert(next.job_receipts ?? [], part, "step_id") };
  }
  if (receipt.status === "pending") next = { ...next, thread: { ...next.thread, receipt_status: "pending", spent_usd: null, saved_usd: null } };
  return next;
}
export function canConfirm(card: Card): boolean {
  return ["pending", "awaiting_confirmation"].includes(card.status ?? "pending") || (card.status === "failed" && card.retry_safe === true);
}
