"""Store-configured guided shopping over verified catalogue rows."""

from __future__ import annotations
from rigbox.vocabulary import (
    scoped as _scoped,
    value as _value,
    term as _term,
    pattern as _pattern,
    pattern_text as _pattern_text,
    say as _say,
)
import re
from typing import Any
from rigbox.logic import (
    _active,
    _limit,
    _similarity,
    _tokens,
    approved,
    money,
    public_product,
)
from rigbox.rank import _FILLER
from rigbox.rank import _FUZZY, _common_english, _intent, _shopping_word
from rigbox.rank import search as rank_search
from rigbox.shopper_attributes import shopper_attributes

_ALL_PARTS = "*"
_IN_STOCK = frozenset({"in_stock", "low_stock"})
_BOUND = re.compile(
    "\\b(under|below|less than|up to|over|above|more than) (?:r ?)?(\\d[\\d ]*)",
    re.IGNORECASE,
)
_IN = re.compile("\\bin stock(?: only)?\\b", re.IGNORECASE)


@_scoped
def shop_search(payload: dict[str, Any]) -> dict[str, Any]:
    raw = str(payload.get("q") or "").strip()
    limit = _limit(payload.get("limit"))
    filters = dict(payload.get("filters") or {})
    products = [row for row in payload.get("products") or [] if _active(row)]
    fits = [row for row in payload.get("fits") or [] if approved(row)]
    shop = payload.get("shop") if isinstance(payload.get("shop"), dict) else {}
    centre_sku = str(shop.get("centre_sku") or "")
    asked = _normalize_shopper(raw, products)
    pack_note = _pack_note(raw)
    query, loose, price_sort = _prepare_query(asked)
    if price_sort:
        filters["sort"] = price_sort
    outsider = _named_outsider(query, shop.get("not_stocked") or [])
    if outsider:
        return {
            "results": [],
            "ids": [],
            "exact_match": False,
            "honest": "not_stocked",
            "message": _say("shop_search", outsider),
            "focus_id": "",
            "groups": [],
        }
    if not query and (not _has_filter(filters)):
        return {
            "results": [],
            "ids": [],
            "exact_match": True,
            "honest": "empty",
            "focus_id": "",
            "groups": [],
        }
    colour_note = _bare_colour(asked, products)
    if colour_note:
        centres = [
            row
            for row in products
            if str(row.get("category") or "") == _value("centre_category", "")
        ]
        return _present_closest(
            centres, _default_centre(products), colour_note, products, fits, filters
        )
    fitted = _fit_answer(raw, products, fits, filters, limit)
    if fitted is not None:
        return fitted
    matched_length = _length_answer(asked, products, fits, filters, limit)
    if matched_length is not None:
        return matched_length
    many = _multi_answer(query, products, fits, filters, limit)
    if many is not None:
        return many
    cleaned, parsed = _parse_constraints(query)
    cleaned = _strip_size(cleaned)
    filters = _merge(filters, parsed)
    filter_only = False
    if not cleaned:
        ranked_rows = list(products)
        exact = True
        message = ""
        filter_only = True
    else:
        ranked = rank_search(
            {
                "q": cleaned,
                "products": products,
                "limit": max(limit, 50),
                "model_letters": True,
            }
        )
        by_sku = {_sku(row): row for row in products}
        ranked_rows = [
            by_sku[item] for item in ranked.get("ids") or [] if item in by_sku
        ]
        if not ranked_rows and _category_only(cleaned) == _ALL_PARTS:
            ranked_rows = [
                row
                for row in products
                if str(row.get("category") or "") in _value("part_categories", [])
            ]
            ranked = {"exact_match": True}
        if not ranked_rows:
            found = _known_gap(asked, products, fits)
            if found is not None:
                rows, host, note = found
                return _gap_body(rows, host, note, products, fits, filters, limit)
            unsold = _unsold_results(asked, products, fits, filters)
            if unsold is not None:
                return unsold
            return _no_hits(cleaned, products, shop)
        exact = bool(ranked.get("exact_match", True))
        message = str(ranked.get("message") or "")
    if (
        ranked_rows
        and (not exact)
        and (not _variant_miss(message, products))
        and (not _size_miss(message))
    ):
        found = _known_gap(asked, products, fits)
        if found is not None:
            rows, host, note = found
            return _gap_body(rows, host, note, products, fits, filters, limit)
        return _no_hits(cleaned or query, products, shop)
    if not exact and _size_miss(message):
        exact = True
        message = ""
    sought, host = _intent(cleaned) if cleaned else ("", "")
    relation = bool(host)
    category_only = _category_only(cleaned)
    centre_default = _centre_row(products, centre_sku)
    if filter_only or not ranked_rows:
        focus = None
    else:
        focus = _pick_focus(sought if relation else cleaned, ranked_rows)
    if filter_only:
        chosen = ranked_rows
        honest = "exact"
    elif ranked_rows and (not exact):
        chosen = _closest_rows(cleaned, ranked_rows)
        focus = _pick_focus(cleaned, chosen) if chosen else focus
        honest = "closest"
    elif category_only == _ALL_PARTS:
        chosen = [
            row
            for row in products
            if str(row.get("category") or "") in _value("part_categories", [])
        ]
        focus = _fitting_focus(cleaned, chosen, centre_default, fits)
        honest = "exact"
    elif category_only:
        chosen = [
            row for row in products if str(row.get("category") or "") == category_only
        ]
        focus = _fitting_focus(cleaned, chosen, centre_default, fits)
        honest = "exact"
    elif relation and _phrase_category(sought):
        host_pool = _closest_rows(host, products) or list(products)
        host_row = _pick_focus(host, host_pool)
        chosen = _related(host_row, products, fits, _phrase_category(sought))
        focus = _fitting_focus(sought, chosen, host_row, fits)
        honest = "exact"
    elif (
        cleaned
        and exact
        and (not relation)
        and _colour_host(cleaned, products, focus)[0]
    ):
        message, colour_host = _colour_host(cleaned, products, focus)
        chosen = _closest_rows(cleaned, products)
        if colour_host is not None:
            focus = colour_host
            if not any((_sku(row) == _sku(colour_host) for row in chosen)):
                chosen = [colour_host, *chosen]
        else:
            focus = _pick_focus(cleaned, chosen) if chosen else focus
        honest = "closest"
    elif cleaned and exact and (not relation) and _wanted_part(cleaned):
        host_row = (
            focus
            if focus is not None
            and str(focus.get("category") or "") == _value("centre_category", "")
            else centre_default
        )
        wanted = _wanted_part(cleaned)
        if wanted in _value("part_categories", []) or wanted == _term(
            "model_words", "view_category"
        ):
            chosen = _related(host_row, products, fits, wanted) or [
                row for row in products if str(row.get("category") or "") == wanted
            ]
            focus = _fitting_focus(cleaned, chosen, host_row, fits)
        else:
            chosen = [
                row
                for row in products
                if str(row.get("category") or "") != _value("centre_category", "")
                and _covered_token(wanted, set(_tokens(_blob(row))))
            ]
            focus = _pick_focus(wanted, chosen) if chosen else focus
        honest = "exact"
    elif (
        focus is not None
        and exact
        and (not relation)
        and _model_query(cleaned)
        and (str(focus.get("category") or "") == _value("centre_category", ""))
    ):
        chosen = _spread(focus, products, fits)
        honest = "exact"
    else:
        chosen = _same_cover(sought if relation else cleaned, ranked_rows)
        if focus is None or not _row_fits(focus, centre_default, fits):
            focus = _fitting_focus(
                sought if relation else cleaned, chosen, centre_default, fits
            )
        honest = "exact"
    stock_note = ""
    if (
        filters.get("in_stock")
        and focus is not None
        and (_status(focus) == "out_of_stock")
    ):
        centre_for_alt = (
            focus
            if str(focus.get("category") or "") == _value("centre_category", "")
            else centre_default
        )
        alternative = _alternative(focus, products, fits, centre_for_alt)
        kept = [focus]
        if alternative:
            other = _by_id(products, str(alternative.get("id") or ""))
            if other is not None:
                kept.append(other)
        chosen = kept
        stock_note = _say("shop_search_2", focus.get("name"))
        honest = "exact"
    else:
        chosen = _apply_filters(chosen, filters)
    if not chosen:
        if not exact:
            return {
                "results": [],
                "ids": [],
                "exact_match": False,
                "honest": "closest",
                "message": message or _say("shop_search_3"),
                "focus_id": _sku(focus) if focus else "",
                "groups": [],
            }
        return _no_hits(cleaned or query, products, shop)
    named = not filter_only
    if named and (focus is None or _sku(focus) not in {_sku(row) for row in chosen}):
        focus = (
            _fitting_focus(cleaned or query, chosen, centre_default, fits)
            if chosen
            else None
        )
    elif focus is not None and (
        not _row_fits(
            focus,
            centre_default
            if str(focus.get("category") or "") != _value("centre_category", "")
            else focus,
            fits,
        )
    ):
        if _cover(cleaned or query, focus) < _best_cover(cleaned or query, chosen):
            focus = _fitting_focus(cleaned or query, chosen, centre_default, fits)
    if price_sort and chosen:
        kind = _category_only(cleaned or query) or _phrase_category(cleaned or query)
        if kind and kind != _ALL_PARTS:
            pool = [
                row for row in chosen if str(row.get("category") or "") == kind
            ] or list(chosen)
            focus = min(pool, key=lambda row: (_amount(row), _sku(row)))
            if price_sort == "price_desc":
                focus = max(pool, key=lambda row: (_amount(row), _sku(row)))
    elif filters.get("price_range") and chosen:
        if not filters.get("sort"):
            filters["sort"] = "price_asc"
        focus = min(chosen, key=lambda row: (_amount(row), _sku(row)))
    chosen = _order(chosen, filters, focus)
    gap = _honesty_gap(
        asked, chosen, focus, honest, products, fits, filters, centre_default
    )
    if gap is not None:
        chosen, focus, honest, message = gap
    elif loose and focus is not None:
        honest = "closest"
        message = _say("shop_search_4", _plain_name(focus))
    chosen = chosen[:limit]
    focus_id = _sku(focus) if focus else ""
    if focus is not None and str(focus.get("category") or "") == _value(
        "centre_category", ""
    ):
        centre = focus
    else:
        centre = centre_default
    results = [
        present(row, products, fits, focus_id, centre, mark_exact=honest != "closest")
        for row in chosen
    ]
    body: dict[str, Any] = {
        "results": results,
        "ids": [item["id"] for item in results],
        "exact_match": honest != "closest",
        "honest": honest,
        "focus_id": focus_id,
        "groups": _groups(results),
        "fits": _public_fits(fits),
    }
    if filters.get("in_stock"):
        body["in_stock"] = True
    if stock_note:
        body["message"] = stock_note
    elif honest == "closest":
        body["message"] = message or _say("shop_search_5")
    elif pack_note:
        body["message"] = pack_note
    return body


