import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { marketLabRequest, MarketLabRequestError, type MarketDocument } from "@/lib/tank";
import { SourceDocumentView } from "./source-view";

export const sourceMetadata: Metadata = { title: "Market Lab · Tervory" };

/** Same source load and error path for the authenticated page and local proof. */
export async function StoredSourcePage({ tenantId, id, row, page, kind }: { tenantId: string; id: string; row?: string; page?: string; kind?: string }) {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) notFound();
  const requestedPage = Number(page ?? 1);
  const offset = Number.isSafeInteger(requestedPage) && requestedPage >= 1 && requestedPage <= 200001 ? (requestedPage - 1) * 50 : 0;
  const requestedRow = row === undefined ? undefined : Number(row);
  const focus = page === undefined && requestedRow !== undefined && Number.isSafeInteger(requestedRow) && requestedRow >= 0 && requestedRow <= 10000000 ? requestedRow : undefined;
  let source: MarketDocument;
  try {
    source = await marketLabRequest<MarketDocument>(tenantId, "document", { document_id: id, offset, row: focus, row_kind: kind === "event" ? "event" : kind === "point" ? "point" : undefined });
  } catch (error) {
    if (error instanceof MarketLabRequestError && error.status === 404) notFound();
    return <main className="ml ml-source"><div className="ml-body"><a href="/market-lab">Back to Market Lab</a><h1>Source unavailable</h1><p>This source could not be read in your workspace. Try again shortly.</p></div></main>;
  }
  return <SourceDocumentView source={source} />;
}
