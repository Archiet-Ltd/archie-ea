"""The defects the review of the sign-up promises found, each shown as the page a person reads.

Two organisations are used wherever data is involved: a person in one organisation never sees the other
organisation's counts, and a read that found nothing in one is not reported for the other.
"""

from __future__ import annotations

import re

import pytest

from tests.test_github_builds_the_model import (  # noqa: F401  (fixtures)
    acme,
    approved,
    connect,
    first_sync,
    github,
    org_a,
    org_b,
    people,
    signed_in,
    sync,
)

pytestmark = pytest.mark.usefixtures("db_session")


def _words(response):
    return " ".join(re.sub(r"<[^>]+>", " ", response.get_data(as_text=True)).split())


def _state(monkeypatch, **state):
    from app.modules.admin import connector_routes

    monkeypatch.setattr(connector_routes, "_github_state", lambda org_id: {"counts": {}, "job": None, **state})


def test_the_status_count_is_the_number_the_review_page_adds_up(db_session, org_a, org_b, people, first_sync, monkeypatch, tenant_ctx):
    """One count of what is in the model: the approvals made are 23, the model holds 8 + 3 + 9."""
    from app.connectors import github as gh
    from app.modules.account.services import onboarding_service as onboarding

    org_a_id, org_b_id, connector_id, admin_id = org_a.id, org_b.id, first_sync.cfg.id, people.admin.id
    monkeypatch.setattr(gh, "run_first_sync", lambda org_id, connector_id: {"success": True, "repositories": 16})
    result = onboarding.run_github_import(org_a_id, connector_id, admin_id)
    counts = gh.model_counts(org_a_id)
    assert (counts["applications"], counts["components"], counts["relationships"]) == (8, 3, 9)
    assert result["added"] == 8 + 3 + 9, result
    assert gh.model_counts(org_b_id) == {"applications": 0, "components": 0, "relationships": 0}


def test_a_connected_organisation_whose_read_found_nothing_is_told_so(signed_in, monkeypatch):
    _state(monkeypatch, state="done", organization="acme", counts={"repositories": 0, "pending": 0})
    text = _words(signed_in.get("/admin/connectors/github/review"))
    assert "found no repositories" in text, text
    assert "Connect GitHub, or read your repositories again" not in text
    assert "Check the GitHub page" in text


def test_a_read_that_failed_says_it_failed_and_offers_to_read_again(signed_in, monkeypatch):
    _state(monkeypatch, state="failed", organization="acme", job={"error": "The token was refused."})
    text = _words(signed_in.get("/admin/connectors/github/review"))
    assert "could not finish: The token was refused." in text, text
    assert "Read again" in text
    assert "Connect GitHub, or read your repositories again" not in text


def test_an_organisation_that_never_connected_is_told_to_connect(signed_in, monkeypatch):
    _state(monkeypatch, state="not_connected", organization=None)
    text = _words(signed_in.get("/admin/connectors/github/review"))
    assert "Connect GitHub, or read your repositories again" in text


def test_the_review_sentence_is_in_the_wrapping_form_of_the_alert(signed_in, monkeypatch):
    """A text argument to the alert is cut to one line; the call form wraps, so a phone reads all of it."""
    _state(monkeypatch, state="done", organization="acme", counts={"repositories": 0, "pending": 0})
    html = signed_in.get("/admin/connectors/github/review").get_data(as_text=True)
    sentence = re.search(r'<span class="([^"]*)">The last read found no repositories', html)
    assert sentence, "the sentence is not inside the alert's wrapping body"
    assert "truncate" not in sentence.group(1)


def test_role_pages_offer_no_payment_page_while_payment_is_closed(client):
    from app.services import billing_plans

    assert not billing_plans.payment_is_open()
    text = _words(client.get("/roles/cto-and-cio"))
    assert "payment page" not in text and "Annual billing is selected" not in text, text
    assert "Paid plans are set up in a conversation with us" in text


def test_the_pricing_page_explains_start_free_and_promises_no_invoice_or_payment_page(client):
    text = _words(client.get("/pricing"))
    assert "Start free puts you on Community. Startup is set up in a conversation with us." in text
    assert "Start free puts you on Community. Team is set up in a conversation with us." in text
    for promise in ("payment page", "Each payment produces an invoice", "from the billing page at any time", "cancels from the billing page"):
        assert promise not in text, promise


def test_the_features_page_promises_buying_only_while_payment_is_open(client, monkeypatch):
    from app.services import public_pages

    closed = _words(client.get("/features"))
    assert "buy, change and cancel yourself" not in closed
    assert "paid plans set up in a conversation with us" in closed

    monkeypatch.setattr(public_pages, "_payment_open", lambda: True)
    public_pages._pages_by_url.cache_clear() if hasattr(public_pages._pages_by_url, "cache_clear") else None
    opened = _words(client.get("/features"))
    assert "plans you can buy, change and cancel yourself" in opened


def test_the_billing_page_says_nothing_about_paying_while_payment_is_closed(signed_in):
    text = _words(signed_in.get("/admin/billing/?plan=startup"))
    assert "Read them before you pay" not in text, text
    assert "Invoices will appear here" not in text and "once online payment is open" not in text
    assert "The terms that apply to your plan" in text


def test_one_name_for_the_organisations_ai_settings(signed_in, client):
    page = _words(signed_in.get("/admin/api-settings"))
    assert "AI settings" in page
    admin_home = _words(signed_in.get("/admin/"))
    assert "API Settings" not in admin_home, admin_home
    assert "AI settings" in admin_home
    assert "API Settings" not in _words(client.get("/account/register"))
