import { projectRadix } from "./radix-presentation";
import { dollars, safeLink, type Card, type CardDetail, type Receipt, type Snapshot, type Thread } from "./contract";
/** Apply only to generated interface labels, never conversation or result text. */
export function clientLabel(text: string, modelNames: string[] = []): string {
  let result = text;
  for (const name of [...modelNames].sort((a, b) => b.length - a.length)) {
    if (name) result = result.replace(new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), "selected option");
  }
  return result.replace(/\b(?:Skala|Tank|ledger|evals?|gold|shadow|retire|rungs?|tenants?|region box)\b/gi, "service");
}
export function presentReceipt(receipt: Receipt, founder: boolean): Receipt {
  if (founder) return receipt;
  return { status: receipt.status, step_id: receipt.step_id, totals: receipt.totals, rows: receipt.rows.map(row => ({ step_id: row.step_id, ledger_id: row.ledger_id, cost_usd: row.cost_usd, baseline_cost_usd: row.baseline_cost_usd, saved_usd: row.saved_usd, auto_cost_usd: row.auto_cost_usd, cost_status: row.cost_status, baseline_status: row.baseline_status })) };
}
export function visibleCard(card: Card, founder: boolean) { return founder || ((!card.tool || !/^(skala|ledger|worker)\./.test(card.tool)) && (card.kind === "paid_run" || card.kind === "other")); }
/** Only named, relevant fields cross the UI boundary; never dump adapter payloads. */
export function presentCard(card: Card, founder: boolean, models: string[] = []): Card {
  const clean = (value: string) => founder ? value : clientLabel(value, models);
  const details = (input: unknown, fields: Record<string, string>): CardDetail[] => {
    if (!input || typeof input !== "object" || Array.isArray(input)) return [];
    const source = input as Record<string, unknown>;
    return Object.entries(fields).flatMap(([key, label]) => {
      const value = source[key];
      if (key.endsWith("_usd") && (value === null || typeof value === "number")) return [{ label, value: dollars(value) }];
      if (!["string", "number", "boolean"].includes(typeof value)) return [];
      if (/url$|^link$/.test(key)) {
        try { const url = new URL(String(value)); if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return []; return [{ label, value: url.href, href: safeLink(url.href) }]; } catch { return []; }
      }
      return [{ label, value: String(value) }];
    });
  };
  const args = card.arguments ?? {};
  const targets = details(args, { url: "URL", label: "Label", batch: "Batch", engine: "Engine", q: "Search", question: "Question", product_id: "Product", name: "Name", slug: "Site", jurisdiction: "Jurisdiction", title: "Title", matter_id: "Matter", playbook_id: "Review guide", skill_id: "Skill", version: "Version", task: "Task", days: "Days", limit: "Limit", page: "Page", brief_markdown: "Brief" });
  for (const [key, label] of [["records", "Records"], ["files", "Files"], ["pages", "Pages"]]) {
    const list = args[key];
    if (Array.isArray(list)) targets.push({ label, value: key === "records" ? String(list.length) : list.map(item => typeof item === "string" ? item : item?.path ?? "File").join(", ") });
  }
  if (typeof args.text === "string") targets.push({ label: "Document", value: `${args.text.length.toLocaleString("en-US")} characters` });
  const resultFields = { ok: "Success", success: "Success", status: "Status", message: "Message", answer_md: "Answer", summary: "Summary", answer: "Answer", text: "Response", title: "Title", name: "Name", url: "Result", pr_url: "Review", link: "Result", count: "Count", total: "Total", n: "Count", queued: "Queued", batch: "Batch", job_id: "Job", build_id: "Build", matter_id: "Matter", watch_id: "Watch", task_type: "Task", calls: "Calls", actual_usd: "Cost", cost_usd: "Cost", baseline_usd: "Would have cost", baseline_cost_usd: "Would have cost", saved_usd: "Saved", receipt_status: "Cost status", ...(founder ? { model: "Model", ledger_id: "Ledger entry" } : {}) };
  const results = card.tool?.startsWith("radix.repo.") ? [] : details(card.result, resultFields);
  const collection = (label: string, list: unknown[]) => {
    results.push({ label, value: `${list.length} items${list.length > 5 ? " (first 5 shown)" : ""}` });
    list.slice(0, 5).forEach((item, index) => results.push(...details(item, resultFields).map(detail => ({ ...detail, label: `${label} ${index + 1} · ${detail.label}` }))));
  };
  if (Array.isArray(card.result)) collection("Results", card.result);
  if (card.result && typeof card.result === "object") for (const key of ["items", "results", "records", "pages", "findings", "tasks", "ledger"]) {
    const list = (card.result as Record<string, unknown>)[key];
    if (key === "findings" && projectRadix(card)) continue;
    if (Array.isArray(list)) collection(key[0].toUpperCase() + key.slice(1), list);
  }
  return { card_id: card.card_id, tool: card.tool, kind: card.kind, summary: clean(card.summary), status: card.status, cost_estimate_usd: card.cost_estimate_usd, retry_safe: card.retry_safe === true, receipt_status: card.receipt_status,
    targets, results, radix: projectRadix(card), error: card.error ? clean(card.error) : undefined };
}
export function presentThread(thread: Thread): Thread { return { ...thread }; }
export function presentSnapshot(snapshot: Snapshot, founder: boolean, models: string[] = []): Snapshot {
  const clean = (value: string) => founder ? value : clientLabel(value, models);
  return { seq: snapshot.seq, thread: presentThread(snapshot.thread),
    messages: snapshot.messages.map(message => ({ ...message, content: message.content, receipt: message.receipt && presentReceipt(message.receipt, founder), attachments: message.attachments?.map(file => ({ name: file.name, content_type: file.content_type, attachment_id: file.attachment_id, size: file.size, model_readable: file.model_readable })) })),
    steps: snapshot.steps.map(step => ({ ...step, label: clean(step.label), actor: clean(step.actor) })),
    job_receipts: snapshot.job_receipts?.map(receipt => presentReceipt(receipt, founder)),
    cards: snapshot.cards.filter(card => visibleCard(card, founder)).map(card => presentCard(card, founder, models)),
  };
}
