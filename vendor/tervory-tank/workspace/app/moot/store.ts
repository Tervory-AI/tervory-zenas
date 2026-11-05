import "server-only";
import type { Region } from "@/app/api/zenas/tank";
import { mootPublishedMatches } from "@/lib/tank";

export type MatchRecord = {
  matchId: string;
  published: boolean;
  title: string;
  fairnessReading: string;
  judgmentOrder: string;
  judgmentReasons?: string[];
  reveal: string;
  transcript?: { role: string; text: string }[];
  authorities?: { name: string; source_url?: string }[];
  costUsd?: number | null;
};


const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const OFFLINE = /fixture|\[TEST\]|CVE-2024-0000|ThreatPayload|00000000-0000-0000-0000/i;

export type MatchFailure = { status: "unavailable" } | { status: "malformed" };
export type PublishedMatchesResult =
  | { status: "available"; matches: MatchRecord[] }
  | { status: "empty"; matches: [] }
  | MatchFailure;
export type MatchResult = { status: "available"; match: MatchRecord } | { status: "missing" } | MatchFailure;

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === "string");
}

function validMatch(row: Record<string, unknown>): boolean {
  return typeof row.matchId === "string" && UUID.test(row.matchId)
    && typeof row.title === "string" && row.title.trim().length > 0
    && typeof row.fairnessReading === "string" && typeof row.judgmentOrder === "string"
    && typeof row.reveal === "string"
    && (row.judgmentReasons === undefined || strings(row.judgmentReasons))
    && (row.transcript === undefined || (Array.isArray(row.transcript) && row.transcript.every(
      turn => object(turn) && typeof turn.role === "string" && typeof turn.text === "string")))
    && (row.authorities === undefined || (Array.isArray(row.authorities) && row.authorities.every(
      authority => object(authority) && typeof authority.name === "string"
        && (authority.source_url === undefined || (typeof authority.source_url === "string"
          && (authority.source_url === "" || /^https?:\/\//i.test(authority.source_url)))))))
    && (row.costUsd === undefined || row.costUsd === null
      || (typeof row.costUsd === "number" && Number.isFinite(row.costUsd) && row.costUsd >= 0));
}

/** Only the room's published platform matches may reach the public watch pages.
 * Private and offline rows are excluded, but broken public data is never called empty.
 * Never return upstream error details or unknown fields to a public component.
 */
export async function listPublishedMatches(matchId?: string, region: Region = "us-east"): Promise<PublishedMatchesResult> {
  let response: unknown;
  try {
    response = await mootPublishedMatches(matchId, undefined, region);
  } catch (error) {
    return { status: error instanceof SyntaxError ? "malformed" : "unavailable" };
  }
  if (!object(response) || !Array.isArray(response.matches)) return { status: "malformed" };
  const matches: MatchRecord[] = [];
  for (const value of response.matches) {
    if (!object(value)) return { status: "malformed" };
    // A private row must never influence the public result or leak its contents.
    if (value.published === false || OFFLINE.test(JSON.stringify(value))) continue;
    if (value.published !== true || !validMatch(value)) return { status: "malformed" };
    const row = value as MatchRecord;
    matches.push({ matchId: row.matchId, published: true, title: row.title,
      fairnessReading: row.fairnessReading, judgmentOrder: row.judgmentOrder, reveal: row.reveal,
      judgmentReasons: row.judgmentReasons,
      transcript: row.transcript?.map(({ role, text }) => ({ role, text })),
      authorities: row.authorities?.map(({ name, source_url }) => ({ name, source_url })),
      costUsd: row.costUsd });
  }
  return matches.length ? { status: "available", matches } : { status: "empty", matches: [] };
}

export async function getMatch(matchId: string, region: Region = "us-east"): Promise<MatchResult> {
  if (!UUID.test(matchId)) return { status: "missing" };
  const result = await listPublishedMatches(matchId, region);
  if (result.status === "unavailable" || result.status === "malformed") return result;
  const match = result.matches.find(row => row.matchId === matchId);
  return match ? { status: "available", match } : { status: "missing" };
}

/** Boolean callers must not silently interpret an outage as a missing public page. */
export async function isPublicWatch(matchId: string): Promise<boolean> {
  const result = await getMatch(matchId);
  if (result.status === "unavailable" || result.status === "malformed") {
    throw new Error(`Moot published matches ${result.status}`);
  }
  return result.status === "available";
}
