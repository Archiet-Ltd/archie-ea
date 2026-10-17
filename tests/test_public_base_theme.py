"""Public marketing theme opt-in: layouts/public_base.html's `body_theme` block.

T-001 defines `.theme-marketing` / `.dark .theme-marketing` in shadcn_tokens.css and the opt-in block
that sets it on <body>; the public marketing templates opt in. This proves the
scope mechanism itself works before anything depends on it: a template that sets the block renders the
class, and the home page sets it (homepage redesign) and the login page, which does not, does not carry it.
"""

from __future__ import annotations

import re

from flask import render_template_string, url_for

BODY_CLASS = re.compile(r'<body\s+class="([^"]*)"')


def _body_classes(html: str) -> list[str]:
    match = BODY_CLASS.search(html)
    assert match, "no <body class=...> found in rendered HTML"
    return match.group(1).split()


# Test-only template string (never written to app/templates): extends the real layout and sets the
# block the way a marketing template will in a later task.
PROBE_TEMPLATE = """{% extends 'layouts/public_base.html' %}
{% block title %}T-001 body_theme probe{% endblock %}
{% block body_theme %}theme-marketing{% endblock %}
{% block content %}<p>probe</p>{% endblock %}
"""


def test_a_template_that_sets_body_theme_renders_the_marketing_class(app, _schema):
    with app.test_request_context("/"):
        html = render_template_string(PROBE_TEMPLATE)
    classes = _body_classes(html)
    assert "theme-marketing" in classes
    assert "public-site" in classes  # the block is additive to the existing body classes, not a replacement


import pytest


@pytest.mark.parametrize("path", ["/", "/pricing", "/modules/arb", "/vs/leanix", "/vs", "/use-cases", "/features"])
def test_marketing_pages_set_the_marketing_class(app, _schema, path):
    with app.test_client() as client:
        resp = client.get(path, follow_redirects=True)
    assert resp.status_code == 200
    assert "theme-marketing" in _body_classes(resp.get_data(as_text=True))


def test_the_registration_page_does_not_set_the_marketing_class(app, _schema):
    with app.test_request_context():
        register_url = url_for("account.register")
    with app.test_client() as client:
        resp = client.get(register_url)
    assert resp.status_code == 200
    assert "theme-marketing" in _body_classes(resp.get_data(as_text=True))


def test_the_login_page_does_not_set_the_marketing_class(app, _schema):
    with app.test_request_context():
        login_url = url_for("account.login")
    with app.test_client() as client:
        resp = client.get(login_url)
    assert resp.status_code == 200
    assert "theme-marketing" not in _body_classes(resp.get_data(as_text=True))
