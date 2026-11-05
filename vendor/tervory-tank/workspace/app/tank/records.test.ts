import assert from "node:assert/strict";
import { test } from "node:test";
import { groundAsk, hideKeys } from "./ask";
import { searchTank, TANK_RECORDS, VAULT_LINE } from "./records";
import { pipelineTo, SQUADS, SQUAD_LINE } from "./squads";

test("an empty search shows nothing", () => {
  assert.deepEqual(searchTank("   "), []);
});

test("search stays inside Tank's own records", () => {
  const hits = searchTank("ledger");
  assert.deepEqual(hits.map((hit) => hit.id), ["ledger"]);
  assert.equal(hits[0]?.href, "/ledger");
  const vault = searchTank("vault");
  assert.ok(vault.every((hit) => hit.title === "tank.vault" || hit.detail.includes("tank.vault")));
  assert.equal(vault.some((hit) => /sk-|api[_-]?key|secret/i.test(hit.detail)), false);
});

test("the vault line names tank.vault and no key", () => {
  assert.match(VAULT_LINE, /Tank keeps the platform keys/);
  assert.match(VAULT_LINE, /No agent can read another agent's vault or the platform keys/);
  assert.match(SQUAD_LINE, /another squad's vault/);
  assert.equal(/sk-|BEGIN |eyJ/.test(VAULT_LINE + SQUAD_LINE), false);
  for (const record of TANK_RECORDS) {
    assert.equal(record.detail.includes("sk-"), false);
    assert.equal(/[A-Za-z0-9]{24,}/.test(record.detail), false);
  }
});

test("each agent has one box, one logo, and a pipeline", () => {
  assert.deepEqual(
    SQUADS.map((squad) => squad.name),
    ["Base", "Tiger", "Market Lab", "Rig", "Tisan", "Radix", "Zenas", "Skala", "Tank"],
  );
  assert.deepEqual(
    SQUADS.map((squad) => squad.logo),
    SQUADS.map((squad) => `/brand/${squad.id}-on-dark.png`),
  );
  assert.equal(new Set(SQUADS.map((squad) => squad.logo)).size, SQUADS.length);
  assert.equal(pipelineTo("Base"), "Pipeline to Base");
  assert.equal(pipelineTo("Tank"), "Pipeline to Tank");
});

test("Ask Tank hides a key and refuses to pass one through", () => {
  const leaked = "the key is sk-live_secretvalue please";
  assert.equal(hideKeys(leaked).includes("sk-"), false);
  assert.match(hideKeys(leaked), /\[hidden\]/);
  const grounded = groundAsk([{ role: "user", content: leaked }]);
  assert.equal(grounded[0]?.content.includes("sk-"), false);
  assert.match(grounded[0]?.content ?? "", /another squad's vault/);
  assert.match(grounded[0]?.content ?? "", /Tank keeps the platform keys/);
});
