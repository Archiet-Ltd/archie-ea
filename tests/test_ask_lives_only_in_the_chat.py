"""The old Ask page is gone: nothing outside the AI chat links to it.

Its address only redirects into the chat, so a page that links to it sends a person through a
detour and a page that builds the address by name keeps the old screen alive. Buttons that open
the chat are fine and are not matched here.
"""
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
# The address of the page itself; the JSON interface under /api/v1 is a different thing.
PAGE = "/intelligence" + "/ask"
RETIRED_ADDRESS = re.compile(r"(?<!v1)" + PAGE + r"(?![\w./-])|url_for\(\s*['\"]intelligence_ui\.ask['\"]")
# The redirect itself is the one place allowed to name it.
ALLOWED = {pathlib.PurePosixPath("modules/intelligence/routes/ui.py")}
SUFFIXES = {".py", ".html", ".js", ".md"}


def offenders(root: pathlib.Path):
    found = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in SUFFIXES or not path.is_file() or "bundles" in path.parts:
            continue
        rel = pathlib.PurePosixPath(path.relative_to(root).as_posix())
        if rel in ALLOWED or "tests" in path.parts:
            continue
        if RETIRED_ADDRESS.search(path.read_text(encoding="utf-8", errors="replace")):
            found.append(str(rel))
    return found


def test_no_page_links_to_the_retired_ask_address():
    assert offenders(APP) == []


def test_the_guard_sees_a_link_when_one_returns(tmp_path):
    (tmp_path / "page.html").write_text('<a href="' + PAGE + '?q=x">Ask</a>')
    (tmp_path / "chat.html").write_text('<a href="/ai-chat/?q=x">Ask</a>')
    (tmp_path / "api.js").write_text("fetch('/api/v1/intelligence/ask')")
    assert offenders(tmp_path) == ["page.html"]

