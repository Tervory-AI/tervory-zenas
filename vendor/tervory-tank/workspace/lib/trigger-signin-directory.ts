import "server-only";
import { tenantBindings } from "./trigger-signin-principal";
import { signinRuntime } from "./trigger-signin-runtime";

type DirectoryUser = { id: string; firstName: string; lastName: string; createdAt: number; publicMetadata: Record<string, unknown>; emailAddresses: { emailAddress: string }[] };
type DirectoryOrg = { id: string; name: string; slug: string };
type PageArgs = { limit?: number; offset?: number; orderBy?: string; query?: string };
type Member = { role: string; publicUserData: { userId: string; firstName: string; lastName: string; identifier: string } };
type UpstreamUser = { id: string; details?: { creationDate?: string }; human?: { profile?: { firstName?: string; lastName?: string }; email?: { email?: string } } };

async function request(path: string, orgId: string, body?: unknown): Promise<Record<string, unknown>> {
  if (!Object.hasOwn(tenantBindings(), orgId)) throw new Error("Trigger sign-in organisation refused");
  const issuer = process.env.TRIGGER_SIGNIN_ISSUER;
  if (!issuer || !["https://auth.tervory.com"].includes(issuer)) throw new Error("Trigger sign-in issuer refused");
  const credential = process.env.TRIGGER_SIGNIN_DIRECTORY_TOKEN;
  if (!credential) throw new Error("Trigger sign-in directory is not configured");
  const response = await fetch(issuer + path, { method: body === undefined ? "GET" : "POST", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(10000),
    headers: { authorization: `Bearer ${credential}`, "x-zitadel-orgid": orgId, "content-type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  if (!response.ok) throw new Error("Trigger sign-in directory is unavailable");
  return response.json() as Promise<Record<string, unknown>>;
}

function userRecord(user: UpstreamUser): DirectoryUser {
  return { id: user.id, firstName: user.human?.profile?.firstName ?? "", lastName: user.human?.profile?.lastName ?? "",
    createdAt: Date.parse(user.details?.creationDate ?? ""), publicMetadata: {},
    emailAddresses: user.human?.email?.email ? [{ emailAddress: user.human.email.email }] : [] };
}

export async function signinDirectory() {
  await signinRuntime();
  const orgIds = Object.keys(tenantBindings());
  const organizations = {
    async getOrganization({ organizationId }: { organizationId: string }): Promise<DirectoryOrg> {
      const result = await request("/management/v1/orgs/me", organizationId);
      const org = result.org as { id?: string; name?: string };
      if (org?.id !== organizationId || typeof org.name !== "string") throw new Error("Trigger sign-in organisation refused");
      return { id: organizationId, name: org.name, slug: organizationId };
    },
    async getOrganizationList({ limit = 100, offset = 0, query }: PageArgs = {}) {
      const all = await Promise.all(orgIds.map(organizationId => organizations.getOrganization({ organizationId })));
      const matching = all.filter(org => !query || org.name === query || org.id === query).sort((left, right) => left.name.localeCompare(right.name));
      return { data: matching.slice(offset, offset + limit), totalCount: matching.length };
    },
    async getOrganizationMembershipList({ organizationId, userId, limit = 100 }: { organizationId: string; userId?: string[]; limit?: number }): Promise<{ data: Member[] }> {
      const project = process.env.TRIGGER_SIGNIN_PROJECT_ID;
      if (!project) throw new Error("Trigger sign-in project required");
      const queries: unknown[] = [{ projectIdQuery: { projectId: project } }];
      if (userId?.length === 1) queries.push({ userIdQuery: { userId: userId[0] } });
      const result = await request("/management/v1/users/grants/_search", organizationId, { query: { limit }, queries });
      const grants = result.result as { userId: string; details?: { resourceOwner?: string }; projectId: string; state: string; roleKeys?: string[]; firstName?: string; lastName?: string; displayName?: string }[] | undefined;
      return { data: (grants ?? []).filter(grant => grant.projectId === project && grant.details?.resourceOwner === organizationId && grant.state === "USER_GRANT_STATE_ACTIVE" && (!userId || userId.includes(grant.userId))).map(grant => ({
        role: grant.roleKeys?.includes("org_admin") ? "org:admin" : "org:member",
        publicUserData: { userId: grant.userId, firstName: grant.firstName ?? "", lastName: grant.lastName ?? "", identifier: grant.displayName ?? grant.userId },
      })) };
    },
  };
  const users = {
    async getUserList({ limit = 100, offset = 0 }: PageArgs = {}) {
      const found = await Promise.all(orgIds.map(async orgId => {
        const result = await request("/management/v1/users/_search", orgId, { query: { limit: 1000, offset: "0", asc: false } });
        const records = (result.result ?? []) as UpstreamUser[];
        const total = Number((result.details as { totalResult?: string })?.totalResult ?? records.length);
        if (total > records.length) throw new Error("Trigger sign-in directory pagination is incomplete");
        return records.map(userRecord);
      }));
      const all = [...new Map(found.flat().map(user => [user.id, user])).values()].sort((left, right) => right.createdAt - left.createdAt);
      return { data: all.slice(offset, offset + limit), totalCount: all.length };
    },
    async getCount() { return (await users.getUserList()).totalCount; },
    async getUser(userId: string) {
      if (!/^[A-Za-z0-9_-]{1,200}$/.test(userId)) throw new Error("Trigger sign-in user refused");
      const all = await users.getUserList({ limit: 1000 });
      const user = all.data.find(record => record.id === userId);
      if (!user) throw new Error("Trigger sign-in user unavailable");
      return user;
    },
  };
  return { organizations, users };
}
