import { auth, signinDirectory } from "@/lib/trigger-signin";
import Link from "next/link";
import { notFound } from "next/navigation";
import { isFounder } from "@/lib/admin";
import { tenantIdFor } from "@/lib/tenant";
import { HomeBoard, type OrgHome } from "./board";
import "./homes.css";

export const dynamic = "force-dynamic";

async function listOrganisations(): Promise<OrgHome[]> {
  const client = await signinDirectory();
  const found: OrgHome[] = [];
  const limit = 100;
  let offset = 0;
  for (;;) {
    const page = await client.organizations.getOrganizationList({ limit, offset, orderBy: "name" });
    for (const org of page.data) {
      found.push({ orgId: org.id, name: org.name, tenantId: tenantIdFor(org.id) });
    }
    offset += page.data.length;
    if (page.data.length < limit || offset >= page.totalCount || offset >= 1000) break;
  }
  return found;
}

export default async function HomeRegionPage() {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  let orgs: OrgHome[] = [];
  let note = "";
  try {
    orgs = await listOrganisations();
  } catch {
    note = "Organisations could not be loaded.";
  }

  return (
    <div className="shell">
      <header className="head">
        <div>
          <picture>
            <source srcSet="/brand/tervory-logo-reversed.png" media="(prefers-color-scheme: dark)" />
            <img src="/brand/tervory-logo.png" alt="Tervory" />
          </picture>
          <h1>Home region</h1>
        </div>
        <Link className="back" href="/admin">Back to the founder console</Link>
      </header>
      <p className="lede">Set each organisation&apos;s home once. South Africa is za. The United States is us-east. There is no default.</p>
      <HomeBoard orgs={orgs} note={note} />
    </div>
  );
}
