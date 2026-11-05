"""base command declarations; argument classes remain exported by command."""
TOOLS = {'base': ['base.batch', 'base.search', 'base.find', 'base.list.save', 'base.lists', 'base.list.show', 'base.list.export', 'base.list.products', 'base.compare.apollo', 'base.list.to_crm']}
FOUNDER_TOOLS = ['base.campaign.start', 'base.campaign.show', 'base.campaign.csv', 'base.audit.start', 'base.audit.csv', 'base.audit.review']
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset([])
REQUEST_BODY_TOOLS = frozenset(['base.audit.review', 'base.audit.start', 'base.campaign.start', 'base.list.save', 'base.list.to_crm'])
CONFIRMED = frozenset(['base.audit.review', 'base.audit.start', 'base.campaign.start', 'base.list.save', 'base.list.to_crm'])

def adapters(command):
    return {
        'base.campaign.start': command.Adapter('POST', '/v1/command/base/campaigns/start', __import__('base_campaign_commands').StartArgs, 'base.campaign.start', True, 'paid_run'),
        'base.campaign.show': command.Adapter('GET', '/v1/command/base/campaigns/show', __import__('base_campaign_commands').CampaignArgs, 'base.campaign.show'),
        'base.campaign.csv': command.Adapter('GET', '/v1/command/base/campaigns/export', __import__('base_campaign_commands').CampaignArgs, 'base.campaign.csv'),
        'base.audit.start': command.Adapter('POST', '/v1/command/base/audits/start', command.AuditStartArgs, 'base.audit.start'),
        'base.audit.csv': command.Adapter('GET', '/v1/command/base/audits/export', command.AuditCsvArgs, 'base.audit.csv'),
        'base.audit.review': command.Adapter('POST', '/v1/command/base/audits/review', command.AuditReviewArgs, 'base.audit.review'),
        'base.batch': command.Adapter('POST', '/v1/base/run', command.BaseBatchArgs, 'Start a Base batch', True, 'paid_run'),
        'base.search': command.Adapter('GET', '/v1/base/records', command.BaseSearchArgs, 'Read Base records'),
        'base.find': command.Adapter('GET', '/v1/command/base/lists/find', command.FindArgs, 'base.find'),
        'base.list.save': command.Adapter('POST', '/v1/command/base/lists/save', command.SaveArgs, 'base.list.save'),
        'base.lists': command.Adapter('GET', '/v1/command/base/lists', command.Body, 'base.lists'),
        'base.list.show': command.Adapter('GET', '/v1/command/base/lists/show', command.ListArgs, 'base.list.show'),
        'base.list.export': command.Adapter('GET', '/v1/command/base/lists/export', command.ListArgs, 'base.list.export'),
        'base.compare.apollo': command.Adapter('GET', '/v1/command/base/compare/apollo', __import__('base_compare').ApolloArgs, 'base.compare.apollo'),
        'base.list.products': command.Adapter('GET', '/v1/command/base/lists/products', command.ListArgs, 'base.list.products'),
        'base.list.to_crm': command.Adapter('POST', '/v1/command/base/lists/to-crm', command.SendArgs, 'base.list.to_crm'),
    }
