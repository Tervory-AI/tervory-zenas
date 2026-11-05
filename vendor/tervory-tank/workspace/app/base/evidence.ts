/** Only recorded evidence counts. Upload time and a claimed website are not a check. */
export type EvidenceRecord = {
  status?: string;
  error?: string | null;
  profile?: Record<string, { value: unknown; source?: string } | undefined> | null;
  passes?: Record<string, Record<string, unknown>>;
};

export function sourceUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    if ([...url.searchParams.keys()].some((key) => /token|secret|signature|credential|api.?key/i.test(key))) return null;
    return url.href;
  } catch { return null; }
}

export function evidence(record: EvidenceRecord): { sourceUrl: string; checkedAt: string } | null {
  const observation = record.passes?.["2b"]?.gold_observation;
  if (!observation || typeof observation !== "object" || Array.isArray(observation)) return null;
  const data = observation as Record<string, unknown>;
  const url = sourceUrl(data.source_url);
  const date = data.observed_on;
  if (!url || data.status !== 200 || data.blocked !== false || typeof date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  const parsed = new Date(date);
  if (!Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== date) return null;
  if (record.passes?.["2b"]?.archive_date || record.passes?.["2b"]?.error) return null;
  return { sourceUrl: url, checkedAt: date };
}

export function blockedSources(record: EvidenceRecord): string[] {
  const access = record.passes?.["2b"]?.access_error;
  const urls = access && typeof access === "object" && !Array.isArray(access)
    && (access as Record<string, unknown>).status === "blocked"
    ? [(access as Record<string, unknown>).url] : [];
  const found = record.passes?.["2a"]?.blocked_by_check;
  if (Array.isArray(found)) urls.push(...found);
  return [...new Set(urls.map(sourceUrl).filter((url): url is string => Boolean(url)))];
}

export function resultStatus(record: EvidenceRecord): { status: "verified" | "failed" | "pending"; reason: string } {
  const scrape = record.passes?.["2b"];
  const failure = String(scrape?.error ?? record.error ?? "").toLowerCase();
  if (/captcha|human check|access denied|login|blocked/.test(failure) || blockedSources(record).length)
    return { status: "failed", reason: "Blocked: the website requires a human access check or restricts this page. Automatic reads have stopped." };
  if (record.status === "queued" || record.status === "running") return { status: "pending", reason: record.status === "queued" ? "Waiting to be checked." : "Checking this business." };
  if (scrape?.archive_date) return { status: "failed", reason: "Only an archived copy was available; current details could not be checked." };
  if (/timeout|timed out/.test(failure)) return { status: "failed", reason: "The website or verification service did not respond in time." };
  if (record.status !== "done") return { status: "failed", reason: "A verification step failed; the result could not be checked." };
  if (!evidence(record)) return { status: "failed", reason: !record.profile?.website?.value
    ? "No matching business website was found." : scrape?.error ? "The business website could not be read."
      : "No current source link and check date were recorded." };
  const checks = record.passes?.["7"]?.checks;
  if (!checks || typeof checks !== "object" || Array.isArray(checks)) return { status: "failed", reason: "The final checks were not recorded." };
  for (const [key, value] of Object.entries(checks)) {
    if (value === false) return { status: "failed", reason: key === "phone_matches_site"
      ? "The supplied phone does not match the website."
      : key === "email_domain_matches_site" ? "The email domain does not match the website." : "The source disagrees with the business details." };
  }
  return { status: "verified", reason: "" };
}
