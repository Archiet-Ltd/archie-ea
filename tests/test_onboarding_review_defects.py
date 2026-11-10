"""The defects the review of the sign-up promises found, each shown as the page a person reads.

Two organisations are used wherever data is involved: a person in one organisation never sees the other
organisation's counts, and a read that found nothing in one is not reported for the other.
"""

from __future__ import annotations

import html as html_module
import re

import pytest
from sqlalchemy import text

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


def test_the_status_count_is_every_row_the_read_put_in_the_model(db_session, org_a, org_b, people, first_sync, monkeypatch, tenant_ctx, signed_in):
    """One count of what is in the model: 12 elements (the technology among them) and 9 relationships."""
    from app.connectors import github as gh
    from app.modules.account.services import onboarding_service as onboarding

    org_a_id, org_b_id, connector_id, admin_id = org_a.id, org_b.id, first_sync.cfg.id, people.admin.id
    monkeypatch.setattr(gh, "run_first_sync", lambda org_id, connector_id: {"success": True, "repositories": 16})
    result = onboarding.run_github_import(org_a_id, connector_id, admin_id)
    counts = gh.model_counts(org_a_id)
    assert counts == {"applications": 8, "components": 3, "technologies": 1, "relationships": 9}, counts
    assert result["added"] == 8 + 3 + 1 + 9, result
    rows = db_session.execute(
        text(
            "SELECT (SELECT count(*) FROM archimate_elements WHERE organization_id = :o AND source_kind = 'connector') "
            "+ (SELECT count(*) FROM archimate_relationships WHERE organization_id = :o AND source_kind = 'connector')"
        ),
        {"o": org_a_id},
    ).scalar()
    assert result["added"] == rows
    assert gh.model_counts(org_b_id) == {"applications": 0, "components": 0, "technologies": 0, "relationships": 0}


def test_the_review_page_names_every_kind_of_row_the_read_added(signed_in, first_sync):
    assert signed_in.post("/admin/connectors/github/review/approve-all").status_code in (302, 303)
    text = _words(signed_in.get("/admin/connectors/github/review"))
    found = re.search(r"already in your model: ([^.]*)\.", text)
    assert found, text[-600:]
    assert found.group(1) == "8 applications, 3 parts, 1 technology, 9 links", found.group(1)


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


def _pages_with_payment(monkeypatch, app, opened):
    from app.services import legal_pages, public_pages

    monkeypatch.setitem(app.config, legal_pages.LEGAL_PAGES_FLAG, True)
    monkeypatch.setattr(public_pages, "_payment_open", lambda: opened)
    if hasattr(public_pages._pages_by_url, "cache_clear"):
        public_pages._pages_by_url.cache_clear()


@pytest.mark.parametrize("url", ["/team-annual-onboarding", "/refund-policy"])
def test_cancelling_from_the_billing_page_is_promised_only_while_payment_is_open(app, client, monkeypatch, url):
    _pages_with_payment(monkeypatch, app, False)
    closed = _words(client.get(url))
    assert "from the billing page" not in closed, closed
    assert "contacting us" in closed

    _pages_with_payment(monkeypatch, app, True)
    opened = _words(client.get(url))
    assert "from the billing page" in opened


def test_the_closed_billing_page_describes_only_the_plan_and_its_limits(signed_in):
    from app.services import billing_plans

    assert not billing_plans.payment_is_open()
    html = signed_in.get("/admin/billing/").get_data(as_text=True)
    description = re.search(r'<p[^>]*class="[^"]*text-muted-foreground[^"]*"[^>]*>\s*(Your organisation[^<]*)</p>', html)
    assert description, "the page description is missing"
    assert html_module.unescape(description.group(1)).strip() == "Your organisation's plan and its limits.", description.group(1)


def test_no_text_in_the_application_calls_the_ai_settings_page_by_its_old_name():
    """One name everywhere: the page, the menu entries and every error text that sends a person there."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    hits = []
    for path in root.rglob("*"):
        if path.suffix in (".py", ".js", ".html") and path.is_file():
            for number, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
                if "API Settings" in line:
                    hits.append("%s:%d" % (path.relative_to(root.parent), number))
    assert not hits, hits


def test_the_ai_settings_page_opened_from_sign_up_offers_the_way_back(signed_in):
    opened = signed_in.get("/admin/api-settings?from=signup").get_data(as_text=True)
    assert 'data-testid="ai-settings-back-to-signup"' in opened
    assert "/account/welcome?step=4" in opened
    on_its_own = signed_in.get("/admin/api-settings").get_data(as_text=True)
    assert "ai-settings-back-to-signup" not in on_its_own


def test_a_wrong_master_key_reaches_the_person_as_a_plain_sentence(db_session, org_a, people, acme, connect, tenant_ctx, monkeypatch, caplog):
    """The server's key is not the one the organisation's key was saved with: the read says what failed
    and who can fix it, never the library's error class; the log keeps the class."""
    from cryptography.fernet import Fernet

    from app.connectors import github as gh
    from app.modules.codegen.services import credential_encryption as encryption

    cfg = connect(org_a, token="placeholder-read-only-token")
    db_session.commit()
    monkeypatch.setitem(encryption.current_app.config, "ORG_ENCRYPTION_MASTER_KEY", Fernet.generate_key().decode())
    with caplog.at_level("ERROR"):
        result = gh.run_first_sync(org_a.id, cfg.id)
    assert result["success"] is False, result
    sentence = result["error"]
    assert "InvalidToken" not in sentence and "CredentialUnreadable" not in sentence, sentence
    assert "encryption key" in sentence and "runs this installation" in sentence, sentence
    assert "InvalidToken" in caplog.text


def test_saving_a_key_opened_from_sign_up_keeps_the_way_back(signed_in):
    form = {"provider": "openai", "key_label": "signup-key", "api_key": "placeholder-key-for-the-test", "enabled": "y",
            "default_model": "", "max_tokens": "4000", "temperature": "0.7"}
    opened = signed_in.get("/admin/api-settings?from=signup").get_data(as_text=True)
    assert "/admin/api-settings?from=signup" in opened  # the form posts where the person is
    saved = signed_in.post("/admin/api-settings?from=signup", data=form, follow_redirects=True)
    page = saved.get_data(as_text=True)
    assert "saved successfully" in page
    assert 'data-testid="ai-settings-back-to-signup"' in page
    assert "/account/welcome?step=4" in page
    alone = signed_in.post("/admin/api-settings", data={**form, "key_label": "other-key"}, follow_redirects=True)
    assert "ai-settings-back-to-signup" not in alone.get_data(as_text=True)
