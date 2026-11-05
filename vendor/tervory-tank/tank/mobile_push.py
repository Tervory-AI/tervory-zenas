"""One tenant-scoped notification channel, independent of products and event names."""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tenant_db import tenant_scope
import vault

router = APIRouter(prefix="/v1/mobile")


def push_configured() -> bool:
    return bool(os.environ.get("MOBILE_FCM_SERVICE_ACCOUNT_JSON") or
                all(os.environ.get(name) for name in ("MOBILE_APNS_KEY", "MOBILE_APNS_KEY_ID", "MOBILE_APNS_TEAM_ID")))


class Notification(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    tenant_id: uuid.UUID
    user_id: str | None = Field(default=None, min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=1000)
    link: str = Field(default="/chats", max_length=2048)

    @model_validator(mode="after")
    def payload_size(self):
        payload = {"aps": {"alert": {"title": self.title, "body": self.body}, "sound": "default"}, "path": self.link}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 3800:
            raise ValueError("Notification text and link must fit in 3800 UTF-8 bytes")
        return self

    @field_validator("link")
    @classmethod
    def workspace_link(cls, value: str) -> str:
        if any(ord(char) < 33 or char == "\\" or ord(char) == 127 for char in value):
            raise ValueError("A Workspace link is required")
        parsed = urlsplit(value)
        if value.startswith("/") and not value.startswith("//"):
            return value
        if (parsed.scheme == "https" and parsed.netloc == "app.tervory.com"
                and not parsed.username and not parsed.password):
            return value
        raise ValueError("A Workspace link is required")


async def sender(request: Request) -> None:
    from main import box_region, require_key
    await require_key(request, request.headers.get("x-tank-key"))
    if request.headers.get("x-tervory-region", box_region()) != box_region():
        raise HTTPException(403, "Notifications must stay on their home box")


class PushProviders:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.oauth: tuple[str, float] | None = None
        self.oauth_lock = asyncio.Lock()

    async def fcm_bearer(self, account: dict) -> str:
        async with self.oauth_lock:
            if self.oauth and self.oauth[1] > time.time() + 60:
                return self.oauth[0]
            now = int(time.time())
            assertion = jwt.encode({"iss": account["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                                    "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3600},
                                   account["private_key"], algorithm="RS256")
            response = await self.client.post("https://oauth2.googleapis.com/token", data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
            response.raise_for_status()
            result = response.json()
            self.oauth = (result["access_token"], time.time() + min(int(result["expires_in"]), 3600))
            return self.oauth[0]

    async def deliver(self, platform: str, token: str, message: Notification) -> str:
        if platform == "android":
            raw = os.environ.get("MOBILE_FCM_SERVICE_ACCOUNT_JSON")
            if not raw:
                return "unavailable"
            account = json.loads(raw)
            project = account["project_id"]
            if not isinstance(project, str) or not project.replace("-", "").isalnum():
                return "unavailable"
            bearer = await self.fcm_bearer(account)
            response = await self.client.post(f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
                headers={"Authorization": f"Bearer {bearer}"}, json={"message": {
                    "token": token, "notification": {"title": message.title, "body": message.body},
                    "data": {"path": message.link}, "android": {"ttl": "86400s"}}})
            if response.is_success:
                return "sent"
            details = response.json().get("error", {}).get("details", [])
            if any(item.get("@type") == "type.googleapis.com/google.firebase.fcm.v1.FcmError"
                   and item.get("errorCode") == "UNREGISTERED" for item in details):
                return "dead"
            return "failed"
        if not all(os.environ.get(name) for name in ("MOBILE_APNS_KEY", "MOBILE_APNS_KEY_ID", "MOBILE_APNS_TEAM_ID")):
            return "unavailable"
        bearer = jwt.encode({"iss": os.environ["MOBILE_APNS_TEAM_ID"], "iat": int(time.time())},
                            os.environ["MOBILE_APNS_KEY"], algorithm="ES256", headers={"kid": os.environ["MOBILE_APNS_KEY_ID"]})
        host = "https://api.sandbox.push.apple.com" if os.environ.get("MOBILE_APNS_ENVIRONMENT") == "sandbox" else "https://api.push.apple.com"
        response = await self.client.post(f"{host}/3/device/{token}", headers={
            "authorization": f"bearer {bearer}", "apns-topic": "com.tervory.app", "apns-push-type": "alert",
            "apns-priority": "10", "apns-expiration": str(int(time.time()) + 86400)},
            json={"aps": {"alert": {"title": message.title, "body": message.body}, "sound": "default"}, "path": message.link})
        if response.is_success:
            return "sent"
        if response.status_code in (400, 410) and response.json().get("reason") in {"Unregistered", "BadDeviceToken"}:
            return "dead"
        return "failed"


async def notify(tenant_id: uuid.UUID, title: str, body: str, link: str = "/chats", *,
                 user_id: str | None = None, pool=None, provider=None) -> dict[str, int]:
    from mobile import db
    message = Notification(tenant_id=tenant_id, user_id=user_id, title=title, body=body, link=link)
    store = pool if pool is not None else db()
    counts = dict(sent=0, dead=0, failed=0, unavailable=0)
    with tenant_scope(message.tenant_id):
        async with store.acquire() as conn:
            devices = await conn.fetch("SELECT device.user_id,device.token_hash,device.token,device.platform,device.session_hash FROM tank.mobile_devices device JOIN tank.mobile_sessions mobile_session ON mobile_session.tenant_id=device.tenant_id AND mobile_session.token_hash=device.session_hash AND mobile_session.user_id=device.user_id WHERE device.tenant_id=$1 AND ($2::text IS NULL OR device.user_id=$2) AND mobile_session.expires_at>now() AND NOT mobile_session.revoked", message.tenant_id, user_id)
        async with httpx.AsyncClient(http2=True, timeout=15, follow_redirects=False, limits=httpx.Limits(max_connections=8)) as client:
            transport = provider if provider is not None else PushProviders(client)
            semaphore = asyncio.Semaphore(8)

            async def deliver(device):
                async with semaphore:
                    try:
                        token = vault.open_seal(device["token"], f"mobile:{message.tenant_id}:{device['user_id']}:{device['token_hash']}")["token"]
                        result = await transport.deliver(device["platform"], token, message)
                        if result not in counts:
                            result = "failed"
                    except Exception:
                        result = "failed"
                    if result == "dead":
                        async with store.acquire() as conn:
                            await conn.execute("DELETE FROM tank.mobile_devices WHERE tenant_id=$1 AND token_hash=$2 AND session_hash=$3", message.tenant_id, device["token_hash"], device["session_hash"])
                    counts[result] += 1

            await asyncio.gather(*(deliver(device) for device in devices))
    return counts


@router.post("/notify", dependencies=[Depends(sender)])
async def send_notification(body: Notification):
    return await notify(body.tenant_id, body.title, body.body, body.link, user_id=body.user_id)
