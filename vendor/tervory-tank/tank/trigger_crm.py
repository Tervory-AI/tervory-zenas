"""Tenant-bound CRM corridor: fixed endpoints, explicit tenant binding, no raw proxy."""
from __future__ import annotations

import json
import os
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from command import Actor, authorized_actor, identity, require_tool
from fastapi import APIRouter, Depends, HTTPException, Request
from jsonschema import Draft4Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, Field, model_validator
from trigger_crm_schema import ROOTS, SCHEMAS

router = APIRouter(prefix="/v1/trigger/crm", tags=["trigger-crm"])
Resource = Literal["leads", "contacts", "estimates", "invoices"]
PATHS = {"leads": "/api/leads/", "contacts": "/api/contacts/", "estimates": "/api/invoices/estimates/", "invoices": "/api/invoices/"}
FIELDS = frozenset({"id", "title", "first_name", "last_name", "email", "phone", "status", "source",
                    "organization", "company_name", "city", "state", "country", "description", "created_at",
                    "estimate_number", "client_name", "account_name", "contact_name", "issue_date", "expiry_date",
                    "total_amount", "currency", "is_expired", "introduction", "notes", "terms",
                    "invoice_number", "invoice_title", "due_date", "amount_due", "amount_paid", "is_overdue",
                    "reminder_enabled", "reminder_days_before", "reminder_days_after", "reminder_frequency",
                    "last_reminder_sent", "reminder_count", "account", "contact"})
# Approved CRM field register (PR #642); credentials and public invoice links are excluded.
FIELDS = FIELDS | {"payments"} | frozenset(['accepted_at', 'accepted_by_email', 'accepted_by_name', 'accepted_user_agent', 'account', 'address_line', 'amount_due', 'amount_paid', 'assigned_to', 'billing_address_line', 'billing_city', 'billing_country', 'billing_period', 'billing_postcode', 'billing_state', 'city', 'client_address_line', 'client_city', 'client_country', 'client_email', 'client_name', 'client_phone', 'client_postcode', 'client_state', 'close_date', 'company_name', 'contact', 'contacts', 'converted_to_invoice', 'country', 'created_at', 'created_by', 'currency', 'custom_fields', 'declined_at', 'department', 'deposit_invoice', 'deposit_percent', 'description', 'details', 'discount_amount', 'discount_type', 'discount_value', 'do_not_call', 'due_date', 'email', 'estimate_number', 'expiry_date', 'external_id', 'external_source', 'first_name', 'first_response_at', 'first_response_by', 'first_response_by_details', 'industry', 'introduction', 'invoice_number', 'invoice_title', 'is_active', 'is_expired', 'is_overdue', 'is_unanswered', 'issue_date', 'job_title', 'last_contacted', 'last_enquiry_at', 'last_name', 'last_outbound_at', 'last_reminder_sent', 'line_items', 'linked_accounts', 'linkedin_url', 'next_follow_up', 'notes', 'opportunity', 'opportunity_amount', 'organization', 'paid_at', 'payment_terms', 'phone', 'phone_e164', 'po_number', 'postcode', 'probability', 'project', 'project_billing_type', 'rating', 'reminder_count', 'reminder_days_after', 'reminder_days_before', 'reminder_enabled', 'reminder_frequency', 'salutation', 'sent_at', 'shipping_amount', 'source', 'source_detail', 'source_ref', 'stage', 'start_project', 'state', 'status', 'subtotal', 'tags', 'tax_amount', 'tax_rate', 'teams', 'template', 'terms', 'title', 'total_amount', 'updated_at', 'updated_by', 'viewed_at', 'waiting_since', 'website'])
STATUSES = {"assigned", "closed", "converted", "in process", "recycled"}


def validate_data(resource: str, data: dict, update: bool = False) -> dict:
    schema = dict(SCHEMAS[ROOTS[resource]])
    schema["components"] = {"schemas": SCHEMAS}
    if not data or list(Draft4Validator(schema, format_checker=FormatChecker()).iter_errors(data)):
        raise ValueError("Invalid CRM data")
    return data


class CRMWriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: Resource
    record_id: UUID | None = None
    data: dict = Field(min_length=1)

    @model_validator(mode="after")
    def pinned_contract(self):
        validate_data(self.resource, self.data, self.record_id is not None)
        return self


async def authority(actor: Actor, tool: str = "trigger.crm.write") -> Actor:
    actor = await authorized_actor(actor)
    # Preserve the actor-bound, durably confirmed Base list capability.
    from trigger_commands import CONFIRMED, confirmation
    bound = CONFIRMED.get()
    if bound and bound[3] == "base.list.to_crm":
        require_tool(actor, "base.list.to_crm")
        confirmation(actor, "base.list.to_crm", bound[4])
        return actor
    if not (actor.founder or actor.tenant_admin):
        raise HTTPException(403, "This tool is not available to this user")
    require_tool(actor, tool)
    return actor


