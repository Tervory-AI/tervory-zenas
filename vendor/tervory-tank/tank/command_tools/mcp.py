"""mcp command declarations; argument classes remain exported by command."""
TOOLS = {}
FOUNDER_TOOLS = ['mcp.servers.list', 'mcp.servers.approve', 'mcp.servers.remove']
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset([])
REQUEST_BODY_TOOLS = frozenset([])
CONFIRMED = frozenset([])

def adapters(command):
    return {
        'mcp.servers.list': command.Adapter('GET', '/v1/command/mcp/servers/list', command.EmptyArgs, 'mcp.servers.list'),
        'mcp.servers.approve': command.Adapter('POST', '/v1/command/mcp/servers/approve', command.MCPApproveArgs, 'mcp.servers.approve'),
        'mcp.servers.remove': command.Adapter('POST', '/v1/command/mcp/servers/remove', command.MCPServerArgs, 'mcp.servers.remove'),
    }
