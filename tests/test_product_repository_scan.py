"""A product manager brings a Product's repositories into its page: chosen repositories are read, every
proposal carries the Product, approving joins what landed to it with the file it was read from, and one
organisation's Product is never touched by another's read.

GitHub is never called: the recorded organisation of the connector tests is served in its place.
"""

from __future__ import annotations

import json
import re
import uuid
from types import SimpleNamespace

import pytest

from tests.test_entity_history_trigger import _entity_history_trigger  # noqa: F401
from tests.test_event_log import _install_guards  # noqa: F401
from tests.test_github_builds_the_model import TOKEN, acme, people, _approve_all  # noqa: F401
from tests.test_github_connector import (  # noqa: F401
    _payload,
    _proposals,
    _user,
    connect,
    github,
    org_a,
    org_b,
    sync,
)

pytestmark = pytest.mark.usefixtures("db_session")

CHOSEN = ["acme/payments-service", "acme/customer-portal"]


def _product(db_session, org, name):
    from app.models import ArchiMateElement

    row = ArchiMateElement(organization_id=org.id, name=name, type="Product", layer="business")
    db_session.add(row)
    db_session.flush()
    return row


def _scoped(db_session, tenant_ctx, org, cfg, product, repositories):
    from app.services.connector_framework import run_connector_sync

    with tenant_ctx(org.id):
        result = run_connector_sync(
            org_id=org.id, connector_id=cfg.id, delegated=True,
            scope={"repositories": repositories, "product_id": product.id},
        )
    db_session.flush()
    return result


def _pending(org, operation="repository_classification"):
    from app.models.ai_chat_crud_approval import ApprovalStatus

    return [_payload(a) for a in _proposals(org.id, operation, ApprovalStatus.PENDING)]


@pytest.fixture
def world(db_session, org_a, org_b, people, acme, connect, tenant_ctx):
    cfg = connect(org_a, token=TOKEN)
    return SimpleNamespace(
        cfg=cfg,
        product=_product(db_session, org_a, "Shop " + uuid.uuid4().hex[:6]),
        theirs=_product(db_session, org_b, "Theirs " + uuid.uuid4().hex[:6]),
    )


def test_a_read_of_two_chosen_repositories_proposes_only_those_and_tags_each_with_the_product(
    db_session, tenant_ctx, org_a, world
):
    result = _scoped(db_session, tenant_ctx, org_a, world.cfg, world.product, CHOSEN)
    assert result["success"] is True, result
    assert result["records_processed"] == 2
    pending = _pending(org_a)
    assert sorted(p["repository"] for p in pending) == sorted(CHOSEN)
    assert {p["product_id"] for p in pending} == {world.product.id}
    links = _pending(org_a, "repository_relationship")
    assert links and {p["product_id"] for p in links} == {world.product.id}


def test_a_read_of_chosen_repositories_flags_nothing_missing(db_session, tenant_ctx, org_a, world, people):
    from app.models import ArchiMateElement

    _scoped(db_session, tenant_ctx, org_a, world.cfg, world.product, CHOSEN)
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    # Read only one of them again: the other is not in this listing, and must not be called missing.
    _scoped(db_session, tenant_ctx, org_a, world.cfg, world.product, CHOSEN[:1])
    for element in ArchiMateElement.query.filter_by(organization_id=org_a.id, source_kind="connector").all():
        assert "github_repository_missing" not in (element.acm_properties or {}), element.name


def test_approving_joins_what_landed_to_the_product_and_cites_the_file(
    db_session, tenant_ctx, org_a, world, people
):
    from app.models import ArchiMateElement, ArchiMateRelationship
    from app.modules.architecture.routes.archimate_crud import product_page

    _scoped(db_session, tenant_ctx, org_a, world.cfg, world.product, CHOSEN)
    wanted = {p["name"]: p for p in _pending(org_a)}
    technologies = _pending(org_a, "repository_technology")
    assert [t["technology"] for t in technologies] == ["Redis"]
    assert {t["product_id"] for t in technologies} == {world.product.id}
    assert ArchiMateRelationship.query.filter_by(organization_id=org_a.id, source_id=world.product.id).count() == 0
    _approve_all(tenant_ctx, db_session, org_a, people.admin)

    joined = ArchiMateRelationship.query.filter_by(
        organization_id=org_a.id, source_id=world.product.id, type="association").all()
    assert len(wanted) == 2
    assert len(joined) == len(wanted) + len(technologies), (len(joined), sorted(wanted), technologies)
    assert {r.source_kind for r in joined} == {"connector"}
    with tenant_ctx(org_a.id):
        walked = product_page.members(world.product.id, org_a.id)
    shown = {r["element"].name: r["element"] for r in walked["rows"]}
    for name, payload in wanted.items():
        assert name in shown, f"{name} is not on the product page"
        source = product_page.source_of(shown[name])
        assert source["repository"] == payload["repository"]
        assert source["file"] == payload["evidence_file"]
        assert payload["evidence_file"], "the fixture's repositories each have a file that shows them"
        assert re.fullmatch(
            r"https://github\.com/acme/[a-z-]+/blob/[0-9a-f]{40}/.+", source["href"]), source["href"]
        assert payload["evidence_file"] in source["text"]
    assert ArchiMateElement.query.filter_by(organization_id=org_a.id).count() >= 3


