"""Checkpoint existing paid HTTP reads before their metering hooks run."""
import base64

import httpx

import paid_steps
import vendor_meter


class Client(httpx.AsyncClient):
    def __init__(self, *args, **kwargs):
        from product_identity import request_hook
        hooks = {name: list(values) for name, values in (kwargs.get("event_hooks") or {}).items()}
        hooks.setdefault("request", []).append(request_hook)
        kwargs["event_hooks"] = hooks
        super().__init__(*args, **kwargs)

    async def send(self, request, **kwargs):
        if request.url.host not in vendor_meter.PAID_HOSTS or paid_steps.current.get() is None:
            return await super().send(request, **kwargs)
        if kwargs.get("stream"):
            # No paid vendor uses streaming on the shared client. Never buffer an
            # unbounded stream or dispatch a request that cannot be checkpointed.
            raise RuntimeError("Paid vendor streaming is not supported")
        key = paid_steps.fingerprint(["http", request.method, str(request.url), request.content.hex()])

        async def invoke():
            response = await super(Client, self).send(request, **kwargs)
            body = await response.aread()
            if len(body) > 5_000_000:
                raise RuntimeError("Paid vendor response exceeds checkpoint limit")
            return {"status": response.status_code, "body": base64.b64encode(body).decode(),
                    "headers": {k: v for k, v in response.headers.items() if k.lower() == "content-type"}}

        value = await paid_steps.checkpoint(key, invoke)
        return httpx.Response(value["status"], content=base64.b64decode(value["body"]),
                              headers=value["headers"], request=request)
