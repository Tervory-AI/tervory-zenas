import assert from "node:assert/strict";
import test from "node:test";
import { answerRequest, isChangeRequest, matchRecords, recordHit, searchTerms, type RecordIn } from "./records";

const rec = (id: string, name: string, extra: Record<string, unknown> = {}, batch = "ATF FFL list Sep 2026 · HI"): RecordIn => ({
  id,
  batch,
  raw: {},
  profile: Object.fromEntries(
    Object.entries({ name, ...extra }).map(([key, value]) => [key, { value, source: key === "website" ? "find_site 1.7.0" : "licence list", confidence: 0.9 }]),
  ),
});

const records: RecordIn[] = [
  rec("1", "Kauai Tactical Firearms", { city: "Lihue", state: "HI", phone: "(808) 555-0101" }),
  rec("2", "Kauai Gun Company", { city: "Kapaa", state: "HI", website: "https://kauaigunco.example", emails: ["shop@kauaigunco.example"] }),
  rec("3", "Hilo Firearms", { city: "Hilo", state: "HI", phone: "808-555-0199" }),
  rec("4", "Desert Sporting Goods", { city: "Las Vegas", state: "NV" }, "ATF FFL list Sep 2026 · NV"),
];

test("search matches every word, by name, city or phone written any way", () => {
  assert.deepEqual(matchRecords(records, "kauai").hits.map((h) => h.id), ["2", "1"]);
  assert.deepEqual(matchRecords(records, "hilo").hits.map((h) => h.id), ["3"]);
  assert.deepEqual(matchRecords(records, "8085550199").hits.map((h) => h.id), ["3"]);
  assert.deepEqual(matchRecords(records, "las vegas").hits.map((h) => h.id), ["4"]);
  assert.equal(matchRecords(records, "   ").total, 0);
  assert.equal(matchRecords(records, "nowhere at all").total, 0);
});

test("a hit shows each value with where it came from, and no value as none", () => {
  const hit = recordHit(records[1]);
  assert.equal(hit.website?.value, "https://kauaigunco.example");
  assert.equal(hit.website?.source, "find_site 1.7.0");
  assert.equal(hit.email?.value, "shop@kauaigunco.example");
  assert.equal(recordHit(records[0]).website, null);
  assert.equal(recordHit({ id: "x", batch: null, raw: { BUSINESS_NAME: "Raw Name" }, profile: {} }).name, "Raw Name");
});

test("Ask Base answers from records and lists the records it used as Sources", () => {
  const answer = answerRequest(records, "Which Kauai shops have no website?");
  assert.equal(answer.matched, 1);
  assert.equal(answer.sources.length, 1);
  assert.equal(answer.sources[0].title, "Kauai Tactical Firearms");
  assert.match(answer.text, /^I found 1 record that matches “kauai” with no website\./);
  const none = answerRequest(records, "Find Maui");
  assert.equal(none.matched, 0);
  assert.equal(none.sources.length, 0);
});

test("Ask Base never starts or changes anything", () => {
  for (const request of ["Re-run the Hawaii batch", "rerun HI", "restore Kauai Gun Company", "approve find_site", "delete Hilo Firearms"]) {
    assert.ok(isChangeRequest(request), request);
    const answer = answerRequest(records, request);
    assert.match(answer.text, /only read/);
    assert.equal(answer.sources.length, 0);
  }
  assert.equal(searchTerms("Show me shops in Hilo"), "hilo");
});
