"""rig command declarations; argument classes remain exported by command."""
TOOLS = {'rig': ['rig.search', 'rig.ask', 'rig.quote.confirm_payment', 'rig.store.onboard', 'rig.rooms.list', 'rig.store.destroy']}
FOUNDER_TOOLS = []
TENANT_ADMIN_TOOLS = ['rig.quote.confirm_payment']
ADMIN_ONLY_TOOLS = frozenset(['rig.quote.confirm_payment', 'rig.store.onboard', 'rig.rooms.list', 'rig.store.destroy'])
REQUEST_BODY_TOOLS = frozenset([])
CONFIRMED = frozenset(['rig.quote.confirm_payment', 'rig.store.onboard', 'rig.store.destroy'])

def adapters(command):
    return {
        'rig.search': command.Adapter('GET', '/v1/rig/search', command.QueryArgs, 'Search the product catalogue'),
        'rig.ask': command.Adapter('POST', '/v1/rig/ask', command.RigAskArgs, 'Ask about a product', True, 'paid_run'),
        'rig.quote.confirm_payment': command.Adapter('POST', '/v1/access/rig/admin/confirm-store-payment', command.RigConfirmPaymentArgs, 'rig.quote.confirm_payment'),
        'rig.store.onboard': command.Adapter('POST', '/v1/access/rig/rooms/onboard', command.EmptyArgs, 'rig.store.onboard'),
        'rig.rooms.list': command.Adapter('POST', '/v1/access/rig/rooms/list', command.EmptyArgs, 'rig.rooms.list'),
        'rig.store.destroy': command.Adapter('POST', '/v1/access/rig/rooms/destroy', command.EmptyArgs, 'rig.store.destroy'),
    }
