"use server";

import { baseRecords, type BaseRecord } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { answerRequest, matchRecords, type Answer, type RecordHit } from "./records";

/*
 * Search and Ask Base. Both only read this tenant's Base records from Tank.
 * Nothing is queued, re-run, restored or saved, and no model is called.
 */

const PAGE = 2000;
const CAP = 20000;

async function readRecords(): Promise<BaseRecord[]> {
  const { tenantId } = await currentTenant();
  const all: BaseRecord[] = [];
  for (let offset = 0; offset < CAP; offset += PAGE) {
    const page = await baseRecords(tenantId, { limit: PAGE, offset });
    all.push(...page);
    if (page.length < PAGE) break;
  }
  return all;
}

export type SearchResult = { ok: true; query: string; hits: RecordHit[]; total: number } | { ok: false; query: string };

export async function searchBase(query: string): Promise<SearchResult> {
  const q = String(query ?? "").trim().slice(0, 200);
  if (!q) return { ok: true, query: q, hits: [], total: 0 };
  try {
    const { hits, total } = matchRecords(await readRecords(), q);
    return { ok: true, query: q, hits, total };
  } catch {
    return { ok: false, query: q };
  }
}

export type AskResult = { ok: true; answer: Answer } | { ok: false };

export async function askBase(request: string): Promise<AskResult> {
  const asked = String(request ?? "").trim().slice(0, 1000);
  try {
    return { ok: true, answer: answerRequest(asked ? await readRecords() : [], asked) };
  } catch {
    return { ok: false };
  }
}
