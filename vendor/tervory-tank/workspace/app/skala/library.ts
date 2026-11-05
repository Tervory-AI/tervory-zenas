// Skills library. Rows come from the skills list. No vault and no transfer.

export type LibrarySkill = {
  id: string;
  version: string;
  agent: string;
  rung: number;
  task_type: string | null;
  description: string;
  state: string;
  last_eval_score: number | string | null;
  last_eval_at: string | null;
  present: boolean;
  approved_at?: string | null;
  runs?: number;
  model?: boolean;
  reason?: string | null;
  calls_30d?: number | null;
  avg_cost_usd?: number | string | null;
};

export type AgentGroup = {
  id: string;
  label: string;
  agents: readonly string[];
  logo: string;
  width: number;
  height: number;
};

/** Official on-dark marks, natural pixel size. Display height stays at least 40. */
export const AGENT_GROUPS: readonly AgentGroup[] = [
  { id: "base", label: "Base", agents: ["base"], logo: "/brand/base-on-dark.png", width: 2322, height: 601 },
  { id: "tiger", label: "Tiger", agents: ["tiger"], logo: "/brand/tiger-on-dark.png", width: 2607, height: 801 },
  { id: "market_lab", label: "Market Lab", agents: ["market_lab", "market-lab"], logo: "/brand/market-lab-on-dark.png", width: 3685, height: 608 },
  { id: "rig", label: "Rig", agents: ["rig"], logo: "/brand/rig-on-dark.png", width: 1173, height: 651 },
  { id: "tisan", label: "Tisan", agents: ["tisan"], logo: "/brand/tisan-on-dark.png", width: 2806, height: 701 },
  { id: "radix", label: "Radix", agents: ["radix"], logo: "/brand/radix-on-dark.png", width: 1893, height: 651 },
  { id: "zenas", label: "Zenas", agents: ["zenas"], logo: "/brand/zenas-on-dark.png", width: 1970, height: 701 },
  { id: "tank", label: "Tank", agents: ["tank", "tier0", "orchestrator"], logo: "/brand/tank-on-dark.png", width: 2466, height: 601 },
];

export const STATES = ["candidate", "staging", "approved", "retired"] as const;

export type VersionAction = "evals" | "approve" | "retire";

export type SkillGroup = {
  id: string;
  label: string;
  logo: string | null;
  logoWidth: number;
  logoHeight: number;
  skills: { id: string; description: string; versions: LibrarySkill[] }[];
};

const LOGO_HEIGHT = 40;

export function logoBox(width: number, height: number, target = LOGO_HEIGHT): { width: number; height: number } {
  const safeHeight = height > 0 ? height : 1;
  return { width: Math.max(1, Math.round((target * width) / safeHeight)), height: target };
}

export function oneLine(description: string): string {
  const line = description.replace(/\s+/g, " ").trim();
  return line || "No description yet.";
}

function usesModel(skill: { rung: number; task_type: string | null }): boolean {
  if (skill.task_type) return true;
  if (skill.rung >= 3 || skill.rung === 0) return false;
  return true;
}

/** Four places, matching a few cents of a cent. A smaller non-zero price keeps six. */
export function formatCallCost(usd: number): string {
  const four = usd.toFixed(4);
  if ((four === "0.0000" || four === "-0.0000") && usd !== 0) return `$${usd.toFixed(6)}`;
  return `$${four}`;
}

/** Code skills have no model call. A model skill shows this box's 30-day average. */
export function costLabel(skill: {
  rung: number;
  task_type: string | null;
  calls_30d?: number | null;
  avg_cost_usd?: number | string | null;
}): string {
  if (!usesModel(skill)) return "no model";
  const calls = Number(skill.calls_30d ?? 0);
  const avg = typeof skill.avg_cost_usd === "number" ? skill.avg_cost_usd : Number(skill.avg_cost_usd);
  if (!Number.isFinite(calls) || calls <= 0 || !Number.isFinite(avg)) return "uses a model · no calls yet";
  return `uses a model · ${formatCallCost(avg)}/call (30 days)`;
}

export function scoreLabel(score: number | string | null): string {
  if (score === null || score === "") return "No score yet";
  const value = typeof score === "number" ? score : Number(score);
  if (!Number.isFinite(value)) return "No score yet";
  const pct = Math.round(value * 1000) / 10;
  return Number.isInteger(pct) ? `${pct}%` : `${pct.toFixed(1)}%`;
}

export function scoreDate(iso: string | null): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function versionAction(skill: { state: string; present: boolean }): VersionAction | null {
  if ((skill.state === "candidate" || skill.state === "approved") && skill.present) return "evals";
  if (skill.state === "staging" && skill.present) return "approve";

  return null;
}

export function confirmApprove(id: string, version: string): string {
  return `Approve ${id} ${version}?`;
}

/** Tank's 409 body, as the sentence to show. A raw "Tank … 409" string is not that sentence. */
export function tankRefusal(message: string): { status: 409; error: string } | null {
  const match = /^Tank \S+ (\d+): ([\s\S]*)$/.exec(message.trim());
  if (!match || Number(match[1]) !== 409) return null;
  let error = "";
  try {
    const parsed = JSON.parse(match[2].trim()) as { detail?: unknown };
    if (typeof parsed.detail === "string") error = parsed.detail.trim();
  } catch {
    error = "";
  }
  if (!error || error.startsWith("Tank ")) return null;
  return { status: 409, error };
}

