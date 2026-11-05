/** Fixture data for the public Autumbra product page. No live HF. */

export type PublicThreatReceipt = {
  id: string;
  schema_version: "ghost.threat.v1";
  action: "propose_shield" | "handoff" | "write_brief";
  target: string;
  severity: string;
  summary: string;
  status: "propose_only" | "awaiting_founder";
  cve_ids?: string[];
};

export const PUBLIC_AUTUMBRA_FIXTURE = {
  banner: "",
  monitor: { running: true, unavailable_checks: 1 },
  lookTargets: ["workspace", "site", "za", "tank", "base", "tiger", "market lab", "rig", "tisan", "radix", "zenas", "Autumbra Watch"],
  investigations: [
    {
      id: "pub-fix-site-001",
      target: "site",
      signal: "availability",
      summary: "Two probe rounds returned 503. Receipt kept; no recovery run from the public page.",
    },
    {
      id: "pub-fix-dns-002",
      target: "za",
      signal: "DNS",
      summary: "DNS check completed clean on the second pass after a transient NXDOMAIN.",
    },
  ],
  threatReceipts: [
    {
      id: "pub-threat-001",
      schema_version: "ghost.threat.v1",
      action: "propose_shield",
      target: "site",
      severity: "medium",
      summary: "Advisory match mapped to a shield proposal for founder review (propose-only).",
      status: "propose_only",
      cve_ids: ["CVE-2024-0000"],
    },
    {
      id: "pub-threat-002",
      schema_version: "ghost.threat.v1",
      action: "write_brief",
      target: "workspace",
      severity: "low",
      summary: "Worker brief prepared; Autumbra Enforce will not act until a founder approves.",
      status: "awaiting_founder",
    },
  ] satisfies PublicThreatReceipt[],
};
