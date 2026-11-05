/**
 * Offline Autumbra Watch test data.
 * Live pages never import these values.
 * No live HF. Propose-only ThreatPayload receipts — never auto-enforce.
 */

import type { GhostIncident, GhostPanel } from "@/lib/tank";

/** Panel-visible ThreatPayload / advisory receipt (propose-only). */
export type AutumbraThreatReceipt = {
  id: string;
  schema_version: "ghost.threat.v1";
  action: "propose_shield" | "open_match" | "write_brief" | "handoff";
  target: string;
  severity: string;
  summary: string;
  status: "propose_only" | "burned" | "awaiting_founder";
  cve_ids?: string[];
  match_id?: string;
  confidence?: number;
  flagged_at: string;
};

export type AutumbraWatchFixture = {
  panel: GhostPanel;
  threatReceipts: AutumbraThreatReceipt[];
};

const lookSite: GhostIncident = {
  id: "fixture-look-site-health-001",
  target: "site",
  signal: "health",
  origin: "look",
  state: "needs_review",
  summary: "Public site returned 503 on two of five probe rounds. Autumbra Watch held a receipt; no recovery was run.",
  created_at: "2026-10-24T18:12:00Z",
  cost_usd: 0.00042,
  steps: [
    { step: "probe_health", ledger_id: "led_probe_site_001", cost_usd: 0.00012 },
    { step: "compare_baseline", ledger_id: "led_base_site_001", cost_usd: 0.00008 },
    { step: "record_receipt", ledger_id: "led_rcpt_site_001", cost_usd: 0.00022 },
  ],
  previous: [{ cause: "crashed_room", fix: "room_restarted" }],
  brief: {
    title: "Site availability — review before any worker handoff",
    cause: "Intermittent 503 on the public edge; room health still green in the last completed cycle.",
    status: "needs_approval",
    instructions: [
      "Confirm Coolify deploy status for the public site.",
      "Do not restart rooms from this panel — founder check required.",
      "Approve the brief only after the cause is confirmed.",
    ],
  },
};

const lookWorkspace: GhostIncident = {
  id: "fixture-look-workspace-headers-002",
  target: "workspace",
  signal: "headers",
  origin: "look",
  state: "not_reproduced",
  summary: "Security-header check on workspace did not reproduce the reported gap on a second pass.",
  created_at: "2026-10-24T16:40:00Z",
  cost_usd: 0.00018,
  steps: [
    { step: "probe_headers", ledger_id: "led_probe_ws_002", cost_usd: 0.00009 },
    { step: "record_receipt", ledger_id: "led_rcpt_ws_002", cost_usd: 0.00009 },
  ],
};

const lookTank: GhostIncident = {
  id: "fixture-look-tank-slow-003",
  target: "tank",
  signal: "slow",
  origin: "monitor",
  state: "blocked",
  summary: "Tank latency crossed the watch threshold once; follow-up probe could not reach the room metrics feed.",
  created_at: "2026-10-24T19:05:00Z",
  cost_usd: 0.00031,
  steps: [
    { step: "probe_latency", ledger_id: "led_probe_tank_003", cost_usd: 0.00011 },
    { step: "fetch_metrics", ledger_id: "led_metrics_tank_003", cost_usd: 0.0001 },
    { step: "record_receipt", ledger_id: "led_rcpt_tank_003", cost_usd: 0.0001 },
  ],
};

/** Mirrors tests/fixtures/autumbra/patch_connect_match.json into a panel receipt. */
const connectMatchReceipt: AutumbraThreatReceipt = {
  id: "fixture-match-autumbra-connect-001",
  schema_version: "ghost.threat.v1",
  action: "propose_shield",
  target: "coolify",
  severity: "high",
  summary:
    "requests before 2.32.3 has a crafted redirect issue matched on Autumbra CONNECT. Propose-only — burn-on-fail; no auto-enforce.",
  status: "propose_only",
  cve_ids: ["CVE-2026-55555"],
  match_id: "fixture-match-autumbra-connect-001",
  confidence: 0.91,
  flagged_at: "2026-10-24T17:00:00Z",
};

const briefHandoffReceipt: AutumbraThreatReceipt = {
  id: "fixture-threat-brief-handoff-002",
  schema_version: "ghost.threat.v1",
  action: "handoff",
  target: "site",
  severity: "medium",
  summary:
    "Advisory match is outside the edge shield kinds Autumbra Watch may propose. Receipt kept for founder handoff; nothing was changed.",
  status: "awaiting_founder",
  cve_ids: [],
  confidence: 0.72,
  flagged_at: "2026-10-24T18:30:00Z",
};

const burnedReceipt: AutumbraThreatReceipt = {
  id: "fixture-threat-burned-003",
  schema_version: "ghost.threat.v1",
  action: "open_match",
  target: "workspace",
  severity: "unknown",
  summary: "Threat payload burned. No action was taken.",
  status: "burned",
  flagged_at: "2026-10-24T18:45:00Z",
};

export const AUTUMBRA_WATCH_FIXTURE: AutumbraWatchFixture = {
  panel: {
    incidents: [lookSite, lookWorkspace, lookTank],
    monitor: { running: true, unavailable_checks: 1 },
    integrations: {
      explain: "not_connected",
      private_storage: "not_connected",
      log_feed: "not_connected",
      deploy_feed: "not_connected",
      worker_handoff: "not_connected",
      recovery: "not_connected",
    },
  },
  threatReceipts: [connectMatchReceipt, briefHandoffReceipt, burnedReceipt],
};
