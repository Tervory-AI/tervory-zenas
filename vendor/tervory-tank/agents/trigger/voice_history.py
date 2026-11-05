"""Tenant-scoped immutable versions and optimistic confirmation; no models."""
import json
from chassis.tenant_db import tenant_transaction
from uuid import UUID

from .voice import Voice, VoiceStore
from .voice_parser import parse_voice, preview_hash


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


class VoiceHistory(VoiceStore):
    async def show(self, tenant: UUID, offset: int = 0, target: int | None = None) -> dict:
        async with tenant_transaction(self.pool, isolation='repeatable_read', readonly=True) as conn:
            current = await conn.fetchval('SELECT voice FROM agent_trigger.voices WHERE tenant_id=$1', tenant)
            history = await conn.fetch('SELECT revision,restored_from,created_at FROM agent_trigger.voice_versions WHERE tenant_id=$1 ORDER BY revision DESC LIMIT 20 OFFSET $2', tenant, offset)
            selected = await conn.fetchval('SELECT voice FROM agent_trigger.voice_versions WHERE tenant_id=$1 AND revision=$2', tenant, target) if target else None
            if target and selected is None:
                raise ValueError('Voice version unavailable')
            total = await conn.fetchval('SELECT count(*) FROM agent_trigger.voice_versions WHERE tenant_id=$1', tenant)
        return {'voice': obj(current) if current else None, 'selected_voice': obj(selected) if selected else None,
                'history_total': total, 'next_offset': offset + 20 if total > offset + 20 else None,
                'history': [{**dict(row), 'created_at': row['created_at'].isoformat()} for row in history]}

    async def preview(self, tenant: UUID, text: str = '', target: int | None = None, rules: list[str] | None = None) -> dict:
        current = await self.get(tenant)
        revision = current.revision if current else 0
        if rules is not None:
            text = json.dumps(rules, ensure_ascii=False)
            voice = learned_voice(current, rules, revision + 1)
        elif target is None:
            voice = parse_voice(text, revision + 1)
        else:
            raw = await self.pool.fetchval('SELECT voice FROM agent_trigger.voice_versions WHERE tenant_id=$1 AND revision=$2', tenant, target)
            if raw is None:
                raise ValueError('Voice version unavailable')
            voice = Voice.model_validate(obj(raw)).model_copy(update={'revision': revision + 1})
        return {'voice': voice.model_dump(exclude_unset=True), 'expected_revision': revision,
                'preview_hash': preview_hash(voice, text, revision)}

    async def commit(self, tenant: UUID, *, text: str, expected_revision: int, digest: str,
                     operation_id: UUID, target: int | None = None, rules: list[str] | None = None) -> dict:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('trigger-voice:' || $1, 0))", str(tenant))
            previous = await conn.fetchval('SELECT voice FROM agent_trigger.voice_versions WHERE tenant_id=$1 AND operation_id=$2', tenant, operation_id)
            if previous:
                return obj(previous)
            current = await conn.fetchval('SELECT revision FROM agent_trigger.voices WHERE tenant_id=$1', tenant)
            if (current or 0) != expected_revision:
                raise ValueError('Voice changed after preview; preview again')
            if rules is not None:
                raw = await conn.fetchval('SELECT voice FROM agent_trigger.voices WHERE tenant_id=$1', tenant)
                text = json.dumps(rules, ensure_ascii=False)
                voice = learned_voice(Voice.model_validate(obj(raw)) if raw else None, rules, expected_revision + 1)
            elif target is None:
                voice = parse_voice(text, expected_revision + 1)
            else:
                raw = await conn.fetchval('SELECT voice FROM agent_trigger.voice_versions WHERE tenant_id=$1 AND revision=$2', tenant, target)
                if raw is None:
                    raise ValueError('Voice version unavailable')
                voice = Voice.model_validate(obj(raw)).model_copy(update={'revision': expected_revision + 1})
            if preview_hash(voice, text, expected_revision) != digest:
                raise ValueError('Voice preview does not match')
            await conn.execute('INSERT INTO agent_trigger.voice_versions(tenant_id,revision,voice,source_text,operation_id,restored_from) VALUES($1,$2,$3::jsonb,$4,$5,$6)', tenant, voice.revision, voice.model_dump_json(exclude_unset=True), text, operation_id, target)
            await conn.execute('INSERT INTO agent_trigger.voices(tenant_id,revision,voice) VALUES($1,$2,$3::jsonb) ON CONFLICT(tenant_id) DO UPDATE SET revision=EXCLUDED.revision,voice=EXCLUDED.voice,updated_at=now()', tenant, voice.revision, voice.model_dump_json(exclude_unset=True))
        return voice.model_dump(exclude_unset=True)


def learned_voice(current: Voice | None, rules: list[str], revision: int) -> Voice:
    """Append exact chosen feedback to Rules; never reinterpret embedded headings."""
    if not rules or any(not r.strip() for r in rules) or len(json.dumps(rules, ensure_ascii=False).encode()) > 12000:
        raise ValueError('Voice notes must contain 1–12000 UTF-8 bytes')
    value = current.model_dump(exclude_unset=True) if current else {'brand': '', 'status': 'live'}
    value.update(revision=revision, guidelines=[*value.get('guidelines', []), *({'rule': r} for r in rules)])
    return Voice.model_validate(value)
