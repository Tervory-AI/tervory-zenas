/** Closed read-only Workspace corridor; record ownership remains authoritative in Tank. */
export const CRM_MODULES = ["leads", "contacts", "estimates"] as const;
export type CrmModule = typeof CRM_MODULES[number];
export function crmReadPath(parts: readonly string[]): string | null {
  if (parts.length < 1 || parts.length > 2 || !CRM_MODULES.some(module => module === parts[0])) return null;
  if (parts.length === 2 && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(parts[1])) return null;
  return parts.join("/");
}
/** Unknown filters fail rather than silently widening results. */
export function crmReadQuery(query: URLSearchParams, detail: boolean): URLSearchParams | null {
  const result = new URLSearchParams();
  for (const [key, value] of query) {
    if (detail || key !== "page" || result.has(key) || !/^[1-9][0-9]{0,4}$/.test(value) || Number(value) > 10000) return null;
    result.set(key, value);
  }
  return result;
}
