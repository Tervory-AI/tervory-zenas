"use client";

import { useRef, useState } from "react";

import { SessionProvider, signIn, signOut, useSession } from "next-auth/react";

export function TriggerSigninProvider({ children }: { children: React.ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}

export function useSignin() {
  const { data, status } = useSession();
  const principal = (data as { principal?: { userId: string; orgId: string } } | null)?.principal;
  return { isLoaded: status !== "loading", isSignedIn: !!principal, userId: principal?.userId ?? null, orgId: principal?.orgId ?? null };
}

function useAccountAction() {
  const pending = useRef(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(false);
  async function perform(action: () => Promise<unknown>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(false);
    let timer: ReturnType<typeof setTimeout>;
    try { await Promise.race([action(), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(Error("timeout")), 8000); })]); }
    catch { setError(true); }
    finally { clearTimeout(timer!); pending.current = false; setBusy(false); }
  }
  return { busy, error, perform };
}

export function OrganisationSwitcher() {
  const { data, update } = useSession();
  const action = useAccountAction();
  const session = data as { principal?: { orgId: string }; organisations?: string[] } | null;
  return <><label>Organisation<select aria-label="Switch organisation" disabled={action.busy} value={session?.principal?.orgId ?? ""} onChange={event => { const orgId = event.target.value; void action.perform(() => update({ orgId }).then(() => window.location.reload())); }}>
    {(session?.organisations ?? []).map(org => <option key={org} value={org}>{org}</option>)}
  </select><button type="button" disabled={action.busy} onClick={() => void action.perform(() => signIn("trigger-signin", { callbackUrl: window.location.href }, { prompt: "select_account" }))}>Use another Trigger sign-in account</button></label>{action.error && <p role="alert">The workspace switch could not be confirmed. Reload to check your organisation.</p>}</>;
}

export function SignoutButton() {
  const action = useAccountAction();
  return <><button type="button" disabled={action.busy} onClick={() => void action.perform(() => signOut({ callbackUrl: "/api/trigger-signin/signed-out" }))}>Sign out of Trigger sign-in</button>{action.error && <p role="alert">Sign-out could not be confirmed. Please try again.</p>}</>;
}

export function TriggerSigninButton({ register = false }: { register?: boolean }) {
  const action = useAccountAction();
  return <><button type="button" disabled={action.busy} onClick={() => void action.perform(() => signIn("trigger-signin", { callbackUrl: new URLSearchParams(window.location.search).get("callbackUrl") ?? "/" }, { prompt: register ? "create" : "select_account" }))}>Continue to Trigger sign-in</button>{action.error && <p role="alert">Sign-in could not be completed. Please try again.</p>}</>;
}
