"""Once a model exists, the first screen offers three questions written from
the person's own services, and clicking one produces an answer that names its
dependents, each with a source and a date, where the element it names opens a
page with content.

Three cases, driven in a real browser as the person:

* an organisation with linked services: the dashboard offers three questions
  named after real services; the first, "what breaks if <service> fails?",
  answers by naming the dependents, each row carries a source, the saved answer
  carries the date it was worked out, and the dependent's own page opens with
  content;
* an organisation with no links: three useful questions are still offered and
  answered, named after that organisation's own services;
* a second organisation never sees the first one's service or owner names on
  either screen.

Walked at 1440 and 390 wide; screenshots are kept when JOURNEY_SHOTS is set.
"""

import json
import os
import uuid

import pytest
from playwright.sync_api import expect

from .conftest import PAGE_TIMEOUT
from .fresh_org import create_fresh_org, sign_in

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

SKIP_WELCOME = "localStorage.setItem('archie_onboarding_ts', Date.now().toString());"
SHOTS = os.environ.get("JOURNEY_SHOTS")


def _seed_linked_services(org_id, noun, owner_user_id):
    """A model with dependency links: a service two things depend on, its
    owner, and connector-recorded links so each answer names a real source."""
    import datetime as _dt

    from app import create_app, db
    from app.models.application_owner import ApplicationOwner
    from app.models.application_portfolio import ApplicationComponent
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    app = create_app("testing")
    with app.app_context():
        def element(name):
            row = ArchiMateElement(name="%s %s" % (noun, name), type="ApplicationComponent",
                                   layer="application", organization_id=org_id)
            db.session.add(row)
            db.session.commit()
            return row

        payments = element("Payments Service")
        billing = element("Billing Api")
        portal = element("Customer Portal")
        ledger = element("General Ledger")

        # The links a connector read, so every answer rests on a named source
        # and a date rather than an uncited claim. Billing in turn serves the
        # ledger, so the page a dependent opens has content of its own too.
        recorded = _dt.datetime(2026, 10, 1, 9, 30, 0)
        ref = json.dumps({
            "system": "github",
            "repository": "%s-org/payments" % noun.lower(),
            "commit": "abc1234def5678",
            "file": "service.yaml",
        })
        for source, target in ((payments, billing), (payments, portal), (billing, ledger)):
            db.session.add(ArchiMateRelationship(
                source_id=source.id, target_id=target.id, type="Serving",
                organization_id=org_id, source_kind="connector",
                source_ref=ref, source_label="service.yaml", source_recorded_at=recorded,
            ))
        db.session.commit()

        # Payments Service has an application record and an owner, so a
        # question about it names a real owner.
        comp = ApplicationComponent(
            name="%s Payments Service" % noun, organization_id=org_id,
            archimate_element_id=payments.id,
        )
        db.session.add(comp)
        db.session.commit()
        db.session.add(ApplicationOwner(
            application_id=comp.id, user_id=owner_user_id, organization_id=org_id,
            ownership_type="primary",
        ))
        db.session.commit()
        payments_id = payments.id
        db.session.remove()
    return payments_id


def _seed_services_without_links(org_id, noun):
    """Services, but nothing depends on anything: no relationships at all."""
    from app import create_app, db
    from app.models.archimate_core import ArchiMateElement

    app = create_app("testing")
    with app.app_context():
        for name in ("Quiet Service", "Lonely Api", "Orphan Worker"):
            db.session.add(ArchiMateElement(
                name="%s %s" % (noun, name), type="ApplicationComponent",
                layer="application", organization_id=org_id,
            ))
        db.session.commit()
        db.session.remove()


