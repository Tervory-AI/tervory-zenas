"""skala command declarations; argument classes remain exported by command."""
import json
import re
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field


class TeachersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$")


TOOLS = {}
FOUNDER_TOOLS = ['skala.list', 'skala.evals', 'skala.approve', 'skala.retire', 'ledger.receipts', 'ledger.savings', 'skala.teachers']
TENANT_ADMIN_TOOLS = []
ADMIN_ONLY_TOOLS = frozenset([])
REQUEST_BODY_TOOLS = frozenset([])
CONFIRMED = frozenset([])

def adapters(command):
    return {
        'skala.list': command.Adapter('GET', '/v1/skala', command.EmptyArgs, 'List skills'),
        'skala.evals': command.Adapter('POST', '/v1/skala/{skill_id}/{version}/evals', command.SkillVersionArgs, 'Run skill evaluations', True, 'paid_run'),
        'skala.approve': command.Adapter('POST', '/v1/skala/{skill_id}/{version}/approve', command.SkillVersionArgs, 'Approve a skill version', False, 'approve_skill'),
        'skala.retire': command.Adapter('POST', '/v1/skala/{skill_id}/{version}/retire', command.SkillVersionArgs, 'Retire a skill version', False, 'retire'),
        'ledger.receipts': command.Adapter('GET', '/v1/proof', command.ProofArgs, 'Read ledger receipts'),
        'ledger.savings': command.Adapter('GET', '/v1/proof', command.SavingsArgs, 'Read recorded costs and savings'),
        'skala.teachers': command.Adapter('GET', '/v1/training/teachers', TeachersArgs, 'skala.teachers'),
    }


async def code_plan(actor, messages, task_id):
    """An explicit teachers read needs no model to propose its confirmation card."""
    if not messages or messages[-1].get("role") != "user":
        return None
    match = re.fullmatch(r"skala\.teachers\s+([A-Za-z0-9_.-]+)", messages[-1].get("content", "").strip())
    if not match:
        return None
    from command import authorized_actor, allowed_tools
    from trigger_routes import code_receipt
    actor = await authorized_actor(actor)
    if "skala.teachers" not in allowed_tools(actor):
        return None
    args = TeachersArgs(skill_id=match[1])
    plan = {"text": "", "actions": [{"tool": "skala.teachers", "arguments": args.model_dump()}]}
    receipt = await code_receipt(actor.tenant_id, "command.turn", task_id=task_id, agent="tank")
    return SimpleNamespace(content=json.dumps(plan), ledger_id=receipt)
