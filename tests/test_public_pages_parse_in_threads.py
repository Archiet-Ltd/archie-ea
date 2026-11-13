"""Two request threads that render content pages at the same moment each get the whole page.

The server answers requests on several threads. A closed address answered with its early-access page reads the
features page while another thread may be rendering it for a visitor; neither may break the other.
"""

from __future__ import annotations

import threading


def test_pages_rendered_at_once_on_several_threads_all_render_whole(app):
    from app.services.public_pages import load_page

    with app.app_context():
        expected = str(load_page("site", slug="features").body_html)
    errors: list[BaseException] = []
    bodies: list[str] = []
    start = threading.Barrier(6)

    def render():
        with app.app_context():
            start.wait()
            for _ in range(15):
                try:
                    bodies.append(str(load_page("site", slug="features").body_html))
                except BaseException as exc:  # noqa: BLE001 - the failure is what is counted
                    errors.append(exc)

    threads = [threading.Thread(target=render) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, "a page broke while another thread rendered: %r" % errors[:3]
    assert bodies and all(body == expected for body in bodies), "a page rendered differently while another thread rendered"