def present(
    row: dict[str, Any],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    focus_id: str,
    centre: dict[str, Any] | None,
    mark_exact: bool = True,
    exact_ids: set[str] | None = None,
) -> dict[str, Any]:
    item = public_product(row)
    attributes = item["attributes"]
    item["family"] = str(row.get("family") or "")
    item["price_label"] = str(attributes.get("price_label") or "")
    item["demo_item"] = bool(attributes.get("demo_item"))
    item["availability"] = item.get("availability") or _stock(attributes)
    item["options"] = (
        attributes.get("options") if isinstance(attributes.get("options"), dict) else {}
    )
    item["defaults"] = (
        attributes.get("defaults")
        if isinstance(attributes.get("defaults"), dict)
        else {}
    )
    item["fit"] = (
        fit_state(_sku(row), centre, fits, _centre_cut(centre))
        if centre is not None
        else None
    )
    item["compare"] = _compare(row, products)
    item["badges"] = _badges(row, focus_id, item["compare"], mark_exact, exact_ids)
    item["alternative"] = _alternative(row, products, fits, centre)
    history = attributes.get("price_history")
    item["price_history"] = history if isinstance(history, dict) else None
    return item


@_scoped
def present_detail(
    detail: dict[str, Any],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    centre_sku: str = "",
) -> dict[str, Any]:
    row = next(
        (item for item in products if _sku(item) == str(detail.get("id") or "")), None
    )
    if row is None:
        return detail
    centre = (
        row
        if str(row.get("category") or "") == _value("centre_category", "")
        else _centre_row(products, centre_sku)
    )
    rich = present(row, products, fits, _sku(row), centre)
    rich["categories_that_fit"] = detail.get("categories_that_fit") or []
    rich["fits"] = _public_fits(fits)
    return rich


def fit_state(
    part_sku: str, centre: dict[str, Any] | None, fits: list[dict[str, Any]], cut: str
) -> dict[str, Any] | None:
    if centre is None:
        return None
    rows = [
        fit
        for fit in fits
        if str(fit.get("part_sku") or "") == part_sku and _targets(fit, centre)
    ]
    if not rows:
        return None
    for fit in rows:
        if _negative_fit(fit):
            note = str(fit.get("note") or "")
            sentence = _fit_sentence(note)
            return {
                "part_sku": part_sku,
                "fits": False,
                "explicit": True,
                "badge": sentence or _say("fit_state"),
                "note": note,
            }
    for fit in rows:
        rule = fit.get("rule") if isinstance(fit.get("rule"), dict) else {}
        needed = str(rule.get(_term("model_words", "option_attribute")) or "")
        if not needed or needed == cut:
            note = str(fit.get("note") or "")
            badge = (
                _say("fit_state_2", needed) if needed else _fit_sentence(note) or "Fits"
            )
            return {"part_sku": part_sku, "fits": True, "badge": badge, "note": note}
    rule = rows[0].get("rule") if isinstance(rows[0].get("rule"), dict) else {}
    needed = str(rule.get(_term("model_words", "option_attribute")) or "")
    note = str(rows[0].get("note") or "")
    badge = (
        note
        if note.lower().startswith("needs")
        else _say("fit_state_3", needed)
        if needed
        else note
    )
    return {"part_sku": part_sku, "fits": False, "badge": badge, "note": note}


