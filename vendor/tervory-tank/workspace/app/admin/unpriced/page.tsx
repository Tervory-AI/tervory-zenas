import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { isFounder, readUnpricedCalls } from "@/lib/admin";
import { dateLabel } from "../../base/display";

export const dynamic = "force-dynamic";
export default async function UnpricedPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) notFound();
  const filters = await searchParams;
  const query = new URLSearchParams();
  for (const key of ["tenant_id", "days", "day", "task", "agent", "offset"]) {
    if (typeof filters[key] === "string") query.set(key, filters[key]);
  }
  const offset = Math.max(0, Number(query.get("offset")) || 0);
  const data = await readUnpricedCalls(userId, query);
  const next = new URLSearchParams(query);
  next.set("offset", String(offset + data.rows.length));
  return <section><a href="/admin">Back to the founder console</a><h1>Unpriced calls</h1>
    <p>These calls are excluded from recorded spend and savings. Setting a price affects new calls only.</p>
    <p>{data.total} {data.total === 1 ? "call" : "calls"}. {data.rows.length > 0 && <>Showing {offset + 1}–{offset + data.rows.length}.</>}</p>
    <table><thead><tr><th>When</th><th>Account</th><th>Work</th><th>Provider</th><th>Cost</th><th>Receipt</th></tr></thead>
      <tbody>{data.rows.map(row => <tr key={row.id}><td>{dateLabel(row.ts)}</td><td>{row.tenant_id}</td><td>{row.agent} · {row.task_type}</td><td>{row.vendor || "Processing"}</td><td>price not set</td><td>{row.id}</td></tr>)}</tbody></table>
    {!data.rows.length && <p>No unpriced calls match this period.</p>}
    {offset + data.rows.length < data.total && <a href={`/admin/unpriced?${next}`}>Next page</a>}
  </section>;
}
