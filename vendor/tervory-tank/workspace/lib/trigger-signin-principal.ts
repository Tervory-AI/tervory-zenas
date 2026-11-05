export const ORG_CLAIM = "urn:zitadel:iam:user:resourceowner:id";
export const ROLES_CLAIM = "urn:zitadel:iam:org:project:roles";
export type SigninPrincipal = { userId: string; orgId: string; tenantId: string; orgRole: string | null; expiresAt: number };

export function tenantBindings(raw = process.env.TRIGGER_SIGNIN_ORG_TENANTS ?? "{}"): Record<string, string> {
  const bindings: unknown = JSON.parse(raw);
  if (!bindings || typeof bindings !== "object" || Array.isArray(bindings)) throw new Error("Trigger sign-in organisation settings refused");
  for (const [org, tenant] of Object.entries(bindings)) {
    if (!/^\d{1,30}$/.test(org) || typeof tenant !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(tenant)) throw new Error("Trigger sign-in organisation settings refused");
  }
  return bindings as Record<string, string>;
}

export function signinOrganisations(claims: Record<string, unknown>): string[] {
  const roles = claims[ROLES_CLAIM];
  if (!roles || typeof roles !== "object" || Array.isArray(roles)) return [];
  const bindings = tenantBindings();
  const organisations = new Set<string>();
  for (const grants of Object.values(roles)) {
    if (!grants || typeof grants !== "object" || Array.isArray(grants)) continue;
    for (const org of Object.keys(grants)) if (Object.hasOwn(bindings, org)) organisations.add(org);
  }
  return [...organisations].sort();
}

export function signinPrincipal(claims: Record<string, unknown>, now = Date.now(), selectedOrg?: unknown): SigninPrincipal {
  const subject = claims.sub;
  const organisations = signinOrganisations(claims);
  const home = claims[ORG_CLAIM];
  const org = selectedOrg ?? (typeof home === "string" && organisations.includes(home) ? home : organisations[0]);
  const expires = claims.exp;
  if (typeof subject !== "string" || !/^[A-Za-z0-9_-]{1,200}$/.test(subject) || typeof org !== "string" || !/^\d{1,30}$/.test(org)
    || typeof expires !== "number" || !Number.isSafeInteger(expires) || expires * 1000 <= now) throw new Error("Trigger sign-in identity refused");
  const tenantId = organisations.includes(org) ? tenantBindings()[org] : undefined;
  if (!tenantId) throw new Error("Trigger sign-in organisation is not linked to a workspace");
  const roles = claims[ROLES_CLAIM] as Record<string, unknown> | undefined;
  const admins = roles?.org_admin;
  const admin = !!admins && typeof admins === "object" && !Array.isArray(admins) && Object.hasOwn(admins, org);
  const reviewers = roles?.reviewer;
  const reviewer = !!reviewers && typeof reviewers === "object" && !Array.isArray(reviewers) && Object.hasOwn(reviewers, org);
  return { userId: subject, orgId: org, tenantId, orgRole: admin ? "org:admin" : reviewer ? "org:reviewer" : null, expiresAt: expires * 1000 };
}
