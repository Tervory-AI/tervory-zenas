import assert from "node:assert/strict";
import { test } from "node:test";
import { usesMatterRelease } from "./release-path";

test("approve with a matter id uses the release path", () => {
  assert.equal(usesMatterRelease("approved", "00000000-0000-0000-0000-0000000000c1"), true);
});

test("reject never uses the release path", () => {
  assert.equal(usesMatterRelease("rejected", "00000000-0000-0000-0000-0000000000c1"), false);
});

test("approve without a matter id keeps the reviews path", () => {
  assert.equal(usesMatterRelease("approved", ""), false);
  assert.equal(usesMatterRelease("approved", "   "), false);
});
