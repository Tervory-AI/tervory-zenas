// Public OAuth Client ID Metadata Document. No credentials or per-tenant data.
export async function GET() {
  return Response.json({
    client_id: "https://app.tervory.com/oauth/client/mcp.json",
    client_name: "Tervory",
    redirect_uris: ["https://app.tervory.com/oauth/callback"],
    grant_types: ["authorization_code", "refresh_token"],
    response_types: ["code"],
    token_endpoint_auth_method: "none",
  }, { headers: { "Cache-Control": "public, max-age=3600" } });
}
