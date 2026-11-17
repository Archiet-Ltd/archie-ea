"""The public vendor catalogue (ADR-0021): the review rule, what organisations
see, that they cannot write to it, and that each sees only its own links."""

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.vendor.vendor_catalogue import (
    UnsourcedFactError,
    VendorCatalogueFact,
    VendorCatalogueLink,
)


def _catalogue_product(db_session, slug=None):
    from app.models.vendor.vendor_organization import VendorOrganization
    from app.models.vendor.vendor_product import VendorProductDetail, VendorProductFamily

    tag = uuid.uuid4().hex[:8]
    vendor = VendorOrganization(name=f"Vendor {tag}")
    db_session.add(vendor)
    db_session.flush()
    family = VendorProductFamily(vendor_id=vendor.id, family_name=f"Family {tag}", family_code=f"T-{tag}")
    db_session.add(family)
    db_session.flush()
    product = VendorProductDetail(family_id=family.id, product_name=f"Product {tag}",
                                  catalogue_slug=slug or f"test-{tag}")
    db_session.add(product)
    db_session.flush()
    return vendor, family, product


def _fact(db_session, product, name, source=True, publish=False):
    fact = VendorCatalogueFact(
        product_detail_id=product.id, kind="certification", name=name,
        source_url="https://example.com/trust" if source else None,
        checked_on=date(2026, 10, 25) if source else None,
    )
    db_session.add(fact)
    db_session.flush()
    if publish:
        fact.publish("Reviewer")
        db_session.flush()
    return fact


# ── The review rule ────────────────────────────────────────────────────────────


def test_a_fact_without_a_source_cannot_be_published(db_session):
    _, _, product = _catalogue_product(db_session)
    fact = _fact(db_session, product, "SOC 2", source=False)
    with pytest.raises(UnsourcedFactError):
        fact.publish("Reviewer")
    with pytest.raises(UnsourcedFactError):
        fact.review_state = "published"
    assert fact.review_state == "draft"


def test_a_fact_with_a_source_but_no_check_date_cannot_be_published(db_session):
    _, _, product = _catalogue_product(db_session)
    fact = _fact(db_session, product, "ISO 27001")
    fact.checked_on = None
    with pytest.raises(UnsourcedFactError):
        fact.publish("Reviewer")


def test_the_database_refuses_an_unsourced_published_fact(db_session):
    _, _, product = _catalogue_product(db_session)
    fact = _fact(db_session, product, "FedRAMP", source=False)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                VendorCatalogueFact.__table__.update()
                .where(VendorCatalogueFact.id == fact.id)
                .values(review_state="published", reviewed_at=datetime.utcnow())
            )


def test_publishing_a_sourced_fact_records_who_reviewed_it(db_session):
    _, _, product = _catalogue_product(db_session)
    fact = _fact(db_session, product, "PCI DSS", publish=True)
    assert fact.review_state == "published"
    assert fact.reviewed_by_name == "Reviewer" and fact.reviewed_at is not None


def test_research_never_publishes_and_unconfirmed_facts_have_no_check_date(db_session):
    from app.services import vendor_catalogue_service as svc

    tag = uuid.uuid4().hex[:6]
    entry = {
        "slug": f"res-{tag}", "name": f"Research {tag}", "vendor": f"Research vendor {tag}",
        "category": "Test",
        "facts": [
            {"kind": "certification", "name": "SOC 2", "source_url": "https://a.example/trust", "evidence": "soc 2"},
            {"kind": "certification", "name": "ISO 27001", "source_url": "https://a.example/trust", "evidence": "iso 27001"},
            {"kind": "data_region", "name": "EU", "source_url": "https://b.example/", "evidence": "eu"},
        ],
    }
    pages = {
        "https://a.example/trust": svc.FetchResult(True, 200, "<p>We hold a SOC 2 Type II report.</p>"),
        "https://b.example/": svc.FetchResult(False, None, ""),
    }
    report = svc.research_product(entry, fetch=pages.__getitem__, today=date(2026, 10, 25))
    assert report["confirmed"] == ["SOC 2"]
    product = svc.catalogue_product(entry["slug"])
    facts = {f.name: f for f in product.catalogue_facts}
    assert all(f.review_state == "draft" for f in facts.values())
    assert facts["SOC 2"].checked_on == date(2026, 10, 25)
    assert facts["ISO 27001"].checked_on is None and facts["EU"].checked_on is None
    result = svc.publish_product(entry["slug"], "Reviewer")
    assert result == {"slug": entry["slug"], "published": 1, "held": 2}


# ── What organisations see ─────────────────────────────────────────────────────


def _org_user(db_session, make_org, label):
    from app.models.user import User

    org = make_org(label)
    user = User(email=f"{label}-{uuid.uuid4().hex[:8]}@example.com", first_name=label, last_name="CTO",
                organization_id=org.id, enterprise_role="cto", confirmed=True)
    user.password = "Passw0rd!Passw0rd"
    db_session.add(user)
    db_session.flush()
    return org, user