def binding(actor: Actor) -> tuple[str, str, str]:
    """Configuration is founder-supplied. No Trigger sign-in/BottleCRM org identity is inferred."""
    missing = [name for name in ("TRIGGER_CRM_URL", "TRIGGER_CRM_TENANT_BINDINGS") if not os.environ.get(name)]
    if missing:
        raise HTTPException(503, {"missing_settings": missing})
    try:
        bindings = json.loads(os.environ.get("TRIGGER_CRM_TENANT_BINDINGS", "{}"))
        if not isinstance(bindings, dict):
            raise TypeError
        names, orgs, tokens = set(), set(), set()
        for tenant, item in bindings.items():
            UUID(tenant)
            if not isinstance(item, dict) or set(item) != {"token_env", "organization_id"}:
                raise ValueError
            name, org = item["token_env"], str(UUID(item["organization_id"]))
            if not isinstance(name, str) or not re.fullmatch(r"TRIGGER_CRM_PAT_[A-Z0-9_]{1,60}", name):
                raise ValueError
            token = os.environ.get(name, "")
            if not token:
                raise HTTPException(503, {"missing_settings": [name]})
            # Duplicate credentials/orgs could accidentally bind two tenants to one CRM org.
            if not token or len(token) > 4096 or not token.isascii() or any(c.isspace() for c in token) or name in names or org in orgs or token in tokens:
                raise ValueError
            names.add(name)
            orgs.add(org)
            tokens.add(token)
        selected = bindings[str(actor.tenant_id)]
        token = os.environ[selected["token_env"]]
        url = os.environ.get("TRIGGER_CRM_URL", "").rstrip("/")
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError
        if not ((parsed.scheme == "https" and parsed.netloc == "crm.tervory.com") or
                (parsed.scheme == "http" and parsed.netloc == "trigger-crm-backend:8000")):
            raise ValueError
        return url, token, str(UUID(selected["organization_id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(503, "CRM is not configured for this tenant") from None


async def upstream(actor: Actor, resource: Resource, *, record_id: UUID | None = None,
                   method: str = "GET", data: dict | None = None, params: dict | None = None,
                   _authorize=None) -> dict:
    url, token, organization = binding(actor)
    if method != "GET":
        # An old/unmodified CRM must fail before any write, not after a side effect.
        await upstream(actor, resource, params={"offset": 0, "limit": 1})
        actor = await (_authorize or authority)(actor)
    path = PATHS[resource] + (f"{record_id}/" if record_id else "")
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(20), follow_redirects=False, trust_env=False) as client,
            client.stream(method, url + path, headers={"Authorization": "Bearer " + token,
                                                             "X-Tervory-CRM-Organization": organization},
                          params=params, json=data) as response,
        ):
            if response.status_code == 404:
                raise HTTPException(404, "CRM record unavailable")
            if response.status_code in {401, 403}:
                raise HTTPException(503, "CRM access is not configured")
            if not 200 <= response.status_code < 300:
                raise HTTPException(502, "CRM could not complete the request")
            try:
                attested = str(UUID(response.headers.get("X-Tervory-CRM-Organization", "")))
            except (ValueError, TypeError):
                raise HTTPException(502, "CRM organization binding is unavailable") from None
            if attested != organization:
                raise HTTPException(502, "CRM organization binding refused")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 2_000_000:
                    raise HTTPException(502, "CRM response exceeds the limit")
        result = json.loads(content)
        if not isinstance(result, dict) or result.get("error") is True:
            raise ValueError
        return result
    except (httpx.HTTPError, ValueError):
        raise HTTPException(503, "CRM is unavailable") from None


def project(item: dict) -> dict:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise HTTPException(502, "Invalid CRM response")
    try:
        UUID(item["id"])
    except ValueError:
        raise HTTPException(502, "Invalid CRM response") from None
    return {key: projected({k: v for k, v in value.items() if k in {'id', 'name', 'first_name', 'last_name'}}
                           if key in {'created_by', 'updated_by', 'first_response_by', 'first_response_by_details'} and isinstance(value, dict)
                           else value) for key, value in item.items() if key in FIELDS}


def projected(value, depth=0):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value[:20000] if isinstance(value, str) else value
    if depth >= 4:
        return None
    if isinstance(value, list):
        return [projected(item, depth+1) for item in value[:200]]
    if isinstance(value, dict):
        safe = {'id', 'name', 'title', 'first_name', 'last_name', 'email', 'phone', 'company_name',
                'description', 'quantity', 'unit_price', 'price', 'amount', 'total', 'subtotal',
                'product_name', 'payment_date', 'payment_method', 'reference_number',
                'tax_rate', 'tax_amount', 'discount', 'currency', 'product', 'sku', 'value', 'label'}
        return {key: projected(item, depth+1) for key, item in value.items() if key in safe}
    return None


@router.get("/{resource}")
async def listing(resource: Resource, request: Request, actor: Annotated[Actor, Depends(identity)]):
    from trigger_routes import code_receipt
    actor = await authority(actor, "trigger.crm.list")
    query = dict(request.query_params)
    allowed = {"page"} | ({"name"} if resource == "contacts" else {"title", "status"} if resource == "leads" else set())
    if set(query) - allowed or len(request.query_params.multi_items()) != len(query):
        raise HTTPException(422, "Invalid CRM query")
    try:
        page = int(query.get("page", "1"))
        if not 1 <= page <= 10000 or any(len(v) > 255 for v in query.values()):
            raise ValueError
        if "status" in query and query["status"] not in STATUSES:
            raise ValueError
    except ValueError:
        raise HTTPException(422, "Invalid CRM query") from None
    # Pinned BottleCRM views use DRF LimitOffsetPagination, not a page parameter.
    params = {key: value for key, value in query.items() if key != "page"}
    raw = await upstream(actor, resource, params={**params, "offset": (page - 1) * 10, "limit": 10})
    if resource == "leads":
        groups = [raw.get("open_leads"), raw.get("close_leads")]
        if not all(isinstance(group, dict) for group in groups):
            raise HTTPException(502, "Invalid CRM response")
        opened, closed = groups[0].get("open_leads"), groups[1].get("close_leads")
        if not isinstance(opened, list) or not isinstance(closed, list):
            raise HTTPException(502, "Invalid CRM response")
        rows = opened + closed
        counts = [group.get("leads_count") for group in groups]
        count = sum(counts) if all(type(n) is int and n >= 0 for n in counts) else None
    else:
        rows = raw.get("results", raw.get("contact_obj_list"))
        count = raw.get("count")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise HTTPException(502, "Invalid CRM response")
    return {"resource": resource, "items": [project(item) for item in rows], "page": page,
            "count": count if type(count) is int and count >= 0 else None,
            "model_calls": 0, "cost_usd": 0,
            "ledger_id": str(await code_receipt(actor.tenant_id, "trigger.crm.list"))}


@router.get("/{resource}/{record_id}")
async def detail(resource: Resource, record_id: UUID, actor: Annotated[Actor, Depends(identity)]):
    from trigger_routes import code_receipt
    actor = await authority(actor, "trigger.crm.get")
    raw = await upstream(actor, resource, record_id=record_id)
    key = {"leads": "lead_obj", "contacts": "contact_obj", "estimates": "estimate", "invoices": "invoice"}[resource]
    item = project(raw.get(key))
    return {"resource": resource, "record_id": str(record_id), "item": item,
            "model_calls": 0, "cost_usd": 0,
            "ledger_id": str(await code_receipt(actor.tenant_id, "trigger.crm.get"))}


@router.post("/write")
async def write(request: Request, actor: Annotated[Actor, Depends(identity)]):
    from trigger_commands import confirmation
    from trigger_routes import code_receipt
    actor = await authority(actor)
    raw = await request.body()
    if len(raw) > 32000:
        raise HTTPException(413, "Request too large")
    try:
        body = json.loads(raw)
        if body.pop("tenant_id", str(actor.tenant_id)) != str(actor.tenant_id):
            raise HTTPException(403, "Tenant mismatch")
        args = CRMWriteArgs.model_validate(body)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(422, "Invalid CRM request") from None
    canonical = args.model_dump(mode="json", exclude_none=True)
    confirmation(actor, "trigger.crm.write", canonical)
    result = await upstream(actor, args.resource, record_id=args.record_id,
                            method="PUT" if args.record_id else "POST", data=args.data)
    key = {"leads": "lead_obj", "contacts": "contact_obj", "estimates": "estimate", "invoices": "invoice"}[args.resource]
    item = result.get(key, result.get({"leads": "lead", "contacts": "contact", "estimates": "estimate", "invoices": "invoice"}[args.resource]))
    if item is None:
        item = {"id": result.get("id", str(args.record_id) if args.record_id else None)}
    return {"resource": args.resource, "item": project(item), "model_calls": 0, "cost_usd": 0,
            "ledger_id": str(await code_receipt(actor.tenant_id, "trigger.crm.write"))}


async def confirmed_bot_lead(actor: Actor, draft_id: UUID, data: dict, arguments: dict) -> dict:
    """Narrow bot create-only capability; generic CRM access requires a founder or tenant admin."""
    from trigger_commands import confirmation

    async def authorize(current):
        current = await authorized_actor(current)
        if not (current.founder or current.tenant_admin):
            raise HTTPException(403, "This tool is not available to this user")
        require_tool(current, "trigger.bot.lead.confirm")
        confirmation(current, "trigger.bot.lead.confirm", arguments)
        import hashlib
        expected = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if arguments.get("draft_id") != str(draft_id) or arguments.get("expected_digest") != expected:
            raise HTTPException(409, "Confirmed action required")
        return current

    actor = await authorize(actor)
    validate_data("leads", data)
    # binding() and authenticated organization preflight happen before any POST.
    return await upstream(actor, "leads", method="POST", data=data, _authorize=authorize)
