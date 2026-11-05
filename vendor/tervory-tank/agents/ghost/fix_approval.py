"""Rules-only views over the advisory watcher's existing match store."""
from .patch_brief import DOGFOOD_REPOS, PatchBurned, passes_severity_bar
from .patch_connect import match_to_patch_brief


def brief_for(match):
    brief = match_to_patch_brief(match)
    if match.get('repo', brief.repo) not in DOGFOOD_REPOS or brief.repo not in DOGFOOD_REPOS:
        raise PatchBurned()
    if not passes_severity_bar(brief):
        raise PatchBurned()
    return brief


def stored_match(store, tenant, match_id):
    for match in store.matches(tenant, 'open'):
        if match['id'] == match_id:
            brief_for(match)
            return match
    raise PatchBurned()


def fixes(store, tenant):
    items = []
    for match in store.matches(tenant, 'open'):
        try:
            brief = brief_for(match)
        except (PatchBurned, ValueError, TypeError):
            continue
        items.append({'id': match['id'], 'name': brief.package, 'status': brief.severity.value,
                      'summary': f"{brief.from_version} → {brief.to_version} · {match.get('advisory_id') or ''} · {match['id']}",
                      'package': brief.package,
                      'from_version': brief.from_version, 'to_version': brief.to_version,
                      'advisory_id': match.get('advisory_id'), 'severity': brief.severity.value})
    return {'items': items}
