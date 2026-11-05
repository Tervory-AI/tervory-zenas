import { activeTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { commandRequest } from "@/lib/tank";
import { notFound, redirect } from "next/navigation";
import { normalizeSnapshot, dollars, type Snapshot } from "../../../contract";
import "../../../window.css";
export const dynamic = "force-dynamic";
export default async function EvidencePage({ params }: { params: Promise<{ thread: string; row: string }> }) {
  const identity = await activeTenant();
  if (!identity) redirect("/onboarding");
  const { thread, row } = await params;
  if (![thread, row].every(value => /^[\w-]+$/.test(value))) notFound();
  let snapshot: Snapshot;
  try { snapshot = normalizeSnapshot(await (await commandRequest(identity, `threads/${thread}`)).json()); }
  catch { return <section className="command-evidence"><h1>Cost details unavailable</h1><p>Please reopen the chat and try again.</p><a href={`/chats?thread=${thread}`}>Back to chat</a></section>; }
  const evidence = [...snapshot.messages.flatMap(message => message.receipt?.rows ?? []), ...(snapshot.job_receipts ?? []).flatMap(receipt => receipt.rows)].find(item => item.ledger_id === row);
  if (!evidence) notFound();
  return <section className="command-evidence"><h1>Cost details</h1>{evidence.cost_status === "pending" && <p>Cost pending.</p>}{evidence.cost_status === "price not set" && <p>price not set</p>}{evidence.baseline_status === "baseline pending" && <p>Comparison pending.</p>}<dl><dt>Cost</dt><dd>{dollars(evidence.cost_usd)}</dd><dt>Would have cost</dt><dd>{dollars(evidence.baseline_cost_usd)}</dd><dt>Saved</dt><dd>{dollars(evidence.saved_usd)}</dd>{evidence.auto_cost_usd !== undefined && <><dt>Auto would have cost</dt><dd>{dollars(evidence.auto_cost_usd)}</dd></>}{isFounder(identity.userId) && <><dt>Model</dt><dd>{evidence.model}</dd><dt>Baseline model</dt><dd>{evidence.baseline_model}</dd><dt>Tokens in / out</dt><dd>{evidence.tokens_in} / {evidence.tokens_out}</dd><dt>Ledger entry</dt><dd>{evidence.ledger_id}</dd></>}</dl><a href={`/chats?thread=${thread}`}>Back to chat</a></section>;
}
