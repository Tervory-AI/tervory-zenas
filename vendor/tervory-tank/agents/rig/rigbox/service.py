"""Shopper calls. The store is always read and written for one tenant."""

from __future__ import annotations

from chassis.tenant_db import tenant_scope

import hashlib
import hmac
import json
import os
import re
import time
import unicodedata
from decimal import Decimal
from typing import Any
from uuid import uuid4
from urllib.parse import urlparse

from rigbox.catalogue import (
    map_google_merchant,
    map_shopify,
    map_woocommerce,
    price_patch,
)
from rigbox.claims import answerable
from rigbox.claims import grounded_answer
from rigbox.claims import select_prompt
from rigbox.ground import explain as ground_explain
from rigbox.ground import explain_prompt
from rigbox.ground import fit_suggest
from rigbox.ground import suggest_prompt
from rigbox.logic import _why as code_why
from rigbox.logic import approved
from rigbox.logic import money
from rigbox.logic import fitting_parts
from rigbox.logic import graph as build_graph
from rigbox.logic import options_for
from rigbox.logic import product_detail
from rigbox.logic import _limit
from rigbox.logic import public_product
from rigbox.shopper_attributes import shopper_attributes, shopper_rows
from rigbox.logic import reprice
from rigbox.logic import setups as build_setups
from rigbox.rank import search as rank_room
from rigbox.reserve import reservation_letter
from rigbox.recall import latest
from rigbox.recall import recall_days
from rigbox.recall import part_hit
from rigbox.recall import part_query
from rigbox.recall import parts_of
from rigbox.recall import signature
from rigbox.recall import whole_hit
from rigbox.locate import catalogue as supply_catalogue, locate as supply_locate, needs_supply
from rigbox.locate import enabled as locate_enabled
from rigbox.store import _fit_key
from rigbox.quotes.limits import FailedKeyLimits, MemoryRateLimits, PgRateLimits

_KEY = re.compile(r"^rk_pub_[A-Za-z0-9][A-Za-z0-9_-]{0,80}$")
_MAPS = {
    "shopify": map_shopify,
    "woocommerce": map_woocommerce,
    "google": map_google_merchant,
}


def _part_from_rank(piece: str, ranked: dict[str, Any], cap: int) -> dict[str, Any]:
    ids = [str(item["id"]) for item in ranked.get("results") or []]
    exact, message = _exact_and_message(ranked)
    return {
        "part": piece,
        "ids": ids,
        "exact_match": exact if ids else False,
        "message": message,
        "limit": cap,
    }


def _exact_and_message(ranked: dict[str, Any]) -> tuple[bool, str | None]:
    results = ranked.get("results") or []
    if not results:
        return True, None
    exact = bool(ranked.get("exact_match", True))
    if exact:
        return True, None
    message = ranked.get("message")
    if message:
        return False, str(message)
    return False, "No exact match for that model."


def _merge_parts(part_rows: list[dict[str, Any]], cap: int) -> dict[str, Any]:
    ids: list[str] = []
    seen: set[str] = set()
    exact = True
    message: str | None = None
    for row in part_rows:
        row_ids = [str(item) for item in row.get("ids") or []]
        if not row_ids or row.get("exact_match") is False:
            exact = False
            if message is None and row.get("message"):
                message = str(row["message"])
        for sku in row_ids:
            if sku in seen:
                continue
            seen.add(sku)
            ids.append(sku)
            if len(ids) >= cap:
                break
        if len(ids) >= cap:
            break
    return {"results": [{"id": sku} for sku in ids], "exact_match": exact, "message": message}


def _shopper_body(
    ranked: dict[str, Any],
    host: str,
    part_rows: list[dict[str, Any]],
    reused_flags: list[bool],
) -> dict[str, Any]:
    results = list(ranked.get("results") or [])
    if not results:
        return {"results": []}
    exact, message = _exact_and_message(ranked)
    body: dict[str, Any] = {"results": results, "exact_match": exact}
    if message:
        body["message"] = message
    shown: list[dict[str, Any]] = []
    for index, row in enumerate(part_rows):
        shown.append(
            {
                "part": str(row.get("part") or ""),
                "on": host,
                "reused": reused_flags[index] if index < len(reused_flags) else False,
                "rung": "code",
                "cost": 0,
                "ids": [str(item) for item in row.get("ids") or []],
            }
        )
    body["parts"] = shown
    return body


