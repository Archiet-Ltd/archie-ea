"""The public pricing page is built from the component macros only, and its prices are the catalogue's.

The first test scores the template with the same scorer the compliance count uses. The second renders the
page and reads every price on it against the one plan catalogue, so the two cannot drift apart.
"""

from __future__ import annotations

import importlib.util
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, "app", "templates", "public", "pricing.html")


def _scorer():
    spec = importlib.util.spec_from_file_location(
        "entelim_shadcn_compliance", os.path.join(REPO, "scripts", "qa", "entelim_shadcn_compliance.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_pricing_page_has_no_raw_controls_colours_shadows_or_inline_styles():
    with open(TEMPLATE, encoding="utf-8") as handle:
        score = _scorer().score(handle.read())
    assert score["total"] == 0, score


def test_every_price_on_the_page_is_a_price_in_the_catalogue(app, client):
    from app.services import billing_plans

    response = client.get("/pricing")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)

    startup = billing_plans.get_plan("startup")
    team = billing_plans.get_plan("team")
    enterprise = billing_plans.get_plan("enterprise")
    for expected in (
        "USD %d a year" % startup.display_price_annual,
        "USD %d a month" % startup.display_price_monthly,
        "USD %d per editor a year" % team.display_price_annual,
        "USD %d per editor a month" % team.display_price_monthly,
        "From USD %s a year" % format(enterprise.display_price_floor_annual, ","),
    ):
        assert expected in text, (expected, text[:400])
    # No other dollar figure appears in a plan card.
    cards = re.findall(r'data-testid="plan-card-[^"]+".*?</article>', html, re.S)
    assert len(cards) == 4
    allowed = {
        "USD %d" % startup.display_price_annual, "USD %d" % startup.display_price_monthly,
        "USD %d" % team.display_price_annual, "USD %d" % team.display_price_monthly,
        "USD %s" % format(enterprise.display_price_floor_annual, ","),
        # What annual billing saves: twelve months of the monthly price, less the annual price.
        "USD %d" % (12 * startup.display_price_monthly - startup.display_price_annual),
        "USD %d" % (12 * team.display_price_monthly - team.display_price_annual),
    }
    for card in cards:
        for figure in re.findall(r"USD [\d,]+", re.sub(r"<[^>]+>", " ", card)):
            assert figure in allowed, figure


def test_the_billing_period_choice_is_the_shared_radio_group(app, client):
    html = client.get("/pricing").get_data(as_text=True)
    assert html.count('type="radio"') == 2
    assert 'name="pp-interval"' in html
    assert "<fieldset" in html
