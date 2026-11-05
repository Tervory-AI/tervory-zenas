"use client";

import { SessionProvider, signIn, signOut, useSession } from "next-auth/react";

export function TriggerSigninProvider({ children }: { children: React.ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}

export function useSignin() {
  const { data, status } = useSession();
  const principal = (data as { principal?: { userId: string; orgId: string } } | null)?.principal;
  return { isLoaded: status !== "loading", isSignedIn: !!principal, userId: principal?.userId ?? null, orgId: principal?.orgId ?? null };
}

export function OrganisationSwitcher() {
  const { data, update } = useSession();
  const session = data as { principal?: { orgId: string }; organisations?: string[] } | null;
  return <label>Organisation<select aria-label="Switch organisation" value={session?.principal?.orgId ?? ""} onChange={event => void update({ orgId: event.target.value }).then(() => window.location.reload())}>
    {(session?.organisations ?? []).map(org => <option key={org} value={org}>{org}</option>)}
  </select><button type="button" onClick={() => void signIn("trigger-signin", { callbackUrl: window.location.href }, { prompt: "select_account" })}>Use another Trigger sign-in account</button></label>;
}

export function SignoutButton() {
  return <button type="button" onClick={() => void signOut({ callbackUrl: "/api/trigger-signin/signed-out" })}>Sign out of Trigger sign-in</button>;
}

export function TriggerSigninButton({ register = false }: { register?: boolean }) {
  return <button type="button" onClick={() => void signIn("trigger-signin", { callbackUrl: new URLSearchParams(window.location.search).get("callbackUrl") ?? "/" }, { prompt: register ? "create" : "select_account" })}>Continue to Trigger sign-in</button>;
}
