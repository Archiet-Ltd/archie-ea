"""No public page, description or heading speaks of releases, quarters or dates for what is not open yet.

A page that is not open to everyone says so plainly ("Early access") and offers the waiting list; it never says
which release it belongs to or on what day it will open, because a visitor reads that as a promise.
"""

from __future__ import annotations

import re

import pytest

from app.services.public_pages import CONTENT_ROOT

TIMELINE = re.compile(
    r"later release|next release|upcoming release|first release|in a release"
    r"|\bin q[1-4]\b|the day it (?:ships|opens)|\bwave [0-9]\b",
    re.I,
)

# A quarter, or a published roadmap, offered as when something will exist. A roadmap the product builds for a
# customer's own estate is not a timetable and is not matched.
TIMETABLE = re.compile(
    r"\b(?:has|have|with|in) a quarter\b|\bquarter on\b|\bq[1-4]\b"
    r"|\b(?:published|public|product) roadmap\b",
    re.I,
)


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _front_matter_split(raw):
    parts = raw.split("---", 2)
    return parts[2] if raw.startswith("---") and len(parts) == 3 else raw


@pytest.fixture
def wave1(app, _schema, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    return app


def test_the_early_access_hub_and_every_group_page_use_customer_words(wave1):
    client = wave1.test_client()
    html = client.get("/early-access").get_data(as_text=True)
    description = re.search(r'<meta name="description" content="([^"]*)"', html).group(1)
    assert description, "the hub has no description"
    assert not TIMELINE.search(description), description
    assert not TIMELINE.search(_text(html)), TIMELINE.search(_text(html)).group(0)
    slugs = re.findall(r'href="(/early-access/[a-z0-9-]+)"', html)
    assert slugs, "the hub links to no early-access page"
    for path in slugs:
        page = client.get(path).get_data(as_text=True)
        meta = re.search(r'<meta name="description" content="([^"]*)"', page).group(1)
        assert not TIMELINE.search(meta), (path, meta)
        assert not TIMELINE.search(_text(page)), (path, TIMELINE.search(_text(page)).group(0))


def test_no_published_content_page_speaks_of_a_release_or_a_date_it_opens():
    problems = []
    for path in sorted(CONTENT_ROOT.rglob("*.md")):
        body = _front_matter_split(path.read_text(encoding="utf-8"))
        found = TIMELINE.search(body)
        if found:
            problems.append("%s: %s" % (path.relative_to(CONTENT_ROOT), found.group(0)))
    assert not problems, "\n".join(problems)


def test_the_early_access_notices_in_templates_do_not_promise_a_day(app):
    from pathlib import Path

    root = Path(app.root_path) / "templates" / "public"
    problems = []
    for path in sorted(root.rglob("*.html")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("{#"):
                continue
            found = TIMELINE.search(line)
            if found:
                problems.append("%s: %s" % (path.name, found.group(0)))
    assert not problems, "\n".join(problems)


def test_the_home_page_and_the_features_page_promise_no_timetable(app, _schema):
    client = app.test_client()
    problems = []
    for path in ("/", "/features"):
        response = client.get(path)
        assert response.status_code == 200, path
        text = _text(response.get_data(as_text=True))
        assert "Coming soon" in text or path == "/", path
        found = TIMETABLE.search(text)
        if found:
            problems.append("%s: %s" % (path, found.group(0)))
    assert not problems, "\n".join(problems)


def test_no_published_content_page_or_public_template_promises_a_timetable(app):
    from pathlib import Path

    problems = []
    for path in sorted(CONTENT_ROOT.rglob("*.md")):
        found = TIMETABLE.search(_front_matter_split(path.read_text(encoding="utf-8")))
        if found:
            problems.append("%s: %s" % (path.relative_to(CONTENT_ROOT), found.group(0)))
    templates = Path(app.root_path) / "templates"
    for path in sorted(list((templates / "public").rglob("*.html")) + [templates / "main" / "index.html"]):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("{#"):
                continue
            found = TIMETABLE.search(line)
            if found:
                problems.append("%s: %s" % (path.name, found.group(0)))
    assert not problems, "\n".join(problems)
