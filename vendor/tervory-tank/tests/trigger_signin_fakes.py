import time
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt

import identity_oidc as oidc

_IDENTITY_HTTP_CLIENT = httpx.AsyncClient

CLIENT = '123@tervory'
ORG = '10001'
SUB = '20001'


def set_founders(monkeypatch, subjects):
    from contextvars import ContextVar
    import trigger_signin_config as registry
    region = 'us-east'
    issuer = registry.ISSUERS[region]
    record = {**registry._records.get(region, {}), 'issuer': issuer,
              'founders': [subject.strip() for subject in subjects.split(',') if subject.strip()]}
    monkeypatch.setattr(registry, '_records', {**registry._records, region: record})
    monkeypatch.setattr(registry, '_issuer', ContextVar('offline_verified_issuer', default=issuer))
    monkeypatch.setenv('TERVORY_ADMIN_USER_IDS', subjects)
    async def refresh(**kwargs):
        return None
    monkeypatch.setattr(registry, 'refresh', refresh)


def install_registry(monkeypatch, providers, bindings, *, workspace_client=None):
    import trigger_signin_config as registry
    records = {'us-east': {'region': 'us-east', 'issuer': provider.issuer, 'client_id': CLIENT,
               'workspace_client_id': workspace_client or CLIENT, 'project_id': '30001',
               'organisations': dict(bindings), 'founders': []} for provider in providers}
    monkeypatch.setattr(registry, '_records', records)
    async def refresh(**kwargs):
        return None
    monkeypatch.setattr(registry, 'refresh', refresh)


def native_provider(monkeypatch, bindings):
    import trigger_signin_config as registry
    founders = registry._records.get('us-east', {}).get('founders', [])
    provider = Provider('us-east')
    install_registry(monkeypatch, [provider], bindings)
    set_founders(monkeypatch, ','.join(founders))
    original_client = _IDENTITY_HTTP_CLIENT
    monkeypatch.setattr(oidc, 'http_client', lambda: original_client(transport=httpx.MockTransport(provider)))
    oidc._cache.clear()
    return provider


class Provider:
    def __init__(self, region='za'):
        self.region = region
        self.issuer = oidc.ISSUERS[region]
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = 'first-key'
        self.keys = [self.jwk()]
        self.calls = []
        self.claims = {'iss': self.issuer, 'aud': CLIENT, 'sub': SUB, 'exp': int(time.time()) + 600,
                       'iat': int(time.time()), oidc.ORG_CLAIM: ORG,
                       oidc.ROLES_CLAIM: {'org_admin': {ORG: 'tervory'}}}

    def workspace_headers(self, subject, tenant, role='org_admin'):
        import trigger_signin_config as registry
        organisations = registry._records['us-east']['organisations']
        org = next(org for org, owner in organisations.items() if str(owner) == str(tenant))
        return {'X-Tervory-User': subject, 'X-Tervory-Tenant': str(tenant),
                'x-trigger-signin-org': org,
                'x-trigger-signin-token': self.token(sub=subject, **{
                    oidc.ORG_CLAIM: org, oidc.ROLES_CLAIM: {role: {org: 'Fixture'}}}),
                **({'X-Tervory-Admin': subject} if role == 'org_admin' else {})}

    def jwk(self):
        return {**jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True),
                'kid': self.kid, 'alg': 'RS256', 'use': 'sig'}

    def token(self, **claims):
        return jwt.encode({**self.claims, **claims}, self.private, algorithm='RS256', headers={'kid': self.kid})

    def __call__(self, request):
        assert request.url.host == urlsplit(self.issuer).hostname
        self.calls.append((request.method, request.url.path))
        if request.url.path == '/.well-known/openid-configuration':
            return httpx.Response(200, json={'issuer': self.issuer, 'jwks_uri': self.issuer + '/keys',
                'token_endpoint': self.issuer + '/oauth/v2/token',
                'device_authorization_endpoint': self.issuer + '/oauth/v2/device_authorization'})
        if request.url.path == '/keys':
            return httpx.Response(200, json={'keys': self.keys})
        form = parse_qs(request.content.decode())
        assert request.method == 'POST' and form['client_id'] == [CLIENT]
        if request.url.path == '/oauth/v2/device_authorization':
            assert form['scope'] == [oidc.SCOPE]
            return httpx.Response(200, json={'device_code': 'synthetic-provider-code', 'user_code': '1234-5678',
                'verification_uri': self.issuer + '/ui/v2/login/device',
                'verification_uri_complete': self.issuer + '/ui/v2/login/device?user_code=1234-5678',
                'expires_in': 600, 'interval': 5})
        assert request.url.path == '/oauth/v2/token'
        assert form['device_code'] == ['synthetic-provider-code']
        assert form['grant_type'] == ['urn:ietf:params:oauth:grant-type:device_code']
        return httpx.Response(200, json={'id_token': self.token(), 'access_token': 'unused-synthetic-upstream-token'})


async def persisted_identity(conn, tenant, subject, *, founder=False):
    """A real, hash-only native grant in an explicitly disposable fixture DB."""
    import hashlib
    import secrets
    import uuid
    tenant = uuid.UUID(str(tenant))
    org = str(tenant.int % (10 ** 25))
    issuer = 'https://auth.tervory.com'
    await conn.execute("""INSERT INTO tank.trigger_signin_config
        (region,issuer,client_id,workspace_client_id,project_id)
        VALUES('us-east',$1,'123@tervory','123@tervory','30001') ON CONFLICT DO NOTHING""", issuer)
    await conn.execute("""INSERT INTO tank.trigger_signin_organisations(region,org_id,tenant_id)
        VALUES('us-east',$1,$2) ON CONFLICT DO NOTHING""", org, tenant)
    if founder:
        await conn.execute("""INSERT INTO tank.trigger_signin_founders(region,subject)
            VALUES('us-east',$1) ON CONFLICT DO NOTHING""", subject)
    token = f'tv1.us-east.{tenant.hex}.' + secrets.token_urlsafe(32)
    await conn.execute("""INSERT INTO tank.personal_tokens
        (tenant_id,user_id,region,name,token_hash,trigger_signin,trigger_signin_issuer,identity_tenant_admin)
        VALUES($1,$2,'us-east','placeholder-fixture',$3,true,$4,true)""",
        tenant, subject, hashlib.sha256(token.encode('ascii')).hexdigest(), issuer)
    return {'authorization': 'Bearer ' + token}
