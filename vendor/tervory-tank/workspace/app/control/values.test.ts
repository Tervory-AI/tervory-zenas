import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { calendarDays, count, finiteNumber, money, signupAge, sourceLink, sparkPoints, wholeNumber } from "./values.ts";

// Pure arithmetic and contract checks. No fabricated customer, invoice, signup or ledger fixtures.
test("missing measurements cannot turn into displayed zeroes", () => {
  for (const value of [null, undefined, "", " ", false, true, {}, [], NaN, Infinity, -Infinity, -1]) {
    assert.throws(() => finiteNumber(value));
  }
  assert.throws(() => wholeNumber(Number.MIN_VALUE));
  assert.throws(() => wholeNumber(Number.MAX_SAFE_INTEGER + 1));
  assert.equal(wholeNumber(0), 0);
});

test("the current month's daily buckets use UTC and include today once", () => {
  const now = Date.now();
  const days = calendarDays(now);
  assert.equal(days.length, new Date(now).getUTCDate());
  assert.equal(days.at(-1)?.day, new Date(now).toISOString().slice(0, 10));
  assert.equal(new Set(days.map(day => day.day)).size, days.length);
  assert.ok(days.every(day => day.day.slice(0, 7) === new Date(now).toISOString().slice(0, 7)));
});

test("integer and currency formatting do not depend on the host locale", () => {
  assert.equal(count(1000), "1,000");
  assert.equal(money(1000), "$1,000.00");
  assert.equal(money(0), "$0.00");
});

test("signup age changes at UTC midnight with the approved wording", () => {
  const now = Date.parse(new Date().toISOString().slice(0, 10)) + 3600000;
  assert.equal(signupAge(now, now), "0h ago");
  assert.equal(signupAge(now - 3600000, now), "1h ago");
  assert.equal(signupAge(now - 86400000, now), "Yesterday");
  assert.equal(signupAge(now - 2 * 86400000, now), "2 days ago");
});

test("a sparkline requires two observations and keeps a constant series finite", () => {
  assert.equal(sparkPoints([]), null);
  assert.equal(sparkPoints([0]), null);
  assert.equal(sparkPoints([0, 0]), "0,28 120,28");
  assert.throws(() => sparkPoints([0, NaN]));
});

test("source URLs remain relative and encode task identifiers as data", () => {
  const source = sourceLink("models", { task: "market.research" });
  assert.ok(source.startsWith("/control/source?"));
  assert.equal(new URL(source, "https://app.tervory.com").searchParams.get("task"), "market.research");
});

test("every control entry point authorizes the founder before reading or writing sources", () => {
  const page = readFileSync(new URL("./page.tsx", import.meta.url), "utf8");
  const route = readFileSync(new URL("./source/route.ts", import.meta.url), "utf8");
  const actions = readFileSync(new URL("./actions.ts", import.meta.url), "utf8");
  for (const source of [page, route]) {
    assert.ok(source.includes("!userId || !isFounder(userId)"));
    assert.ok(source.indexOf("!isFounder(userId)") < source.lastIndexOf("readFounderDashboard(userId)"));
  }
  for (const name of ["pauseAction", "switchAction", "workersPauseAction"]) {
    const body = actions.split(`export async function ${name}`)[1].split("export async function")[0];
    assert.ok(body.includes("const userId = await founder()"));
    assert.ok(body.indexOf("await founder()") < body.indexOf("try"));
  }
});

test("the approved shared confirmation is reused and removed sections stay absent", () => {
  const widgets = readFileSync(new URL("./widgets.tsx", import.meta.url), "utf8");
  const view = readFileSync(new URL("./view.tsx", import.meta.url), "utf8");
  assert.ok(widgets.includes("<ApproveAllReady"));
  // Founder 1 Nov: retain main's worker diagnostics alongside the reduced layout.
  assert.ok(view.includes("<WorkersTile workers={view.workers} now={now}"));
  assert.ok(view.includes("not checking in"));
  for (const removed of ["GHOST-1", "AGENT-1", "BATCH-1", "Health unavailable", "Case study", "Saved this month", "Daily budget", "Storage used"]) {
    assert.ok(!view.includes(removed) && !widgets.includes(removed), removed);
  }
});
