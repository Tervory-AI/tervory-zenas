"""Walk HTTP routes, including routers FastAPI mounts as wrappers.

FastAPI 0.162 puts an included router on ``app.routes`` as an object with no
``path``. The real routes stay on ``original_router.routes``.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any


def iter_http_routes(routes: Iterable[Any]) -> Iterator[Any]:
    seen: set[int] = set()

    def walk(items: Iterable[Any]) -> Iterator[Any]:
        for route in items:
            marker = id(route)
            if marker in seen:
                continue
            seen.add(marker)
            original = getattr(route, "original_router", None)
            if original is not None:
                yield from walk(getattr(original, "routes", ()) or ())
                continue
            if isinstance(getattr(route, "path", None), str):
                yield route
            nested = getattr(route, "routes", None)
            if nested:
                yield from walk(nested)

    yield from walk(routes)


def http_paths(routes: Iterable[Any]) -> set[str]:
    return {route.path for route in iter_http_routes(routes)}
