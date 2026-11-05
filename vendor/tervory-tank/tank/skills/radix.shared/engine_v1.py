"""RX-5: deterministic analysis of a tenant's supplied business evidence.

No network, database, model, contact enrichment or individual-person index.
The five 1.0.0 packages pin engine_v1.py. Once approved, new behaviour needs
a new engine file and new skill versions; never change this engine in place.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SKILLS = ("verify_business", "entity_links", "account_links", "site_risk", "business_risk")
REGISTERS = {"state_register", "sec_edgar", "cipc"}
FIELDS = {
    "company_name", "registration_number", "status", "registered_address", "registered_agent",
    "officers", "formed_on", "licence_number", "licence_status", "licence_expires_on",
    "domain", "domain_created_on", "owner_organisation", "business_phone", "website_name",
    "website_address", "website_template", "privacy_notice", "cookie_notice", "exposed_admin",
}
BOOL_FIELDS = {"privacy_notice", "cookie_notice", "exposed_admin"}
DATE_FIELDS = {"formed_on", "domain_created_on", "licence_expires_on"}
REGISTRY_FIELDS = {"company_name", "registration_number", "status", "registered_address", "registered_agent", "officers", "formed_on"}
DOMAIN = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
CORPORATE = re.compile(r"\b(?:incorporated|inc|llc|ltd|limited|corporation|corp|company|llp|pllc|pty|gmbh)\.?$", re.I)
IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_-]{1,30}:[A-Z0-9][A-Z0-9/.-]{1,70}$", re.I)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def uuid_text(value: str) -> str:
    return str(UUID(value))


def domain(value: str) -> str:
    value = value.lower().removesuffix(".")
    if not DOMAIN.fullmatch(value):
        raise ValueError("Use a business domain, without a URL, email or phone number")
    return value.removeprefix("www.")


class Lookup(Strict):
    kind: Literal["business_id", "company_name", "registration_number", "domain", "licence_number"]
    value: str = Field(min_length=1, max_length=253)

    @field_validator("value")
    @classmethod
    def business_key(cls, value: str, info: Any) -> str:
        kind = info.data.get("kind")
        if "@" in value or any(ord(c) < 32 for c in value):
            raise ValueError("Individual lookup keys are not supported")
        if kind == "business_id":
            return uuid_text(value)
        if kind == "domain":
            return domain(value)
        if kind == "company_name":
            # Conservative: ambiguous trade names use their business UUID or
            # registry identifier instead. Do not guess whether a bare name is a person.
            if not CORPORATE.search(value.strip()) or re.search(r"\d{7,}", value):
                raise ValueError("Use a legal company name or a business identifier, never a person's name")
        if kind in {"registration_number", "licence_number"} and not IDENTIFIER.fullmatch(value):
            raise ValueError("Use an issuer-qualified business identifier, not a name or phone number")
        return value.strip()


class Source(Strict):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    tenant_id: str
    kind: Literal["state_register", "sec_edgar", "atf_ffl", "cipc", "rdap", "website", "client_platform", "rx_scan"]
    url: str = Field(min_length=1, max_length=1000)
    terms: str = Field(min_length=1, max_length=2000)
    region: Literal["us-east", "za"]
    retrieved_at: str
    key_env: Literal["RADIX_SOURCE_CIPC_KEY", "RADIX_SOURCE_REGISTER_KEY"] | None = None

    @field_validator("tenant_id")
    @classmethod
    def valid_tenant(cls, value: str) -> str:
        return uuid_text(value)

    @field_validator("retrieved_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("Source timestamps need a timezone")
        return value

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "client"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("A source needs an HTTPS citation or a tenant-owned client:// reference")
        if parsed.query or any(ord(c) < 32 for c in value):
            raise ValueError("Source links cannot contain query credentials or control characters")
        return value


class Fact(Strict):
    value: str | bool | list[str]
    source_ids: list[str] = Field(min_length=1, max_length=10)


class Business(Strict):
    id: str
    tenant_id: str
    entity_type: Literal["business"]
    facts: dict[str, Fact]
    base_record_id: str | None = None

    @field_validator("id", "tenant_id", "base_record_id")
    @classmethod
    def valid_id(cls, value: str | None) -> str | None:
        return uuid_text(value) if value is not None else None

    @field_validator("facts")
    @classmethod
    def business_facts(cls, facts: dict[str, Fact]) -> dict[str, Fact]:
        if set(facts) - FIELDS:
            raise ValueError("Only business evidence fields are accepted")
        for name, fact in facts.items():
            value = fact.value
            if name in BOOL_FIELDS:
                if type(value) is not bool:
                    raise ValueError("Notice and scan observations must be boolean")
            elif name == "officers":
                if not isinstance(value, list) or len(value) > 100 or any(len(x) > 200 for x in value):
                    raise ValueError("Officers are register-published context only")
            elif not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise ValueError("Business facts must be bounded nonempty text")
            if name in DATE_FIELDS:
                date.fromisoformat(str(value))
            if name == "domain":
                fact.value = domain(str(value))
            if name in {"registration_number", "licence_number"} and not IDENTIFIER.fullmatch(str(value)):
                raise ValueError("Business numbers require an issuer namespace")
            if name == "owner_organisation" and not CORPORATE.search(str(value)):
                raise ValueError("Individual domain owners are excluded; omit redacted or unknown owners")
        return facts


class Account(Strict):
    id: str
    tenant_id: str
    business_id: str
    status: Fact
    payout_business_name: Fact | None = None
    listing_template: Fact | None = None

    @field_validator("id", "tenant_id", "business_id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return uuid_text(value)


BASE_FIELDS = {
    "company_name": {"profile.name", "raw.company_name"},
    "registration_number": {"profile.registration_number", "raw.registration_number"},
    "registered_address": {"raw.registered_address"},
    "licence_number": {"profile.licence_number", "raw.licence_number"},
    "licence_expires_on": {"profile.licence_expires", "raw.licence_expires_on"},
    "licence_status": {"profile.licence_status", "raw.licence_status"},
    "domain": {"profile.website", "profile.domain", "raw.domain"},
}


class BaseBinding(Strict):
    path: str
    source_ids: list[str] = Field(min_length=1, max_length=10)
    issuer: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_-]{1,30}$")


class BaseImport(Strict):
    # Existing Base export format. Only explicit business fields below leave
    # this adapter. Contacts, emails, phone lists and raw person fields do not.
    record: dict[str, Any]
    bindings: dict[str, BaseBinding]
    tenant_id: str

    def business(self, tenant_id: str) -> Business:
        # Base's /records response omits tenant_id: the authenticated export
        # caller must carry the tenant from that query in this envelope.
        if uuid_text(self.tenant_id) != tenant_id or str(self.record.get("tenant_id", tenant_id)) != tenant_id:
            raise ValueError("Base record belongs to another tenant")
        facts = {}
        for target, binding in self.bindings.items():
            if binding.path not in BASE_FIELDS.get(target, set()):
                raise ValueError("Only allowlisted Base business fields can be imported")
            section, field = binding.path.split(".")
            value = self.record.get(section, {}).get(field)
            if section == "profile":
                # Base profile cells are {value, source, confidence}. The
                # caller explicitly binds the original evidence catalogue;
                # a confidence number or 'input' alone is not a register.
                if not isinstance(value, dict) or not value.get("source"):
                    raise ValueError("Missing Base field provenance")
                value = value.get("value")
            if target in {"registration_number", "licence_number"} and binding.issuer:
                value = f"{binding.issuer}:{value}"
            if target == "domain" and isinstance(value, str) and value.startswith("https://"):
                value = urlsplit(value).hostname
            facts[target] = Fact(value=value, source_ids=binding.source_ids)
        return Business(id=str(self.record["id"]), base_record_id=str(self.record["id"]),
                        tenant_id=tenant_id, entity_type="business", facts=facts)


class Request(Strict):
    tenant_id: str
    region: Literal["us-east", "za"]
    as_of: str
    lookup: Lookup
    sources: list[Source] = Field(max_length=200)
    businesses: list[Business] = Field(max_length=500)
    base_imports: list[BaseImport] = Field(default_factory=list, max_length=200)
    accounts: list[Account] = Field(default_factory=list, max_length=500)
    brand_domains: list[Fact] = Field(default_factory=list, max_length=20)

    @field_validator("tenant_id")
    @classmethod
    def valid_tenant(cls, value: str) -> str:
        return uuid_text(value)

    @field_validator("as_of")
    @classmethod
    def valid_date(cls, value: str) -> str:
        date.fromisoformat(value)
        return value

    @field_validator("brand_domains")
    @classmethod
    def valid_domains(cls, values: list[Fact]) -> list[Fact]:
        for fact in values:
            if not isinstance(fact.value, str):
                raise ValueError("Brand domains must be text")
            fact.value = domain(fact.value)
        return values


def _base_normalizer() -> Any:
    path = Path(__file__).resolve().parents[1] / "base.normalize" / "1.0.0" / "run.py"
    spec = importlib.util.spec_from_file_location("radix_base_normalize", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _normal(value: Any) -> str:
    return " ".join(str(value).casefold().split())


class Analysis:
    def __init__(self, request: Request, env: dict[str, str]) -> None:
        self.request = request
        self.today = date.fromisoformat(request.as_of)
        request.businesses.extend(item.business(request.tenant_id) for item in request.base_imports)
        if len(request.businesses) > 500:
            raise ValueError("Too many supplied businesses")
        self.sources = {s.id: s for s in request.sources}
        self.businesses = {b.id: b for b in request.businesses}
        self.skipped: list[dict] = []
        self.normalizer = _base_normalizer()
        if len(self.sources) != len(request.sources) or len(self.businesses) != len(request.businesses):
            raise ValueError("Duplicate evidence identifiers")
        if len({a.id for a in request.accounts}) != len(request.accounts):
            raise ValueError("Duplicate account identifiers")
        for row in [*request.sources, *request.businesses, *request.accounts]:
            if row.tenant_id != request.tenant_id:
                raise ValueError("Evidence belongs to another tenant")
        for source in request.sources:
            if source.region != request.region or (source.kind == "cipc" and request.region != "za"):
                raise ValueError("Evidence belongs to another region")
            if source.url.startswith("client://") and urlsplit(source.url).netloc != request.tenant_id:
                raise ValueError("Client evidence belongs to another tenant")
            if datetime.fromisoformat(source.retrieved_at.replace("Z", "+00:00")).date() > self.today:
                raise ValueError("Evidence cannot be newer than the assessment date")
            if source.key_env and not env.get(source.key_env):
                self.skipped.append({"source_id": source.id, "reason": "key_not_set", "key_env": source.key_env})
        self.disabled = {s["source_id"] for s in self.skipped}
        for business in request.businesses:
            for name, fact in business.facts.items():
                self.check_fact(fact, name)
                if name in {"formed_on", "domain_created_on"} and date.fromisoformat(fact.value) > self.today:
                    raise ValueError("Formation and domain creation dates cannot be in the future")
        for fact in request.brand_domains:
            self.check_fact(fact, "account")
        for account in request.accounts:
            if account.business_id not in self.businesses:
                raise ValueError("Account business is not in this tenant's supplied records")
            if account.status.value not in {"active", "banned", "suspended"}:
                raise ValueError("Unsupported account status")
            for name in ("status", "payout_business_name", "listing_template"):
                fact = getattr(account, name)
                if fact:
                    self.check_fact(fact, "account")
                    if not isinstance(fact.value, str) or len(fact.value) > 300:
                        raise ValueError("Invalid business account evidence")
                    if name == "payout_business_name" and not CORPORATE.search(fact.value):
                        raise ValueError("Only a payout business name is accepted")

    def check_fact(self, fact: Fact, name: str) -> None:
        if len(set(fact.source_ids)) != len(fact.source_ids) or any(s not in self.sources for s in fact.source_ids):
            raise ValueError("Every fact must cite distinct sources in this tenant's catalogue")
        kinds = {self.sources[s].kind for s in fact.source_ids}
        if name in REGISTRY_FIELDS and not kinds <= REGISTERS:
            raise ValueError("Register fields require register evidence")
        if name in {"licence_status", "licence_number", "licence_expires_on"} and not kinds <= REGISTERS | {"atf_ffl"}:
            raise ValueError("Licence fields require official licence evidence")
        if name in {"domain_created_on", "owner_organisation"} and kinds != {"rdap"}:
            raise ValueError("Domain registration fields require supplied RDAP evidence")
        if name in {"privacy_notice", "cookie_notice", "exposed_admin"} and not kinds <= {"website", "rx_scan"}:
            raise ValueError("Site checks require supplied site or scan evidence")
        if name in {"website_name", "website_address", "website_template"} and not kinds <= {"website", "rx_scan"}:
            raise ValueError("Website fields require supplied website evidence")
        if name == "account" and kinds != {"client_platform"}:
            raise ValueError("Account evidence must come from the client's own platform")

    def fact(self, business: Business, field: str) -> Fact | None:
        fact = business.facts.get(field)
        return fact if fact and any(s not in self.disabled for s in fact.source_ids) else None

    def value(self, business: Business, field: str) -> Any:
        fact = self.fact(business, field)
        return fact.value if fact else None

    def citations(self, record_id: str, field: str, fact: Fact) -> list[dict]:
        digest = hashlib.sha256(json.dumps({"record_id": record_id, "field": field, "value": fact.value}, sort_keys=True).encode()).hexdigest()
        base_id = self.businesses[record_id].base_record_id if record_id in self.businesses else None
        return [{"source_id": sid, "url": self.sources[sid].url, "record_id": record_id, "base_record_id": base_id,
                 "field": field, "retrieved_at": self.sources[sid].retrieved_at, "evidence_sha256": digest}
                for sid in fact.source_ids if sid not in self.disabled]

    def finding(self, code: str, message: str, evidence: list[tuple[str, str, Fact]], *, weight: int = 0) -> dict | None:
        sources = [c for record, field, fact in evidence for c in self.citations(record, field, fact)]
        if not sources:
            return None
        return {"code": code, "message": message, "weight": weight, "sources": sources}

    def select(self) -> list[Business]:
        key = self.request.lookup
        if key.kind == "business_id":
            return [self.businesses[key.value]] if key.value in self.businesses else []
        return [b for b in self.request.businesses if _normal(self.value(b, key.kind)) == _normal(key.value)]

    def verification(self, business: Business) -> dict:
        fields = {}
        for name in FIELDS:
            fact = self.fact(business, name)
            if fact:
                fields[name] = {"value": fact.value, "sources": self.citations(business.id, name, fact)}
        exists = self.fact(business, "registration_number")
        status = self.value(business, "status")
        verdict = "verified_from_supplied_register" if exists and status == "active" else "inactive" if exists and status in {"inactive", "dissolved", "revoked"} else "unknown"
        findings = []
        if exists:
            findings.append(self.finding("registered_business", "The business appears in the supplied register evidence.", [(business.id, "registration_number", exists)]))
        if status in {"inactive", "dissolved", "revoked"}:
            findings.append(self.finding("inactive_business", "The supplied register reports an inactive business.", [(business.id, "status", self.fact(business, "status"))], weight=30))
        for left, right, normalise in (
            ("company_name", "website_name", self.normalizer.normalize_name),
            ("registered_address", "website_address", self.normalizer.normalize_address),
        ):
            a, b = self.fact(business, left), self.fact(business, right)
            if a and b:
                # Base's name normaliser returns (display, matching key).
                av, bv = normalise(a.value), normalise(b.value)
                match = (av[1] == bv[1]) if isinstance(av, tuple) else av == bv
                findings.append(self.finding(f"{right}_{'matches' if match else 'mismatch'}",
                    "The website agrees with the supplied business record." if match else "The website differs from the supplied business record; review it.",
                    [(business.id, left, a), (business.id, right, b)], weight=0 if match else 15))
        licence = self.fact(business, "licence_status")
        if licence and licence.value in {"expired", "revoked", "inactive"}:
            findings.append(self.finding("licence_not_active", "The supplied licence record is not active.", [(business.id, "licence_status", licence)], weight=25))
        expiry = self.fact(business, "licence_expires_on")
        if expiry and date.fromisoformat(expiry.value) < self.today:
            findings.append(self.finding("licence_expired", "The supplied licence expiry date has passed.", [(business.id, "licence_expires_on", expiry)], weight=25))
        created = self.fact(business, "domain_created_on")
        if created:
            fields["domain_age_days"] = {"value": (self.today - date.fromisoformat(created.value)).days,
                                         "sources": self.citations(business.id, "domain_created_on", created)}
        return {"business_id": business.id, "verification": verdict, "fields": fields, "findings": [f for f in findings if f],
                "unknown": sorted(FIELDS - fields.keys())}

    def entity_links(self, selected: set[str]) -> list[dict]:
        links = []
        rows = self.request.businesses
        for i, left in enumerate(rows):
            for right in rows[i + 1:]:
                if left.id not in selected and right.id not in selected:
                    continue
                matches, evidence = [], []
                for name in ("registered_address", "registered_agent", "business_phone", "domain", "website_template"):
                    a, b = self.fact(left, name), self.fact(right, name)
                    norm = self.normalizer.normalize_address if name == "registered_address" else _normal
                    if a and b and norm(a.value) == norm(b.value):
                        matches.append(name)
                        evidence.extend([(left.id, name, a), (right.id, name, b)])
                if not matches:
                    continue
                new = [self.fact(row, "formed_on") for row in (left, right)]
                recent = all(f and 0 <= (self.today - date.fromisoformat(f.value)).days <= 90 for f in new)
                if recent:
                    evidence.extend([(row.id, "formed_on", f) for row, f in zip((left, right), new)])
                finding = self.finding("shared_business_signals", "Shared business details are review signals, not proof of a shell company.", evidence, weight=10 if len(matches) >= 2 else 3)
                links.append({"business_ids": [left.id, right.id], "shared_fields": matches, "newly_formed_cluster": recent, "finding": finding})
        return links

    def account_links(self, selected: set[str]) -> list[dict]:
        links = []
        rows = self.request.accounts
        for i, left in enumerate(rows):
            for right in rows[i + 1:]:
                if left.business_id not in selected and right.business_id not in selected:
                    continue
                evidence, matches = [], []
                for name in ("registration_number", "domain"):
                    a, b = self.fact(self.businesses[left.business_id], name), self.fact(self.businesses[right.business_id], name)
                    if a and b and _normal(a.value) == _normal(b.value):
                        matches.append(name)
                        evidence.extend([(left.business_id, name, a), (right.business_id, name, b)])
                for name in ("payout_business_name", "listing_template"):
                    a, b = getattr(left, name), getattr(right, name)
                    if a and b and _normal(a.value) == _normal(b.value) and self.citations(left.id, name, a) and self.citations(right.id, name, b):
                        matches.append(name)
                        evidence.extend([(left.id, name, a), (right.id, name, b)])
                if not matches:
                    continue
                statuses = all(self.citations(a.id, "status", a.status) for a in (left, right))
                returning = bool(statuses and {left.status.value, right.status.value} == {"banned", "active"})
                if returning:
                    evidence.extend([(a.id, "status", a.status) for a in (left, right)])
                finding = self.finding("possible_returning_operation" if returning else "shared_account_business_signals",
                    "These client accounts share business details; review before taking action.", evidence, weight=25 if returning else 5)
                links.append({"account_ids": [left.id, right.id], "shared_fields": matches, "possible_returning_operation": returning, "finding": finding})
        return links

    def site_risk(self, business: Business) -> list[dict]:
        findings = []
        for name in ("privacy_notice", "cookie_notice", "exposed_admin"):
            fact = self.fact(business, name)
            if fact and fact.value is (True if name == "exposed_admin" else False):
                message = "An admin page was exposed in the supplied scan." if name == "exposed_admin" else "The supplied scan did not find a " + name.replace("_", " ") + "."
                findings.append(self.finding(name, message,
                    [(business.id, name, fact)], weight=20 if name == "exposed_admin" else 5))
        created = self.fact(business, "domain_created_on")
        if created and 0 <= (self.today - date.fromisoformat(created.value)).days <= 90:
            findings.append(self.finding("new_domain", "The supplied domain creation date is within 90 days; this alone is not wrongdoing.", [(business.id, "domain_created_on", created)], weight=5))
        target = self.fact(business, "domain")
        if target:
            for brand_fact in self.request.brand_domains:
                brand = brand_fact.value
                if not self.citations(self.request.tenant_id, "brand_domain", brand_fact):
                    continue
                name = str(target.value)
                if name != brand and not name.endswith("." + brand) and SequenceMatcher(None, name, brand).ratio() >= 0.8:
                    findings.append(self.finding("lookalike_domain", "The supplied domain resembles a client-provided brand domain; ownership is not inferred.", [(business.id, "domain", target), (self.request.tenant_id, "brand_domain", brand_fact)], weight=10))
        return [f for f in findings if f]


def analyse(skill: str, payload: dict, tenant_id: str, env: dict[str, str] | None = None) -> dict:
    try:
        if skill not in SKILLS:
            raise ValueError("Unknown business skill")
        request = Request.model_validate(payload)
        if request.tenant_id != uuid_text(tenant_id):
            raise ValueError("Tenant mismatch")
        analysis = Analysis(request, env or {})
        selected = analysis.select()
        if len(selected) != 1:
            return {"ok": True, "tenant_id": request.tenant_id, "region": request.region, "status": "ambiguous" if selected else "unknown",
                    "findings": [], "factors": [], "score": None, "reason": "Supply one unambiguous business record.",
                    "source_catalogue": [s.model_dump() for s in request.sources], "skipped_sources": analysis.skipped}
        business = selected[0]
        verified = analysis.verification(business)
        result = {"ok": True, "tenant_id": request.tenant_id, "region": request.region, "business_id": business.id, "as_of": request.as_of,
                  "status": "complete" if not analysis.skipped else "incomplete", "skipped_sources": analysis.skipped,
                  "source_catalogue": [s.model_dump() for s in request.sources], "model_calls": 0, "external_calls": 0}
        if skill == "verify_business":
            return {**result, **verified}
        if skill == "entity_links":
            return {**result, "links": analysis.entity_links({business.id}),
                    "findings": [f for f in verified["findings"] if f["code"].endswith("mismatch")]}
        if skill == "account_links":
            return {**result, "links": analysis.account_links({business.id})}
        site = analysis.site_risk(business)
        if skill == "site_risk":
            return {**result, "findings": site, "unknown": [n for n in ("domain_created_on", *sorted(BOOL_FIELDS)) if not analysis.fact(business, n)]}
        factors = [f for f in verified["findings"] + site if f["weight"]]
        factors += [link["finding"] for link in analysis.entity_links({business.id}) + analysis.account_links({business.id})]
        # One factor per rule, even if several neighbours repeat the same signal.
        unique = {}
        for factor in factors:
            if factor["code"] not in unique or factor["weight"] > unique[factor["code"]]["weight"]:
                unique[factor["code"]] = factor
        if "licence_not_active" in unique:
            unique.pop("licence_expired", None)
        required = ("registration_number", "status", "domain", "privacy_notice", "cookie_notice", "exposed_admin")
        missing = [name for name in required if not analysis.fact(business, name)]
        return {**result, "status": "incomplete" if missing or analysis.skipped else "complete",
                "score": min(100, sum(f["weight"] for f in unique.values())), "factors": list(unique.values()),
                "unknown": missing, "score_meaning": "Observed business risk signals, not a probability or a declaration of wrongdoing."}
    except (ValidationError, ValueError, TypeError, KeyError, AttributeError):
        # Never echo supplied identifiers or validation input into logs/results.
        return {"ok": False, "error": "invalid_business_evidence", "message": "Use only tenant-owned business records and typed business lookup keys; every fact needs a source."}


async def run(skill: str, payload: dict, ctx: Any) -> dict:
    # Pure offline eval contexts may omit identity. Production Skala supplies it;
    # a payload can never override that authenticated tenant context.
    if not isinstance(payload, dict) or payload.get("region") != os.environ.get("TANK_REGION", "us-east"):
        return {"ok": False, "error": "invalid_business_evidence"}
    tenant = str(getattr(ctx, "tenant_id", None) or payload.get("tenant_id", ""))
    env = {name: getattr(ctx, "env", {}).get(name) or os.environ.get(name, "")
           for name in ("RADIX_SOURCE_CIPC_KEY", "RADIX_SOURCE_REGISTER_KEY")}
    return analyse(skill, payload, tenant, env)