def test_organisations_see_only_published_facts(db_session, make_org, client, login_as):
    _, _, product = _catalogue_product(db_session)
    _fact(db_session, product, "Published certificate", publish=True)
    _fact(db_session, product, "Draft certificate")
    _, user = _org_user(db_session, make_org, "viewer")
    login_as(client, user)
    resp = client.get(f"/applications/vendors/catalogue/{product.catalogue_slug}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Published certificate" in body and "Draft certificate" not in body
    login_as(client, user)
    listing = client.get("/applications/vendors/catalogue").get_data(as_text=True)
    assert product.product_name in listing


def test_a_product_with_no_published_fact_is_not_shown(db_session, make_org, client, login_as):
    _, _, product = _catalogue_product(db_session)
    _fact(db_session, product, "Only a draft")
    _, user = _org_user(db_session, make_org, "viewer2")
    login_as(client, user)
    assert client.get(f"/applications/vendors/catalogue/{product.catalogue_slug}").status_code == 404
    login_as(client, user)
    assert product.product_name not in client.get("/applications/vendors/catalogue").get_data(as_text=True)


def test_an_organisation_cannot_write_to_the_catalogue(db_session, make_org, client, login_as, app):
    _, _, product = _catalogue_product(db_session)
    _fact(db_session, product, "Certificate", publish=True)
    _, user = _org_user(db_session, make_org, "writer")
    # No route writes catalogue facts: only GET exists on the catalogue pages.
    catalogue_rules = [r for r in app.url_map.iter_rules() if "/vendors/catalogue" in r.rule]
    writes = {m for r in catalogue_rules if not r.rule.endswith("/link") for m in r.methods}
    assert writes <= {"GET", "HEAD", "OPTIONS"}
    login_as(client, user)
    resp = client.post(f"/applications/vendors/catalogue/{product.catalogue_slug}",
                       data={"name": "Forged"})
    assert resp.status_code in (400, 405)
    assert VendorCatalogueFact.query.filter_by(product_detail_id=product.id, name="Forged").count() == 0


# ── Two organisations ──────────────────────────────────────────────────────────


def _uses(db_session, org, vendor):
    from app.models.application_portfolio import ApplicationComponent
    from app.models.capability_to_vendor_mapping import ApplicationVendorProductMapping
    from app.models.vendor.vendor_organization import VendorProduct

    vp = VendorProduct(vendor_organization_id=vendor.id, name=f"{vendor.name} as used by {org.name}")
    db_session.add(vp)
    app_row = ApplicationComponent(name=f"App {uuid.uuid4().hex[:6]}", organization_id=org.id)
    db_session.add(app_row)
    db_session.flush()
    db_session.add(ApplicationVendorProductMapping(application_component_id=app_row.id, vendor_product_id=vp.id))
    db_session.flush()
    return vp


def test_two_organisations_see_the_same_catalogue_and_only_their_own_links(db_session, make_org, tenant_ctx):
    from app.services import vendor_catalogue_service as svc

    vendor, _, product = _catalogue_product(db_session)
    _fact(db_session, product, "Certificate", publish=True)
    org_a, user_a = _org_user(db_session, make_org, "a")
    org_b, user_b = _org_user(db_session, make_org, "b")
    vp_a = _uses(db_session, org_a, vendor)
    vp_b = _uses(db_session, org_b, vendor)

    with tenant_ctx(org_a.id):
        seen_a = {r["product"].id for r in svc.catalogue_products()}
        assert [v.id for v in svc.organisation_vendor_products()] == [vp_a.id]
        svc.link_vendor_product(vp_a.id, product, user_a.id)
        # Org A cannot link a vendor product only org B uses.
        with pytest.raises(svc.LinkRefused):
            svc.link_vendor_product(vp_b.id, product, user_a.id)
        assert [link.vendor_product_id for link in svc.organisation_links_to(product)] == [vp_a.id]

    with tenant_ctx(org_b.id):
        seen_b = {r["product"].id for r in svc.catalogue_products()}
        assert [v.id for v in svc.organisation_vendor_products()] == [vp_b.id]
        assert svc.organisation_links_to(product) == []
        assert svc.link_for_vendor_product(vp_a.id) is None
        assert VendorCatalogueLink.query.count() == 0
        svc.link_vendor_product(vp_b.id, product, user_b.id)
        assert [link.vendor_product_id for link in svc.organisation_links_to(product)] == [vp_b.id]

    with tenant_ctx(org_a.id):
        assert [link.vendor_product_id for link in svc.organisation_links_to(product)] == [vp_a.id]
        assert svc.link_for_vendor_product(vp_b.id) is None

    assert product.id in seen_a and seen_a == seen_b


# ── Linking: who sees the form, and what the page says afterwards ─────────────


def _with_role(db_session, user, role_name):
    from app.models.user import Role

    user.role_id = Role.query.filter_by(name=role_name).one().id
    db_session.flush()
    return user


def test_a_viewer_reads_the_catalogue_but_is_not_offered_the_link_form(db_session, make_org, client, login_as):
    vendor, _, product = _catalogue_product(db_session)
    _fact(db_session, product, "Certificate", publish=True)
    org, viewer = _org_user(db_session, make_org, "viewer")
    _with_role(db_session, viewer, "Viewer")
    vp = _uses(db_session, org, vendor)
    page = f"/applications/vendors/catalogue/{product.catalogue_slug}"

    login_as(client, viewer)
    body = client.get(page).get_data(as_text=True)
    assert "Certificate" in body
    assert 'data-testid="catalogue-link-form"' not in body
    assert "Linking needs permission to edit applications." in body
    login_as(client, viewer)
    assert client.post(page + "/link", data={"vendor_product_id": vp.id}).status_code == 403
    assert VendorCatalogueLink.query.filter_by(vendor_product_id=vp.id).count() == 0


def test_a_person_who_edits_applications_is_offered_the_link_form(db_session, make_org, client, login_as):
    vendor, _, product = _catalogue_product(db_session)
    _fact(db_session, product, "Certificate", publish=True)
    org, editor = _org_user(db_session, make_org, "editor")
    _with_role(db_session, editor, "User")
    vp = _uses(db_session, org, vendor)

    login_as(client, editor)
    body = client.get(f"/applications/vendors/catalogue/{product.catalogue_slug}").get_data(as_text=True)
    assert 'data-testid="catalogue-link-form"' in body
    assert vp.name in body
    assert "Linking needs permission to edit applications." not in body


def test_the_page_says_what_a_link_came_to_and_announces_it(db_session, make_org, client, login_as):
    from app.services import vendor_catalogue_service as svc

    vendor, _, product = _catalogue_product(db_session)
    _fact(db_session, product, "Certificate", publish=True)
    org, editor = _org_user(db_session, make_org, "linker")
    _with_role(db_session, editor, "User")
    other, _ = _org_user(db_session, make_org, "other")
    vp = _uses(db_session, org, vendor)
    theirs = _uses(db_session, other, vendor)
    page = f"/applications/vendors/catalogue/{product.catalogue_slug}"

    login_as(client, editor)
    done = client.post(page + "/link", data={"vendor_product_id": theirs.id}, follow_redirects=True)
    body = done.get_data(as_text=True)
    assert 'role="alert" data-testid="catalogue-link-message"' in body
    assert svc.NOT_YOURS in body

    from app.models.capability_to_vendor_mapping import ApplicationVendorProductMapping
    from app.models.application_portfolio import ApplicationComponent

    used_by = ApplicationComponent.query.join(
        ApplicationVendorProductMapping,
        ApplicationVendorProductMapping.application_component_id == ApplicationComponent.id,
    ).filter(ApplicationVendorProductMapping.vendor_product_id == vp.id).one().name
    login_as(client, editor)
    body = client.post(page + "/link", data={"vendor_product_id": vp.id}, follow_redirects=True).get_data(as_text=True)
    assert 'role="status" data-testid="catalogue-link-message"' in body
    assert f"{product.product_name} is now linked to {used_by}." in body

    login_as(client, editor)
    body = client.get(page).get_data(as_text=True)
    assert 'data-testid="catalogue-link-message"' not in body, "a plain visit says nothing about a link"


def test_an_address_alone_never_reports_a_link(db_session, make_org, client, login_as):
    vendor, _, product = _catalogue_product(db_session)
    _fact(db_session, product, "Certificate", publish=True)
    org, editor = _org_user(db_session, make_org, "visitor")
    _with_role(db_session, editor, "User")
    vp = _uses(db_session, org, vendor)
    page = f"/applications/vendors/catalogue/{product.catalogue_slug}"
    for query in ("?link=refused", "?link=choose", f"?link=done&vp={vp.id}"):
        login_as(client, editor)
        body = client.get(page + query).get_data(as_text=True)
        assert 'data-testid="catalogue-link-message"' not in body, query


# ── The end-of-life sync still writes and never invents ────────────────────────


def test_eol_sync_writes_real_dates_and_never_invents(db_session, monkeypatch):
    from app.services import eol_sync_service

    _, family, _ = _catalogue_product(db_session)
    family.family_name = f"Eolcheck {uuid.uuid4().hex[:6]}"
    db_session.flush()

    monkeypatch.setattr(eol_sync_service, "_fetch_eol_data", lambda slug: None)
    assert eol_sync_service.sync_product_eol(family.family_name)["status"] == "not_found"
    assert family.end_of_life_date is None

    monkeypatch.setattr(eol_sync_service, "_fetch_eol_data", lambda slug: [{"eol": False}])
    assert eol_sync_service.sync_product_eol(family.family_name)["status"] == "no_eol_data"
    assert family.end_of_life_date is None

    monkeypatch.setattr(eol_sync_service, "_fetch_eol_data",
                        lambda slug: [{"eol": "2027-06-30"}, {"eol": "2025-01-01"}])
    assert eol_sync_service.sync_product_eol(family.family_name)["status"] == "updated"
    assert family.end_of_life_date == datetime(2027, 6, 30)
