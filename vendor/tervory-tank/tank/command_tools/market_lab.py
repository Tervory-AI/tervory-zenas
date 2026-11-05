"""market_lab command declarations; argument classes remain exported by command."""
TOOLS = {'market_lab': ['market_lab.research', 'market_lab.warnings', 'market_lab.predictions', 'market_lab.calibration', 'market_lab.calibration.curve', 'market_lab.feeds', 'market_lab.patterns', 'market_lab.gate', 'market_lab.briefs', 'market_lab.alerts.email', 'market_lab.alerts.webhook', 'market_lab.alerts.off']}
FOUNDER_TOOLS = []
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset(['market_lab.alerts.email', 'market_lab.alerts.webhook', 'market_lab.alerts.off'])
REQUEST_BODY_TOOLS = frozenset([])
CONFIRMED = frozenset([])

def adapters(command):
    return {
        'market_lab.warnings': command.Adapter('GET', '/v1/market-lab/reads/warnings', command.MarketLabReadArgs, 'market_lab.warnings'),
        'market_lab.predictions': command.Adapter('GET', '/v1/market-lab/reads/predictions', command.MarketLabReadArgs, 'market_lab.predictions'),
        'market_lab.calibration': command.Adapter('GET', '/v1/market-lab/reads/calibration', command.MarketLabReadArgs, 'market_lab.calibration'),
        'market_lab.calibration.curve': command.Adapter('GET', '/v1/market-lab/reads/calibration.curve', command.MarketLabReadArgs, 'market_lab.calibration.curve'),
        'market_lab.feeds': command.Adapter('GET', '/v1/market-lab/reads/feeds', command.MarketLabReadArgs, 'market_lab.feeds'),
        'market_lab.patterns': command.Adapter('GET', '/v1/market-lab/reads/patterns', command.MarketLabReadArgs, 'market_lab.patterns'),
        'market_lab.gate': command.Adapter('GET', '/v1/market-lab/reads/gate', command.MarketLabReadArgs, 'market_lab.gate'),
        'market_lab.briefs': command.Adapter('GET', '/v1/market-lab/reads/briefs', command.MarketLabBriefArgs, 'market_lab.briefs'),
        'market_lab.alerts.email': command.Adapter('POST', '/v1/market-lab/alerts/email', command.EmptyArgs, 'market_lab.alerts.email'),
        'market_lab.alerts.webhook': command.Adapter('POST', '/v1/market-lab/alerts/webhook', command.MarketLabWebhookArgs, 'market_lab.alerts.webhook'),
        'market_lab.alerts.off': command.Adapter('POST', '/v1/market-lab/alerts/off', command.EmptyArgs, 'market_lab.alerts.off'),
        'market_lab.research': command.Adapter('POST', '/v1/research', command.QuestionArgs, 'Research a market question', True, 'paid_run'),
    }
