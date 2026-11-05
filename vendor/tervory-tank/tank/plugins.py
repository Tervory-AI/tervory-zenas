"""Bundled first-party manifests and tenant-only install records; no credentials."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import yaml
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mcp_connections import ServerArgs, VAULTS, unpack
from plugin_args import PluginArgs

ROOT = Path(__file__).with_name('plugin_packages')


class Server(ServerArgs):
    tools: dict[str, Literal['read', 'write']] = Field(min_length=1, max_length=1000)

    @field_validator('tools')
    @classmethod
    def valid_tools(cls, tools):
        from mcp_client import TOOL_NAME
        if any(not TOOL_NAME.fullmatch(name) for name in tools):
            raise ValueError('Invalid tool name')
        return tools


class Manifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,63}$')
    version: str = Field(pattern=r'^\d+\.\d+\.\d+$')
    name: str = Field(min_length=1, max_length=100)
    products: list[str] = Field(min_length=1, max_length=20)
    servers: list[Server] = Field(min_length=1, max_length=20)
    skills: list[str] = Field(max_length=100)
    code_mode: bool

    @field_validator('products')
    @classmethod
    def products_known(cls, values):
        if len(set(values)) != len(values) or not set(values) <= set(VAULTS.values()):
            raise ValueError('Invalid products')
        return values

    @field_validator('skills')
    @classmethod
    def skill_refs(cls, values):
        import re
        if len(set(values)) != len(values) or any(not re.fullmatch(r'[a-z][a-z0-9_.-]*@\d+\.\d+\.\d+', value) for value in values):
            raise ValueError('Invalid skill reference')
        return values


class ManifestLoader(yaml.SafeLoader):
    """Safe YAML with no ambiguous duplicate keys, including merged mappings."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in seen:
                    raise ValueError('Duplicate YAML key')
                seen.add(key)
            except TypeError as error:
                raise ValueError('Invalid YAML key') from error
        return super().construct_mapping(node, deep=deep)


def load_manifests(root: Path = ROOT) -> dict[tuple[str, str], Manifest]:
    manifests = {}
    for path in sorted(root.glob('*/*/plugin.yaml')):
        manifest = Manifest.model_validate(yaml.load(path.read_text(encoding='utf-8'), Loader=ManifestLoader))
        key = (manifest.id, manifest.version)
        if key != (path.parent.parent.name, path.parent.name) or key in manifests:
            raise ValueError('Plugin path does not match manifest')
        if len({s.url for s in manifest.servers}) != len(manifest.servers):
            raise ValueError('Duplicate server')
        manifests[key] = manifest
    if not manifests:
        raise ValueError('Bundled plugin manifests are missing')
    return manifests


MANIFESTS = load_manifests()


def visible(actor, manifest):
    return actor.founder or bool(set(manifest.products) & actor.squads)


async def readiness(conn, actor, manifest):
    servers = []
    for server in manifest.servers:
        row = await conn.fetchrow('SELECT allowed_tools FROM tank.mcp_servers WHERE url=$1', server.url)
        allowed = unpack(row['allowed_tools']) if row else {}
        approved = bool(row) and all(allowed.get(name) == access for name, access in server.tools.items())
        connections = await conn.fetch("SELECT vault,mcp_state FROM tank.vault_items WHERE tenant_id=$1 AND kind='connections' AND metadata->>'server_url'=$2", actor.tenant_id, server.url)
        states = [dict(row) for row in connections if VAULTS.get(row['vault']) in manifest.products and (actor.founder or VAULTS.get(row['vault']) in actor.squads)]
        servers.append({'url': server.url, 'approved': approved, 'connections': states})
    skills = []
    for ref in manifest.skills:
        ident, version = ref.rsplit('@', 1)
        state = await conn.fetchval('SELECT state FROM tank.skills WHERE id=$1 AND version=$2', ident, version)
        skills.append({'ref': ref, 'approved': state == 'approved'})
    return servers, skills


async def listing(conn, actor):
    installed = {(row['plugin_id'], row['version']) for row in await conn.fetch('SELECT plugin_id,version FROM tank.plugin_installs WHERE tenant_id=$1', actor.tenant_id)}
    result = []
    for key, manifest in MANIFESTS.items():
        if not visible(actor, manifest):
            continue
        servers, skills = await readiness(conn, actor, manifest)
        ready = all(s['approved'] and any(c['mcp_state'] == 'connected' for c in s['connections']) for s in servers) and all(s['approved'] for s in skills)
        result.append({'name': manifest.id, 'version': manifest.version, 'installed': key in installed,
                       'status': 'installed' if key in installed else 'ready' if ready else 'not_ready',
                       'servers': servers, 'skills': skills})
    return {'count': len(result), 'items': result}


