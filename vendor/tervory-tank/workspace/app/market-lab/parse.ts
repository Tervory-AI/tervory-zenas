import { COMMODITIES } from "./catalog";
import { formatDay } from "./format";

export type Notice = { title: string; meta: string };
export type PriceLine = { label: string; value: string };

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

/** Exact digits from the source. No rounding and no thousands separators. */
export function exactNumber(value: unknown): string | null {
  if (typeof value === "string") {
    const text = value.trim();
    if (!text || text === ".") return null;
    return text;
  }
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

export function parseEia(payload: unknown): { value: string; period: string } | null {
  const root = asRecord(payload);
  if (!root) return null;
  const response = asRecord(root.response) ?? root;
  const data = response.data;
  if (!Array.isArray(data)) return null;
  let best: { value: string; period: string } | null = null;
  for (const row of data) {
    const record = asRecord(row);
    if (!record) continue;
    const period = String(record.period ?? "").slice(0, 10);
    const value = exactNumber(record.value);
    if (!period || !value) continue;
    if (!best || period > best.period) best = { value, period };
  }
  return best;
}

export function parseFred(payload: unknown): { value: string; date: string } | null {
  const root = asRecord(payload);
  if (!root || !Array.isArray(root.observations)) return null;
  let best: { value: string; date: string } | null = null;
  for (const row of root.observations) {
    const record = asRecord(row);
    if (!record) continue;
    const date = String(record.date ?? "").slice(0, 10);
    const value = exactNumber(record.value);
    if (!date || !value) continue;
    if (!best || date > best.date) best = { value, date };
  }
  return best;
}

function indicatorCode(record: Record<string, unknown>): string | null {
  const indicator = asRecord(record.indicator);
  if (!indicator || indicator.id == null || indicator.id === "") return null;
  return String(indicator.id);
}

export function parseWorldBank(payload: unknown, code: string): { label: string; value: string; date: string } | null {
  if (!Array.isArray(payload) || payload.length < 2 || !Array.isArray(payload[1])) return null;
  const known = COMMODITIES.find((item) => item.code === code);
  const label = known?.label ?? code;
  let best: { label: string; value: string; date: string } | null = null;
  for (const row of payload[1]) {
    const record = asRecord(row);
    if (!record) continue;
    const id = indicatorCode(record);
    if (id && id !== code) continue;
    const value = exactNumber(record.value);
    const date = String(record.date ?? "").slice(0, 10);
    if (!value || !date) continue;
    const stamped = date.length === 4 ? `${date}-01-01` : date;
    if (!best || stamped > best.date) best = { label, value, date: stamped };
  }
  return best;
}

export function parseAlerts(payload: unknown): { count: number; example: string | null } | null {
  const root = asRecord(payload);
  if (!root || !Array.isArray(root.features)) return null;
  const first = asRecord(root.features[0]);
  const props = first ? asRecord(first.properties) : null;
  const example = typeof props?.event === "string" ? props.event.trim() : "";
  return { count: root.features.length, example: example || null };
}

export function parseRegister(payload: unknown): { notices: Notice[] } | null {
  const root = asRecord(payload);
  if (!root || !Array.isArray(root.results)) return null;
  const notices: Notice[] = [];
  for (const row of root.results) {
    const record = asRecord(row);
    if (!record) continue;
    const title = typeof record.title === "string" ? record.title.trim() : "";
    const date = String(record.publication_date ?? "").slice(0, 10);
    const kind = typeof record.type === "string" && record.type.trim() ? record.type.trim() : "Notice";
    if (!title || !/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;
    notices.push({ title, meta: `${kind} · ${formatDay(date)}` });
    if (notices.length === 2) break;
  }
  return { notices };
}

export function parseEcfr(payload: unknown): { date: string } | null {
  const root = asRecord(payload);
  if (!root) return null;
  const date = String(root.date ?? "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  return { date };
}
