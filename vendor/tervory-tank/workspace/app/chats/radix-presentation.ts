import type { Card, RadixFinding, RadixView } from "./contract";
const tools = new Set(["radix.domain.prove", "radix.passive.scan", "radix.findings", "radix.repo.scan", "radix.repo.findings"]);
const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === "string" ? value : undefined;
const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
/** Allowlist only the approved Radix presentation DTO; never pass the adapter payload. */
export function projectRadix(card: Card): RadixView | undefined {
  if (!tools.has(card.tool ?? "")) return;
  const result = record(card.result), args = record(card.arguments);
  const kind = card.tool === "radix.domain.prove" ? "proof" : (card.tool === "radix.findings" || card.tool === "radix.repo.findings") ? "findings" : "scan";
  const view: RadixView = { kind, domain: text(args.domain), target: text(args.url), scan_id: text(result.scan_id) ?? text(args.scan_id) };
  if (kind === "proof") return { ...view, name: text(result.name), value: text(result.value), expires_at: text(result.expires_at) };
  if (["pending", "running", "failed", "complete"].includes(String(result.status))) view.status = text(result.status);
  view.completed_at = text(result.completed_at);
  if (!card.tool?.startsWith("radix.repo.")) { view.high = count(result.high); view.critical = count(result.critical); }
  if (Array.isArray(result.findings)) view.findings = result.findings.map((item): RadixFinding => {
    const row = record(item);
    return { tool: text(row.tool), severity: text(row.severity), rule: text(row.rule), file: text(row.file), line: count(row.line), explanation: text(row.explanation), remediation: text(row.remediation) };
  });
  if (card.tool === "radix.repo.findings" && result.scan_complete === false && Array.isArray(result.findings) && result.findings.length === 0) view.status = "failed";
  return view;
}
