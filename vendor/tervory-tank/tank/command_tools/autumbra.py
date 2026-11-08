"""Autumbra founder command declarations; runtime behavior stays in command."""
import os
import uuid
from uuid import UUID
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class BrainArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: Literal["ghost.brain.explain_incident", "ghost.brain.summarise_baseline",
                       "ghost.brain.classify_alert", "ghost.brain.plan_fix"]
    text: str = Field(min_length=1, max_length=4000)
    run_id: uuid.UUID


def brain_refusal(task_type):
    if (task_type or "").startswith("ghost.brain.") and os.environ.get("GHOST_BRAIN_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return "GHOST_BRAIN_ENABLED is off."
    return None


def require_brain_enabled(task_type):
    reason = brain_refusal(task_type)
    if reason:
        raise HTTPException(503, reason)


TOOLS = {}
FOUNDER_TOOLS = ['autumbra.enforce.list', 'autumbra.enforce.create', 'autumbra.enforce.destroy', 'autumbra.fixes', 'autumbra.fix.approve', 'autumbra.blocks', 'autumbra.blocks.share', 'autumbra.brain', 'autumbra.memory']
TENANT_ADMIN_TOOLS = ['autumbra.brain', 'autumbra.memory']
ADMIN_ONLY_TOOLS = frozenset()
REQUEST_BODY_TOOLS = frozenset(['autumbra.brain', 'autumbra.blocks', 'autumbra.blocks.share', 'autumbra.enforce.create', 'autumbra.enforce.destroy', 'autumbra.enforce.list', 'autumbra.fix.approve', 'autumbra.fixes'])
CONFIRMED = frozenset(['autumbra.brain', 'autumbra.blocks.share', 'autumbra.enforce.create', 'autumbra.enforce.destroy', 'autumbra.fix.approve'])

def _base_adapters(command):
    return {
        'autumbra.blocks': command.Adapter('GET', '/v1/ghost/autumbra/blocks', command.BlockTenantArgs, 'autumbra.blocks'),
        'autumbra.blocks.share': command.Adapter('POST', '/v1/ghost/autumbra/blocks/share', command.BlockShareArgs, 'autumbra.blocks.share'),
        'autumbra.fixes': command.Adapter('GET', '/v1/ghost/autumbra/fixes', command.EmptyArgs, 'autumbra.fixes'),
        'autumbra.fix.approve': command.Adapter('POST', '/v1/ghost/autumbra/fix/approve', command.FixMatchArgs, 'autumbra.fix.approve'),
        'autumbra.enforce.list': command.Adapter('GET', '/v1/ghost/autumbra/enforce/list', command.EmptyArgs, 'autumbra.enforce.list'),
        'autumbra.enforce.create': command.Adapter('POST', '/v1/ghost/autumbra/enforce/create', command.EnforceTenantArgs, 'autumbra.enforce.create'),
        'autumbra.enforce.destroy': command.Adapter('POST', '/v1/ghost/autumbra/enforce/destroy', command.EnforceTenantArgs, 'autumbra.enforce.destroy'),
        'autumbra.brain': command.Adapter('POST', '/v1/ghost/autumbra/personal/brain/tasks', BrainArgs, 'autumbra.brain'),
        'autumbra.memory': command.Adapter('GET', '/v1/ghost/autumbra/personal/memory', command.EmptyArgs, 'autumbra.memory'),
    }




class NoArgs(BaseModel):
    model_config = ConfigDict(extra='forbid')


class OwnerAction(NoArgs):
    site: str = Field(min_length=1, max_length=253, pattern=r'^[a-zA-Z0-9.-]+$')
    prior_receipt_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,200}$')
    rule_id: str = Field(default='', max_length=200, pattern=r'^[A-Za-z0-9_-]*$')


class Modes(NoArgs):
    shield_mode: Literal['I approve each one', 'Apply automatically when checks pass', 'Never']
    fix_mode: Literal['I approve each one', 'Never'] = 'I approve each one'


