"""tisan command declarations; argument classes remain exported by command."""
from tisan_requests import TOOLS as CLIENT

TOOLS = {'tisan': ['tisan.scaffold']}
FOUNDER_TOOLS = ['tisan.build', *CLIENT]
TENANT_ADMIN_TOOLS = list(CLIENT)
ADMIN_ONLY_TOOLS = frozenset([])
REQUEST_BODY_TOOLS = frozenset(CLIENT)
CONFIRMED = frozenset(['tisan.feedback', 'tisan.handover', 'tisan.request'])

def adapters(command):
    return {
        'tisan.request': command.Adapter('POST', '/v1/command/tisan/request', command.TisanRequestArgs, 'tisan.request'),
        'tisan.preview': command.Adapter('GET', '/v1/command/tisan/preview', command.TisanRequestIdArgs, 'tisan.preview'),
        'tisan.feedback': command.Adapter('POST', '/v1/command/tisan/feedback', command.TisanFeedbackArgs, 'tisan.feedback'),
        'tisan.handover': command.Adapter('POST', '/v1/command/tisan/handover', command.TisanRequestIdArgs, 'tisan.handover'),
        'tisan.scaffold': command.Adapter('POST', '/v1/skala/tisan.scaffold/run', command.ScaffoldArgs, 'Create a site scaffold', False, 'other', 'tisan.scaffold'),
        'tisan.build': command.Adapter('POST', '/v1/skala/tisan.build/run', command.TisanBuildArgs, 'tisan.build', False, 'other', 'tisan.build'),
    }