def test_a_second_read_proposes_nothing_new_and_joins_nothing_twice(db_session, tenant_ctx, org_a, world, people):
    from app.models import ArchiMateRelationship

    _scoped(db_session, tenant_ctx, org_a, world.cfg, world.product, CHOSEN)
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    before = ArchiMateRelationship.query.filter_by(organization_id=org_a.id).count()
    again = _scoped(db_session, tenant_ctx, org_a, world.cfg, world.product, CHOSEN)
    assert again["success"] is True, again
    assert _pending(org_a) == []
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    assert ArchiMateRelationship.query.filter_by(organization_id=org_a.id).count() == before


def test_a_proposal_from_the_whole_organisation_read_is_claimed_by_the_product_that_chose_it(
    db_session, tenant_ctx, org_a, world, sync
):
    sync(org_a, world.cfg)
    assert {p.get("product_id") for p in _pending(org_a)} == {None}
    _scoped(db_session, tenant_ctx, org_a, world.cfg, world.product, CHOSEN)
    by_repo = {}
    for p in _pending(org_a):
        by_repo.setdefault(p["repository"], set()).add(p.get("product_id"))
    assert by_repo["acme/payments-service"] == {world.product.id}
    assert by_repo["acme/customer-portal"] == {world.product.id}
    assert by_repo["acme/billing-api"] == {None}, "a repository nobody chose stays unclaimed"
    technologies = _pending(org_a, "repository_technology")
    assert [(t["technology"], t["product_id"]) for t in technologies] == [("Redis", world.product.id)]


def test_a_product_of_another_organisation_is_never_joined(db_session, tenant_ctx, org_a, org_b, world, people):
    from app.models import ArchiMateRelationship

    _scoped(db_session, tenant_ctx, org_a, world.cfg, world.theirs, CHOSEN)
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    assert ArchiMateRelationship.query.filter_by(organization_id=org_b.id).count() == 0
    touching = ArchiMateRelationship.query.filter(
        (ArchiMateRelationship.source_id == world.theirs.id) | (ArchiMateRelationship.target_id == world.theirs.id)
    ).count()
    assert touching == 0
    assert ArchiMateRelationship.query.filter_by(organization_id=org_a.id, type="association").count() == 0


# --------------------------------------------------------------------- #
# The Product page                                                        #
# --------------------------------------------------------------------- #


def _architect(db_session, person, role="enterprise_architect"):
    user = person
    user.enterprise_role = role
    user.confirmed = True
    db_session.flush()
    return user


def _page(client, login_as, user, product, query=""):
    login_as(client, user)
    return client.get(f"/architecture/business/Product/{product.id}?ae=1{query}")


def test_a_page_with_no_connection_offers_connect_github_that_returns_to_the_product(
    db_session, org_a, people, client, login_as
):
    product = _product(db_session, org_a, "Unconnected " + uuid.uuid4().hex[:6])
    user = _architect(db_session, people.one)
    html = _page(client, login_as, user, product).get_data(as_text=True)
    assert 'data-testid="product-connect-github"' in html
    assert f"/admin/connectors/github?product={product.id}" in html
    connect_page = client.get(f"/admin/connectors/github?product={product.id}").get_data(as_text=True)
    assert 'name="product"' in connect_page and f'value="{product.id}"' in connect_page
    assert product.name in connect_page


def test_choosing_lists_the_organisations_repositories_and_reading_queues_only_the_chosen(
    db_session, org_a, world, people, client, login_as, tenant_ctx
):
    from app.models.job import Job

    user = _architect(db_session, people.one)
    db_session.commit()
    html = _page(client, login_as, user, world.product, "&bring_in=1").get_data(as_text=True)
    options = re.findall(r'name="repository"\s+value="([^"]+)"', html)
    assert "acme/payments-service" in options and "acme/customer-portal" in options and len(options) == 16
    assert "TOKEN" not in html and TOKEN not in html

    login_as(client, user)
    with client.session_transaction() as flask_session:
        token = flask_session.get("csrf_token")
    response = client.post(
        f"/architecture/products/{world.product.id}/repositories",
        data={"repository": CHOSEN + ["other-org/not-ours", "acme/../x"], "csrf_token": token or ""},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303), response.status_code
    job = Job.query.filter(Job.task == "github_first_sync").order_by(Job.id.desc()).first()
    assert job.payload["product_id"] == world.product.id
    assert job.payload["repositories"] == CHOSEN


def test_nobody_outside_the_organisation_or_without_connection_rights_can_start_a_read(
    db_session, org_a, org_b, world, people, client, login_as
):
    outsider = _architect(db_session, _user(db_session, org_b, 'out@example.test'))
    login_as(client, outsider)
    assert client.post(f"/architecture/products/{world.product.id}/repositories",
                       data={"repository": CHOSEN}).status_code in (400, 404)
    viewer = _architect(db_session, people.two, role="viewer")
    login_as(client, viewer)
    assert client.post(f"/architecture/products/{world.product.id}/repositories",
                       data={"repository": CHOSEN}).status_code in (400, 403)
    html = _page(client, login_as, viewer, world.product).get_data(as_text=True)
    assert 'data-testid="product-bring-in"' not in html


def test_the_page_names_the_file_each_element_came_from(db_session, tenant_ctx, org_a, world, people, client, login_as):
    _scoped(db_session, tenant_ctx, org_a, world.cfg, world.product, CHOSEN)
    waiting = _pending(org_a)
    user = _architect(db_session, people.one)
    html = _page(client, login_as, user, world.product).get_data(as_text=True)
    assert re.search(r"\d+ proposals? read for this product", html), "the waiting proposals are counted"
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    html = _page(client, login_as, user, world.product).get_data(as_text=True)
    for payload in waiting:
        assert payload["name"] in html
        assert f"{payload['repository']}: {payload['evidence_file']}" in html
    assert "Entered in the model by a person" not in html
