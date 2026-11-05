import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { isFounder, loadFounder } from "@/lib/admin";
import { savePrice, setAutoPromote } from "./actions";
import { FounderConsole } from "./console";

export const dynamic = "force-dynamic";

export default async function AdminPage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const { userId } = await auth();
  if (!isFounder(userId) || !userId) notFound();
  const params = await searchParams;
  const days = params.days === "30" ? 30 : params.days === "365" ? 365 : 90;
  try {
    const data = await loadFounder(userId, days);
    return <FounderConsole days={days} savePrice={savePrice} setAutoPromote={setAutoPromote} {...data} />;
  } catch (err) {
    const message = err instanceof Error ? err.message : "The console could not load.";
    return <p className="load-error">{message}</p>;
  }
}
