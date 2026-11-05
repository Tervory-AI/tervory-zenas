import { auth } from "@/lib/trigger-signin";
import Link from "next/link";
import { notFound } from "next/navigation";
import { isFounder } from "@/lib/admin";
import { presentFeeds, type FeedRow } from "@/app/api/zenas/rules";
import { homeRegion, zenas } from "@/app/api/zenas/tank";
import { FeedBoard } from "./board";
import "./feeds.css";

export const dynamic = "force-dynamic";

export default async function ZenasFeedsPage() {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  let sources: FeedRow[] = [];
  let note = "";
  const region = homeRegion();
  try {
    const res = await zenas(`pool/status?home_region=${region}`, { method: "GET" });
    if (res.ok) {
      const view = presentFeeds(await res.json());
      sources = view.sources;
    } else if (res.status === 404) note = "Feed status is not on this server yet.";
    else note = "Feeds could not be loaded.";
  } catch {
    note = "Feeds could not be loaded.";
  }

  return (
    <div className="shell">
      <header className="head">
        <div>
          <picture>
            <source srcSet="/brand/tervory-logo-reversed.png" media="(prefers-color-scheme: dark)" />
            <img src="/brand/tervory-logo.png" alt="Tervory" />
          </picture>
          <h1>Zenas feeds</h1>
        </div>
        <Link className="back" href="/admin">Back to the founder console</Link>
      </header>
      <FeedBoard region={region} sources={sources} note={note} />
    </div>
  );
}
