/** Plain-English labels for Autumbra owner pages. Never show raw codes or UUIDs. */

export function formatWhen(iso?: string | null, timeZone?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const tz = (timeZone || "").trim() || "UTC";
  try {
    const stamp = d.toLocaleString("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: tz,
    });
    return `${stamp} ${tz === "UTC" ? "UTC" : tz}`;
  } catch {
    const stamp = d.toLocaleString("en-US", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: "UTC",
    });
    return `${stamp} UTC`;
  }
}

export function outcomeLabel(outcome?: string | null): string {
  switch ((outcome || "").toLowerCase()) {
    case "applied":
      return "We blocked an attacking address";
    case "undone":
      return "We removed that block";
    case "already_undone":
      return "That block was already removed";
    case "undo_failed":
      return "We could not remove that block (it may already be gone)";
    case "queued":
      return "Waiting to act";
    case "awaiting_approval":
      return "Waiting for your approval";
    case "report_only":
      return "Reported only — no block applied";
    default:
      return "Autumbra update";
  }
}

export function severityLabel(severity?: string | null): string {
  switch ((severity || "").toLowerCase()) {
    case "known_exploited":
      return "actively exploited";
    case "critical":
      return "critical";
    case "high":
      return "high";
    case "medium":
      return "medium";
    case "low":
      return "low";
    default:
      return "";
  }
}

export function describeReceipt(
  row: {
    outcome?: string | null;
    severity?: string | null;
    site?: string | null;
    address?: string | null;
    timestamp?: string | null;
  },
  timeZone?: string | null,
): { title: string; when: string } {
  const what = outcomeLabel(row.outcome);
  const site = (row.site || "").trim();
  const address = (row.address || "").trim();
  const sev = severityLabel(row.severity);
  const parts: string[] = [what];
  if (site) parts.push(`on ${site}`);
  if (address) parts.push(`(${address})`);
  if (sev) parts.push(`— ${sev}`);
  return { title: parts.join(" "), when: formatWhen(row.timestamp, timeZone) };
}
