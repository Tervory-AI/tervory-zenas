// The Workers panel on the Tank page. Read only: no action, no key, no ledger write.
// Tank's /v1/workers reads the workers service's /workers page and keeps these columns only.

export const WORKERS_LINE = "Read only. Each row is one task from an issue labelled worker.";

export type WorkerTask = {
  issue: number;
  brief: string;
  status: string;
  pr_url: string | null;
  minutes: number | null;
  cost_usd: number | null;
};

export type WorkersFeed = { available: boolean; tasks: WorkerTask[] };

export type WorkerRow = {
  key: string;
  issue: string;
  brief: string;
  status: string;
  pr: { href: string; label: string } | null;
  minutes: string;
  cost: string;
};

const PR_URL = /^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/([0-9]{1,9})$/;
const STATUS = /^[a-z_]{1,32}$/;
const MAX_ROWS = 50;

function amount(value: unknown): number | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return value;
}

/** Accept only the panel's columns, whatever Tank sends. */
export function readWorkers(raw: unknown): WorkersFeed {
  if (!raw || typeof raw !== "object") return { available: false, tasks: [] };
  const record = raw as Record<string, unknown>;
  if (record.available !== true || !Array.isArray(record.tasks)) return { available: false, tasks: [] };
  const tasks: WorkerTask[] = [];
  for (const item of record.tasks.slice(0, MAX_ROWS)) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const issue = row.issue;
    if (typeof issue !== "number" || !Number.isInteger(issue) || issue <= 0) continue;
    const status = typeof row.status === "string" ? row.status.trim().toLowerCase() : "";
    const prUrl = typeof row.pr_url === "string" && PR_URL.test(row.pr_url) ? row.pr_url : null;
    tasks.push({
      issue,
      brief: typeof row.brief === "string" ? row.brief.trim().slice(0, 200) : "",
      status: STATUS.test(status) ? status : "unknown",
      pr_url: prUrl,
      minutes: amount(row.minutes),
      cost_usd: amount(row.cost_usd),
    });
  }
  return { available: true, tasks };
}

export function workerRows(feed: WorkersFeed): WorkerRow[] {
  return feed.tasks.map((task) => {
    const number = task.pr_url ? PR_URL.exec(task.pr_url)?.[1] : undefined;
    return {
      key: `issue-${task.issue}`,
      issue: `#${task.issue}`,
      brief: task.brief,
      status: task.status.replace(/_/g, " "),
      pr: task.pr_url && number ? { href: task.pr_url, label: `PR #${number}` } : null,
      minutes: task.minutes === null ? "—" : task.minutes.toFixed(1),
      cost: task.cost_usd === null ? "—" : `$${task.cost_usd.toFixed(4)}`,
    };
  });
}
