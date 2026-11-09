"""Product route registration and lifecycle hooks, in the original fixed order."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import import_module
from typing import Any

# phase, module, attribute/build function, argument bindings (None = router), include
ROUTES = (
    ('early', 'admin', 'router', None, True),
    ('early', 'controls', 'router', None, True),
    ('early', 'workers_status', 'router', None, True),
    ('early', 'workers_status', 'founder_router', None, True),
    ('early', 'dashboards', 'router', None, True),
    ('early', 'gold', 'router', None, True),
    ('early', 'training', 'router', None, True),
    ('early', 'storage', 'router', None, True),
    ('early', 'bench', 'router', None, True),
    ('early', 'proof', 'router', None, True),
    ('early', 'routellm_shadow', 'router', None, True),
    ('early', 'proof', 'client_router', None, True),
    ('early', 'case_studies', 'router', None, True),
    ('early', 'trigger_signin_config', 'router', None, True),
    ('early', 'event_bus', 'router', None, True),
    ('early', 'agent_skills', 'build_router', ('tank',), True),
    ('early', 'zenas_savings', 'install', ('tank',), False),
    ('early', 'zenas_law', 'install', ('tank',), False),
    ('early', 'base_product', 'build_router', ('tank',), True),
    ('early', 'base_lists', 'build_router', (), True),
    ('early', 'base_compare', 'build_router', (), True),
    ('early', 'base_campaign_commands', 'build_router', ('tank',), True),
    ('early', 'tiger_watch_commands', 'build_router', (), True),
    ('early', 'tiger_alerts', 'build_router', (), True),
    ('early', 'tisan_requests', 'build_router', (), True),
    ('early', 'base_audit_commands', 'build_router', (), True),
    ('early', 'rig_brain_routes', 'build_router', ('tank',), True),
    ('early', 'moa', 'router', None, True),
    ('early', 'mobile', 'router', None, True),
    ('early', 'mobile_push', 'router', None, True),
    ('late', 'ghost_routes', 'router', None, True),
    ('late', 'research', 'register', ('app', 'globals'), False),
    ('late', 'tiger_savings', 'build_router', ('tank',), True),
    ('late', 'command', 'router', None, True),
    ('late', 'command_tools.workspace', 'build_router', (), True),
    ('late', 'command_tools.opli_memory', 'build_router', (), True),
    ('late', 'personal_tokens', 'register', ('app',), False),
    ('late', 'mcp_server', 'register', ('app',), False),
    ('late', 'trigger_routes', 'router', None, True),
    ('late', 'trigger_marketing_api', 'router', None, True),
    ('late', 'trigger_crm', 'router', None, True),
    ('late', 'trigger_bot_api', 'router', None, True),
    ('late', 'trigger_console', 'router', None, True),
    ('late', 'trigger_widget', 'router', None, True),
    ('late', 'access', 'router', None, True),
    ('late', 'rig_forge_routes', 'build_router', ('tank',), True),
    ('late', 'rig_three_d', 'build_router', ('tank',), True),
    ('late', 'rig_savings', 'build_router', ('tank',), True),
    ('late', 'rig_user_routes', 'build_router', ('tank',), True),
    ('late', 'rig_tools', 'build_router', ('tank',), True),
    ('late', 'rig_forge_provider', 'build_router', ('tank',), True),
    ('late', 'market_lab_routes', 'build_router', ('tank',), True),
    ('late', 'radix_passive_routes', 'build_router', ('tank',), True),
    ('late', 'radix_memory_routes', 'build_router', (), True),
    ('late', 'semantic_cache', 'build_router', ('tank',), True),
    ('late', 'skala_intake', 'install', ('app', 'tank'), False),
    ('late', 'vault_routes', 'build_router', ('tank',), True),
    ('late', 'mcp_client_oauth', 'router', None, True),
    ('late', 'memory_routes', 'router', None, True),
    ('late', 'skala_revalidation', 'install', ('app', 'tank'), False),
    ('late', 'skala_memory_wire', 'attach_skala_memory_routes', ('app',), False),
    ('early', 'tenant_regions', 'build_router', ('tank',), True),
    ('early', 'workspace_board', 'build_router', ('tank',), True),
    ('early', 'morning_report', 'build_router', ('tank',), True),
    ('early', 'engine_admin', 'build_router', ('tank',), True),
)

def register_routes(tank: Any, phase: str) -> None:
    bindings = {"tank": tank, "app": tank.app, "globals": vars(tank)}
    for point, module, attribute, arguments, include in ROUTES:
        if point != phase:
            continue
        target = getattr(import_module(module), attribute)
        router = target if arguments is None else target(*(bindings[name] for name in arguments))
        if include:
            tank.app.include_router(router)


@dataclass(frozen=True)
class ProductHook:
    slot: str
    module: str
    function: str


PRODUCT_HOOKS = (
    ProductHook("_bot_task", "trigger_bot", "refresh_loop"),
    ProductHook("_marketing_task", "trigger_publisher", "publish_loop"),
    ProductHook("_morning_report_task", "morning_report", "loop"),
)


async def startup_products(tank: Any) -> None:
    for hook in PRODUCT_HOOKS:
        function = getattr(import_module(hook.module), hook.function)
        setattr(tank, hook.slot, asyncio.create_task(function(tank)))


async def shutdown_products(tank: Any) -> None:
    for hook in PRODUCT_HOOKS:
        task = getattr(tank, hook.slot, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
