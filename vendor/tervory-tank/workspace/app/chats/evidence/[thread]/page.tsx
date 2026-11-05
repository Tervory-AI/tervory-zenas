import { activeTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { commandRequest } from "@/lib/tank";
import { notFound, redirect } from "next/navigation";
import { dollars, normalizeSnapshot, type Snapshot } from "../../contract";
import "../../window.css";
export const dynamic = "force-dynamic";
export default async function ThreadCosts({ params }: { params: Promise<{ thread: string }> }) {
  const identity = await activeTenant();
  if (!identity) redirect("/onboarding");
  const { thread } = await params;
  if (!/^[\w-]+$/.test(thread)) notFound();
  let snapshot: Snapshot;
  try { snapshot = normalizeSnapshot(await (await commandRequest(identity, `threads/${thread}`)).json()); }
  catch { return <section className="command-evidence"><h1>Cost details unavailable</h1><p>Please reopen the chat and try again.</p><a href={`/chats?thread=${thread}`}>Back to chat</a></section>; }
  const rows = [...snapshot.messages.flatMap(message => message.receipt?.rows ?? []), ...(snapshot.job_receipts ?? []).flatMap(receipt => receipt.rows)];
  const pending = [...snapshot.messages.flatMap(message => message.receipt ? [message.receipt] : []), ...(snapshot.job_receipts ?? [])].filter(receipt => receipt.status === "pending");
  const founder = isFounder(identity.userId);
  return <section className="command-evidence"><h1>Chat costs</h1><p>Spent {dollars(snapshot.thread.spent_usd)} · saved {dollars(snapshot.thread.saved_usd)}</p>{pending.length > 0 && <p>Cost pending for {pending.length} work item(s). Unavailable totals are still unknown.</p>}{rows.map((row, index) => <p key={`${row.ledger_id}-${index}`}><a href={`/chats/evidence/${thread}/${encodeURIComponent(row.ledger_id)}`}>{founder ? row.step_id : `Step ${index + 1}`} · cost {dollars(row.cost_usd)} · would have cost {dollars(row.baseline_cost_usd)} · saved {dollars(row.saved_usd)}{row.baseline_status === "baseline pending" ? " · Comparison pending" : ""}</a></p>)}{!rows.length && !pending.length && <p>No cost details have been recorded yet.</p>}<a href={`/chats?thread=${thread}`}>Back to chat</a></section>;
}
