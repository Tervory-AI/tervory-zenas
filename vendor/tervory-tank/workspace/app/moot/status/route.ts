import { listPublishedMatches } from "../store";

export const dynamic = "force-dynamic";

/** Public readiness only: never include matches or upstream diagnostics. */
export async function GET() {
  const result = await listPublishedMatches();
  const status = result.status === "unavailable" ? 503 : result.status === "malformed" ? 502 : 200;
  return Response.json({ status: result.status }, {
    status,
    headers: { "Cache-Control": "no-store", "X-Robots-Tag": "noindex" },
  });
}
