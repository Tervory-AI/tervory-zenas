import type { Metadata } from "next";
import Link from "next/link";
import { listPublishedMatches } from "./store";
import "./moot-watch.css";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Moot Court · Zenas",
  description: "Public Moot watch — text edition. Training only; nothing here is a filed matter.",
};

/** Public Moot index on zenas.tervory.com (product host). */
export default async function MootIndexPage({ searchParams }: { searchParams?: Promise<{ region?: string }> }) {
  const region = (await searchParams)?.region === "za" ? "za" : "us-east";
  const result = await listPublishedMatches(undefined, region);
  return (
    <main className="moot-watch" aria-label="Moot Court">
      <h1>Moot Court</h1>
      <p>
        This is where Zenas trains in text. Public watch pages only — nothing spoken here yet.
        Law students will use it later, for free.
      </p>
      <p>
        Full Zenas panel: <Link href="/zenas">zenas.tervory.com/zenas</Link>
      </p>
      <section aria-label="Published matches">
        <h2>Published matches</h2>
        {result.status === "unavailable" || result.status === "malformed" ? (
          <p role="alert" data-moot-state={result.status}>Room unavailable</p>
        ) : result.status === "empty" ? (
          <p>No published matches yet.</p>
        ) : (
          <ul>
            {result.matches.map((row) => (
              <li key={row.matchId}>
                <Link href={`/moot/${row.matchId}${region === "za" ? "?region=za" : ""}`}>{row.title}</Link> <span>unverified</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
