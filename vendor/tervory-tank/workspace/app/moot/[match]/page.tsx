import { notFound } from "next/navigation";
import { WatchPanel } from "../WatchPanel";
import { getMatch } from "../store";
import "../moot-watch.css";

/** Public watch page. Unpublished matches return 404. */
export default async function MootWatchPage({ params, searchParams }: { params: Promise<{ match: string }>; searchParams?: Promise<{ region?: string }> }) {
  const { match: matchId } = await params;
  const region = (await searchParams)?.region === "za" ? "za" : "us-east";
  const result = await getMatch(matchId, region);
  if (result.status === "missing") notFound();
  if (result.status === "unavailable" || result.status === "malformed") {
    return <main className="moot-watch" aria-label="Moot watch">
      <p role="alert" data-moot-state={result.status}>Room unavailable</p>
    </main>;
  }
  return <WatchPanel row={result.match} />;
}