class RigError(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class RateLimiter:
    def __init__(self, limit: int = 60, window: float = 60) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key_hash: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        hits = [item for item in self._hits.get(key_hash, []) if moment - item < self.window]
        if len(hits) >= self.limit:
            self._hits[key_hash] = hits
            return False
        hits.append(moment)
        self._hits[key_hash] = hits
        return True


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class RecordingHandoff:
    """Test double. It records the handoff and does not send mail."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self,
        *,
        tenant_id: str,
        email: str | None,
        webhook: str | None,
        quote: dict[str, Any],
        letter: dict[str, str] | None = None,
    ) -> None:
        self.sent.append(
            {
                "tenant_id": tenant_id,
                "email": email,
                "webhook": webhook,
                "quote": quote,
                "letter": letter,
            }
        )


class WebhookHandoff:
    """Posts the quote to the shop webhook, or sends the letter to the shop email.

    Which one runs is the shop's publish settings. A webhook wins when it is
    set. Email is used when there is no webhook. The mailer is injected so
    tests never open a connection.
    """

    def __init__(self, http: Any, mailer: Any | None = None) -> None:
        self.http = http
        self.mailer = mailer

    async def send(
        self,
        *,
        tenant_id: str,
        email: str | None,
        webhook: str | None,
        quote: dict[str, Any],
        letter: dict[str, str] | None = None,
    ) -> None:
        if webhook and self.http is not None:
            await self.http.post(webhook, json={"tenant_id": tenant_id, "quote": quote})
            return
        # A shop that does not ask for the letter keeps the old handoff:
        # an address with no webhook is stored and marked sent, and nothing is mailed.
        if letter is None:
            return
        if self.mailer is None or not email:
            raise RuntimeError("mail is not configured")
        await self.mailer.send(letter)


class Service:
    def __init__(
        self,
        store: Any,
        models: Any | None = None,
        handoff: Any | None = None,
        *,
        rate_limit: int = 60,
    ) -> None:
        self.store = store
        self.models = models
        self.handoff = handoff or RecordingHandoff()
        # Locate confirm reads offers live. The app wires the product checkers,
        # tests inject doubles. None means confirm is not available.
        self.locate_checker: Any | None = None
        self.limiter = RateLimiter(limit=rate_limit)
        self.failed_keys = FailedKeyLimits()
        pool = getattr(store, "pool", None)
        self.public_limits = PgRateLimits(pool, rate_limit) if pool is not None else MemoryRateLimits(rate_limit)

    async def authenticate(self, raw_key: str, address: str = "unknown") -> dict[str, Any]:
        reservation = self.failed_keys.reserve(address)
        if reservation is None:
            raise RigError("rate_limit", "Too many failed keys. Try again in a minute.", 429)
        presented = raw_key or ""
        digest = hash_key(presented)
        if _KEY.fullmatch(presented) is None:
            raise RigError("unauthorized", "That key is not valid.", 401)
        try:
            row = await self.store.key(digest)
        except Exception:
            self.failed_keys.release(reservation)
            raise
        if row is None or row.get("kind") != "publishable":
            raise RigError("unauthorized", "That key is not valid.", 401)
        self.failed_keys.release(reservation)
        from rigbox.rooms import require_owner
        require_owner(row["tenant_id"])
        return row

    async def authenticate_admin(self, raw_key: str) -> dict[str, Any]:
        """An admin key from the keys table. No login. A publishable key is refused."""
        presented = raw_key or ""
        digest = hash_key(presented)
        if not self.limiter.allow(digest):
            raise RigError("rate_limit", "Too many requests. Try again in a minute.", 429)
        if not presented:
            raise RigError("unauthorized", "That key is not valid.", 401)
        row = await self.store.key(digest)
        if row is not None:
            from rigbox.rooms import require_owner
            require_owner(row["tenant_id"])
            if row.get("kind") != "admin":
                raise RigError("forbidden", "That key cannot manage fits.", 403)
            return row
        env_key = os.environ.get("RIG_ADMIN_KEY") or ""
        if env_key and hmac.compare_digest(presented, env_key):
            with tenant_scope(None, administrative=True):
                tenants = await self.store.tenant_ids()
            if len(tenants) == 1:
                return {"tenant_id": tenants[0], "kind": "admin", "origins": []}
        raise RigError("unauthorized", "That key is not valid.", 401)

    async def search(
        self,
        tenant_id: str,
        query: str,
        limit: int | None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Both /v1/rig/search entry paths share Workspace's 200-character limit.
        query = " ".join((query or "").split())[:200]
        result = await self._search(tenant_id, query, limit, filters)
        if (query or "").strip() and not result.get("results"):
            # Include cached misses: each completed search is a distinct event.
            await self.store.training_event(tenant_id, "rig_no_result", uuid4().hex,
                                            {"query": query.strip(), "filters": filters or {}}, {"results": []})
        return result

    async def _search(
        self,
        tenant_id: str,
        query: str,
        limit: int | None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        text = (query or "").strip()
        shop = await self._shop(tenant_id)
        if shop.get("search"):
            from rigbox.guided_search import shop_search

            cap = _limit(limit)
            version = await self._catalogue_version(tenant_id)
            products = await self._shopper_products(tenant_id)
            fits = await self._approved_fits(tenant_id)
            body = shop_search(
                {
                    "q": text,
                    "limit": cap,
                    "products": products,
                    "fits": fits,
                    "filters": filters or {},
                    "shop": shop,
                }
            )
            # Build Spec: every finished shopper search is kept (stand-in catalogues).
            host, pieces = parts_of(text) if text else ("", [])
            if pieces:
                part_rows = [
                    {
                        "part": piece,
                        "ids": [str(item.get("id") or "") for item in (body.get("results") or []) if isinstance(item, dict)],
                        "exact_match": bool(body.get("exact_match", True)),
                        "message": body.get("message"),
                        "limit": cap,
                    }
                    for piece in pieces
                ]
                await self._keep_search(tenant_id, version, text, host, pieces, cap, body, part_rows)
            return body
        if not text:
            return {"results": []}
        host, pieces = parts_of(text)
        if not pieces:
            return {"results": []}
        cap = _limit(limit)
        version = await self._catalogue_version(tenant_id)
        saved = await self.store.searches_for(tenant_id, version, recall_days())
        wholes, known = latest(saved)
        whole = whole_hit(wholes, host, pieces, cap)
        if whole is not None:
            body = await self._body_from_saved(tenant_id, whole, pieces, host, cap)
            if body is not None:
                return body
        products = await self._shopper_products(tenant_id)
        reusable = False
        for piece in pieces:
            if part_hit(known, host, piece, cap) is not None:
                reusable = True
                break
        if not reusable:
            ranked = self._rank(tenant_id, products, text, cap)
            part_rows = await self._parts_from_first(tenant_id, products, host, pieces, ranked, cap)
            await self._keep_search(tenant_id, version, text, host, pieces, cap, ranked, part_rows)
            return _shopper_body(ranked, host, part_rows, [False] * len(part_rows))
        part_rows: list[dict[str, Any]] = []
        reused_flags: list[bool] = []
        for piece in pieces:
            hit = part_hit(known, host, piece, cap)
            taken = None if hit is None else await self._take(tenant_id, list(hit.get("ids") or [])[:cap])
            if hit is None or taken is None:
                ranked_part = self._rank(tenant_id, products, part_query(piece, host), cap)
                part_rows.append(_part_from_rank(piece, ranked_part, cap))
                reused_flags.append(False)
                continue
            part_rows.append(
                {
                    "part": piece,
                    "ids": [str(item) for item in hit.get("ids") or []],
                    "exact_match": bool(hit.get("exact_match", True)),
                    "message": hit.get("message"),
                    "limit": int(hit.get("limit") or cap),
                }
            )
            reused_flags.append(True)
        merged = _merge_parts(part_rows, cap)
        ids = [str(item["id"]) for item in merged["results"]]
        taken = await self._take(tenant_id, ids)
        if taken is None:
            ranked = self._rank(tenant_id, products, text, cap)
            part_rows = await self._parts_from_first(tenant_id, products, host, pieces, ranked, cap)
            await self._keep_search(tenant_id, version, text, host, pieces, cap, ranked, part_rows)
            return _shopper_body(ranked, host, part_rows, [False] * len(part_rows))
        public = {"results": taken, "exact_match": merged["exact_match"], "message": merged["message"]}
        await self._keep_search(tenant_id, version, text, host, pieces, cap, public, part_rows)
        return _shopper_body(public, host, part_rows, reused_flags)

    def _rank(self, tenant_id: str, products: list[dict[str, Any]], text: str, cap: int) -> dict[str, Any]:
        return rank_room({"tenant_id": tenant_id, "q": text, "limit": cap, "products": products})

    async def _parts_from_first(
        self,
        tenant_id: str,
        products: list[dict[str, Any]],
        host: str,
        pieces: list[str],
        ranked: dict[str, Any],
        cap: int,
    ) -> list[dict[str, Any]]:
        if len(pieces) == 1:
            return [_part_from_rank(pieces[0], ranked, cap)]
        rows: list[dict[str, Any]] = []
        for piece in pieces:
            rows.append(_part_from_rank(piece, self._rank(tenant_id, products, part_query(piece, host), cap), cap))
        return rows

    async def _keep_search(
        self,
        tenant_id: str,
        version: str,
        text: str,
        host: str,
        pieces: list[str],
        cap: int,
        ranked: dict[str, Any],
        part_rows: list[dict[str, Any]],
    ) -> None:
        ids = [str(item["id"]) for item in ranked.get("results") or []]
        exact, message = _exact_and_message(ranked)
        await self.store.add_search(
            tenant_id,
            version,
            {
                "id": uuid4().hex,
                "query": text,
                "host": host,
                "signature": signature(pieces),
                "limit": cap,
                "ids": ids,
                "exact_match": exact if ids else True,
                "message": message,
                "parts": part_rows,
            },
        )

    async def _body_from_saved(
        self,
        tenant_id: str,
        whole: dict[str, Any],
        pieces: list[str],
        host: str,
        cap: int,
    ) -> dict[str, Any] | None:
        ids = [str(item) for item in whole.get("ids") or []][:cap]
        taken = await self._take(tenant_id, ids)
        if taken is None:
            return None
        if not taken:
            return {"results": []}
        stored_parts = {str(part.get("part") or ""): part for part in whole.get("parts") or []}
        part_rows = [stored_parts.get(piece) or {"part": piece, "ids": [], "limit": cap} for piece in pieces]
        flags = [True] * len(pieces)
        ranked = {
            "results": taken,
            "exact_match": bool(whole.get("exact_match", True)),
            "message": whole.get("message"),
        }
        return _shopper_body(ranked, host, part_rows, flags)

    async def _take(self, tenant_id: str, ids: list[str]) -> list[dict[str, Any]] | None:
        if not ids:
            return []
        by_id: dict[str, dict[str, Any]] = {}
        for row in await supply_catalogue(self.store, tenant_id):
            if row.get("active", True) is False:
                continue
            public = public_product(row)
            by_id[public["id"]] = public
        found: list[dict[str, Any]] = []
        for sku in ids:
            item = by_id.get(sku)
            if item is None:
                return None
            found.append(item)
        return found

    async def expire_searches(self) -> int:
        """Delete saved searches older than the recall window. Every retailer."""
        return await self.store.expire_searches(recall_days())

    async def product(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        detail = product_detail(await self._bundle(tenant_id, product_id))
        if detail is None:
            raise RigError("not_found", "No such product.", 404)
        shop = await self._shop(tenant_id)
        if shop.get("search"):
            from rigbox.guided_search import present_detail

            return present_detail(
                detail,
                await self._active(tenant_id),
                await self._approved_fits(tenant_id),
                str(shop.get("centre_sku") or ""),
                vocabulary=shop.get("vocabulary"),
            )
        return detail

    async def setups(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        version = await self._catalogue_version(tenant_id)
        cached = await self.store.get_answer(tenant_id, "setups", product_id, version)
        if cached is not None:
            return cached
        bundle = await self._bundle(tenant_id, product_id)
        if product_detail(bundle) is None:
            raise RigError("not_found", "No such product.", 404)
        built = build_setups(bundle)
        if (await self._shop(tenant_id)).get("search"):
            from rigbox.guided_search import enrich_setups

            built = enrich_setups(built, bundle["products"], bundle["fits"], vocabulary=(await self._shop(tenant_id)).get("vocabulary"))
        centre = _centre(bundle["products"], product_id)
        for setup in built["setups"]:
            setup["why"] = await self._compose_why(
                tenant_id, centre, setup, bundle["products"], bundle["fits"]
            )
        body = {"setups": built["setups"]}
        await self.store.put_answer(tenant_id, "setups", product_id, version, body)
        return body

    async def swap(self, tenant_id: str, setup: dict[str, Any], role: str) -> dict[str, Any]:
        products = await self._active(tenant_id)
        fits = await self._approved_fits(tenant_id)
        try:
            priced = reprice(setup, products)
        except KeyError:
            raise RigError("invalid", "That setup is not in this catalogue.", 422) from None
        return {
            "options": options_for({"role": role, "setup": priced, "products": products, "fits": fits}),
            "setup": priced,
        }

    async def graph(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        version = await self._catalogue_version(tenant_id)
        cached = await self.store.get_answer(tenant_id, "graph", product_id, version)
        if cached is not None:
            return cached
        bundle = await self._bundle(tenant_id, product_id)
        built = build_graph(bundle)
        if built is None:
            raise RigError("not_found", "No such product.", 404)
        await self._annotate_graph(tenant_id, bundle, built)
        if (await self._shop(tenant_id)).get("search"):
            from rigbox.guided_search import enrich_graph

            built = enrich_graph(built, bundle["products"], bundle["fits"], vocabulary=(await self._shop(tenant_id)).get("vocabulary"))
        await self.store.put_answer(tenant_id, "graph", product_id, version, built)
        return built

    async def ask(
        self,
        tenant_id: str,
        product_id: str,
        question: str,
        setup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(question or "").strip():
            raise RigError("invalid", "Ask a question about this catalogue.", 422)
        if product_detail(await self._bundle(tenant_id, product_id)) is None:
            raise RigError("not_found", "No such product.", 404)
        products = []
        for row in await self._active(tenant_id):
            item = public_product(row)
            # The model and the grounded sentence only see shopper-safe facts.
            item["attributes"] = shopper_attributes(item["attributes"])
            item["family"] = str(row.get("family") or "")
            products.append(item)
        fits = await self._approved_fits(tenant_id)
        payload = {"question": question, "products": products, "fits": fits, "product_id": product_id, "setup": setup}
        # Subject words must be whole tokens on a row. The model only names rows.
        # The sentence is built from that row, or from one approved fit.
        if not answerable(question, products, fits):
            return {"answer": "I don't know", "cites": []}
        if self.models is None:
            return {"answer": "I don't know", "cites": []}
        text = await self.models.complete(
            tenant_id,
            "rig.ask",
            [{"role": "user", "content": select_prompt(payload)}],
        )
        return grounded_answer(payload, text)

    async def link_quote(self, tenant_id: str, url: str, note: str) -> dict[str, str]:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RigError("invalid", "That link is not a web address.", 422)
        return await self._store_quote(
            tenant_id,
            {"kind": "link", "url": parsed.geturl(), "note": str(note or "").strip()},
            {},
        )

    async def quote(self, tenant_id: str, body: dict[str, Any]) -> dict[str, str]:
        payload, contact = await self.quote_payload(tenant_id, body)
        return await self._store_quote(tenant_id, payload, contact)

    async def quote_payload(self, tenant_id: str, body: dict[str, Any]) -> tuple[dict, dict]:
        contact = _contact(body.get("contact") or {})
        if isinstance(body.get("setup"), dict):
            products = await self._active(tenant_id)
            try:
                priced = reprice(body["setup"], products)
            except KeyError:
                raise RigError("invalid", "That setup is not in this catalogue.", 422) from None
            _restore_choices(priced, body["setup"])
            shop = await self._shop(tenant_id)
            if shop.get("search"):
                from rigbox.guided_search import prepare_reservation

                problem = prepare_reservation(
                    priced,
                    products,
                    await self._approved_fits(tenant_id),
                    str(shop.get("centre_sku") or ""),
                    vocabulary=shop.get("vocabulary"),
                )
                if problem:
                    raise RigError("invalid", problem, 422)
            payload: dict[str, Any] = {"kind": "setup", "setup": priced}
        elif body.get("product_id"):
            product = await self.product(tenant_id, str(body["product_id"]))
            shop = await self._shop(tenant_id)
            if shop.get("search"):
                from rigbox.guided_search import prepare_reservation

                problem = prepare_reservation(
                    {"parts": [{"product": product, "choice": ""}]},
                    await self._active(tenant_id),
                    await self._approved_fits(tenant_id),
                    str(shop.get("centre_sku") or ""),
                    vocabulary=shop.get("vocabulary"),
                )
                if problem:
                    raise RigError("invalid", problem, 422)
            payload = {"kind": "product", "product": product}
        else:
            raise RigError("invalid", "A setup or a product is required.", 422)
        # A visitor request is metadata, never an invented price or invoice.
        request = {key: body[key] for key in ("title", "request", "quantity") if body.get(key) is not None}
        if request:
            payload["request"] = request
        wanted = ([part["product"]["id"] for part in payload["setup"]["parts"]]
                  if payload["kind"] == "setup" else [payload["product"]["id"]])
        private = []
        # Optional per store: no supply lookup at all until the store opts in.
        rows = await self.store.products(tenant_id) if await locate_enabled(self.store, tenant_id) else []
        for row in rows:
            if str(row["sku"]) in wanted and needs_supply(row):
                result = await supply_locate(self.store, tenant_id, row)
                private.append({"product_id": row["sku"], **result})
        if private:
            payload["locate"] = private
        retailer = await self.store.retailer(tenant_id) or {}
        own_delivery = retailer.get("delivery") or {}
        catalogue_rows = {str(row["sku"]): row for row in await self.store.products(tenant_id)}
        located = {str(line["product_id"]): line["public"] for line in private}
        items = ([part["product"] for part in payload["setup"]["parts"]]
                 if payload["kind"] == "setup" else [payload["product"]])
        for item in items:
            row = catalogue_rows.get(str(item["id"]), {})
            if needs_supply(row):
                offer = located.get(str(item["id"]), {})
                item["delivery"] = {"ship_days": offer.get("ship_days"), "eta": offer.get("eta"),
                                    "back_order": offer.get("status") != "available_to_order"}
            else:
                item["delivery"] = {"min_days": own_delivery.get("min_days"),
                                    "max_days": own_delivery.get("max_days"), "back_order": False}
        return payload, contact

    async def _locate_row(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        if not await locate_enabled(self.store, tenant_id):
            raise RigError("locate_disabled", "This store does not offer ordering from other stores.", 403)
        row = next((p for p in await self.store.products(tenant_id) if p["sku"] == product_id), None)
        if row is None:
            raise RigError("not_found", "No such product.", 404)
        return row

    async def locate(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        row = await self._locate_row(tenant_id, product_id)
        return (await supply_locate(self.store, tenant_id, row))["public"]

    async def locate_confirm(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        """Live re-check of the located offer, then a short-lived signed hold."""
        from rigbox.locate_live import LocateRefused, confirm, hold_secret

        row = await self._locate_row(tenant_id, product_id)
        if not needs_supply(row):
            raise RigError("invalid", "This product is available from the store.", 422)
        if self.locate_checker is None:
            raise RigError("unavailable", "Live stock checks are not available.", 503)
        try:
            secret = hold_secret()
        except LocateRefused:
            raise RigError("unavailable", "Live stock checks are not available.", 503) from None
        return (await confirm(self.store, tenant_id, row, self.locate_checker, secret=secret))["public"]

    async def compare(self, tenant_id: str, product_ids: list[str]) -> dict[str, Any]:
        """Other sellers' prices for a selection the shopper already made.

        Search and the accessory list do not call this. No figure is invented:
        a sku with no rows comes back without a market price.
        """
        wanted = [str(sku) for sku in product_ids if str(sku).strip()]
        if not wanted:
            raise RigError("invalid", "Choose a product first.", 422)
        products = {str(row.get("sku") or row.get("id") or ""): row for row in await self._active(tenant_id)}
        chosen: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sku in wanted:
            row = products.get(sku)
            if row is None or sku in seen:
                continue
            seen.add(sku)
            chosen.append(row)
        if not chosen:
            raise RigError("not_found", "No such product.", 404)
        retailer = await self.store.retailer(tenant_id) or {}
        retailer_name = str(retailer.get("name") or "")
        observations = await self.store.market_for(tenant_id, [str(row.get("sku") or "") for row in chosen])
        by_sku: dict[str, list[dict[str, Any]]] = {}
        for row in observations:
            if retailer_name and str(row.get("source") or "").casefold() == retailer_name.casefold():
                continue
            by_sku.setdefault(str(row.get("sku") or ""), []).append(row)
        lines: list[dict[str, Any]] = []
        for row in chosen:
            public = public_product(row)
            lines.append(
                {
                    "id": public["id"],
                    "name": public["name"],
                    "price": public["price"],
                    "market": _market_average(by_sku.get(public["id"], []), str(public["price"].get("currency") or "")),
                }
            )
        return {"retailer": retailer_name, "lines": lines}

    async def suggest(self, tenant_id: str) -> dict[str, Any]:
        if self.models is None:
            return {"fits": []}
        slim = [
            {
                "id": row["sku"],
                "sku": row["sku"],
                "name": row["name"],
                "family": row.get("family") or "",
                "attributes": row.get("attributes") or {},
            }
            for row in await self._active(tenant_id)
        ]
        text = await self.models.complete(
            tenant_id,
            "rig.fit_suggest",
            [{"role": "user", "content": suggest_prompt({"products": slim})}],
        )
        suggested = fit_suggest({"products": slim}, text)
        async with self.store.atomic(tenant_id) as store:
            reviewed = {_fit_key(row) for row in await store.fits(tenant_id)
                        if row.get("approved") or row.get("source") == "rejected"}
            for row in suggested["fits"]:
                if _fit_key(row) not in reviewed:
                    await store.upsert_fit(tenant_id, row)
        return suggested

    async def approve(self, tenant_id: str, part_sku: str, fits_sku: str | None, fits_family: str | None) -> bool:
        async with self.store.atomic(tenant_id) as store:
            return await store.approve_fit(tenant_id, part_sku, fits_sku, fits_family)

    async def reject(self, tenant_id: str, part_sku: str, fits_sku: str | None, fits_family: str | None) -> bool:
        async with self.store.atomic(tenant_id) as store:
            return await store.reject_fit(tenant_id, part_sku, fits_sku, fits_family)

    async def suggested_fits(self, tenant_id: str) -> dict[str, Any]:
        names = {
            str(row.get("sku") or row.get("id") or ""): str(row.get("name") or "")
            for row in await supply_catalogue(self.store, tenant_id)
        }
        rows: list[dict[str, Any]] = []
        for row in await self.store.fits(tenant_id):
            if str(row.get("source") or "") != "suggested" or row.get("approved"):
                continue
            part_sku = str(row.get("part_sku") or "")
            fits_sku = row.get("fits_sku") or None
            fits_family = row.get("fits_family") or None
            if fits_sku:
                fits_with = names.get(str(fits_sku)) or str(fits_sku)
            else:
                fits_with = str(fits_family or "")
            rows.append(
                {
                    "part_sku": part_sku,
                    "part_name": names.get(part_sku) or part_sku,
                    "fits_sku": fits_sku,
                    "fits_family": fits_family,
                    "fits_with": fits_with,
                    "confidence": _confidence(row.get("confidence")),
                    "note": str(row.get("note") or ""),
                    "rule": row.get("rule") if isinstance(row.get("rule"), dict) else {},
                }
            )
        rows.sort(key=lambda item: (item["part_sku"], item["fits_sku"] or "", item["fits_family"] or ""))
        return {"fits": rows}

    async def apply_watch(self, tenant_id: str, sku: str, update: dict[str, Any]) -> bool:
        patch = price_patch(update)
        if not patch:
            return False
        async with self.store.atomic(tenant_id) as store:
            if await store.is_feed_product(tenant_id, sku):
                return False
            url, stamp = update.get("url"), update.get("observed_at")
            if url is not None or stamp is not None:
                from datetime import datetime
                if not isinstance(url, str) or not isinstance(stamp, str):
                    raise ValueError("A page and observation time are required.")
                stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("An observation timezone is required.")
                eligible = await store.watch_products(tenant_id)
                if not any(row["sku"] == sku and row["url"] == url for row in eligible):
                    return False
                applied = await store.watch_applied_at(tenant_id, sku, url)
                if applied is not None and stamp <= applied:
                    return True  # An acknowledged retry cannot regress a newer observation.
            changed = await store.update_price(tenant_id, sku, patch)
            if changed and url is not None:
                await store.mark_watch_applied(tenant_id, sku, url, stamp)
            return changed


    async def pull_feeds(self, tenant_id: str, fetch: Any) -> list[dict[str, Any]]:
        reports = []
        for feed in await self.store.feeds_for(tenant_id):
            fmt = str(feed.get("format") or "")
            mapper = _MAPS.get(fmt)
            if mapper is None:
                reports.append(
                    {"format": fmt, "added": 0, "updated": 0, "rejected": [{"row": 0, "reason": "unknown feed format"}]}
                )
                continue
            body = await fetch(str(feed["url"]))
            if fmt == "google":
                mapped = mapper(body if isinstance(body, str) else str(body))
            else:
                mapped = mapper(body, str(feed.get("currency") or ""))
            if any(item.get("row") == 0 for item in mapped["rejected"]):
                reports.append({"format": fmt, "added": 0, "updated": 0, "rejected": mapped["rejected"]})
                continue
            added = updated = 0
            async with self.store.atomic(tenant_id) as store:
                for row in mapped["rows"]:
                    action = await store.upsert_product(tenant_id, row)
                    if action == "added":
                        added += 1
                    elif action == "updated":
                        updated += 1
                await store.index_feed_products(tenant_id, str(feed["url"]), [row["sku"] for row in mapped["rows"]],
                                                complete=not mapped["rejected"])
            reports.append({"format": fmt, "added": added, "updated": updated, "rejected": mapped["rejected"]})
        return reports

    async def _bundle(self, tenant_id: str, product_id: str) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "product_id": product_id,
            "products": await self._active(tenant_id),
            "fits": await self._approved_fits(tenant_id),
            "templates": await self.store.templates_for(tenant_id),
        }

    async def _shopper_products(self, tenant_id: str) -> list[dict[str, Any]]:
        """Catalogue rows for shopper search, ranking and answers: attributes allowlisted.

        rigbox.logic (mirrored into Skala) indexes every attribute it is given,
        so private keys are removed here, before any search or setup sees a row.
        """
        return shopper_rows(await supply_catalogue(self.store, tenant_id))

    async def _active(self, tenant_id: str) -> list[dict[str, Any]]:
        return [row for row in await self._shopper_products(tenant_id) if row.get("active", True)]

    async def _approved_fits(self, tenant_id: str) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for row in await self.store.fits(tenant_id):
            if str(row.get("source") or "") == "rejected":
                continue
            if approved(row):
                kept.append(row)
        return kept

    async def _catalogue_version(self, tenant_id: str) -> str:
        products = [_version_product(row) for row in await supply_catalogue(self.store, tenant_id)]
        fits = [_version_fit(row) for row in await self._approved_fits(tenant_id)]
        fits.sort(key=lambda row: (row["part_sku"], row["fits_sku"], row["fits_family"]))
        payload = {
            "products": products,
            "fits": fits,
            "templates": await self.store.templates_for(tenant_id),
        }
        raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _compose_why(
        self,
        tenant_id: str,
        centre: dict[str, Any],
        setup: dict[str, Any],
        products: list[dict[str, Any]],
        fits: list[dict[str, Any]],
    ) -> list[str]:
        pool = fitting_parts(centre, products, fits)
        by_sku = {str(part.get("sku") or part.get("id") or ""): (part, fit) for part, fit in pool}
        header = f"Fits {centre.get('name')}"
        lines = [header]
        for slot in (setup.get("parts") or [])[1:]:
            sku = str((slot.get("product") or {}).get("id") or "")
            pair = by_sku.get(sku)
            if pair is None:
                continue
            for line in await self._fit_why(tenant_id, centre, pair[0], pair[1]):
                if line not in lines:
                    lines.append(line)
        return lines

    async def _fit_why(
        self,
        tenant_id: str,
        centre: dict[str, Any],
        part: dict[str, Any],
        fit: dict[str, Any],
    ) -> list[str]:
        fit_id = _fit_identity(fit)
        digest = _rule_note_hash(fit)
        stored = await self.store.get_fit_why(tenant_id, fit_id, digest)
        if stored is not None:
            return stored
        header = f"Fits {centre.get('name')}"
        specific = [line for line in code_why(centre, [(part, fit)]) if line != header]
        if specific:
            await self.store.put_fit_why(tenant_id, fit_id, digest, specific)
            return specific
        if self.models is None:
            return []
        material = [
            {
                "role": str(part.get("category") or ""),
                "id": str(part.get("sku") or part.get("id") or ""),
                "name": str(part.get("name") or ""),
            }
        ]
        try:
            text = await self.models.complete(
                tenant_id,
                "rig.explain",
                [{"role": "user", "content": explain_prompt({"material": material})}],
            )
        except Exception:  # noqa: BLE001 — a model outage keeps the plain Fits line
            return []
        why = [line for line in ground_explain({"fallback_why": []}, text)["why"] if line != header]
        if not why:
            return []
        await self.store.put_fit_why(tenant_id, fit_id, digest, why)
        return why

    async def _annotate_graph(self, tenant_id: str, bundle: dict[str, Any], built: dict[str, Any]) -> None:
        """Add the role name and one why-line to each approved fit on the graph."""
        product_id = str(bundle.get("product_id") or "")
        centre = _centre(bundle.get("products") or [], product_id)
        pool = fitting_parts(centre, bundle.get("products") or [], bundle.get("fits") or [])
        by_sku = {str(part.get("sku") or part.get("id") or ""): (part, fit) for part, fit in pool}
        roles = _roles_for(bundle.get("templates"), str(centre.get("category") or ""))
        for branch in built.get("branches") or []:
            category = str(branch.get("category") or "")
            branch["role"] = roles.get(category, category)
            for product in branch.get("products") or []:
                pair = by_sku.get(str(product.get("id") or ""))
                if pair is None:
                    product["why"] = ""
                    continue
                lines = await self._fit_why(tenant_id, centre, pair[0], pair[1])
                product["why"] = " · ".join(line for line in lines if line)



    async def _store_quote(self, tenant_id: str, payload: dict[str, Any], contact: dict[str, Any]) -> dict[str, Any]:
        quote_id = "req_" + uuid4().hex
        await self.store.add_quote(tenant_id, quote_id, payload, contact)
        retailer = await self.store.retailer(tenant_id) or {}
        shop = await self._shop(tenant_id)
        mails = bool(shop.get("mail_reservations"))
        letter = reservation_letter(quote_id, payload, contact, retailer) if mails else None
        try:
            await self.handoff.send(
                tenant_id=tenant_id,
                email=retailer.get("email"),
                webhook=retailer.get("webhook"),
                quote={"id": quote_id, "payload": payload, "contact": contact},
                letter=letter,
            )
        except Exception:  # noqa: BLE001 — the quote is stored even if the handoff fails
            if mails:
                return {"request_id": quote_id, "delivered": False}
            return {"request_id": quote_id}
        await self.store.mark_sent(tenant_id, quote_id)
        if mails:
            return {"request_id": quote_id, "delivered": True}
        return {"request_id": quote_id}

    async def _shop(self, tenant_id: str) -> dict[str, Any]:
        retailer = await self.store.retailer(tenant_id) or {}
        shop = retailer.get("shop")
        if not isinstance(shop, dict):
            return {}
        if "vocabulary" in shop:
            from rigbox.vocabulary import validate
            checked = validate(shop["vocabulary"])
            return {**shop, "vocabulary": checked} if checked else {key: value for key, value in shop.items() if key != "vocabulary"}
        return shop


def _market_average(rows: list[dict[str, Any]], currency: str) -> dict[str, Any] | None:
    code = currency.upper()
    amounts: list[Decimal] = []
    for row in rows:
        if str(row.get("currency") or "").upper() != code or not code:
            continue
        amounts.append(Decimal(str(row.get("price"))))
    if not amounts:
        return None
    mean = sum(amounts, Decimal("0")) / Decimal(len(amounts))
    return {
        "amount": money(mean),
        "currency": code,
        "indicative": True,
        "low": money(min(amounts)),
        "high": money(max(amounts)),
        "sources": len(amounts),
    }


def _restore_choices(priced: dict[str, Any], original: dict[str, Any]) -> None:
    choices: dict[str, str] = {}
    for part in original.get("parts") or []:
        product = part.get("product") if isinstance(part.get("product"), dict) else {}
        sku = str(product.get("id") or "")
        choice = " ".join(str(part.get("choice") or "").replace('\n', " ").replace('\r', " ").split())
        if sku and choice:
            choices[sku] = choice[:200]
    for part in priced.get("parts") or []:
        product = part.get("product") if isinstance(part.get("product"), dict) else {}
        sku = str(product.get("id") or "")
        if sku in choices:
            part["choice"] = choices[sku]


def _roles_for(templates: Any, category: str) -> dict[str, str]:
    raw: Any = None
    if isinstance(templates, dict):
        raw = templates.get(category)
    if isinstance(raw, dict) and "roles" in raw and "tiers" not in raw:
        raw = raw.get("roles")
    tiers: list[Any] = []
    if isinstance(raw, dict) and isinstance(raw.get("tiers"), list):
        tiers = list(raw["tiers"])
    elif isinstance(raw, list):
        tiers = list(raw)
    found: dict[str, str] = {}
    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        for role in tier.get("roles") or []:
            if not isinstance(role, dict):
                continue
            name = str(role.get("role") or role.get("category") or "")
            cat = str(role.get("category") or name)
            if cat and cat not in found:
                found[cat] = name or cat
    return found


def _centre(products: list[dict[str, Any]], product_id: str) -> dict[str, Any]:
    for row in products:
        if str(row.get("sku") or row.get("id") or "") == product_id:
            return row
    return {}


def _version_product(row: dict[str, Any]) -> dict[str, Any]:
    attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    price = row.get("price")
    if isinstance(price, dict):
        price = price.get("amount")
    return {
        "sku": str(row.get("sku") or row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "brand": str(row.get("brand") or ""),
        "category": str(row.get("category") or ""),
        "price": str(price),
        "currency": str(row.get("currency") or ""),
        "family": str(row.get("family") or ""),
        "image": str(row.get("image_url") or row.get("image") or ""),
        "active": bool(row.get("active", True)),
        "attributes": attributes,
        "availability": row.get("availability"),
    }


def _version_fit(row: dict[str, Any]) -> dict[str, Any]:
    rule = row.get("rule") if isinstance(row.get("rule"), dict) else {}
    return {
        "part_sku": str(row.get("part_sku") or ""),
        "fits_sku": str(row.get("fits_sku") or ""),
        "fits_family": str(row.get("fits_family") or ""),
        "rule": rule,
        "note": str(row.get("note") or ""),
        "source": str(row.get("source") or ""),
    }


def _fit_identity(fit: dict[str, Any]) -> str:
    return "\x1f".join(
        [
            str(fit.get("part_sku") or ""),
            str(fit.get("fits_sku") or ""),
            str(fit.get("fits_family") or ""),
        ]
    )


def _rule_note_hash(fit: dict[str, Any]) -> str:
    rule = fit.get("rule") if isinstance(fit.get("rule"), dict) else {}
    payload = json.dumps(
        {"rule": rule, "note": str(fit.get("note") or "")},
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _confidence(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any, limit: int) -> str:
    value = str(value or "")
    if len(value) > limit:
        raise RigError("invalid", "A contact field or note is too long.", 422)
    return "".join(c for c in value if not unicodedata.category(c).startswith("C")).strip()


def _contact(contact: dict[str, Any]) -> dict[str, str]:
    name = _clean_text(contact.get("name"), 120)
    email = _clean_text(contact.get("email"), 254)
    domain = email.split("@")[-1] if "@" in email else ""
    if not name or email.count("@") != 1 or "." not in domain or any(c.isspace() for c in email):
        raise RigError("invalid", "A name and email are required.", 422)
    out = {"name": name, "email": email}
    phone = _clean_text(contact.get("phone"), 40)
    if phone:
        out["phone"] = phone
    return out

