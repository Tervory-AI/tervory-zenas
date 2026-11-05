import assert from "node:assert/strict";
import { test } from "node:test";
import { MOOT_LABEL, MOOT_LATER, MOOT_LEAD, MOOT_PARTS, trainingRound } from "./moot";

test("moot court text edition panel starts with no selected match", () => {
  assert.equal(MOOT_LABEL, "Moot Court");
  assert.equal(MOOT_LEAD, "This is where Zenas trains in text.");
  assert.equal(MOOT_LATER, "Law students will use it later, for free.");
  assert.equal(trainingRound(), null);
  assert.deepEqual(
    MOOT_PARTS.map((part) => part.title),
    ["The problem", "The hearing", "The ruling"],
  );
  assert.equal(MOOT_PARTS[0]?.empty, "Load a locked civil appeal problem to begin.");
  assert.match(MOOT_PARTS[1]?.empty ?? "", /AI against AI/);
});

test("the panel does not invent a case, a student score, or a sign-up", () => {
  const text = [MOOT_LABEL, MOOT_LEAD, MOOT_LATER, ...MOOT_PARTS.flatMap((part) => [part.title, part.empty])].join(" ");
  assert.equal(/\bv\b|criminal|score|%\b|sign up|signup|account/i.test(text), false);
  assert.equal((text.match(/student/gi) ?? []).length >= 1, true);
});
