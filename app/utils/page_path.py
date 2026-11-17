"""The path of a page by endpoint name, the one way to build an address."""
from typing import Any

_RAISE = object()


def page_path(endpoint: str, *, default: Any = _RAISE, **values: Any) -> Any:
    """The site-relative path of an endpoint, built the same way inside and outside a request (a chat turn
    or a worker thread runs after its request has ended), so no path is written out by hand. An endpoint
    that does not exist raises, unless a default is given, which is returned instead."""
    from flask import current_app, has_request_context, url_for

    try:
        if has_request_context():
            return url_for(endpoint, **values)
        return current_app.url_map.bind("").build(endpoint, values)
    except Exception:
        if default is _RAISE:
            raise
        return default
