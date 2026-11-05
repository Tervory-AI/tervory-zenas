/**
 * Plain data for the Base page. No network, no keys, no writes.
 * Shapes follow BaseBatch and Skill in workspace/lib/tank.ts.
 */

export type BatchIn = {
  batch: string;
  started: string;
  total: number;
  done: number;
  failed: number;
  pending: number;
  with_site: number;
  with_email: number;
  model_skipped?: number;
};

export type SkillIn = {
  id: string;
  version: string;
  agent: string;
  state: string;
  description: string;
  approved_at?: string | null;
  present: boolean;
};

export type BatchStatus = "Running" | "Some failed" | "Skipped steps waiting" | "Done";

export type BatchRow = {
  name: string;
  total: number;
  finished: number;
  withSite: number;
  withEmail: number;
  skipped: number | null;
  status: BatchStatus;
  settled: boolean;
};

export type SkillRow = {
  label: string;
  id: string;
  version: string;
  approvedOn: string | null;
};

export type Totals = {
  records: number;
  withSite: number;
  withEmail: number;
  skipped: number | null;
};

export type PageModel = {
  contextTrial?: import("./context-report").ContextReport | null;
  loadError: boolean;
  empty: boolean;
  totals: Totals;
  batches: BatchRow[];
  skills: SkillRow[];
  skillsError: boolean;
  refreshedLabel: string;
};

const ZONE = "America/Los_Angeles";

/** Plain names for the skills Base runs. Anything else falls back to its description. */
export const SKILL_LABEL: Record<string, string> = {
  "base.find_site": "Find the website",
  "base.extract": "Write the description",
  "base.business_data": "Business size",
  "ffl.profile": "FFL licence profile",
};

export function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(Math.max(0, Math.round(value)));
}

function dayKey(date: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

/** "17 Oct" in Pacific time, or null for a missing or bad date. */
export function shortDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", { timeZone: ZONE, day: "numeric", month: "short" }).format(date);
}

/** "Today, 8:40 AM PT", or "16 Oct, 8:40 AM PT" on another day. */
export function refreshedLabel(at: Date, now: Date = at): string {
  const time = new Intl.DateTimeFormat("en-US", { timeZone: ZONE, hour: "numeric", minute: "2-digit" }).format(at);
  const day = dayKey(at) === dayKey(now) ? "Today" : shortDate(at.toISOString());
  return `${day}, ${time} PT`;
}

function num(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

export function batchStatus(batch: BatchIn): BatchStatus {
  if (num(batch.pending) > 0) return "Running";
  if (num(batch.failed) > 0) return "Some failed";
  if (num(batch.model_skipped) > 0) return "Skipped steps waiting";
  return "Done";
}

export function batchRow(batch: BatchIn): BatchRow {
  const status = batchStatus(batch);
  return {
    name: batch.batch || "Unnamed batch",
    total: num(batch.total),
    finished: num(batch.done),
    withSite: num(batch.with_site),
    withEmail: num(batch.with_email),
    skipped: batch.model_skipped === undefined || batch.model_skipped === null ? null : num(batch.model_skipped),
    status,
    settled: status === "Done",
  };
}

export function totals(rows: BatchRow[]): Totals {
  const reported = rows.some((row) => row.skipped !== null);
  return {
    records: rows.reduce((sum, row) => sum + row.total, 0),
    withSite: rows.reduce((sum, row) => sum + row.withSite, 0),
    withEmail: rows.reduce((sum, row) => sum + row.withEmail, 0),
    skipped: reported ? rows.reduce((sum, row) => sum + (row.skipped ?? 0), 0) : null,
  };
}

function firstSentence(text: string): string {
  const clean = text.replace(/^Pass \d[a-z]?[,.]?\s*/i, "").trim();
  const match = clean.match(/^(.*?[.!?])(\s|$)/);
  return (match ? match[1] : clean).slice(0, 80);
}

export function isBaseSkill(skill: SkillIn): boolean {
  return skill.agent === "base" || skill.id.startsWith("base.") || skill.id.startsWith("ffl.");
}

/** Only approved versions. A candidate is never shown as in use. */
export function approvedSkills(skills: SkillIn[]): SkillRow[] {
  return skills
    .filter((skill) => skill.state === "approved" && isBaseSkill(skill))
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((skill) => ({
      label: SKILL_LABEL[skill.id] ?? (firstSentence(skill.description || "") || skill.id),
      id: skill.id,
      version: skill.version,
      approvedOn: shortDate(skill.approved_at ?? null),
    }));
}

export function buildModel(input: { batches: BatchIn[] | null; skills: SkillIn[] | null; now: Date }): PageModel {
  const rows = (input.batches ?? [])
    .slice()
    .sort((a, b) => String(b.started).localeCompare(String(a.started)))
    .map(batchRow);
  return {
    loadError: input.batches === null,
    empty: input.batches !== null && rows.length === 0,
    totals: totals(rows),
    batches: rows,
    skills: approvedSkills(input.skills ?? []),
    skillsError: input.skills === null,
    refreshedLabel: refreshedLabel(input.now),
  };
}

/** Strip only the private uniqueness suffix generated for uploads. */
export function batchLabel(batch: string): string {
  return batch.replace(/ · [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i, "");
}