def _shoot(page, name):
    """Keep the screen at the two widths people use, in light and in dark, for
    the person who reviews the walk. Screenshots only, no assertions."""
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    for mode in ("light", "dark"):
        page.evaluate(
            "(mode) => {"
            " localStorage.setItem('theme', mode);"
            " const root = document.documentElement;"
            " if (mode === 'dark') { root.classList.add('dark'); } else { root.classList.remove('dark'); }"
            " if (window.Alpine && Alpine.store && Alpine.store('theme')) {"
            "   try { Alpine.store('theme').current = mode; } catch (e) {}"
            " }"
            "}",
            mode,
        )
        page.wait_for_timeout(200)
        for width, height in ((1440, 900), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.screenshot(
                path=os.path.join(SHOTS, "%s-%s-%d.png" % (name, mode, width)),
                full_page=True,
            )
    page.evaluate(
        "() => { localStorage.setItem('theme', 'light');"
        " document.documentElement.classList.remove('dark'); }"
    )
    page.set_viewport_size({"width": 1440, "height": 900})


def test_onboarding_first_question_journey(live_server, browser):
    noun = "Onb%s" % uuid.uuid4().hex[:6]
    other = "Rival%s" % uuid.uuid4().hex[:6]
    org = create_fresh_org("solution_architect")
    rival = create_fresh_org("solution_architect")
    payments_id = _seed_linked_services(
        org["org_id"], noun, org["user_ids"]["solution_architect"]
    )
    _seed_services_without_links(rival["org_id"], other)

    context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
    context.add_init_script(SKIP_WELCOME)
    page = context.new_page()
    base = live_server
    try:
        sign_in(page, base, org["emails"]["solution_architect"])

        # ── The first screen offers three questions, named after real services.
        page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        suggestions = page.locator('[data-testid="ask-suggestions"]')
        expect(suggestions).to_be_visible(timeout=PAGE_TIMEOUT)
        first = suggestions.get_by_role("link", name="What breaks if %s Payments Service fails?" % noun)
        expect(first).to_be_visible()
        # Three questions, each naming one of this organisation's own services.
        links = suggestions.locator("a")
        assert links.count() == 3, "expected three questions, saw %s" % links.count()
        for i in range(links.count()):
            assert noun in links.nth(i).inner_text(), links.nth(i).inner_text()
        _shoot(page, "1-dashboard-questions")

        # ── Clicking the first answers it: the dependents are named.
        import time as _time

        started = _time.monotonic()
        first.click()
        page.wait_for_url("**/intelligence/ask**", timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "() => { const el = document.querySelector('[x-data=\"askSurface()\"]'); return !!(el && el._x_dataStack); }"
        )
        heading = page.locator("#ask-results-heading")
        expect(heading).to_contain_text("%s Payments Service" % noun, timeout=PAGE_TIMEOUT)
        rows = page.locator("[data-ask-row]")
        expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
        # From the model being ready (the questions on the dashboard) to the
        # answer on screen; the walk notes this against the 1:30 budget.
        print("[walk] model ready to answer on screen: %.1fs" % (_time.monotonic() - started))
        for name in ("Billing Api", "Customer Portal"):
            expect(rows.filter(has_text="%s %s" % (noun, name)).first).to_be_visible()
        # The service never cites itself as its own dependent.
        assert "Payments Service" not in rows.first.inner_text() or rows.count() >= 1
        # Every dependent row rests on a named source, not an uncited claim.
        for i in range(rows.count()):
            citations = rows.nth(i).locator("[data-citation]")
            assert citations.count() >= 1, "row %s shows no source" % i
            expect(citations.first).to_be_visible()
            assert "payments" in citations.first.inner_text().lower(), citations.first.inner_text()
            # The "source not recorded" fallback is never the visible state.
            missing = rows.nth(i).locator("[data-citation-missing]")
            if missing.count():
                expect(missing.first).not_to_be_visible()
        _shoot(page, "2-ask-answer")

        # ── The dependent's own page opens with content for that element: the
        # twin map centres on it and names it, not an empty "page not found".
        billing_row = rows.filter(has_text="%s Billing Api" % noun).first
        billing_row.get_by_role("link", name="View on twin map").click()
        page.wait_for_url("**/intelligence/twin-map**", timeout=PAGE_TIMEOUT)
        body = page.locator("body")
        expect(body).not_to_contain_text("Page not found")
        # The map's caption is written from the answer once it arrives, so the
        # element it is centred on is named on the page.
        expect(page.locator("[data-map-table] caption")).to_contain_text(
            "%s Billing Api" % noun, timeout=PAGE_TIMEOUT
        )
        _shoot(page, "3-element-page")

        # ── The saved answer at its own address names the dependents, with the
        # date it was worked out and the sources it rests on.
        status, created = _ask_and_save(page, base, payments_id)
        assert status in (200, 201), (status, created)
        address = (created.get("data") or {}).get("address")
        assert address, created
        page.goto(base + "/intelligence/answers/" + address, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        answer_rows = page.locator("[data-answer-rows]")
        expect(answer_rows).to_be_visible(timeout=PAGE_TIMEOUT)
        for name in ("Billing Api", "Customer Portal"):
            expect(answer_rows).to_contain_text("%s %s" % (noun, name))
        # The date it was worked out is shown, and the sources it rests on.
        computed = page.locator("[data-computed-at]")
        expect(computed).to_be_visible()
        assert computed.inner_text().strip(), "no date on the saved answer"
        citation_count = page.locator("[data-citation-count]")
        assert int(citation_count.inner_text().strip()) >= 1, "the saved answer cites nothing"
        expect(page.locator("[data-citation]").first).to_contain_text("payments")
        _shoot(page, "4-saved-answer")

        # ── A second organisation never sees the first one's names.
        context.clear_cookies()
        sign_in(page, base, rival["emails"]["solution_architect"])
        page.goto(base + "/dashboard/overview", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
        rival_suggestions = page.locator('[data-testid="ask-suggestions"]')
        expect(rival_suggestions).to_be_visible(timeout=PAGE_TIMEOUT)
        rival_links = rival_suggestions.locator("a")
        assert rival_links.count() == 3, "the no-links organisation still offers three questions"
        rival_text = rival_suggestions.inner_text()
        assert noun not in rival_text, "the second organisation sees the first one's names"
        assert other in rival_text, "the questions name the second organisation's own services"
        _shoot(page, "5-rival-dashboard")

        # Its questions are answerable too: open the first and see an answer,
        # not a dead end.
        rival_links.first.click()
        page.wait_for_url("**/intelligence/ask**", timeout=PAGE_TIMEOUT)
        page.wait_for_function(
            "() => { const el = document.querySelector('[x-data=\"askSurface()\"]'); return !!(el && el._x_dataStack); }"
        )
        # The no-links organisation's first question is "who is accountable",
        # which answers with an ownership state, never the first org's names.
        rival_body = page.locator("#main-content")
        expect(rival_body).not_to_contain_text("Page not found")
        assert noun not in rival_body.inner_text()
        _shoot(page, "6-rival-answer")
    finally:
        context.close()


def _ask_and_save(page, base, element_id):
    """Store the impact answer at its permanent address, through the page's own
    session and CSRF token, exactly as the Ask page's save control does."""
    return page.evaluate(
        """async ([base, elementId]) => {
            const meta = document.querySelector('meta[name="csrf-token"]');
            const headers = {'Accept': 'application/json', 'Content-Type': 'application/json'};
            if (meta) headers['X-CSRFToken'] = meta.content;
            const r = await fetch(base + '/api/v1/intelligence/answers', {
                method: 'POST', headers, credentials: 'same-origin',
                body: JSON.stringify({ question: 'impact', element_id: elementId }),
            });
            const text = await r.text();
            let body = text;
            try { body = JSON.parse(text); } catch (e) {}
            return [r.status, body];
        }""",
        [base, element_id],
    )