class HandArgs(NoArgs):
    hand: Literal['cloudflare', 'google_workspace', 'shopify', 'host_firewall']
    connected: bool = Field(strict=True)


class LookArgs(NoArgs):
    target: Literal['workspace', 'za', 'site', 'tank', 'base', 'tiger', 'market_lab', 'rig', 'tisan', 'radix', 'zenas', 'ghost']
    signal: Literal['health', 'slow', 'dns', 'certificate', 'headers', 'errors', 'deploy', 'ci', 'radix', 'human_check'] = 'health'


class ResolveArgs(NoArgs):
    incident: UUID
    cause: str = Field(pattern=r'^(crashed_room|failed_deploy|application_bug|dns_configuration|certificate_expired|missing_headers|dependency_failure)$')
    fix: str = Field(pattern=r'^(room_restarted|deploy_rerun|reviewed_pr_deployed|dns_corrected|certificate_renewed|headers_corrected)$')


class ShieldArgs(NoArgs):
    shield: UUID


CLIENT_ACTIONS = {'settings': NoArgs, 'settings.set': Modes, 'receipts': NoArgs,
                  'shield.approve': OwnerAction, 'shield.undo': OwnerAction, 'hands': NoArgs, 'hands.set': HandArgs}
WATCH_ACTIONS = {'look': LookArgs, 'incidents': NoArgs, 'resolve': ResolveArgs,
                 'shields': NoArgs, 'shields.raise': ShieldArgs, 'shields.undo': ShieldArgs}
TENANT_ADMIN_TOOLS += ['autumbra.settings', 'autumbra.settings.set', 'autumbra.receipts', 'autumbra.shield.approve', 'autumbra.shield.undo', 'autumbra.hands', 'autumbra.hands.set']
FOUNDER_TOOLS += ['autumbra.settings', 'autumbra.settings.set', 'autumbra.receipts', 'autumbra.shield.approve', 'autumbra.shield.undo', 'autumbra.hands', 'autumbra.hands.set', 'autumbra.look', 'autumbra.incidents', 'autumbra.resolve', 'autumbra.shields', 'autumbra.shields.raise', 'autumbra.shields.undo']
REQUEST_BODY_TOOLS |= frozenset('autumbra.' + name for name in CLIENT_ACTIONS | WATCH_ACTIONS)
CONFIRMED |= frozenset('autumbra.' + name for name in
                      ('hands.set', 'settings.set', 'shield.approve', 'shield.undo', 'resolve', 'shields.raise', 'shields.undo'))


def adapters(command):
    return {**_base_adapters(command), **{
        'autumbra.' + name: command.Adapter('GET' if args is NoArgs else 'POST', '/v1/ghost/autumbra/tools/' + name, args, 'autumbra.' + name)
        for name, args in (CLIENT_ACTIONS | WATCH_ACTIONS).items()}}


async def code_plan(actor, messages, task_id):
    """Explicit tool names and JSON use the same validated card path, at zero model cost."""
    import json
    import command
    from trigger_routes import code_receipt
    from types import SimpleNamespace
    last = messages[-1] if messages else {}
    if last.get('role') != 'user' or not isinstance(last.get('content'), str):
        return None
    name, _, raw = last['content'].strip().partition(' ')
    if name.removeprefix('autumbra.') not in CLIENT_ACTIONS | WATCH_ACTIONS or not name.startswith('autumbra.'):
        return None
    try:
        action = command.validate_action(command.PlannedAction(tool=name, arguments=json.loads(raw or '{}')), actor)
    except (ValueError, TypeError):
        from fastapi import HTTPException
        raise HTTPException(422, 'Invalid Autumbra request.') from None
    return SimpleNamespace(content=json.dumps({'text': '', 'actions': [action.model_dump(mode='json')]}),
        ledger_id=await code_receipt(actor.tenant_id, 'command.turn', task_id=task_id, agent='ghost'))
