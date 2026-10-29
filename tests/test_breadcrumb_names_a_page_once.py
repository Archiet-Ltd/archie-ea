"""The page header's breadcrumb names a page once, even when the sidebar offers this person no link to it."""

from __future__ import annotations

import re

from flask import render_template_string

PAGE = """
{% from 'macros/page_shell.html' import page_shell with context %}
{{ page_shell('Vendors', breadcrumb=[('Home', '/'), ('Vendors', None)]) }}
"""
RECORD = """
{% from 'macros/page_shell.html' import page_shell with context %}
{{ page_shell('Acme', breadcrumb=[('Home', '/'), ('Vendors', '/applications/vendors'), ('Acme', None)]) }}
"""


def _crumbs(html):
    nav = re.search(r'<nav aria-label="Breadcrumb".*?</nav>', html, re.DOTALL).group(0)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", item)).strip()
            for item in re.findall(r"<li\b.*?</li>", nav, re.DOTALL)]


def _render(app, template, path, trail):
    """The macro is imported with context so the trail it reads is the one this test states."""
    with app.test_request_context(path):
        return render_template_string(template, page_trail=lambda: {"home": "/", "path": path, "trail": trail})


def test_an_entry_without_a_link_for_this_person_is_not_repeated(app):
    """A compliance persona reaches Vendors, which their sidebar names under Spend without a link."""
    html = _render(app, PAGE, "/applications/vendors", [("Spend", None), ("Vendors", None)])
    assert _crumbs(html) == ["Home", "Spend", "Vendors"], html


def test_an_entry_with_a_link_to_this_page_reads_the_same(app):
    html = _render(app, PAGE, "/applications/vendors", [("Spend", None), ("Vendors", "/applications/vendors")])
    assert _crumbs(html) == ["Home", "Spend", "Vendors"], html


def test_a_record_below_an_unlinked_entry_keeps_its_own_crumb(app):
    html = _render(app, RECORD, "/applications/vendors/7", [("Spend", None), ("Vendors", None)])
    assert _crumbs(html) == ["Home", "Spend", "Vendors", "Acme"], html
