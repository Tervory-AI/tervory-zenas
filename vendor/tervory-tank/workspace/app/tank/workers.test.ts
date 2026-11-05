import assert from "node:assert/strict";
import { test } from "node:test";
import { readWorkers, workerRows, WORKERS_LINE } from "./workers";

// What Tank's GET /v1/workers returns for three recent tasks.
const FIXTURE = {
  available: true,
  tasks: [
    {
      issue: 42,
      brief: "docs/cursor/MODELS-1-add-deepseek-kimi.md",
      status: "ready",
      pr_url: "https://github.com/Tervory-AI/tervory-tank/pull/154",
      minutes: 12.34,
      cost_usd: 0.0213,
    },
    { issue: 43, brief: "docs/cursor/WORKERS-1b-follow-up.md", status: "running", pr_url: null, minutes: null, cost_usd: null },
    {
      issue: 44,
      brief: "docs/cursor/X.md",
      status: "ci_failed",
      pr_url: "https://github.com/Tervory-AI/tervory-tank/pull/160",
      minutes: 3,
      cost_usd: 0,
    },
  ],
};

test("the panel shows the fixture tasks: status, PR link, minutes, model cost", () => {
  const rows = workerRows(readWorkers(FIXTURE));
  assert.deepEqual(
    rows.map((row) => [row.issue, row.status, row.pr?.href ?? null, row.pr?.label ?? null, row.minutes, row.cost]),
    [
      ["#42", "ready", "https://github.com/Tervory-AI/tervory-tank/pull/154", "PR #154", "12.3", "$0.0213"],
      ["#43", "running", null, null, "—", "—"],
      ["#44", "ci failed", "https://github.com/Tervory-AI/tervory-tank/pull/160", "PR #160", "3.0", "$0.0000"],
    ],
  );
  assert.equal(rows[0]?.brief, "docs/cursor/MODELS-1-add-deepseek-kimi.md");
  assert.match(WORKERS_LINE, /Read only/);
});

test("only the panel's columns survive, and only GitHub pull request links", () => {
  const feed = readWorkers({
    available: true,
    tasks: [
      { issue: 7, status: "draft", pr_url: "javascript:alert(1)", minutes: 1, cost_usd: 0.1, token: "ghs_example", key: "x" },
      { issue: 8, status: "<b>", pr_url: "https://evil.example/pull/1", minutes: -1, cost_usd: "0.2" },
      { issue: "9", status: "ready" },
      null,
    ],
  });
  assert.deepEqual(feed.tasks, [
    { issue: 7, brief: "", status: "draft", pr_url: null, minutes: 1, cost_usd: 0.1 },
    { issue: 8, brief: "", status: "unknown", pr_url: null, minutes: null, cost_usd: null },
  ]);
  assert.equal(JSON.stringify(feed).includes("ghs_"), false);
});

test("no workers, or no answer, is an empty read-only panel", () => {
  assert.deepEqual(readWorkers(null), { available: false, tasks: [] });
  assert.deepEqual(readWorkers({ available: false, tasks: FIXTURE.tasks }), { available: false, tasks: [] });
  assert.deepEqual(workerRows(readWorkers({ available: true, tasks: [] })), []);
});
