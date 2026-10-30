"""The public content pages are converted once per change to the content, and safely from many threads (T-perf01).

The roles, comparison and use-case pages each asked for every content page, twice, and the markdown and
sanitising work ran for all of them on every request: about 1.4 s of processor time per visit on the roles page.
"""
from __future__ import annotations

import threading


def _forget_loaded_pages(public_pages):
    """Start from nothing loaded, whichever way the module remembers what it has read."""
    for name in ("_all_pages_for_fingerprint", "_pages_by_url_for_fingerprint"):
        remembered = getattr(public_pages, name, None)
        if remembered is not None:
            remembered.cache_clear()


def test_public_content_is_read_once_per_change_not_once_per_request():
    """The roles, comparison and use-case pages ask for every content page; the files are converted once."""
    from app.services import public_pages

    _forget_loaded_pages(public_pages)
    calls = []
    real = public_pages._load_page

    def spy(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)

    public_pages._load_page = spy
    try:
        first = public_pages.load_all_pages()
        loaded = len(calls)
        for _ in range(3):
            assert [p.url for p in public_pages.load_feed_pages()]
        again = public_pages.load_all_pages()
    finally:
        public_pages._load_page = real
    assert loaded == len(first) > 0
    assert len(calls) == loaded, "content was converted again after the first read"
    assert [p.url for p in again] == [p.url for p in first]


def test_public_content_converts_safely_from_many_threads():
    """Under load the roles page answered 500 when two requests converted at once."""
    from app.services import public_pages

    _forget_loaded_pages(public_pages)
    errors = []

    def read():
        try:
            assert public_pages.load_all_pages()
        except Exception as exc:  # noqa: BLE001 - the test reports any failure
            errors.append(repr(exc))

    threads = [threading.Thread(target=read) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
