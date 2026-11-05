export const dynamic = "force-dynamic";
export function GET() {
  const team = process.env.MOBILE_APPLE_TEAM_ID;
  const details = team && /^[A-Z0-9]{10}$/.test(team)
    ? [{ appIDs: [`${team}.com.tervory.app`], components: [{ "/": "/mobile/callback" }] }] : [];
  return Response.json({ applinks: { details } }, { headers: { "Cache-Control": "public, max-age=300" } });
}
