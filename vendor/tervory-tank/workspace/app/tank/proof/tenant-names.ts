const PLATFORM = "00000000-0000-0000-0000-000000000001";
type OrganisationPage = { data: { id: string; name: string; slug?: string | null }[]; totalCount: number };

/** Names are display metadata only. Never combine accounts or change their data scope. */
export async function resolveTenantNames(
  tenantIds: string[],
  listPage: (params: { limit: number; offset: number; orderBy: "name" }) => Promise<OrganisationPage>,
  tenantIdFor: (orgId: string) => string,
  originalTenantIdFor: (orgId: string) => string = tenantIdFor,
): Promise<{ tenant_names: Record<string, string>; tenant_names_note: string | null }> {
  const ids = [...new Set(tenantIds)].sort();
  const wanted = new Set(ids);
  const names: Record<string, string> = { [PLATFORM]: "Tervory (platform)" };
  const candidates = new Map<string, { name: string; slug: string; prior: boolean }[]>();
  let unavailable = false;
  try {
    let offset = 0;
    // Read all pages: an alias can match an old organisation on an early page
    // and the authoritative current organisation on a later page.
    while (ids.some(id => id !== PLATFORM)) {
      const page = await listPage({ limit: 100, offset, orderBy: "name" });
      for (const org of page.data) {
        const name = org.name.trim();
        if (!name) continue;
        const current = tenantIdFor(org.id), original = originalTenantIdFor(org.id);
        for (const id of new Set([current, original])) {
          if (!wanted.has(id) || id === PLATFORM) continue;
          const rows = candidates.get(id) ?? [];
          rows.push({ name, slug: org.slug?.trim() || "", prior: current !== original && id === original });
          // Prefer the active alias target over an obsolete directory entry.
          if (current !== original && id === current) rows.unshift(rows.pop()!);
          candidates.set(id, rows);
        }
      }
      offset += page.data.length;
      if (!page.data.length || offset >= page.totalCount) break;
    }
  } catch { unavailable = true; }
  const labels = new Map<string, string[]>();
  for (const id of ids) {
    if (id === PLATFORM) continue;
    const account = candidates.get(id)?.[0];
    if (!account) continue;
    names[id] = account.name + (account.prior ? " (before account link)" : "");
    const key = names[id].toLocaleLowerCase();
    labels.set(key, [...(labels.get(key) ?? []), id]);
  }
  // Two distinct organisations may legitimately share a name. Preserve both
  // accounts and make every option distinguishable, including the platform.
  for (const group of labels.values()) {
    const conflicts = group.length > 1 || names[group[0]].toLocaleLowerCase() === names[PLATFORM].toLocaleLowerCase();
    if (!conflicts) continue;
    group.forEach((id,index) => {
      const slug = candidates.get(id)?.[0].slug;
      names[id] += ` (${slug || `workspace ${index + 1}`})`;
    });
  }
  // A deleted organisation or direct API tenant has no verifiable SigninDirectory name.
  // Keep its evidence reachable without pretending it is a known client.
  const missing = ids.filter(id => !names[id]);
  missing.forEach((id,index) => { names[id] = `Unlinked account ${index + 1}`; });
  const notes = [];
  if (unavailable) notes.push("The organisation directory could not be fully loaded. Try again to refresh account names.");
  if (missing.length) notes.push(`${missing.length} account${missing.length === 1 ? "" : "s"} could not be linked to an active organisation. Their evidence remains available as unlinked accounts.`);
  return { tenant_names: names, tenant_names_note: notes.length ? notes.join(" ") : null };
}
