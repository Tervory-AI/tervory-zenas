import { auth } from "@/lib/trigger-signin";
import Link from "next/link";
import { notFound } from "next/navigation";
import { isFounder } from "@/lib/admin";
import { emptyConvert, publicConvert, publicFeeds, publicScores, type PublicConvert, type PublicScore, type PublicSource } from "@/app/api/zenas/feeds/public";
import { homeRegion, zenasIn, type Region } from "@/app/api/zenas/tank";
import { FeedSwitch } from "./board";
import "./feeds.css";

export const dynamic = "force-dynamic";

async function readBox(region: Region): Promise<{
  sources: PublicSource[];
  scores: PublicScore[];
  review: "on" | "off";
  convert: PublicConvert;
  note: string;
}> {
  let sources: PublicSource[] = [];
  let scores: PublicScore[] = [];
  let review: "on" | "off" = "off";
  let convert: PublicConvert = emptyConvert(region);
  let note = "";
  try {
    const feeds = await zenasIn(region, `pool/status?home_region=${region}`, { method: "GET" });
    if (!feeds) note = "That box is not connected.";
    else if (feeds.ok) sources = publicFeeds(await feeds.json()).sources;
    else note = "Feeds could not be loaded.";
  } catch {
    note = "Feeds could not be loaded.";
  }
  try {
    const scored = await zenasIn(region, `selftest?home_region=${region}`, { method: "GET" });
    if (scored && scored.ok) scores = publicScores(await scored.json()).runs;
  } catch {
    scores = [];
  }
  try {
    const switched = await zenasIn(region, `review-switch?home_region=${region}`, { method: "GET" });
    if (switched && switched.ok) {
      const body = (await switched.json()) as { review?: string };
      review = body.review === "on" ? "on" : "off";
    }
  } catch {
    review = "off";
  }
  try {
    const cases = await zenasIn(region, `cases/convert?home_region=${region}`, { method: "GET" });
    if (cases && cases.ok) convert = publicConvert(await cases.json());
  } catch {
    convert = emptyConvert(region);
  }
  return { sources, scores, review, convert, note };
}

export default async function ZenasFeedsPage() {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  const region = homeRegion();
  const box = await readBox(region);

  return (
    <div className="zf">
      <header>
        <div>
          <picture>
            <source srcSet="/brand/tervory-logo-reversed.png" media="(prefers-color-scheme: dark)" />
            <img src="/brand/tervory-logo.png" alt="Tervory" />
          </picture>
          <h1>Zenas feeds</h1>
        </div>
        <Link className="back" href="/zenas">Back to Zenas</Link>
      </header>
      <main>
        <FeedSwitch
          initialRegion={region}
          initialSources={box.sources}
          initialScores={box.scores}
          initialReview={box.review}
          initialConvert={box.convert}
          note={box.note}
        />
      </main>
    </div>
  );
}