@_scoped
def enrich_graph(
    built: dict[str, Any], products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> dict[str, Any]:
    centre_row = _by_id(products, str((built.get("centre") or {}).get("id") or ""))
    if centre_row is not None:
        why = (built.get("centre") or {}).get("why")
        centre = present(centre_row, products, fits, _sku(centre_row), centre_row)
        if why:
            centre["why"] = why
        built["centre"] = centre
    focus = centre_row
    for branch in built.get("branches") or []:
        products_out = []
        for item in branch.get("products") or []:
            row = _by_id(products, str(item.get("id") or ""))
            if row is None:
                products_out.append(item)
                continue
            rich = present(row, products, fits, _sku(row), focus)
            if item.get("why"):
                rich["why"] = item["why"]
            products_out.append(rich)
        branch["products"] = products_out
    return built


@_scoped
def enrich_setups(
    body: dict[str, Any], products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> dict[str, Any]:
    centre_id = ""
    setups = body.get("setups") or []
    if setups:
        first = (setups[0].get("parts") or [{}])[0]
        centre_id = str((first.get("product") or {}).get("id") or "")
    centre = _by_id(products, centre_id)
    for setup in setups:
        for part in setup.get("parts") or []:
            product = part.get("product") or {}
            row = _by_id(products, str(product.get("id") or ""))
            if row is None:
                continue
            rich = present(
                row, products, fits, centre_id, centre if centre is not None else row
            )
            part["product"] = rich
    return body


def _pick_focus(query: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    needles = [
        token for token in _tokens(query) if len(token) >= 2 and token not in _FILLER
    ]

    def key(row: dict[str, Any]) -> tuple[int, float, int, int, int, str]:
        have = set(_tokens(_blob(row)))
        cover = sum((1 for token in needles if _covered_token(token, have)))
        sim = _similarity(query, str(row.get("name") or ""))
        centre = (
            1 if str(row.get("category") or "") == _value("centre_category", "") else 0
        )
        stocked = 1 if _available(row) else 0
        return (
            cover,
            centre,
            sim,
            stocked,
            -len(str(row.get("name") or "")),
            _sku(row),
        )

    return max(rows, key=key)


def _same_cover(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    focus = _pick_focus(query, rows)
    best = _cover(query, focus)
    kept = [row for row in rows if _cover(query, row) >= best]
    return kept or rows


def _blob(row: dict[str, Any]) -> str:
    parts = [str(row.get("name") or ""), str(row.get("brand") or "")]
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    features = attributes.get("features") or []
    if isinstance(features, list):
        parts.extend((str(item) for item in features))
    if attributes.get("thread"):
        parts.append(str(attributes["thread"]))
    return " ".join(parts)


def _cover(query: str, row: dict[str, Any]) -> int:
    needles = [
        token for token in _tokens(query) if len(token) >= 2 and token not in _FILLER
    ]
    have = set(_tokens(_blob(row)))
    return sum((1 for token in needles if _covered_token(token, have)))


def _best_cover(query: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    return max((_cover(query, row) for row in rows))


def _covered_token(token: str, have: set[str]) -> bool:
    if token in have:
        return True
    if len(token) >= 4 and token.endswith("s") and (token[:-1] in have):
        return True
    return any(
        (
            len(item) >= 4 and item.endswith("s") and (item[:-1] == token)
            for item in have
        )
    )


def _category_only(query: str) -> str:
    tokens = [
        token for token in _tokens(query) if len(token) >= 2 and token not in _FILLER
    ]
    if len(tokens) != 1:
        return ""
    if tokens[0] in {"part", "parts"}:
        return _ALL_PARTS
    return _value("category_words", {}).get(tokens[0], "")


def _phrase_category(phrase: str) -> str:
    tokens = [token for token in _tokens(phrase) if token not in _FILLER]
    if any((token in frozenset(_value("sight_words", [])) for token in tokens)):
        return _term("model_words", "view_category")
    for token in tokens:
        found = _value("category_words", {}).get(token, "")
        if found and found != _ALL_PARTS:
            return found
    return ""


def _wanted_part(query: str) -> str:
    if re.search("\\band\\b", query, re.IGNORECASE):
        return ""
    tokens = [
        token for token in _tokens(query) if token not in _FILLER and len(token) >= 2
    ]
    if len(tokens) < 2 or not any(
        (
            token
            in {
                _term("model_words", "model_prefix"),
                _term("model_words", "model_brand"),
            }
            for token in tokens[:-1]
        )
    ):
        return ""
    tail = tokens[-1]
    if tail in {
        _term("model_words", "primary_colour"),
        _term("model_words", "secondary_colour"),
        _term("model_words", "variant_feature"),
        _term("model_words", "model_prefix"),
        _term("model_words", "model_brand"),
    }:
        return ""
    return _value("part_tail", {}).get(tail, "")


def _unoffered_colour(query: str, row: dict[str, Any] | None) -> str:
    if row is None or str(row.get("category") or "") != _value("centre_category", ""):
        return ""
    offered: set[str] = set()
    for label in _option_labels(row):
        offered.update(_tokens(label))
    text = " ".join(_tokens(query))
    name = str(row.get("name") or "this model")
    if (
        _term("model_words", "compound_colour_query")
        and _term("model_words", "compound_colour_query") in text
        and _term("model_words", "compound_colour_prefix") not in offered
        and (_term("model_words", "compound_colour_tail") not in offered)
    ):
        return _say("unoffered_colour", name)
    for word in _tokens(query):
        if (
            word == _term("model_words", "compound_colour_tail")
            and _term("model_words", "compound_colour_query")
            and _term("model_words", "compound_colour_query") in text
        ):
            continue
        if word in frozenset(_value("colour_words", [])) and word not in offered:
            shown = (
                _term("model_words", "compound_prefix_label")
                if word == _term("model_words", "compound_colour_prefix")
                else word.capitalize()
            )
            return _say("unoffered_colour_2", shown, name)
    return ""


def _model_query(query: str) -> bool:
    tokens = [
        token for token in _tokens(query) if len(token) >= 2 and token not in _FILLER
    ]
    if (
        _term("model_words", "model_prefix") in tokens
        or _term("model_words", "model_brand") in tokens
    ):
        return True
    return any(
        (
            len(token) >= 4
            and _similarity(token, _term("model_words", "model_brand")) >= 0.6
            for token in tokens
        )
    )


def _variant_miss(message: str, products: list[dict[str, Any]]) -> bool:
    if _say("variant_miss") not in message:
        return False
    phrase = message.split(_say("variant_miss_2"), 1)[1].rstrip(".")
    listed: set[str] = set()
    for row in products:
        listed.update(_tokens(str(row.get("name") or "")))
        listed.update(_tokens(str(row.get("brand") or "")))
        listed.update(_tokens(str(row.get("category") or "")))
    for part in phrase.split(", "):
        words = [word for word in part.split() if word]
        if words and words[0].lower() in listed:
            return True
    return False


def _closest_rows(query: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    needles = [
        token for token in _tokens(query) if len(token) >= 2 and token not in _FILLER
    ]
    covering = []
    for row in rows:
        have = set(_tokens(f"{row.get('name') or ''} {row.get('brand') or ''}"))
        if any((_covered_token(token, have) for token in needles)):
            covering.append(row)
    pool = covering or list(rows)
    centres = [
        row
        for row in pool
        if str(row.get("category") or "") == _value("centre_category", "")
    ]
    if centres:
        return centres
    category = str(pool[0].get("category") or "")
    return [row for row in pool if str(row.get("category") or "") == category]


def _spread(
    focus: dict[str, Any], products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    brand = str(focus.get("brand") or "")
    family = str(focus.get("family") or "")
    centres = [
        row
        for row in products
        if str(row.get("category") or "") == _value("centre_category", "")
        and str(row.get("brand") or "") == brand
    ]
    linked: set[str] = set()
    for fit in fits:
        if not _positive_fit(fit):
            continue
        if str(fit.get("fits_family") or "") == family or str(
            fit.get("fits_sku") or ""
        ) == _sku(focus):
            linked.add(str(fit.get("part_sku") or ""))
    upgrades = []
    for row in products:
        if str(row.get("category") or "") not in _value("part_categories", []):
            continue
        attrs = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        upgrade_for = attrs.get("upgrade_for") or []
        if isinstance(upgrade_for, str):
            upgrade_for = [upgrade_for]
        if _sku(row) in linked or family in upgrade_for:
            upgrades.append(row)
    seen: set[str] = set()
    chosen = []
    for row in centres + upgrades:
        if _sku(row) in seen:
            continue
        seen.add(_sku(row))
        chosen.append(row)
    return chosen


def _apply_filters(
    rows: list[dict[str, Any]], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    kept = []
    for row in rows:
        amount = _amount(row)
        if filters.get("in_stock") and (not _available(row)):
            continue
        if filters.get("max_price") is not None and amount >= float(
            filters["max_price"]
        ):
            continue
        if filters.get("max_price_inclusive") is not None and amount > float(
            filters["max_price_inclusive"]
        ):
            continue
        if filters.get("min_price") is not None and amount < float(
            filters["min_price"]
        ):
            continue
        category = str(filters.get("category") or "")
        if (
            category
            and category.lower() != "all"
            and (str(row.get("category") or "").lower() != category.lower())
        ):
            continue
        kept.append(row)
    return kept


def _order(
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
    focus: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    sort = str(filters.get("sort") or "")
    if sort in {"price_asc", "price_desc"}:
        ordered = sorted(
            rows,
            key=lambda row: (_amount(row), _sku(row)),
            reverse=sort == "price_desc",
        )
        return ordered
    if focus is None:
        return list(rows)
    sku = _sku(focus)
    return [row for row in rows if _sku(row) == sku] + [
        row for row in rows if _sku(row) != sku
    ]


def _no_hits(
    query: str, products: list[dict[str, Any]], shop: dict[str, Any] | None = None
) -> dict[str, Any]:
    named = _named_outsider(query, (shop or {}).get("not_stocked") or [])
    if named:
        return {
            "results": [],
            "ids": [],
            "exact_match": False,
            "honest": "not_stocked",
            "message": _say("no_hits", named),
            "focus_id": "",
            "groups": [],
        }
    brand = _outside_brand(query, products)
    if brand:
        return {
            "results": [],
            "ids": [],
            "exact_match": False,
            "honest": "not_stocked",
            "message": _say("no_hits_2", brand),
            "focus_id": "",
            "groups": [],
        }
    return {
        "results": [],
        "ids": [],
        "exact_match": True,
        "honest": "nothing",
        "message": _say("no_hits_3"),
        "focus_id": "",
        "groups": [],
    }


def _outside_brand(query: str, products: list[dict[str, Any]]) -> str:
    listed: set[str] = set()
    for row in products:
        listed.update(_tokens(str(row.get("name") or "")))
        listed.update(_tokens(str(row.get("brand") or "")))
        listed.update(_tokens(str(row.get("category") or "")))
        listed.update(_tokens(_sku(row)))
        for key, value in shopper_attributes(row.get("attributes")).items():
            listed.update(_tokens(str(key)))
            listed.update(_tokens(str(value)))
    words = re.findall("[A-Za-z0-9]+", query)
    has_digit = any((any((char.isdigit() for char in word)) for word in words))
    if not has_digit:
        return ""
    for word in words:
        token = word.lower()
        if any((char.isdigit() for char in token)) or len(token) < 4:
            continue
        if token in listed or _shopping_word(token) or _common_english(token):
            continue
        if any((len(item) >= 4 and _similarity(token, item) >= 0.6 for item in listed)):
            continue
        return word
    return ""


def _parse_constraints(query: str) -> tuple[str, dict[str, Any]]:
    filters: dict[str, Any] = {}
    text = " ".join(query.split())
    if _IN.search(text):
        filters["in_stock"] = True
        text = _IN.sub(" ", text)
    span = _RANGE.search(text)
    if span and (
        not any(
            (
                filters.get(key) is not None
                for key in ("max_price", "max_price_inclusive", "min_price")
            )
        )
    ):
        low, high = sorted((int(span.group(1)), int(span.group(2))))
        filters["min_price"] = low
        filters["max_price_inclusive"] = high
        filters["price_range"] = True
        text = text[: span.start()] + " " + text[span.end() :]
    text = " ".join(text.split())
    match = _BOUND.search(text)
    if match:
        amount = int(re.sub("\\s+", "", match.group(2)))
        word = match.group(1).lower()
        if word in {"over", "above", "more than"}:
            filters["min_price"] = amount
        elif word == "up to":
            filters["max_price_inclusive"] = amount
        else:
            filters["max_price"] = amount
        text = _BOUND.sub(" ", text)
    bare = re.search("\\br\\s*(\\d{4,})\\b", text, re.IGNORECASE)
    if bare and (
        not any(
            (
                filters.get(key) is not None
                for key in ("max_price", "max_price_inclusive", "min_price")
            )
        )
    ):
        filters["max_price"] = int(bare.group(1))
        text = text[: bare.start()] + " " + text[bare.end() :]
    return (" ".join(text.split()), filters)


def _compare(row: dict[str, Any], products: list[dict[str, Any]]) -> dict[str, Any]:
    category = str(row.get("category") or "")
    same = [item for item in products if str(item.get("category") or "") == category]
    cheapest = min(same, key=lambda item: (_amount(item), _sku(item)))
    delta = _amount(row) - _amount(cheapest)
    return {
        "cheapest_id": _sku(cheapest),
        "delta": money(delta),
        "currency": str(row.get("currency") or ""),
        "peers": len(same),
    }


def _alternative(
    row: dict[str, Any],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    centre: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if _status(row) != "out_of_stock":
        return None
    cut = ""
    if centre is not None:
        defaults = (
            (centre.get("attributes") or {}).get("defaults")
            if isinstance(centre.get("attributes"), dict)
            else {}
        )
        cut = str((defaults or {}).get(_term("model_words", "option_attribute")) or "")
    found: list[tuple[int, float, str, dict[str, Any]]] = []
    for other in products:
        if _sku(other) == _sku(row) or str(other.get("category") or "") != str(
            row.get("category") or ""
        ):
            continue
        if not _available(other):
            continue
        state = (
            fit_state(_sku(other), centre, fits, cut) if centre is not None else None
        )
        fits_now = 0 if state and state.get("fits") else 1
        found.append((fits_now, abs(_amount(other) - _amount(row)), _sku(other), other))
    if not found:
        return None
    found.sort()
    best = found[0][3]
    public = public_product(best)
    return {"id": public["id"], "name": public["name"], "price": public["price"]}


def _badges(
    row: dict[str, Any],
    focus_id: str,
    compare: dict[str, Any],
    mark_exact: bool = True,
    exact_ids: set[str] | None = None,
) -> list[str]:
    badges: list[str] = []
    exact = _sku(row) in exact_ids if exact_ids is not None else _sku(row) == focus_id
    if mark_exact and exact:
        badges.append(_say("badges"))
    relation = ""
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    if isinstance(attributes.get("relation"), str):
        relation = attributes["relation"]
    if relation and _sku(row) != focus_id:
        badges.append(relation)
    if compare.get("delta") == 0 and int(compare.get("peers") or 0) > 1:
        badges.append("Cheapest")
    if attributes.get("demo_item"):
        badges.append(_say("badges_2"))
    return badges


def _stock(attributes: dict[str, Any]) -> dict[str, Any] | None:
    stock = attributes.get("stock")
    if not isinstance(stock, dict):
        return None
    return {
        "status": str(stock.get("status") or ""),
        "qty": stock.get("qty"),
        "location": str(stock.get("location") or ""),
        "lead": str(stock.get("lead") or ""),
        "label": str(stock.get("label") or ""),
    }


def _public_fits(fits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for fit in fits:
        rule = fit.get("rule") if isinstance(fit.get("rule"), dict) else {}
        rows.append(
            {
                "part_sku": str(fit.get("part_sku") or ""),
                "fits_sku": str(fit.get("fits_sku") or ""),
                "fits_family": str(fit.get("fits_family") or ""),
                "rule": rule,
                "note": str(fit.get("note") or ""),
            }
        )
    rows.sort(
        key=lambda item: (item["part_sku"], item["fits_family"], item["fits_sku"])
    )
    return rows


def _groups(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order: list[str] = []
    buckets: dict[str, list[str]] = {}
    for item in results:
        category = str(item.get("category") or "")
        if category not in buckets:
            order.append(category)
            buckets[category] = []
        buckets[category].append(str(item.get("id") or ""))
    return [
        {
            "category": category,
            "count": len(buckets[category]),
            "ids": buckets[category],
        }
        for category in order
    ]


def _targets(fit: dict[str, Any], centre: dict[str, Any]) -> bool:
    if str(fit.get("fits_sku") or "") == _sku(centre):
        return True
    family = str(fit.get("fits_family") or "")
    return bool(family) and family == str(centre.get("family") or "")


def _centre_cut(centre: dict[str, Any] | None) -> str:
    if centre is None:
        return ""
    attributes = (
        centre.get("attributes") if isinstance(centre.get("attributes"), dict) else {}
    )
    defaults = (
        attributes.get("defaults")
        if isinstance(attributes.get("defaults"), dict)
        else {}
    )
    return str(defaults.get(_term("model_words", "option_attribute")) or "")


def _centre_row(
    products: list[dict[str, Any]], centre_sku: str
) -> dict[str, Any] | None:
    if centre_sku:
        found = _by_id(products, centre_sku)
        if found is not None:
            return found
    return next(
        (
            row
            for row in products
            if str(row.get("category") or "") == _value("centre_category", "")
        ),
        None,
    )


def _named_outsider(query: str, configured: Any) -> str:
    entries = configured if isinstance(configured, list) else []
    words = [word.lower() for word in re.findall("[A-Za-z0-9]+", query)]
    if not words:
        return ""
    for entry in entries:
        if isinstance(entry, str):
            name = entry.strip()
            aliases = [name.lower()]
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            aliases = [name.lower()] + [
                str(item).lower() for item in entry.get("also") or []
            ]
        else:
            continue
        if not name:
            continue
        for word in words:
            if word in aliases:
                return name
            if len(word) >= 4 and any(
                (
                    len(alias) >= 4 and _similarity(word, alias) >= 0.75
                    for alias in aliases
                )
            ):
                return name
    return ""


def _strip_size(query: str) -> str:
    return " ".join(_pattern("size").sub(" ", query).split())


def _size_miss(message: str) -> bool:
    if _say("size_miss") not in message:
        return False
    phrase = message.split(_say("size_miss_2"), 1)[1].rstrip(".")
    parts = [part.strip() for part in phrase.split(",") if part.strip()]
    return bool(parts) and all(
        (
            _pattern("size").fullmatch(part) or _pattern("size").search(part)
            for part in parts
        )
    )


def _related(
    host: dict[str, Any],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    category: str,
) -> list[dict[str, Any]]:
    family = str(host.get("family") or "")
    chosen = []
    for row in products:
        if str(row.get("category") or "") != category:
            continue
        attributes = (
            row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        )
        upgrade_for = attributes.get("upgrade_for") or []
        if isinstance(upgrade_for, str):
            upgrade_for = [upgrade_for]
        linked = _sku(row) in {
            str(fit.get("part_sku") or "")
            for fit in fits
            if _positive_fit(fit)
            and (
                str(fit.get("fits_family") or "") == family
                or str(fit.get("fits_sku") or "") == _sku(host)
            )
        }
        if (linked or family in upgrade_for) and (not _negative_for(row, host, fits)):
            chosen.append(row)
    if chosen:
        return chosen
    return [row for row in products if str(row.get("category") or "") == category]


def _row_fits(
    row: dict[str, Any] | None,
    centre: dict[str, Any] | None,
    fits: list[dict[str, Any]],
) -> bool:
    if row is None:
        return False
    if (
        str(row.get("category") or "") == _value("centre_category", "")
        or centre is None
    ):
        return True
    state = fit_state(_sku(row), centre, fits, _centre_cut(centre))
    return state is None or bool(state.get("fits"))


def _fitting_focus(
    query: str,
    rows: list[dict[str, Any]],
    centre: dict[str, Any] | None,
    fits: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not rows:
        return None
    fitting = [row for row in rows if _row_fits(row, centre, fits)]
    pool = fitting or list(rows)
    stocked = [row for row in pool if _available(row)]
    return _pick_focus(query, stocked or pool)


_PRICE_WORDS = frozenset(
    {
        "under",
        "below",
        "less",
        "than",
        "up",
        "over",
        "above",
        "more",
        "price",
        "only",
        "between",
        "from",
        "to",
    }
)
_QUERY_GLUE = frozenset(
    {
        "fit",
        "fits",
        "fitting",
        "compatible",
        "suit",
        "suits",
        "go",
        "goes",
        "pair",
        "pairs",
        "mount",
        "replace",
        "use",
        "using",
        "what",
        "which",
        "show",
        "find",
        "any",
    }
)
_PACK_ASK = re.compile("\\b(\\d+)\\s+packs?\\b", re.IGNORECASE)
_RANGE = re.compile(
    "(?:\\b(?:between|from)\\s+)?(?:R\\s*)?(\\d{3,})\\s*(?:and|to|-)\\s*(?:R\\s*)?(\\d{3,})\\b",
    re.IGNORECASE,
)
_Q_PREFIX = re.compile(
    "^(?:do you (?:have|sell|stock|carry)|have you got|how much (?:is|for|does)|what(?:'s| is) the price of|price of|is there|is the|what fits)\\b\\s*(?:the\\s+|a\\s+|an\\s+)?",
    re.IGNORECASE,
)
_DOES_FIT = re.compile(
    "^(?:does|do|will|can) (?:the |a |an )?(?>(.+?) fits? )(?:on )?(?:the |a |an )?(.+?)\\??$",
    re.IGNORECASE | re.DOTALL,
)
_INCH = re.compile("\\b(\\d+(?:\\.\\d+)?)\\s*(?:inch|inches)\\b", re.IGNORECASE)
_KILO = re.compile("(^|[^A-Za-z0-9])(?:[rR]\\s*)?(\\d+(?:\\.\\d+)?)\\s*[kK]\\b")
_GROUPED_PRICE = re.compile("\\b[rR]\\s*(\\d{1,3}(?:[\\s,]\\d{3})+)\\b")


def _rule(fit: dict[str, Any]) -> dict[str, Any]:
    rule = fit.get("rule")
    return rule if isinstance(rule, dict) else {}


def _negative_fit(fit: dict[str, Any]) -> bool:
    return _rule(fit).get("fits") is False


def _positive_fit(fit: dict[str, Any]) -> bool:
    return not _negative_fit(fit)


def _fit_sentence(note: str) -> str:
    return re.sub("\\s+https?://\\S+", "", note).strip().rstrip(".")


def _negative_for(
    part: dict[str, Any], host: dict[str, Any], fits: list[dict[str, Any]]
) -> bool:
    part_sku = _sku(part)
    return any(
        (
            str(fit.get("part_sku") or "") == part_sku
            and _negative_fit(fit)
            and _targets(fit, host)
            for fit in fits
        )
    )


def _transposed(left: str, right: str) -> bool:
    if len(left) != len(right) or len(left) < 2:
        return False
    for index in range(len(left) - 1):
        if left[index] == right[index]:
            continue
        swapped = left[:index] + left[index + 1] + left[index] + left[index + 2 :]
        return swapped == right
    return False


def _one_edit(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = (right, left)
    index = 0
    other = 0
    edits = 0
    while index < len(left) and other < len(right):
        if left[index] == right[other]:
            index += 1
            other += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) == len(right):
            index += 1
        other += 1
    edits += len(right) - other + (len(left) - index)
    return edits == 1


def _correct_typos(query: str, products: list[dict[str, Any]]) -> str:
    vocab: set[str] = set()
    for row in products:
        blob = f"{row.get('name') or ''} {row.get('brand') or ''} {row.get('category') or ''}"
        for token in _tokens(blob):
            if len(token) >= 3:
                vocab.add(token)

    def misspelled(token: str, item: str) -> bool:
        if token == item or not (_one_edit(token, item) or _transposed(token, item)):
            return False
        if len(token) < 4:
            return True
        return _similarity(token, item) < _FUZZY

    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        token = word.lower()
        if (
            len(token) < 3
            or token in vocab
            or _common_english(token)
            or _shopping_word(token)
        ):
            return word
        fixes = [item for item in vocab if misspelled(token, item)]
        if len(fixes) == 1:
            return fixes[0]
        return word

    return re.sub("[A-Za-z0-9]+", fix, query)


def _expand_prices(query: str) -> str:

    def kilo(match: re.Match[str]) -> str:
        return _say("kilo", match.group(1), int(float(match.group(2)) * 1000))

    text = _KILO.sub(kilo, query)
    text = _GROUPED_PRICE.sub(
        lambda match: _say("expand_prices") + re.sub("[\\s,]", "", match.group(1)), text
    )
    return re.sub("(^|[^A-Za-z0-9])[rR](\\d{3,})\\b", "\\1R \\2", text)


def _is_question(query: str) -> bool:
    text = query.strip()
    return text.endswith("?") or bool(_Q_PREFIX.match(text))


def _rewrite_fit_question(query: str) -> str:
    match = _DOES_FIT.match(" ".join(query.split()))
    if match is None:
        return query
    return _say("rewrite_fit_question", match.group(1).strip(), match.group(2).strip())


def _normalize_shopper(query: str, products: list[dict[str, Any]]) -> str:
    question = _is_question(query)
    text = _correct_typos(query, products).strip().rstrip("?").strip()
    text = _rewrite_fit_question(text)
    text = _Q_PREFIX.sub("", text)
    text = _expand_prices(text)
    show_anyway = question and bool(
        re.match("do you have\\b", query.strip(), re.IGNORECASE)
    )
    if show_anyway and (not re.search("\\bin stock only\\b", text, re.IGNORECASE)):
        text = re.sub("\\bin stock\\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(
        _pattern_text("count_alias"),
        _term("model_words", "count_replacement"),
        text,
        flags=re.IGNORECASE,
    )
    text = _PACK_ASK.sub(" ", text)
    text = re.sub("\\bany\\b", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _shop_colours(products: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for row in products:
        if str(row.get("category") or "") != _value("centre_category", ""):
            continue
        for label in _option_labels(row):
            seen.update(_tokens(label))
    listed = []
    if _term("model_words", "secondary_colour") in seen:
        listed.append(_term("model_words", "secondary_colour_label"))
    if _term("model_words", "primary_colour") in seen:
        listed.append(_term("model_words", "primary_colour_label"))
    return listed


def _bare_colour(query: str, products: list[dict[str, Any]]) -> str:
    if _named_centre(query, products):
        return ""
    text = " ".join(_tokens(query))
    offered: set[str] = set()
    for row in products:
        if str(row.get("category") or "") != _value("centre_category", ""):
            continue
        for label in _option_labels(row):
            offered.update(_tokens(label))
    asked = ""
    if (
        _term("model_words", "compound_colour_query")
        and _term("model_words", "compound_colour_query") in text
        and _term("model_words", "compound_colour_prefix") not in offered
        and (_term("model_words", "compound_colour_tail") not in offered)
    ):
        asked = _term("model_words", "compound_colour_label")
    else:
        for word in _tokens(query):
            if word in frozenset(_value("colour_words", [])) and word not in offered:
                asked = (
                    _term("model_words", "compound_prefix_label")
                    if word == _term("model_words", "compound_colour_prefix")
                    else word.capitalize()
                )
                break
    if not asked:
        return ""
    listed = _and_list(_shop_colours(products))
    if not listed:
        return _say("bare_colour", asked)
    return _say("bare_colour_2", asked, listed)


def _cut_named(
    part: dict[str, Any], host: dict[str, Any], fits: list[dict[str, Any]]
) -> str:
    attributes = (
        host.get("attributes") if isinstance(host.get("attributes"), dict) else {}
    )
    options = (
        attributes.get("options") if isinstance(attributes.get("options"), dict) else {}
    )
    cuts = [
        str(item)
        for item in options.get(_term("model_words", "option_attribute")) or []
        if str(item or "").strip()
    ]
    part_sku = _sku(part)
    for fit in fits:
        if str(fit.get("part_sku") or "") != part_sku:
            continue
        needed = str(_rule(fit).get(_term("model_words", "option_attribute")) or "")
        if not needed:
            continue
        if any((needed == cut or needed.split()[0] == cut.split()[0] for cut in cuts)):
            return needed.split()[0]
    return ""


def _exact_body(
    rows: list[dict[str, Any]],
    focus: dict[str, Any] | None,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int,
    message: str = "",
    exact_ids: set[str] | None = None,
) -> dict[str, Any]:
    ordered = _order(list(rows), filters, focus)[:limit]
    focus_id = _sku(focus) if focus else ""
    centre = next(
        (
            row
            for row in ordered
            if str(row.get("category") or "") == _value("centre_category", "")
        ),
        None,
    )
    if centre is None:
        centre = (
            focus
            if focus is not None
            and str(focus.get("category") or "") == _value("centre_category", "")
            else _default_centre(products)
        )
    results = [
        present(
            row, products, fits, focus_id, centre, mark_exact=True, exact_ids=exact_ids
        )
        for row in ordered
    ]
    body: dict[str, Any] = {
        "results": results,
        "ids": [item["id"] for item in results],
        "exact_match": True,
        "honest": "exact",
        "focus_id": focus_id,
        "groups": _groups(results),
        "fits": _public_fits(fits),
    }
    if message:
        body["message"] = message
    return body


def _fit_answer(
    raw: str,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int,
) -> dict[str, Any] | None:
    match = _DOES_FIT.match(" ".join(raw.split()))
    if match is None:
        return None
    host = _named_centre(match.group(2), products)
    if host is None:
        return None
    ranked = rank_search(
        {
            "q": match.group(1).strip(),
            "products": products,
            "limit": 20,
            "model_letters": True,
        }
    )
    by_sku = {_sku(row): row for row in products}
    rows = [by_sku[item] for item in ranked.get("ids") or [] if item in by_sku]
    parts = [
        row
        for row in rows
        if str(row.get("category") or "") != _value("centre_category", "")
    ]
    pool = parts or rows
    if not pool:
        return None
    focus = _pick_focus(match.group(1).strip(), pool)
    cut = _cut_named(focus, host, fits)
    state = _cut_state(_sku(focus), host, fits, cut or _centre_cut(host))
    if not (
        state is not None and state.get("fits") or _approved_link(focus, host, fits)
    ):
        return None
    message = _say("fit_answer", cut) if cut else "Yes."
    return _exact_body([focus], focus, products, fits, filters, limit, message)


def _length_says(row: dict[str, Any], wanted: str) -> bool:
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    verified = attributes.get("verified") or []
    if not isinstance(verified, list):
        return False
    pattern = re.compile(f"\\b{re.escape(wanted)}\\s*inch\\b", re.IGNORECASE)
    for spec in verified:
        if not isinstance(spec, dict) or str(spec.get("name") or "") not in {
            _term("model_words", "length_word"),
            _term("model_words", "length_spec"),
        }:
            continue
        if pattern.search(str(spec.get("value") or "")):
            return True
    return False


def _length_answer(
    query: str,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int,
) -> dict[str, Any] | None:
    match = _INCH.search(query)
    if match is None:
        return None
    wanted = match.group(1)
    found = [
        row
        for row in products
        if str(row.get("category") or "") == _value("centre_category", "")
        and _length_says(row, wanted)
    ]
    if not found:
        return None
    return _exact_body(found, found[0], products, fits, filters, limit)


def _clause_names(clause: str, products: list[dict[str, Any]]) -> bool:
    tokens = set(_tokens(clause))
    if not tokens:
        return False
    match = _pattern("count").search(clause)
    if match and tokens & {
        _term("model_words", "capacity_short"),
        _term("model_words", "capacity_plural"),
        _term("model_words", "capacity_word"),
        _term("model_words", "capacity_spec"),
        _term("model_words", "count_word"),
        _term("model_words", "count_attribute"),
    }:
        wanted = int(match.group(1))
        return any(
            (
                str(row.get("category") or "")
                == _term("model_words", "capacity_category")
                and _count_count(row) == wanted
                for row in products
            )
        )
    if _category_only(clause) or _phrase_category(clause):
        return True
    if _named_centre(clause, products) is not None and (not _wanted_part(clause)):
        return True
    ranked = rank_search(
        {"q": clause, "products": products, "limit": 12, "model_letters": True}
    )
    if not ranked.get("exact_match") or not ranked.get("ids"):
        return False
    by_sku = {_sku(row): row for row in products}
    found = [by_sku[item] for item in ranked["ids"] if item in by_sku]
    if not found:
        return False
    focus = _pick_focus(clause, found)
    have = set(_tokens(f"{focus.get('name') or ''} {_sku(focus)}"))
    content = [
        token
        for token in _tokens(clause)
        if len(token) >= 3
        and token not in _FILLER
        and (token not in _QUERY_GLUE)
        and (not token.isdigit())
    ]
    return any((token in have or _covered_token(token, have) for token in content))


def _split_clauses(query: str, products: list[dict[str, Any]]) -> list[str]:
    query = " ".join(query.split())
    forced = "+" in query or "," in query
    # A relation phrase names a part for a host, rather than a two-item build.
    if not forced and _intent(query)[1]:
        return []
    if not forced and (not re.search("\\b(?:with|and)\\b", query, re.IGNORECASE)):
        return []
    parts = re.split("\\+|,|\\bwith\\b|\\band\\b", query, flags=re.IGNORECASE)
    cleaned = [" ".join(part.split()) for part in parts if part.strip()]
    if len(cleaned) < 2:
        return []
    if not forced and (not all((_clause_names(part, products) for part in cleaned))):
        return []
    return cleaned


def _clause_rows(
    clause: str, products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    tokens = set(_tokens(clause))
    match = _pattern("count").search(clause)
    if match and tokens & {
        _term("model_words", "capacity_short"),
        _term("model_words", "capacity_plural"),
        _term("model_words", "capacity_word"),
        _term("model_words", "capacity_spec"),
    }:
        wanted = int(match.group(1))
        capacities = [
            row
            for row in products
            if str(row.get("category") or "")
            == _term("model_words", "capacity_category")
            and _count_count(row) == wanted
        ]
        if capacities:
            return (capacities, capacities[0])
    category = _category_only(clause) or _phrase_category(clause)
    if category and category != _ALL_PARTS:
        pool = [row for row in products if str(row.get("category") or "") == category]
        if not pool:
            return ([], None)
        named = _named_in_pool(clause, pool)
        if named is not None:
            return ([named], named)
        return (pool, _fitting_focus(clause, pool, _default_centre(products), fits))
    host = _named_centre(clause, products)
    if host is not None and (not _wanted_part(clause)):
        return ([host], host)
    ranked = rank_search(
        {"q": clause, "products": products, "limit": 20, "model_letters": True}
    )
    by_sku = {_sku(row): row for row in products}
    found = [by_sku[item] for item in ranked.get("ids") or [] if item in by_sku]
    if not found:
        return ([], None)
    focus = _pick_focus(clause, found)
    return ([focus], focus)


def _multi_answer(
    query: str,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int,
) -> dict[str, Any] | None:
    clauses = _split_clauses(query, products)
    if not clauses:
        return None
    rows: list[dict[str, Any]] = []
    exact_ids: set[str] = set()
    seen: set[str] = set()
    focus: dict[str, Any] | None = None
    for clause in clauses:
        picked, clause_focus = _clause_rows(clause, products, fits)
        for row in picked:
            sku = _sku(row)
            if sku in seen:
                continue
            seen.add(sku)
            rows.append(row)
        if clause_focus is not None:
            exact_ids.add(_sku(clause_focus))
            if focus is None:
                focus = clause_focus
    if not rows:
        return None
    centre = next(
        (
            row
            for row in rows
            if str(row.get("category") or "") == _value("centre_category", "")
        ),
        None,
    )
    if centre is not None:
        blocked: list[tuple[dict[str, Any], str]] = []
        for row in rows:
            if _sku(row) == _sku(centre):
                continue
            note = _build_block(row, centre, fits)
            if note:
                blocked.append((row, note))
        if blocked:
            return _blocked_build(rows, centre, blocked, products, fits, filters, limit)
    return _exact_body(
        rows, centre or focus, products, fits, filters, limit, "", exact_ids
    )


def _named_in_pool(clause: str, pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(pool) < 2:
        return None
    skip = (
        set(_value("category_words", {}))
        | frozenset(_value("sight_words", []))
        | frozenset(_value("modifier_words", []))
        | _FILLER
        | {
            _term("model_words", "model_prefix"),
            _term("model_words", "model_brand"),
            "for",
        }
    )
    tokens = [
        token for token in _tokens(clause) if len(token) >= 3 and token not in skip
    ]
    if not tokens:
        return None

    def covers(row: dict[str, Any]) -> bool:
        owned = set(_tokens(f"{row.get('name') or ''} {_sku(row)}"))
        return all((token in owned or _covered_token(token, owned) for token in tokens))

    matches = [row for row in pool if covers(row)]
    if len(matches) != 1:
        return None
    return matches[0]


def _option_cuts(host: dict[str, Any]) -> list[str]:
    attributes = (
        host.get("attributes") if isinstance(host.get("attributes"), dict) else {}
    )
    options = (
        attributes.get("options") if isinstance(attributes.get("options"), dict) else {}
    )
    return [
        str(item)
        for item in options.get(_term("model_words", "option_attribute")) or []
        if str(item or "").strip()
    ]


def _required_cut(part: dict[str, Any], fits: list[dict[str, Any]]) -> str:
    part_sku = _sku(part)
    for fit in fits:
        if str(fit.get("part_sku") or "") != part_sku or not _positive_fit(fit):
            continue
        needed = str(_rule(fit).get(_term("model_words", "option_attribute")) or "")
        if needed:
            return needed
    return ""


def _cut_matches(needed: str, offered: list[str]) -> bool:
    head = needed.split()[0]
    return any((needed == cut or head == str(cut).split()[0] for cut in offered))


def _cut_note(part: dict[str, Any], fits: list[dict[str, Any]], needed: str) -> str:
    part_sku = _sku(part)
    for fit in fits:
        if str(fit.get("part_sku") or "") != part_sku:
            continue
        if (
            str(_rule(fit).get(_term("model_words", "option_attribute")) or "")
            == needed
        ):
            return str(fit.get("note") or "")
    return ""


def _expand_label(sentence: str, part: dict[str, Any]) -> str:
    bits = [bit.strip() for bit in sentence.split(".") if bit.strip()]
    if not bits:
        return sentence
    first = bits[0]
    if re.search("\\b(?:is|are|for|ships|needs|does|with)\\b", first, re.IGNORECASE):
        return sentence if sentence.endswith(".") else f"{sentence}."
    name = _plain_name(part)
    model = re.match(_pattern_text("model_prefix"), first, re.IGNORECASE)
    if model:
        name = re.sub(
            f"^{re.escape(model.group(0))}\\s+", "", name, count=1, flags=re.IGNORECASE
        ).strip()
    name = name or _plain_name(part)
    article = "an" if name[:1].lower() in "aeiou" else "a"
    lead = _say("expand_label", name, article, first)
    tail = ". ".join(bits[1:])
    if tail:
        return f"{lead}. {tail}."
    return f"{lead}."


def _refusal_message(part: dict[str, Any], host: dict[str, Any], note: str) -> str:
    sentence = _fit_sentence(note)
    host_name = _plain_name(host)
    if not sentence:
        return _say("refusal_message", _plain_name(part), host_name)
    if str(part.get("category") or "") == _value("carry_category", ""):
        return _say(
            "refusal_message_2", sentence, _term("model_words", "carry_word"), host_name
        )
    return _expand_label(sentence, part)


def _build_block(
    part: dict[str, Any], host: dict[str, Any], fits: list[dict[str, Any]]
) -> str:
    state = fit_state(_sku(part), host, fits, _centre_cut(host))
    if state is not None and state.get("explicit") and (not state.get("fits")):
        return _refusal_message(part, host, str(state.get("note") or ""))
    needed = _required_cut(part, fits)
    offered = _option_cuts(host)
    if needed and offered and (not _cut_matches(needed, offered)):
        note = _cut_note(part, fits, needed)
        if note:
            return _refusal_message(part, host, note)
        return _say("build_block", _plain_name(part), _plain_name(host))
    return ""


def _blocked_build(
    rows: list[dict[str, Any]],
    centre: dict[str, Any],
    blocked: list[tuple[dict[str, Any], str]],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    ordered = _order(list(rows), filters, centre)[:limit]
    focus_id = _sku(centre)
    results = [
        present(row, products, fits, focus_id, centre, mark_exact=False)
        for row in ordered
    ]
    blocked_ids = {_sku(row) for row, _note in blocked}
    chip = _say("blocked_build", _plain_name(centre))
    for item in results:
        if str(item.get("id") or "") not in blocked_ids:
            continue
        fit = item.get("fit") if isinstance(item.get("fit"), dict) else {}
        cited = str(fit.get("note") or "")
        if not cited:
            continue
        item["fit"] = {
            "part_sku": item["id"],
            "fits": False,
            "badge": chip,
            "note": cited,
        }
    return {
        "results": results,
        "ids": [item["id"] for item in results],
        "exact_match": False,
        "honest": "closest",
        "message": blocked[0][1],
        "focus_id": focus_id,
        "groups": _groups(results),
        "fits": _public_fits(fits),
    }


def _known_gap(
    query: str, products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any], str] | None:
    return _count_gap(query, products, fits) or _size_gap(query, products)


def _gap_body(
    rows: list[dict[str, Any]],
    host: dict[str, Any],
    note: str,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    shown = list(rows)[:limit]
    focus_id = _sku(host) if host else ""
    centre = (
        host
        if host is not None
        and str(host.get("category") or "") == _value("centre_category", "")
        else _default_centre(products)
    )
    results = [
        present(row, products, fits, focus_id, centre, mark_exact=False)
        for row in shown
    ]
    return {
        "results": results,
        "ids": [item["id"] for item in results],
        "exact_match": False,
        "honest": "closest",
        "message": note,
        "focus_id": focus_id,
        "groups": _groups(results),
        "fits": _public_fits(fits),
    }


def _fit_decision(
    part: dict[str, Any], host: dict[str, Any], fits: list[dict[str, Any]], cut: str
) -> tuple[str, str]:
    state = _cut_state(_sku(part), host, fits, cut)
    name = str(part.get("name") or _say("fit_decision"))
    plain = _plain_name(host)
    if state is not None and (not state.get("fits")):
        if state.get("explicit"):
            sentence = _fit_sentence(str(state.get("note") or ""))
            tail = f" {sentence}." if sentence else ""
            return (
                "refuse",
                _say("fit_decision_2", name, plain, tail).replace("..", "."),
            )
        return ("refuse", _say("fit_decision_3", name, cut or "chosen"))
    if state is not None and state.get("fits") or _approved_link(part, host, fits):
        return ("fits", "")
    if _negative_for(part, host, fits):
        return ("refuse", _say("fit_decision_4", name, plain))
    return ("confirm", "")


def _prepare_query(query: str) -> tuple[str, bool, str]:
    cheapest = bool(re.search("\\bcheapest\\b", query, re.IGNORECASE))
    expensive = bool(re.search("\\bmost expensive\\b", query, re.IGNORECASE))
    text = _normalize_threads(query)
    if cheapest:
        text = re.sub("\\bcheapest\\b", " ", text, flags=re.IGNORECASE)
    if expensive:
        text = re.sub("\\bmost expensive\\b", " ", text, flags=re.IGNORECASE)
    loose = bool(_pattern("loose").search(text))
    if loose:
        text = _pattern("loose").sub(" ", text)
    tokens = set(_tokens(text))
    capacities = {
        _term("model_words", "capacity_short"),
        _term("model_words", "capacity_plural"),
        _term("model_words", "capacity_word"),
        _term("model_words", "capacity_spec"),
    }
    if tokens & {"spare", "extra"} and tokens & capacities:
        text = re.sub("\\b(?:spare|extra)\\b", " ", text, flags=re.IGNORECASE)
    if "extended" in tokens and tokens & capacities:
        text = _term("model_words", "extended_query")
        loose = False
    text = " ".join(text.split())
    if loose:
        text = _part_for_model(text)
    sort = "price_desc" if expensive else "price_asc" if cheapest else ""
    return (text, loose, sort)


def _normalize_threads(query: str) -> str:
    text = re.sub(
        _pattern_text("thread_short"),
        _term("model_words", "thread_short_value"),
        query,
        flags=re.IGNORECASE,
    )
    return re.sub(
        _pattern_text("thread_long"),
        _term("model_words", "thread_long_value"),
        text,
        flags=re.IGNORECASE,
    )


def _pack_note(query: str) -> str:
    if _PACK_ASK.search(query) is None:
        return ""
    return _say("pack_note")


def _part_for_model(text: str) -> str:
    if re.search("\\bfor\\b", text, re.IGNORECASE):
        return text
    tokens = _tokens(text)
    part = ""
    for token in tokens:
        found = _value("category_words", {}).get(token, "")
        if found and found != _ALL_PARTS:
            part = token
            break
    letter = ""
    for index, token in enumerate(tokens):
        model = token in {
            _term("model_words", "model_prefix"),
            _term("model_words", "model_abbreviation"),
        } or (
            len(token) >= 3 and _term("model_words", "model_prefix").startswith(token)
        )
        if (
            model
            and index + 1 < len(tokens)
            and (tokens[index + 1] in set(_term("model_words", "variants").split()))
        ):
            letter = tokens[index + 1]
            break
    if part and letter:
        return _say("part_for_model", part, letter)
    return text


def _colour_host(
    query: str, products: list[dict[str, Any]], focus: dict[str, Any] | None
) -> tuple[str, dict[str, Any] | None]:
    named = _named_centre(query, products)
    host = named
    if (
        host is None
        and focus is not None
        and (str(focus.get("category") or "") == _value("centre_category", ""))
    ):
        if _term("model_words", "length_word") not in set(_tokens(query)):
            host = focus
    if host is None:
        return ("", None)
    note = _unoffered_colour(query, host)
    if not note:
        return ("", None)
    return (note, host)


def _brand_note(query: str, focus: dict[str, Any] | None) -> str:
    if focus is None or _term("model_words", "compared_brand") not in set(
        _tokens(query)
    ):
        return ""
    if _term("model_words", "mount_model") not in set(
        _tokens(str(focus.get("name") or ""))
    ):
        return ""
    return _say("brand_note")


def _unlinked_part(
    query: str,
    chosen: list[dict[str, Any]],
    focus: dict[str, Any] | None,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str, str] | None:
    _sought, host_phrase = _intent(query)
    if not host_phrase or focus is None:
        return None
    host = _named_centre(query, products)
    if host is None or str(focus.get("category") or "") == _value(
        "centre_category", ""
    ):
        return None
    if _approved_link(focus, host, fits):
        return None
    return (chosen, focus, "closest", _unlink_message(focus, host, products, fits))


def _unlink_message(
    part: dict[str, Any],
    host: dict[str, Any],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
) -> str:
    host_name = _plain_name(host)
    part_name = _plain_name(part)
    if str(part.get("category") or "") == _value("carry_category", ""):
        owner = _linked_host(part, products, fits)
        owner_name = _plain_name(owner) if owner is not None else host_name
        return _say(
            "unlink_message",
            _term("model_words", "carry_word"),
            host_name,
            part_name,
            owner_name,
        )
    return _say("unlink_message_2", part_name)


def _approved_link(
    part: dict[str, Any], host: dict[str, Any], fits: list[dict[str, Any]]
) -> bool:
    family = str(host.get("family") or "")
    sku = _sku(host)
    part_sku = _sku(part)
    for fit in fits:
        if str(fit.get("part_sku") or "") != part_sku or not _positive_fit(fit):
            continue
        target_family = str(fit.get("fits_family") or "")
        target_sku = str(fit.get("fits_sku") or "")
        if target_family and target_family == family:
            return True
        if target_sku and target_sku in {sku, family}:
            return True
    attributes = (
        part.get("attributes") if isinstance(part.get("attributes"), dict) else {}
    )
    upgrade = attributes.get("upgrade_for") or []
    if isinstance(upgrade, str):
        upgrade = [upgrade]
    targets = {str(item) for item in upgrade}
    return sku in targets or (bool(family) and family in targets)


def _catalog_linked(part: dict[str, Any], fits: list[dict[str, Any]]) -> bool:
    part_sku = _sku(part)
    if any((str(fit.get("part_sku") or "") == part_sku for fit in fits)):
        return True
    attributes = (
        part.get("attributes") if isinstance(part.get("attributes"), dict) else {}
    )
    upgrade = attributes.get("upgrade_for") or []
    if isinstance(upgrade, str):
        upgrade = [upgrade]
    return bool(upgrade)


def _linked_host(
    part: dict[str, Any], products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> dict[str, Any] | None:
    plain = [
        row
        for row in products
        if str(row.get("category") or "") == _value("centre_category", "")
        and _term("model_words", "variant_feature")
        not in set(_tokens(str(row.get("name") or "")))
    ]
    for row in plain + [
        row
        for row in products
        if str(row.get("category") or "") == _value("centre_category", "")
    ]:
        if _approved_link(part, row, fits):
            return row
    return None


def _unsold_kind(query: str) -> str:
    tokens = set(_tokens(query))
    if _term("unsold", "correction") in tokens and _term("unsold", "ready") in tokens:
        return ""
    if _term("unsold", "dark") in tokens and _mentions_view(query):
        return _term("unsold", "dark")
    if _term("unsold", "care") in tokens and tokens & {
        _term("unsold", "kit"),
        _term("unsold", "kits"),
    }:
        return _term("unsold", "care")
    if _term("unsold", "correction") in tokens:
        return _term("unsold", "correction")
    for word in (
        _term("unsold", "attachment"),
        _term("unsold", "pointer"),
        _term("unsold", "strap"),
        _term("unsold", "container"),
        _term("unsold", "bundle"),
    ):
        if word in tokens:
            return word
    if (
        _term("unsold", "consumable_short") in tokens
        or _term("unsold", "consumable_full") in tokens
    ):
        return "supply"
    return ""


def _unsold_note(kind: str, host: dict[str, Any] | None) -> str:
    name = _plain_name(host) if host is not None else ""
    tail = _say("unsold_note", name) if name else ""
    if kind == _term("unsold", "attachment"):
        return _say("unsold_note_2", tail)
    if kind == _term("unsold", "pointer"):
        return _say("unsold_note_3", tail)
    if kind == _term("unsold", "strap"):
        return _say("unsold_note_4", tail)
    if kind == _term("unsold", "container"):
        return _say("unsold_note_5", tail)
    if kind == _term("unsold", "bundle"):
        return _say("unsold_note_6", tail)
    if kind == _term("unsold", "dark"):
        return _say("unsold_note_7")
    if kind == _term("unsold", "correction"):
        return _say("unsold_note_8")
    if kind == _term("unsold", "care"):
        return _say("unsold_note_9")
    if kind == "supply":
        return _say("unsold_note_10", _term("unsold", "consumable_short"))
    return _say("unsold_note_11")


def _unsold_results(
    query: str,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
) -> dict[str, Any] | None:
    kind = _unsold_kind(query)
    if not kind or not _only_known(query, products):
        return None
    if kind == _term("unsold", "correction"):
        found = _correction_gap(query, products)
        if found is None:
            return None
        rows, host, note = found
        return _present_closest(rows, host, note, products, fits, filters)
    if kind == _term("unsold", "dark"):
        rows = [
            row
            for row in products
            if str(row.get("category") or "") == _term("model_words", "view_category")
        ]
        host = _pick_focus(_term("model_words", "view_word"), rows) if rows else None
        return _present_closest(
            rows,
            host,
            _unsold_note(_term("unsold", "dark"), None),
            products,
            fits,
            filters,
        )
    host = _named_centre(query, products) or _default_centre(products)
    if host is None:
        return None
    rows = _spread(host, products, fits) or [host]
    named = _named_centre(query, products)
    return _present_closest(
        rows, host, _unsold_note(kind, named), products, fits, filters
    )


def _present_closest(
    rows: list[dict[str, Any]],
    focus: dict[str, Any] | None,
    note: str,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
) -> dict[str, Any]:
    ordered = _order(list(rows), filters, focus)
    focus_id = _sku(focus) if focus else ""
    if focus is not None and str(focus.get("category") or "") == _value(
        "centre_category", ""
    ):
        centre = focus
    else:
        centre = _default_centre(products)
    results = [
        present(row, products, fits, focus_id, centre, mark_exact=False)
        for row in ordered
    ]
    return {
        "results": results,
        "ids": [item["id"] for item in results],
        "exact_match": False,
        "honest": "closest",
        "message": note,
        "focus_id": focus_id,
        "groups": _groups(results),
        "fits": _public_fits(fits),
    }


def _only_known(query: str, products: list[dict[str, Any]]) -> bool:
    brands: set[str] = set()
    for row in products:
        brands.update(_tokens(str(row.get("brand") or "")))
    skipped = {
        _term("unsold", "consumable_short"),
        _term("unsold", "consumable_full"),
        _term("unsold", "attachment"),
        _term("unsold", "pointer"),
        _term("unsold", "strap"),
        _term("unsold", "container"),
        _term("unsold", "bundle"),
        _term("unsold", "correction"),
        _term("unsold", "dark"),
        _term("unsold", "care"),
        _term("unsold", "kit"),
        _term("unsold", "kits"),
    }
    for token in _tokens(query):
        if token in skipped or token in frozenset(_value("modifier_words", [])):
            continue
        if not _meaningful(token, brands):
            continue
        if token in set(_term("model_words", "variants").split()):
            continue
        if any((_word_covered(token, row) for row in products)):
            continue
        return False
    return True


def _default_centre(products: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in products:
        if str(row.get("category") or "") != _value("centre_category", ""):
            continue
        name = set(_tokens(str(row.get("name") or "")))
        if (
            _term("model_words", "default_variant") in name
            and _term("model_words", "variant_feature") not in name
        ):
            return row
    return next(
        (
            row
            for row in products
            if str(row.get("category") or "") == _value("centre_category", "")
        ),
        None,
    )


def _honesty_gap(
    query: str,
    chosen: list[dict[str, Any]],
    focus: dict[str, Any] | None,
    honest: str,
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    filters: dict[str, Any],
    centre: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str, str] | None:
    if not query or not chosen:
        return None
    counts = _count_gap(query, products, fits)
    if counts is not None:
        rows, host, note = counts
        return (rows, host, "closest", note)
    size = _size_gap(query, products)
    if size is not None:
        rows, host, note = size
        return (rows, host, "closest", note)
    quiet = _correction_gap(query, products)
    if quiet is not None:
        rows, host, note = quiet
        return (rows, host, "closest", note)
    priced = _price_gap(query, chosen, products, filters)
    if priced is not None:
        return (chosen, focus, "closest", priced)
    brand = _brand_note(query, focus)
    if brand:
        return (chosen, focus, "closest", brand)
    unlinked = _unlinked_part(query, chosen, focus, products, fits)
    if unlinked is not None:
        return unlinked
    if honest != "exact":
        return None
    missing = _uncovered_words(query, chosen, products)
    if not missing:
        return None
    kind = _unsold_kind(query)
    if kind == _term("unsold", "dark"):
        kept = [
            row
            for row in chosen
            if str(row.get("category") or "") != _term("model_words", "light_category")
        ]
        if not kept:
            kept = [
                row
                for row in products
                if str(row.get("category") or "")
                == _term("model_words", "view_category")
            ]
        host = _fitting_focus(query, kept, centre, fits)
        return (
            _order(kept, filters, host),
            host,
            "closest",
            _unsold_note(_term("unsold", "dark"), None),
        )
    if kind:
        named = _named_centre(query, products)
        return (chosen, focus, "closest", _unsold_note(kind, named))
    label = _plain_name(focus) if focus is not None else "these"
    return (chosen, focus, "closest", _say("honesty_gap", label))


def _count_gap(
    query: str, products: list[dict[str, Any]], fits: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any], str] | None:
    match = _pattern("count").search(query)
    if match is None:
        return None
    wanted = int(match.group(1))
    host = _named_centre(query, products) or _default_centre(products)
    if host is None:
        return None
    offered = _count_counts(host, products)
    if not offered or wanted in offered:
        return None
    capacities = _hosted_parts(
        host, products, fits, _term("model_words", "capacity_category")
    )
    if not capacities:
        return None
    capacities.sort(key=lambda row: (_count_count(row), _sku(row)))
    nearest = min(
        capacities, key=lambda row: (abs(_count_count(row) - wanted), _sku(row))
    )
    listed = _and_list([str(count) for count in offered])
    return (capacities, nearest, _say("count_gap", listed, _plain_name(host)))


def _size_gap(
    query: str, products: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any], str] | None:
    if (
        _pattern("other_size").search(query) is None
        and _pattern("named_size").search(query) is None
    ):
        return None
    host = _named_centre(query, products)
    brand = str(host.get("brand") or "") if host is not None else ""
    centres = [
        row
        for row in products
        if str(row.get("category") or "") == _value("centre_category", "")
    ]
    if brand:
        same = [row for row in centres if str(row.get("brand") or "") == brand]
        centres = same or centres
    if not centres:
        return None
    sizes = {_size_of(row) for row in centres}
    sizes.discard("")
    if len(sizes) != 1:
        if host is None:
            return None
        size = _size_of(host)
        if not size:
            return None
        ordered = [host] + [row for row in centres if _sku(row) != _sku(host)]
        return (ordered, host, _say("size_gap", _plain_name(host), size))
    size = next(iter(sizes))
    focus = host or _default_centre(products) or centres[0]
    ordered = [focus] + [row for row in centres if _sku(row) != _sku(focus)]
    if host is not None and _pattern("named_size").search(query) is None:
        return (ordered, host, _say("size_gap_2", _plain_name(host), size))
    label = str(focus.get("brand") or "").split(" ")[0] or _term(
        "model_words", "group_label"
    )
    return (
        ordered,
        focus,
        _say("size_gap_3", label, _value("centre_category", "").lower(), size),
    )


def _correction_gap(
    query: str, products: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any], str] | None:
    tokens = set(_tokens(query))
    if (
        _term("unsold", "correction") not in tokens
        or _term("unsold", "ready") in tokens
    ):
        return None
    length = next(
        (
            row
            for row in products
            if _term("unsold", "correction") in set(_tokens(_coverage_text(row)))
            and str(row.get("category") or "")
            == _term("model_words", "length_category")
        ),
        None,
    )
    if length is None:
        return None
    host = _named_centre(query, products)
    if host is None:
        host = next(
            (
                row
                for row in products
                if str(row.get("category") or "") == _value("centre_category", "")
                and _term("unsold", "correction") in set(_tokens(_coverage_text(row)))
            ),
            None,
        )
    rows = [length]
    if host is not None and _sku(host) != _sku(length):
        rows.append(host)
    return (rows, length, _say("correction_gap"))


def _price_gap(
    query: str,
    chosen: list[dict[str, Any]],
    products: list[dict[str, Any]],
    filters: dict[str, Any],
) -> str | None:
    if not any(
        (
            filters.get(key) is not None
            for key in ("max_price", "max_price_inclusive", "min_price")
        )
    ):
        return None
    host = _named_centre(query, products)
    if host is None or any((_sku(row) == _sku(host) for row in chosen)):
        return None
    amount = _amount(host)
    ceiling = filters.get("max_price")
    if ceiling is None:
        ceiling = filters.get("max_price_inclusive")
    floor = filters.get("min_price")
    label = _plain_name(host)
    shown = _rand(amount)
    if ceiling is not None and amount >= float(ceiling):
        return _say("price_gap", label, shown)
    if floor is not None and amount < float(floor):
        return _say("price_gap_2", label, shown)
    return None


def _uncovered_words(
    query: str, chosen: list[dict[str, Any]], products: list[dict[str, Any]]
) -> list[str]:
    brands = set()
    for row in products:
        brands.update(_tokens(str(row.get("brand") or "")))
    missing: list[str] = []
    seen: set[str] = set()
    cleaned, _parsed = _parse_constraints(query)
    for token in _tokens(cleaned):
        if token in seen or not _meaningful(token, brands):
            continue
        seen.add(token)
        if any((_word_covered(token, row) for row in chosen)):
            continue
        missing.append(token)
    return missing


def _meaningful(token: str, brands: set[str]) -> bool:
    token = " ".join(token.split())
    if (
        len(token) < 2
        or token in _FILLER
        or token in _QUERY_GLUE
        or (token in _PRICE_WORDS)
    ):
        return False
    if token.isdigit() or _pattern("thread").fullmatch(token):
        return False
    if token in frozenset(_value("colour_words", [])) or token in {
        _term("model_words", "primary_colour"),
        _term("model_words", "secondary_colour"),
    }:
        return False
    if token in frozenset(_value("modifier_words", [])) or token in frozenset(
        _value("spec_words", [])
    ):
        return False
    if token in brands or token in {
        _term("model_words", "model_prefix"),
        _term("model_words", "model_brand"),
    }:
        return False
    if len(token) >= 3 and (
        _term("model_words", "model_prefix").startswith(token)
        or _term("model_words", "model_brand").startswith(token)
    ):
        return False
    if len(token) >= 4 and (
        _similarity(token, _term("model_words", "model_prefix")) >= 0.6
        or _similarity(token, _term("model_words", "model_brand")) >= 0.6
        or any(
            (len(brand) >= 4 and _similarity(token, brand) >= 0.6 for brand in brands)
        )
    ):
        return False
    return True


def _word_covered(token: str, row: dict[str, Any]) -> bool:
    have = set(_tokens(_coverage_text(row)))
    if str(row.get("category") or "") == _term("model_words", "view_category"):
        have.update(
            {
                _term("model_words", "view_alias"),
                _term("model_words", "view_alias_plural"),
                _term("model_words", "view_word"),
                _term("model_words", "view_plural"),
                _term("model_words", "view_short"),
                _term("model_words", "view_short_plural"),
                _term("model_words", "view_colour"),
            }
        )
    if _covered_token(token, have):
        return True
    if token in {
        _term("model_words", "capacity_short"),
        _term("model_words", "capacity_plural"),
    } and (
        _covered_token(_term("model_words", "capacity_word"), have)
        or _term("model_words", "capacity_word") in have
    ):
        return True
    return _size_covers(token, row)


def _coverage_text(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("name") or ""),
        str(row.get("brand") or ""),
        str(row.get("category") or ""),
    ]
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    features = attributes.get("features") or []
    if isinstance(features, list):
        parts.extend((str(item) for item in features))
    if attributes.get("thread"):
        parts.append(str(attributes["thread"]))
    parts.extend(_option_labels(row))
    stock = attributes.get("stock") if isinstance(attributes.get("stock"), dict) else {}
    parts.append(str(stock.get("status") or ""))
    parts.append(str(stock.get("label") or ""))
    verified = attributes.get("verified") or []
    if isinstance(verified, list):
        for spec in verified:
            if not isinstance(spec, dict) or str(spec.get("name") or "") == "in_box":
                continue
            parts.append(str(spec.get("name") or ""))
            parts.append(str(spec.get("value") or ""))
    if attributes.get(_term("model_words", "count_attribute")) is not None:
        parts.append(str(attributes.get(_term("model_words", "count_attribute"))))
        parts.append(_term("model_words", "count_attribute"))
    return " ".join(parts)


def _size_covers(token: str, row: dict[str, Any]) -> bool:
    size = _size_of(row).lower().replace("×", "x").replace(" ", "")
    if token in {
        _term("model_words", "size_alias"),
        _term("model_words", "size_value"),
    } and size in {
        _term("model_words", "size_value"),
        _term("model_words", "size_alias"),
    }:
        return True
    return False


def _size_of(row: dict[str, Any]) -> str:
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    verified = attributes.get("verified") or []
    if not isinstance(verified, list):
        return ""
    for spec in verified:
        if isinstance(spec, dict) and str(spec.get("name") or "") == _value(
            "attribute_key", ""
        ):
            return str(spec.get("value") or "")
    return ""


def _count_counts(host: dict[str, Any], products: list[dict[str, Any]]) -> list[int]:
    found: set[int] = set()
    attributes = (
        host.get("attributes") if isinstance(host.get("attributes"), dict) else {}
    )
    verified = attributes.get("verified") or []
    if isinstance(verified, list):
        for spec in verified:
            if not isinstance(spec, dict) or str(spec.get("name") or "") != _term(
                "model_words", "capacity_spec"
            ):
                continue
            value = spec.get("value")
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, int | float) and (not isinstance(item, bool)):
                        found.add(int(item))
    for row in products:
        if str(row.get("category") or "") != _term("model_words", "capacity_category"):
            continue
        count = _count_count(row)
        if count:
            found.add(count)
    return sorted(found)


def _count_count(row: dict[str, Any]) -> int:
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    value = attributes.get(_term("model_words", "count_attribute"))
    if isinstance(value, int | float) and (not isinstance(value, bool)):
        return int(value)
    return 0


def _hosted_parts(
    host: dict[str, Any],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    category: str,
) -> list[dict[str, Any]]:
    family = str(host.get("family") or "")
    linked = {
        str(fit.get("part_sku") or "")
        for fit in fits
        if _positive_fit(fit)
        and (
            str(fit.get("fits_family") or "") == family
            or str(fit.get("fits_sku") or "") == _sku(host)
        )
    }
    return [
        row
        for row in products
        if str(row.get("category") or "") == category
        and (_sku(row) in linked or not linked)
    ]


def _named_centre(query: str, products: list[dict[str, Any]]) -> dict[str, Any] | None:
    words = _tokens(query)
    tokens = set(words)
    centres = [
        row
        for row in products
        if str(row.get("category") or "") == _value("centre_category", "")
    ]
    if _term("model_words", "variant_feature") in tokens:
        found = next(
            (
                row
                for row in centres
                if _term("model_words", "variant_feature")
                in set(_tokens(str(row.get("name") or "")))
            ),
            None,
        )
        if found is not None:
            return found
    letter = ""
    for index, token in enumerate(words):
        model = token in {
            _term("model_words", "model_prefix"),
            _term("model_words", "model_abbreviation"),
        } or (
            len(token) >= 3 and _term("model_words", "model_prefix").startswith(token)
        )
        if (
            model
            and index + 1 < len(words)
            and (words[index + 1] in set(_term("model_words", "variants").split()))
        ):
            letter = words[index + 1]
            break
    if not letter:
        return None
    for row in centres:
        name = set(_tokens(str(row.get("name") or "")))
        if letter in name and _term("model_words", "variant_feature") not in name:
            return row
    return None


def _plain_name(row: dict[str, Any]) -> str:
    name = str(row.get("name") or "").strip()
    brand = str(row.get("brand") or "").strip()
    first = brand.split(" ")[0] if brand else ""
    if first and name.lower().startswith(first.lower() + " "):
        return name[len(first) + 1 :]
    return name


def _mentions_view(query: str) -> bool:
    return any(
        (token in frozenset(_value("sight_words", [])) for token in _tokens(query))
    )


def _and_list(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    if len(words) == 2:
        return _say("and_list", words[0], words[1])
    return ", ".join(words[:-1]) + " and " + words[-1]


def _rand(amount: float) -> str:
    number = int(money(amount))
    text = f"{number:,}".replace(",", " ")
    return _say("rand", text)


def _offers_cuts(row: dict[str, Any]) -> bool:
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    options = (
        attributes.get("options") if isinstance(attributes.get("options"), dict) else {}
    )
    cuts = options.get(_term("model_words", "option_attribute"))
    return isinstance(cuts, list) and any((str(item or "").strip() for item in cuts))


def _cut_state(
    part_sku: str, centre: dict[str, Any] | None, fits: list[dict[str, Any]], cut: str
) -> dict[str, Any] | None:
    state = fit_state(part_sku, centre, fits, cut)
    if state is not None or centre is None or (not _offers_cuts(centre)):
        return state
    ruled = []
    for fit in fits:
        if str(fit.get("part_sku") or "") != part_sku:
            continue
        rule = fit.get("rule") if isinstance(fit.get("rule"), dict) else {}
        if str(rule.get(_term("model_words", "option_attribute")) or ""):
            ruled.append(fit)
    if not ruled:
        return None
    for fit in ruled:
        rule = fit.get("rule") if isinstance(fit.get("rule"), dict) else {}
        needed = str(rule.get(_term("model_words", "option_attribute")) or "")
        if needed == cut:
            badge = (
                _say("cut_state", needed) if needed else str(fit.get("note") or "Fits")
            )
            return {
                "part_sku": part_sku,
                "fits": True,
                "badge": badge,
                "note": str(fit.get("note") or ""),
            }
    rule = ruled[0].get("rule") if isinstance(ruled[0].get("rule"), dict) else {}
    needed = str(rule.get(_term("model_words", "option_attribute")) or "")
    note = str(ruled[0].get("note") or "")
    badge = (
        note
        if note.lower().startswith("needs")
        else _say("cut_state_2", needed)
        if needed
        else note
    )
    return {"part_sku": part_sku, "fits": False, "badge": badge, "note": note}


@_scoped
def prepare_reservation(
    setup: dict[str, Any],
    products: list[dict[str, Any]],
    fits: list[dict[str, Any]],
    centre_sku: str,
) -> str:
    by_id = {_sku(row): row for row in products}
    parts = list(setup.get("parts") or [])
    centre_row = None
    for part in parts:
        product = part.get("product") if isinstance(part.get("product"), dict) else {}
        row = by_id.get(str(product.get("id") or ""))
        if row is not None and str(row.get("category") or "") == _value(
            "centre_category", ""
        ):
            centre_row = row
            break
    cut = ""
    if centre_row is not None:
        cut = _cut_in_choice(centre_row, "")
        for part in parts:
            product = (
                part.get("product") if isinstance(part.get("product"), dict) else {}
            )
            if str(product.get("id") or "") == _sku(centre_row):
                part["choice"] = _readable_choice(
                    centre_row, str(part.get("choice") or "")
                )
                cut = _cut_in_choice(centre_row, str(part.get("choice") or "")) or cut
                break
    if not cut:
        cut = _centre_cut(_centre_row(products, centre_sku))
    for part in parts:
        product = part.get("product") if isinstance(part.get("product"), dict) else {}
        row = by_id.get(str(product.get("id") or ""))
        if row is None:
            return _say("prepare_reservation")
        choice = _readable_choice(row, str(part.get("choice") or ""))
        refused = _unknown_choice(row, choice)
        if refused:
            return refused
        part["choice"] = choice
        if _status(row) == "out_of_stock":
            return _say("prepare_reservation_2", row.get("name"))
        if centre_row is not None and _sku(row) != _sku(centre_row):
            decision, reason = _fit_decision(row, centre_row, fits, cut)
            if decision == "refuse":
                return reason
            if decision == "confirm":
                choice = str(part.get("choice") or "")
                if "fit to confirm" not in choice.lower():
                    part["choice"] = _say("prepare_reservation_3", choice).strip(" ·")
    return ""


def _readable_choice(row: dict[str, Any], choice: str) -> str:
    text = " ".join(str(choice or "").replace("\n", " ").replace("\r", " ").split())
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    options = (
        attributes.get("options") if isinstance(attributes.get("options"), dict) else {}
    )
    for values in options.values():
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                code = str(value.get("code") or "")
                label = str(value.get("label") or code)
                if code and code in text:
                    text = text.replace(code, label)
            elif isinstance(value, str) and value in text:
                continue
    return text[:200]


def _option_labels(row: dict[str, Any]) -> set[str]:
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    options = (
        attributes.get("options") if isinstance(attributes.get("options"), dict) else {}
    )
    labels: set[str] = set()
    for values in options.values():
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                if value.get("label"):
                    labels.add(str(value["label"]))
                if value.get("code"):
                    labels.add(str(value["code"]))
            elif isinstance(value, str):
                labels.add(value)
    return labels


def _fold_choice(value: str) -> str:
    return (
        value.lower()
        .replace('"', "")
        .replace("″", "")
        .replace("“", "")
        .replace("”", "")
        .replace("'", "")
        .strip()
    )


def _label_hit(piece: str, labels: set[str]) -> bool:
    folded = _fold_choice(piece)
    if not folded:
        return False
    for label in labels:
        if _fold_choice(label) == folded:
            return True
    return False


def _unknown_choice(row: dict[str, Any], choice: str) -> str:
    if not choice:
        return ""
    labels = _option_labels(row)
    if not labels:
        if re.search("\\b[a-z]+-\\d+\\b", choice):
            return _say("unknown_choice", choice, row.get("name"))
        return ""
    pieces = [piece.strip() for piece in choice.split("·")]
    for piece in pieces:
        if not piece:
            continue
        if piece == str(row.get("name") or "") or _label_hit(piece, labels):
            continue
        return _say("unknown_choice_2", piece, row.get("name"))
    return ""


def _cut_in_choice(row: dict[str, Any], choice: str) -> str:
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    options = (
        attributes.get("options") if isinstance(attributes.get("options"), dict) else {}
    )
    cuts = (
        options.get(_term("model_words", "option_attribute"))
        if isinstance(options.get(_term("model_words", "option_attribute")), list)
        else []
    )
    for cut in cuts:
        if isinstance(cut, str) and cut and (cut in choice):
            return cut
    defaults = (
        attributes.get("defaults")
        if isinstance(attributes.get("defaults"), dict)
        else {}
    )
    return str(defaults.get(_term("model_words", "option_attribute")) or "")


def _by_id(products: list[dict[str, Any]], sku: str) -> dict[str, Any] | None:
    return next((row for row in products if _sku(row) == sku), None)


def _status(row: dict[str, Any]) -> str:
    attributes = (
        row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    )
    stock = attributes.get("stock") if isinstance(attributes.get("stock"), dict) else {}
    return str(stock.get("status") or "")


def _available(row: dict[str, Any]) -> bool:
    return (
        _status(row) in _IN_STOCK
        or (row.get("availability") or {}).get("status") == "available_to_order"
    )


def _amount(row: dict[str, Any]) -> float:
    price = row.get("price")
    if isinstance(price, dict):
        return float(price.get("amount") or 0)
    return float(price or 0)


def _sku(row: dict[str, Any]) -> str:
    return str(row.get("sku") or row.get("id") or "")


def _has_filter(filters: dict[str, Any]) -> bool:
    keys = (
        "in_stock",
        "max_price",
        "max_price_inclusive",
        "min_price",
        "category",
        "sort",
    )
    return any((filters.get(key) not in (None, "", False) for key in keys))


def _merge(filters: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    merged = dict(parsed)
    merged.update(
        {key: value for key, value in filters.items() if value not in (None, "", False)}
    )
    return merged
