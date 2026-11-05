import { auth, signinDirectory } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { isFounder } from "@/lib/admin";
import "../dashboard.css";
export default async function SignupsPage({ searchParams }: { searchParams: Promise<{ page?: string }> }) {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) notFound();
  const page = Math.max(1, Math.min(100000, Number((await searchParams).page) || 1));
  const client = await signinDirectory();
  try {
    const users = await client.users.getUserList({ limit: 50, offset: (page - 1) * 50, orderBy: "-created_at" });
    return <div className="dashboard-root dash-control"><a href="/control">Back to control panel</a><h1>Sign-ups</h1><section className="dash-card">{users.data.map(user => <p className="dash-row" key={user.id}>{[user.firstName,user.lastName].filter(Boolean).join(" ") || "Name not provided"} · {new Date(user.createdAt).toISOString().slice(0,10)}</p>)}{users.data.length === 0 && <p>No sign-ups on this page.</p>}</section>{page > 1 && <a className="dash-button" href={`?page=${page - 1}`}>Previous</a>}{page * 50 < users.totalCount && <a className="dash-button" href={`?page=${page + 1}`}>Next</a>}</div>;
  } catch { return <div className="dashboard-root dash-control"><h1>Sign-ups</h1><p role="alert">Sign-ups could not be loaded. Please try again.</p></div>; }
}
