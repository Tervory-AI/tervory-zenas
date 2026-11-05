import type { GhostPanel } from "@/lib/tank";

export function isUsableGhostPanel(data: GhostPanel | null | undefined): data is GhostPanel {
  if (!data || typeof data !== "object") return false;
  if (!Array.isArray(data.incidents)) return false;
  if (!data.monitor || typeof data.monitor.running !== "boolean") return false;
  if (typeof data.monitor.unavailable_checks !== "number") return false;
  if (!data.integrations || typeof data.integrations !== "object") return false;
  return true;
}
