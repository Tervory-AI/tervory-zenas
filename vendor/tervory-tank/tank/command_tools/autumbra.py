"""Autumbra founder command declarations; runtime behavior stays in command."""
TOOLS = {}
FOUNDER_TOOLS = ['autumbra.enforce.list', 'autumbra.enforce.create', 'autumbra.enforce.destroy', 'autumbra.fixes', 'autumbra.fix.approve', 'autumbra.blocks', 'autumbra.blocks.share']
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset()
REQUEST_BODY_TOOLS = frozenset(['autumbra.blocks', 'autumbra.blocks.share', 'autumbra.enforce.create', 'autumbra.enforce.destroy', 'autumbra.enforce.list', 'autumbra.fix.approve', 'autumbra.fixes'])
CONFIRMED = frozenset(['autumbra.blocks.share', 'autumbra.enforce.create', 'autumbra.enforce.destroy', 'autumbra.fix.approve'])

def adapters(command):
    return {
        'autumbra.blocks': command.Adapter('GET', '/v1/ghost/autumbra/blocks', command.BlockTenantArgs, 'autumbra.blocks'),
        'autumbra.blocks.share': command.Adapter('POST', '/v1/ghost/autumbra/blocks/share', command.BlockShareArgs, 'autumbra.blocks.share'),
        'autumbra.fixes': command.Adapter('GET', '/v1/ghost/autumbra/fixes', command.EmptyArgs, 'autumbra.fixes'),
        'autumbra.fix.approve': command.Adapter('POST', '/v1/ghost/autumbra/fix/approve', command.FixMatchArgs, 'autumbra.fix.approve'),
        'autumbra.enforce.list': command.Adapter('GET', '/v1/ghost/autumbra/enforce/list', command.EmptyArgs, 'autumbra.enforce.list'),
        'autumbra.enforce.create': command.Adapter('POST', '/v1/ghost/autumbra/enforce/create', command.EnforceTenantArgs, 'autumbra.enforce.create'),
        'autumbra.enforce.destroy': command.Adapter('POST', '/v1/ghost/autumbra/enforce/destroy', command.EnforceTenantArgs, 'autumbra.enforce.destroy'),
    }
