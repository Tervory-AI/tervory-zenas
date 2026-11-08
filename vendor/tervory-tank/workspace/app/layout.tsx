import type { Metadata, Viewport } from "next";
import { TriggerSigninProvider } from "@/app/trigger-signin-provider";
import { auth } from "@/lib/trigger-signin";
import { isListedFounder } from "./review/founder";
import { Shell, type ShellAccess } from "./shell";
import { inter, jost, mono } from "./fonts";
import "./globals.css";
import { InstallShell } from "./mobile-shell/install";
import { mobileIdentity } from "@/lib/mobile";
import { MobileAccount } from "./mobile/account";
import { workspaceSetupMessage } from "@/lib/workspace-setup";

// Setup and identity are request state, including when settings are absent at build time.
export const dynamic = "force-dynamic";

export const viewport: Viewport = { themeColor: "#001840" };

export const metadata: Metadata = {
  title: "Tervory",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "Tervory", statusBarStyle: "default" },
  icons: { apple: "/icons/tervory-180.png", icon: "/icons/tervory-192.png" },
  description: "Ask for work. See what it cost.",
};

async function accessForRequest(): Promise<ShellAccess> {
  const mobile = await mobileIdentity();
  if (mobile) return { founder: isListedFounder(mobile.userId, process.env["TERVORY_ADMIN_USER_IDS"]), reviewer: false };
  const { userId, orgRole } = await auth();
  const founder = isListedFounder(userId, process.env["TERVORY_ADMIN_USER_IDS"]);
  const role = (orgRole ?? "").replace(/^org:/, "");
  return { founder, reviewer: role === "reviewer" };
}

async function readAccess(): Promise<ShellAccess> {
  "use server";
  return accessForRequest();
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const setup = (message: string) => <html lang="en" className={`${jost.variable} ${inter.variable} ${mono.variable}`}><body><main><h1>Workspace setup</h1><p role="status">{message}</p><p>Add the settings before signing in or starting work.</p></main></body></html>;
  const problem = workspaceSetupMessage();
  if (problem) return setup(problem);
  let access: ShellAccess;
  let mobile: Awaited<ReturnType<typeof mobileIdentity>>;
  try {
    access = await accessForRequest();
    mobile = await mobileIdentity();
  } catch {
    return setup("Trigger sign-in configuration is unavailable. Check TANK_URL and TANK_API_KEY.");
  }
  return (
    <html lang="en" className={`${jost.variable} ${inter.variable} ${mono.variable}`}>
      <body>
        <InstallShell />
        {mobile && <MobileAccount name={mobile.orgName} />}
        <TriggerSigninProvider>
          <Shell founder={access.founder} reviewer={access.reviewer} readAccess={readAccess}>{children}</Shell>
        </TriggerSigninProvider>
      </body>
    </html>
  );
}
