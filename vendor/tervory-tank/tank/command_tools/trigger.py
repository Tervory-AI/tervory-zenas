"""trigger command declarations; argument classes remain exported by command."""
from trigger_bot_contract import TOOLS as BOT_TOOLS
from trigger_marketing_contract import TOOLS as MARKETING_TOOLS

MARKETING_PAID_TOOLS = frozenset({'post.create', 'gbp.reply.draft'})

TOOLS = {"trigger": ['trigger.revise', 'trigger.voice.learn', 'trigger.write.batch', 'trigger.scorecard', 'trigger.write', 'trigger.check', 'trigger.voice.show', 'trigger.voice.set', 'trigger.voice.rollback', 'trigger.feedback'] + ["trigger." + name for name in MARKETING_TOOLS] + ["trigger.crm.list", "trigger.crm.get", "trigger.crm.write"] + ["trigger.bot." + name for name in BOT_TOOLS]}
FOUNDER_TOOLS = []
TENANT_ADMIN_TOOLS = TOOLS["trigger"]
ADMIN_ONLY_TOOLS = frozenset([])
REQUEST_BODY_TOOLS = frozenset([])
CONFIRMED = frozenset([])

def adapters(command):
    result = {
        'trigger.crm.list': command.Adapter('GET', '/v1/trigger/crm/{resource}', command.TriggerCRMListArgs, 'trigger.crm.list'),
        'trigger.crm.get': command.Adapter('GET', '/v1/trigger/crm/{resource}/{record_id}', command.TriggerCRMGetArgs, 'trigger.crm.get'),
        'trigger.crm.write': command.Adapter('POST', '/v1/trigger/crm/write', command.TriggerCRMWriteArgs, 'trigger.crm.write'),
        'trigger.voice.show': command.Adapter('POST', '/v1/trigger/voice.show', command.TriggerVoiceShowArgs, 'trigger.voice.show'),
        'trigger.voice.set': command.Adapter('POST', '/v1/trigger/voice.set', command.TriggerVoiceSetArgs, 'trigger.voice.set'),
        'trigger.voice.rollback': command.Adapter('POST', '/v1/trigger/voice.rollback', command.TriggerVoiceRollbackArgs, 'trigger.voice.rollback'),
        'trigger.feedback': command.Adapter('POST', '/v1/trigger/feedback', command.TriggerFeedbackArgs, 'trigger.feedback'),
        'trigger.revise': command.Adapter('POST', '/v1/trigger/revise', command.TriggerReviseArgs, 'trigger.revise', True, 'paid_run'),
        'trigger.voice.learn': command.Adapter('POST', '/v1/trigger/voice.learn', command.TriggerVoiceLearnArgs, 'trigger.voice.learn'),
        'trigger.write.batch': command.Adapter('POST', '/v1/trigger/write.batch', command.TriggerBatchArgs, 'trigger.write.batch', True, 'paid_run'),
        'trigger.scorecard': command.Adapter('GET', '/v1/trigger/scorecard', command.TriggerScorecardArgs, 'trigger.scorecard'),
        'trigger.write': command.Adapter('POST', '/v1/trigger/write', command.TriggerWriteArgs, 'trigger.write', True, 'paid_run'),
        'trigger.check': command.Adapter('POST', '/v1/trigger/check', command.TriggerCheckArgs, 'trigger.check'),
    }
    result.update({"trigger.bot." + name: command.Adapter("POST", "/v1/trigger/bot/" + name, schema, "trigger.bot." + name)
                   for name, schema in BOT_TOOLS.items()})
    result.update({"trigger." + name: command.Adapter("POST", "/v1/trigger/marketing/" + name, schema, "trigger." + name,
                   name in MARKETING_PAID_TOOLS, 'paid_run' if name in MARKETING_PAID_TOOLS else 'other')
                   for name, schema in MARKETING_TOOLS.items()})
    return result
