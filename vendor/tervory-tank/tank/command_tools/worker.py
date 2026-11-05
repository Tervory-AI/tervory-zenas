"""worker command declarations; argument classes remain exported by command."""
TOOLS = {}
FOUNDER_TOOLS = ['tank.policy', 'tank.policy.fallbacks', 'worker.build', 'repo.prs', 'repo.pr', 'repo.file']
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset([])
REQUEST_BODY_TOOLS = frozenset([])
CONFIRMED = frozenset([])

def adapters(command):
    return {
        'tank.policy': command.Adapter('GET', '/v1/command/tank/policy', command.PolicyArgs, 'tank.policy'),
        'tank.policy.fallbacks': command.Adapter('POST', '/v1/command/tank/policy/fallbacks', command.PolicyFallbackArgs, 'tank.policy.fallbacks'),
        'repo.prs': command.Adapter('GET', '/v1/command/repo/prs', command.RepoPRsArgs, 'open PRs on Tervory-AI/tervory-tank'),
        'repo.pr': command.Adapter('GET', '/v1/command/repo/pr', command.RepoPRArgs, "one PR's changed files and check results"),
        'repo.file': command.Adapter('GET', '/v1/command/repo/file', command.RepoFileArgs, 'read one file from main'),
    }