/** The sentence under the Approve button. Anything else stays off the row. */
export function approvalRowText(status: number, error: unknown): string | null {
  if (status !== 409 || typeof error !== "string") return null;
  const text = error.trim();
  if (!text || /^Tank \S+ \d+:/.test(text)) return null;
  return text;
}

/** Founder actions are refused for everyone else. The route applies the same rule. */
export function founderActionRefusal(founder: boolean): { error: "Not found"; status: 404 } | null {
  if (founder) return null;
  return { error: "Not found", status: 404 };
}

export function applyApproval(skills: LibrarySkill[], id: string, version: string): LibrarySkill[] {
  return skills.map((skill) => {
    if (skill.id !== id) return skill;
    if (skill.version === version) return { ...skill, state: "approved" };
    if (skill.state === "approved") return { ...skill, state: "staging" };
    return skill;
  });
}

export function applyEval(
  skills: LibrarySkill[],
  id: string,
  version: string,
  score: number,
  state: string,
  at: string,
): LibrarySkill[] {
  return skills.map((skill) => (
    skill.id === id && skill.version === version
      ? { ...skill, last_eval_score: score, last_eval_at: at, state }
      : skill
  ));
}

function compareVersions(a: string, b: string): number {
  const parts = (value: string) => value.split(".").map((part) => (/^\d+$/.test(part) ? Number(part) : 0));
  const av = parts(a);
  const bv = parts(b);
  const length = Math.max(av.length, bv.length);
  for (let i = 0; i < length; i += 1) {
    const diff = (bv[i] ?? 0) - (av[i] ?? 0);
    if (diff !== 0) return diff;
  }
  if (a === b) return 0;
  return a < b ? 1 : -1;
}

export function groupForAgent(agent: string): AgentGroup | null {
  const key = agent.trim().toLowerCase();
  return AGENT_GROUPS.find((group) => group.agents.some((name) => name === key)) ?? null;
}

export function agentChoices(skills: LibrarySkill[]): { id: string; label: string }[] {
  const known = AGENT_GROUPS.map((group) => ({ id: group.id, label: group.label }));
  const extra = new Map<string, string>();
  for (const skill of skills) {
    if (groupForAgent(skill.agent)) continue;
    const id = skill.agent.trim() || "other";
    if (!extra.has(id)) extra.set(id, id.replace(/[_-]+/g, " "));
  }
  return [...known, ...[...extra.entries()].sort((a, b) => a[1].localeCompare(b[1])).map(([id, label]) => ({ id, label }))];
}

function matchesQuery(skill: LibrarySkill, needle: string): boolean {
  if (!needle) return true;
  const group = groupForAgent(skill.agent);
  const haystack = [skill.id, skill.version, skill.agent, group?.label ?? "", skill.description, skill.state].join(" ").toLowerCase();
  return haystack.includes(needle);
}

export function visibleLibrary(
  skills: LibrarySkill[],
  query: string,
  agentId: string,
  state: string,
): SkillGroup[] {
  const needle = query.trim().toLowerCase();
  const grouped = new Map<string, { group: AgentGroup | null; label: string; rows: Map<string, LibrarySkill[]> }>();

  for (const skill of skills) {
    const known = groupForAgent(skill.agent);
    const id = known?.id ?? (skill.agent.trim() || "other");
    if (agentId && agentId !== id) continue;
    if (state && skill.state !== state) continue;
    if (!matchesQuery(skill, needle)) continue;
    let bucket = grouped.get(id);
    if (!bucket) {
      bucket = { group: known, label: known?.label ?? (skill.agent.trim() || "Other").replace(/[_-]+/g, " "), rows: new Map() };
      grouped.set(id, bucket);
    }
    const versions = bucket.rows.get(skill.id) ?? [];
    versions.push(skill);
    bucket.rows.set(skill.id, versions);
  }

  const order = AGENT_GROUPS.map((group) => group.id);
  const ids = [...grouped.keys()].sort((a, b) => {
    const ai = order.indexOf(a);
    const bi = order.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  });

  return ids.map((id) => {
    const bucket = grouped.get(id)!;
    const box = bucket.group ? logoBox(bucket.group.width, bucket.group.height) : { width: 0, height: 0 };
    const skillsInGroup = [...bucket.rows.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([skillId, versions]) => {
        const ordered = [...versions].sort((a, b) => compareVersions(a.version, b.version));
        return {
          id: skillId,
          description: oneLine(ordered[0]?.description ?? ""),
          versions: ordered,
        };
      });
    return {
      id,
      label: bucket.label,
      logo: bucket.group?.logo ?? null,
      logoWidth: box.width,
      logoHeight: box.height,
      skills: skillsInGroup,
    };
  });
}

/** Keep one skill row, with its current and newest pending versions visible. */
export function versionSections(versions: LibrarySkill[]) {
  const ordered = [...versions].sort((a, b) => compareVersions(a.version, b.version));
  const approved = ordered.find(v => v.state === "approved");
  const pending = ordered.find(v => v.state === "candidate" || v.state === "staging");
  const current = [approved, pending].filter((v): v is LibrarySkill => Boolean(v));
  return { current, history: ordered.filter(v => !current.includes(v)) };
}
