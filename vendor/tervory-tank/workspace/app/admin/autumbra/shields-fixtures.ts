// Offline test fixtures only. Live pages never import these values.
// No Cloudflare apply. No Hugging Face. dry_run only.

export type ShieldReceipt = { step: string; at: string; ledger_id: string; status: string };
export type Shield = {
  id: string; site: string; kind: string; path: string; state: string; reason: string; ref: string;
  proposed_at: string; up_at: string | null; ended_at: string | null; note: string | null;
  plan: { plain: string; phase: string; rule: { action: string; expression: string } };
  brief: { title: string; steps: string[] } | null;
  receipts: ShieldReceipt[];
};
export type ShieldTile = {
  mode: "dry_run" | "live";
  fixture?: boolean;
  open_total: number;
  open_by_severity: Record<string, number>;
  time_to_know_seconds: { median: number | null; worst: number | null; target: number };
  time_to_shield_seconds: { median: number | null; worst: number | null; target: number };
  sites_broken_by_a_shield: number;
  cost_this_month_usd: number;
  receipts_this_month: number;
};
export type ShieldBoard = { tile: ShieldTile; shields: Shield[] };

/** Stable fixture shield id (UUID) so taps stay on the fixture path. */
export const PRACTICE_SHIELD_ID = "a1111111-1111-4111-8111-111111111111";

const fixtureStore = new Map<string, Shield>();

function seedFixtureShield(): Shield {
  const proposedAt = "2026-10-24T18:00:00.000Z";
  return {
    id: PRACTICE_SHIELD_ID,
    site: "protect.tervory.com",
    kind: "waf_managed",
    path: "/",
    state: "proposed",
    reason: "Serious issue matched this site in Autumbra Enforce",
    ref: "waf:edge-rule",
    proposed_at: proposedAt,
    up_at: null,
    ended_at: null,
    note: "Proposed by Autumbra Enforce — awaiting founder review",
    plan: {
      plain: "Raise a managed firewall rule that challenges suspicious upload-style traffic on this site.",
      phase: "propose",
      rule: {
        action: "managed_challenge",
        expression: '(http.request.uri.path contains "/upload") and (cf.waf.score lt 20)',
      },
    },
    brief: null,
    receipts: [
      {
        step: "proposed",
        at: proposedAt,
        ledger_id: "fixture-ledger-proposed",
        status: "ok",
      },
    ],
  };
}

function ensureStore(): void {
  if (fixtureStore.size === 0) {
    fixtureStore.set(PRACTICE_SHIELD_ID, seedFixtureShield());
  }
}

export function isFixtureShield(id: string): boolean {
  ensureStore();
  return fixtureStore.has(id);
}

export function fixtureBoard(): ShieldBoard {
  ensureStore();
  const shields = [...fixtureStore.values()].map((s) => ({
    ...s,
    plan: { ...s.plan, rule: { ...s.plan.rule } },
    receipts: [...s.receipts],
  }));
  const open = shields.filter((s) => !["withdrawn", "undone", "not_needed", "refused"].includes(s.state));
  return {
    tile: {
      mode: "dry_run",
      fixture: true,
      open_total: open.length,
      open_by_severity: open.length ? { high: open.length } : {},
      time_to_know_seconds: { median: null, worst: null, target: 3600 },
      time_to_shield_seconds: { median: null, worst: null, target: 86400 },
      sites_broken_by_a_shield: 0,
      cost_this_month_usd: 0,
      receipts_this_month: shields.reduce((n, s) => n + s.receipts.length, 0),
    },
    shields,
  };
}

/** Alias for callers that want a snapshot constant name. */
export const FIXTURE_BOARD = fixtureBoard;

export function applyFixtureTap(id: string, tap: "raise" | "undo"): Shield {
  ensureStore();
  const current = fixtureStore.get(id);
  if (!current) throw new Error("Unknown shield");
  const now = new Date().toISOString();
  let next: Shield;
  if (tap === "raise") {
    next = {
      ...current,
      state: "dry_run",
      note: "Autumbra Enforce recorded what it would change. Nothing was changed at the edge.",
      plan: { ...current.plan, phase: "dry_run" },
      receipts: [
        ...current.receipts,
        {
          step: "raise_dry_run",
          at: now,
          ledger_id: `fixture-raise-${now}`,
          status: "dry_run",
        },
      ],
    };
  } else {
    const withdrawn = current.state === "proposed" || current.state === "dry_run" || current.state === "brief";
    next = {
      ...current,
      state: withdrawn ? "withdrawn" : "undone",
      ended_at: now,
      note: withdrawn
        ? "Proposal withdrawn. Nothing was changed at the edge."
        : "Shield marked down. Nothing was changed at the edge.",
      receipts: [
        ...current.receipts,
        {
          step: withdrawn ? "withdrawn" : "undone",
          at: now,
          ledger_id: `fixture-undo-${now}`,
          status: "ok",
        },
      ],
    };
  }
  fixtureStore.set(id, next);
  return {
    ...next,
    plan: { ...next.plan, rule: { ...next.plan.rule } },
    receipts: [...next.receipts],
  };
}

/** Test helper: clear and re-seed the in-process fixture store. */
export function resetFixtureStore(): void {
  fixtureStore.clear();
  ensureStore();
}
