import { NextResponse } from "next/server";
import { baseBatches, baseEngines, baseRecords, baseRerun, baseRetry, baseRun } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const taskId = url.searchParams.get("task_id") ?? undefined;
  const batch = url.searchParams.get("batch") ?? undefined;
  const limit = Math.min(Number(url.searchParams.get("limit") ?? 200) || 200, 2000);
  try {
    const { tenantId } = await currentTenant();
    if (url.searchParams.get("engines")) return NextResponse.json(await baseEngines());
    if (url.searchParams.get("batches")) return NextResponse.json(await baseBatches(tenantId));
    return NextResponse.json(await baseRecords(tenantId, { taskId, batch, limit }));
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}

// Body: { batch, csv, preview, engine }. CSV header row names the fields; name/address/city/state/zip/phone/website
// are understood directly, and an engine (e.g. "ffl") maps its own industry columns (the ATF list) into those.
// Also accepts multipart/form-data with fields batch, preview, engine and a CSV file in "file" (up to 25 MB).
export async function POST(req: Request) {
  const action = new URL(req.url).searchParams.get("action");
  if (action === "rerun") {
    const b = (await req.json().catch(() => null)) as { batch?: string; flag?: string; preview?: boolean; skill?: string } | null;
    if (!b?.batch) return NextResponse.json({ error: "batch required" }, { status: 400 });
    try {
      const { tenantId } = await currentTenant();
      const preview = typeof b.preview === "boolean" ? b.preview : undefined;
      const skill = typeof b.skill === "string" && /^[a-z0-9_.]+@[0-9.]+$/.test(b.skill) ? b.skill : undefined;
      return NextResponse.json(await baseRerun(tenantId, b.batch, b.flag || "no_website", preview, skill));
    } catch (err) {
      return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
    }
  }
  if (action === "retry") {
    const b = (await req.json().catch(() => null)) as { batch?: string } | null;
    if (!b?.batch) return NextResponse.json({ error: "batch required" }, { status: 400 });
    try {
      const { tenantId } = await currentTenant();
      return NextResponse.json(await baseRetry(tenantId, b.batch));
    } catch (err) {
      return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
    }
  }
  let body: { batch?: string; csv?: string; url?: string; preview?: boolean; engine?: string | null; split_by_state?: boolean } | null = null;
  if ((req.headers.get("content-type") ?? "").includes("multipart/form-data")) {
    const form = await req.formData().catch(() => null);
    const file = form?.get("file");
    if (!form || !(file instanceof File)) return NextResponse.json({ error: "file required" }, { status: 400 });
    if (file.size > 80 * 1024 * 1024) return NextResponse.json({ error: "file over 80 MB; split it" }, { status: 413 });
    body = {
      batch: String(form.get("batch") ?? ""),
      csv: await file.text(),
      preview: String(form.get("preview") ?? "") === "true",
      engine: form.get("engine") ? String(form.get("engine")) : null,
      split_by_state: String(form.get("split_by_state") ?? "") === "true",
    };
  } else {
    body = (await req.json().catch(() => null)) as typeof body;
  }
  const batch = body?.batch?.trim() ?? "";
  let csv = body?.csv ?? "";
  if (!csv.trim() && body?.url) {
    // Import from a link the server can reach (a Drive "anyone with the link" file, an S3/R2 URL, …).
    const fetched = await fetchCsv(body.url);
    if ("error" in fetched) return NextResponse.json({ error: fetched.error }, { status: 400 });
    csv = fetched.text;
  }
  if (!batch || !csv.trim()) return NextResponse.json({ error: "batch and a CSV (file, pasted text or link) required" }, { status: 400 });
  const records = parseCsv(csv);
  if (records.length === 0) return NextResponse.json({ error: "no rows found; first line must be a header" }, { status: 400 });
  if (records.length > 100000) return NextResponse.json({ error: "max 100,000 rows per upload; split the file" }, { status: 400 });
  try {
    const { tenantId, userId } = await currentTenant();
    const engine = typeof body?.engine === "string" && /^[a-z0-9_]{1,32}$/.test(body.engine) ? body.engine : null;
    return NextResponse.json(await baseRun(tenantId, userId, batch.slice(0, 100), records, Boolean(body?.preview), engine, Boolean(body?.split_by_state)), { status: 202 });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}

// Small, forgiving CSV parser: quotes, commas inside quotes, CRLF. Header keys are lower-cased and trimmed.
function parseCsv(text: string): Record<string, string>[] {
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1); // BOM from Excel exports
  const firstLine = text.split(/\r?\n/, 1)[0] ?? "";
  const delim = [",", "\t", ";", "|"].map((d) => [d, firstLine.split(d).length] as const).sort((a, b) => b[1] - a[1])[0][0];
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') q = false;
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === delim) { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += c;
  }
  if (cell.length || row.length) { row.push(cell); rows.push(row); }
  const header = (rows.shift() ?? []).map((h) => h.trim().toLowerCase().replace(/\s+/g, "_"));
  return rows
    .filter((r) => r.some((v) => v.trim()))
    .map((r) => Object.fromEntries(header.map((h, i) => [h, (r[i] ?? "").trim()])));
}

// Turn a Google Drive share link into its direct-download form; leave other URLs alone.
function directUrl(url: string): string {
  const m = url.match(/drive\.google\.com\/file\/d\/([\w-]+)/) ?? url.match(/[?&]id=([\w-]+)/);
  if (m && /drive\.google\.com|docs\.google\.com/.test(url)) return `https://drive.google.com/uc?export=download&id=${m[1]}`;
  return url;
}

async function fetchCsv(url: string): Promise<{ text: string } | { error: string }> {
  let u: URL;
  try { u = new URL(url); } catch { return { error: "not a valid link" }; }
  if (u.protocol !== "https:") return { error: "link must be https" };
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 120_000);
  try {
    const res = await fetch(directUrl(url), { redirect: "follow", signal: ctrl.signal, headers: { "user-agent": "Tervory/1.0 (+https://tervory.com)" } });
    if (!res.ok) return { error: `link answered ${res.status}` };
    const type = res.headers.get("content-type") ?? "";
    if (type.includes("text/html")) {
      return { error: "the link returned a web page, not a file — for Drive, share it as “Anyone with the link” first" };
    }
    const len = Number(res.headers.get("content-length") ?? 0);
    if (len > 80 * 1024 * 1024) return { error: "file over 80 MB; split it" };
    const buf = Buffer.from(await res.arrayBuffer());
    if (buf.length > 80 * 1024 * 1024) return { error: "file over 80 MB; split it" };
    return { text: buf.toString("utf8") };
  } catch (err) {
    return { error: err instanceof Error && err.name === "AbortError" ? "download took over 2 minutes" : "could not download the link" };
  } finally {
    clearTimeout(timer);
  }
}