async def prepare(action, actor, conn):
    if action.tool == 'plugins.list':
        return action
    if not (actor.founder or actor.tenant_admin):
        raise HTTPException(403, 'This tool is not available to this user')
    body = PluginArgs.model_validate(action.arguments)
    manifest = MANIFESTS.get((body.name, body.version))
    if manifest is None or not visible(actor, manifest):
        raise HTTPException(422, 'The action arguments are invalid')
    if action.tool == 'plugins.install':
        servers, skills = await readiness(conn, actor, manifest)
        if not all(s['approved'] for s in servers) or not all(s['approved'] for s in skills):
            raise HTTPException(422, 'The action arguments are invalid')
    return action


async def dispatch(action, actor, thread_id=None, card_id=None):
    from command import db, require_tool
    from trigger_routes import code_receipt
    require_tool(actor, action.tool)
    if action.tool != 'plugins.list' and not (actor.founder or actor.tenant_admin):
        raise HTTPException(403, 'This tool is not available to this user')
    async with db().acquire() as conn:
        if action.tool == 'plugins.list':
            result = await listing(conn, actor)
        else:
            # Writes are reachable only through a durably claimed Confirm card.
            raw = await conn.fetchval('SELECT state FROM tank.command_threads WHERE thread_id=$1 AND tenant_id=$2 AND user_id=$3', thread_id, actor.tenant_id, actor.user_id)
            card = unpack(raw or {}).get('cards', {}).get(str(card_id), {})
            if card.get('status') != 'executing' or card.get('payload') != action.model_dump(mode='json'):
                raise HTTPException(409, 'Confirmed action required')
            body = PluginArgs.model_validate(action.arguments)
            manifest = MANIFESTS.get((body.name, body.version))
            if manifest is None or not visible(actor, manifest):
                raise HTTPException(422, 'The action arguments are invalid')
            if action.tool == 'plugins.install':
                servers, skills = await readiness(conn, actor, manifest)
                if not all(s['approved'] for s in servers) or not all(s['approved'] for s in skills):
                    return 422, {'ledger_id': str(await code_receipt(actor.tenant_id, action.tool))}
                await conn.execute('INSERT INTO tank.plugin_installs(tenant_id,plugin_id,version,installed_by) VALUES($1,$2,$3,$4) ON CONFLICT(tenant_id,plugin_id,version) DO NOTHING', actor.tenant_id, body.name, body.version, actor.user_id)
                result = {'name': body.name, 'status': 'installed'}
            elif action.tool == 'plugins.remove':
                await conn.execute('DELETE FROM tank.plugin_installs WHERE tenant_id=$1 AND plugin_id=$2 AND version=$3', actor.tenant_id, body.name, body.version)
                result = {'name': body.name, 'status': 'removed'}
            else:
                raise HTTPException(403, 'This tool is not available to this user')
    return 200, {**result, 'ledger_id': str(await code_receipt(actor.tenant_id, action.tool))}


async def code_actor(actor, conn):
    """Narrow code mode only; the normal connected MCP catalogue is unchanged."""
    installs = {(row['plugin_id'], row['version']) for row in await conn.fetch('SELECT plugin_id,version FROM tank.plugin_installs WHERE tenant_id=$1', actor.tenant_id)}
    connections = {(row['vault'], row['name']): (row['vault'], unpack(row['metadata']).get('server_url')) for row in await conn.fetch("SELECT name,vault,metadata FROM tank.vault_items WHERE tenant_id=$1 AND kind='connections' AND mcp_state='connected'", actor.tenant_id)}
    allowed = set()
    for key in installs:
        manifest = MANIFESTS.get(key)
        if manifest is None or not manifest.code_mode or not visible(actor, manifest):
            continue
        for name, tool in actor.mcp_tools.items():
            vault, url = connections.get((tool['vault'], tool['connection_id']), (None, None))
            if VAULTS.get(vault) not in manifest.products or tool['access'] != 'read':
                continue
            if any(server.url == url and server.tools.get(tool['name']) == 'read' for server in manifest.servers):
                allowed.add(name)
    return replace(actor, mcp_tools={name: tool for name, tool in actor.mcp_tools.items() if name in allowed})
