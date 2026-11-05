"""Plugin command declarations; identity checks stay in command."""
TOOLS = {'plugins': ['plugins.list', 'plugins.install', 'plugins.remove']}
FOUNDER_TOOLS = []
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset()
REQUEST_BODY_TOOLS = frozenset([])
CONFIRMED = frozenset([])

def adapters(command):
    return {
        'plugins.list': command.Adapter('GET', '/v1/command/plugins/list', command.EmptyArgs, 'plugins.list'),
        'plugins.install': command.Adapter('POST', '/v1/command/plugins/install', command.PluginArgs, 'plugins.install'),
        'plugins.remove': command.Adapter('POST', '/v1/command/plugins/remove', command.PluginArgs, 'plugins.remove'),
    }
