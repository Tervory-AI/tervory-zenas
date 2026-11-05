export const dynamic = "force-dynamic";
export function GET() {
  const fingerprints = (process.env.MOBILE_ANDROID_CERT_SHA256 ?? "").split(",").map(value => value.trim()).filter(value => /^(?:[A-F0-9]{2}:){31}[A-F0-9]{2}$/.test(value));
  const entries = fingerprints.length ? [{ relation: ["delegate_permission/common.handle_all_urls"],
    target: { namespace: "android_app", package_name: "com.tervory.app", sha256_cert_fingerprints: fingerprints } }] : [];
  return Response.json(entries, { headers: { "Cache-Control": "public, max-age=300" } });
}
