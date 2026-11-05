// What the expanded Skala row is allowed to show after Run evals.
// A pass count with no goal and no result is not a record.

export type EvalDetail = {
  case: number;
  name: string | null;
  passed: boolean;
  why: string;
  goal?: unknown;
  result?: unknown;
};

export type EvalResult = {
  skill: string;
  passed: number;
  total: number;
  score: number;
  threshold: number;
  cost_usd: number;
  state: string;
  gate: "passed" | "failed";
  details: EvalDetail[];
};

export type CaseLine = {
  key: number;
  name: string;
  passed: boolean;
  goal: string;
  result: string;
};

export type CaseRecord =
  | { ok: true; cases: CaseLine[] }
  | { ok: false; message: string };

function recorded(detail: EvalDetail): boolean {
  return Object.prototype.hasOwnProperty.call(detail, "goal")
    && Object.prototype.hasOwnProperty.call(detail, "result")
    && detail.goal !== undefined
    && detail.result !== undefined;
}

export function caseRecord(result: Pick<EvalResult, "total" | "details">): CaseRecord {
  const details = result.details ?? [];
  const complete = details.length > 0 && details.length === result.total && details.every(recorded);
  if (!complete) {
    return { ok: false, message: "This run did not record a goal and a result for each case." };
  }
  return {
    ok: true,
    cases: details.map((detail) => ({
      key: detail.case,
      name: detail.name ?? `case ${detail.case}`,
      passed: detail.passed,
      goal: JSON.stringify(detail.goal),
      result: JSON.stringify(detail.result),
    })),
  };
}

type Versioned = { id: string; version: string; state: string };

function cmpVersion(a: string, b: string): number {
  const av = a.split(".").map((part) => (/^\d+$/.test(part) ? Number(part) : 0));
  const bv = b.split(".").map((part) => (/^\d+$/.test(part) ? Number(part) : 0));
  const length = Math.max(av.length, bv.length);
  for (let i = 0; i < length; i++) {
    const diff = (av[i] ?? 0) - (bv[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

function rowKey(row: Versioned): string {
  return `${row.id}@${row.version}`;
}

// Main list: the approved version, plus one newer version that is not approved yet.
// Every other version stays on the page, in the archive.
export function splitPackages<T extends Versioned>(skills: T[]): { main: T[]; archive: T[] } {
  const keep = new Set<string>();
  for (const id of new Set(skills.map((skill) => skill.id))) {
    const versions = skills.filter((skill) => skill.id === id);
    const approved = versions
      .filter((skill) => skill.state === "approved")
      .sort((a, b) => cmpVersion(b.version, a.version))[0];
    if (approved) keep.add(rowKey(approved));
    const newer = versions
      .filter((skill) => (
        (skill.state === "candidate" || skill.state === "staging")
        && (!approved || cmpVersion(skill.version, approved.version) > 0)
      ))
      .sort((a, b) => cmpVersion(b.version, a.version))[0];
    if (newer) keep.add(rowKey(newer));
  }
  return {
    main: skills.filter((skill) => keep.has(rowKey(skill))),
    archive: skills.filter((skill) => !keep.has(rowKey(skill))),
  };
}
