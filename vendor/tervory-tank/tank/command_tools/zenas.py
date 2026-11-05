"""zenas command declarations; argument classes remain exported by command."""
TOOLS = {'zenas': ['zenas.ask', 'zenas.matters', 'zenas.draft', 'zenas.review', 'zenas.playbook.add', 'zenas.playbooks', 'zenas.watch.add', 'zenas.watch.list', 'zenas.watch.enable', 'zenas.watch.disable']}
FOUNDER_TOOLS = []
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset([])
REQUEST_BODY_TOOLS = frozenset([])
CONFIRMED = frozenset(['zenas.draft', 'zenas.playbook.add'])

def adapters(command):
    return {
        'zenas.ask': command.Adapter('POST', '/v1/zenas/answers', command.ZenasAskArgs, 'Ask a civil law question', True, 'paid_run'),
        'zenas.matters': command.Adapter('POST', '/v1/zenas/matters', command.MatterArgs, 'Create a matter'),
        'zenas.draft': command.Adapter('POST', '/v1/zenas/drafts', command.ZenasDraftArgs, 'Draft a document', True, 'paid_run'),
        'zenas.playbook.add': command.Adapter('POST', '/v1/zenas/playbooks', command.ZenasPlaybookAddArgs, 'Add a review guide'),
        'zenas.playbooks': command.Adapter('GET', '/v1/zenas/playbooks', command.EmptyArgs, 'List review guides'),
        'zenas.watch.add': command.Adapter('POST', '/v1/zenas/regulations/watches', command.ZenasWatchAddArgs, 'zenas.watch.add'),
        'zenas.watch.list': command.Adapter('GET', '/v1/zenas/regulations/watches', command.EmptyArgs, 'zenas.watch.list'),
        'zenas.watch.enable': command.Adapter('POST', '/v1/zenas/regulations/watches/{watch_id}', command.ZenasWatchIdArgs, 'zenas.watch.enable'),
        'zenas.watch.disable': command.Adapter('POST', '/v1/zenas/regulations/watches/{watch_id}', command.ZenasWatchIdArgs, 'zenas.watch.disable'),
        'zenas.review': command.Adapter('POST', '/v1/zenas/contract-reviews', command.ReviewArgs, 'Review a contract', True, 'paid_run'),
    }
