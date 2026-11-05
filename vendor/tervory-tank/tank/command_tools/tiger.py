"""tiger command declarations; argument classes remain exported by command."""
TOOLS = {'tiger': ['tiger.watch', 'tiger.read', 'tiger.match', 'tiger.match.confirm', 'tiger.match.reject', 'tiger.match.undo', 'tiger.position', 'tiger.matches', 'tiger.alerts', 'tiger.watches', 'tiger.watch.pause', 'tiger.watch.resume', 'tiger.watch.remove', 'tiger.watch.bulk']}
FOUNDER_TOOLS = []
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset([])
REQUEST_BODY_TOOLS = frozenset(['tiger.watch.bulk', 'tiger.watch.pause', 'tiger.watch.remove', 'tiger.watch.resume'])
CONFIRMED = frozenset(['tiger.match', 'tiger.match.confirm', 'tiger.match.reject', 'tiger.match.undo', 'tiger.watch.bulk', 'tiger.watch.pause', 'tiger.watch.remove', 'tiger.watch.resume'])

def adapters(command):
    return {
        'tiger.match': command.Adapter('POST', '/v1/access/tiger/match', command.EmptyArgs, 'tiger.match'),
        'tiger.match.confirm': command.Adapter('POST', '/v1/access/tiger/match/confirm', command.TigerMatchArgs, 'tiger.match.confirm'),
        'tiger.match.reject': command.Adapter('POST', '/v1/access/tiger/match/reject', command.TigerMatchArgs, 'tiger.match.reject'),
        'tiger.match.undo': command.Adapter('POST', '/v1/access/tiger/match/undo', command.TigerMatchArgs, 'tiger.match.undo'),
        'tiger.position': command.Adapter('GET', '/v1/access/tiger/position', command.EmptyArgs, 'tiger.position'),
        'tiger.matches': command.Adapter('GET', '/v1/access/tiger/matches', command.EmptyArgs, 'tiger.matches'),
        'tiger.watch': command.Adapter('POST', '/v1/tiger/watchlist', command.WatchArgs, 'Start a price watch', True, 'paid_run'),
        'tiger.read': command.Adapter('GET', '/v1/tiger/observations', command.UrlArgs, 'Read price observations'),
        'tiger.watches': command.Adapter('GET', '/v1/command/tiger/watches', command.EmptyArgs, 'tiger.watches'),
        'tiger.watch.pause': command.Adapter('POST', '/v1/command/tiger/watches/pause', command.UrlArgs, 'tiger.watch.pause'),
        'tiger.watch.resume': command.Adapter('POST', '/v1/command/tiger/watches/resume', command.UrlArgs, 'tiger.watch.resume'),
        'tiger.watch.remove': command.Adapter('POST', '/v1/command/tiger/watches/remove', command.UrlArgs, 'tiger.watch.remove'),
        'tiger.watch.bulk': command.Adapter('POST', '/v1/command/tiger/watches/bulk', __import__('tiger_watch_commands').BulkArgs, 'tiger.watch.bulk', True, 'paid_run'),
        'tiger.alerts': command.Adapter('GET', '/v1/command/tiger/alerts', __import__('tiger_alerts').AlertsArgs, 'tiger.alerts'),
    }
