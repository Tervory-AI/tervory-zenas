import { TriggerSigninButton } from "../../trigger-signin-provider";

export default async function Page({ searchParams }: { searchParams?: Promise<{ error?: string }> }) {
  const error = (await searchParams)?.error;
  return <section><h1>Trigger sign-in</h1>{error && <p role="alert">Sign-in could not be completed. Please try again.</p>}<TriggerSigninButton /></section>;
}
