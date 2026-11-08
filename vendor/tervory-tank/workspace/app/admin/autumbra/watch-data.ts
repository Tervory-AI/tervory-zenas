import type { GhostPanel } from "@/lib/tank";

export function isUsableGhostPanel(data: GhostPanel | null | undefined): data is GhostPanel {
  if (!data || typeof data !== "object") return false;
  if (!Array.isArray(data.incidents)) return false;
  if (!data.monitor || typeof data.monitor.running !== "boolean") return false;
  if (!Number.isSafeInteger(data.monitor.unavailable_checks) || data.monitor.unavailable_checks < 0) return false;
  if (data.incidents.some(item => !item || !Number.isFinite(item.cost_usd) || item.cost_usd < 0
      || !Array.isArray(item.steps))) return false;
  if (!data.integrations || typeof data.integrations !== "object") return false;
  return true;
}
