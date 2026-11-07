"""What each application runs on, read from the docker-compose file it holds.

A compose file that runs postgres or redis makes a Technology element in the organisation's model,
serving the application that holds the file, with the file, line and image on the link. Nothing is
added until a person approves; two organisations that both run postgres get two elements.

No test touches the network. The token is a plain placeholder.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.test_entity_history_trigger import _entity_history_trigger  # noqa: F401
from tests.test_event_log import _install_guards  # noqa: F401
from tests.test_github_builds_the_model import (  # noqa: F401
    _approve_all,
    _by_repo,
    _count,
    _element,
    _entry,
    _file_response,
    acme,
    connect,
    github,
    org_a,
    org_b,
    people,
    sync,
)

pytestmark = pytest.mark.usefixtures("db_session")

TOKEN = "placeholder-read-only-token"
PORTAL_COMPOSE = "services:\n  web:\n    build: .\n  db:\n    image: postgres:16\n  cache:\n    image: redis:7\n"
PAYMENTS_COMPOSE = "# payments\nservices:\n  api:\n    build: .\n  database:\n    image: docker.io/library/postgres:15.4\n"


def _put_compose(bundle, repo, body):
    path = f"repos/acme/{repo}/contents"
    listing = bundle.setdefault(path, [])
    if not any(item["name"] == "docker-compose.yml" for item in listing):
        listing.append(_entry("docker-compose.yml", "file"))
    bundle[f"{path}/docker-compose.yml"] = _file_response("docker-compose.yml", body)


@pytest.fixture
def acme_with_databases(acme):
    _put_compose(acme.bundle, "customer-portal", PORTAL_COMPOSE)
    _put_compose(acme.bundle, "payments-service", PAYMENTS_COMPOSE)
    return acme


@pytest.fixture
def synced_databases(db_session, org_a, people, acme_with_databases, connect, sync):
    return sync(org_a, connect(org_a, token=TOKEN))


# --------------------------------------------------------------------- #
# Reading the file                                                       #
# --------------------------------------------------------------------- #


def test_images_with_tags_registries_and_digests_name_their_product():
    from app.connectors.github_model import compose_technologies

    text = "\n".join(
        [
            "services:",
            "  a:",
            "    image: postgres:16",
            "  b:",
            "    image: docker.io/library/redis:7-alpine",
            "  c:",
            "    image: registry.local:5000/team/rabbitmq:3.13-management",
            "  d:",
            "    image: bitnami/kafka@sha256:" + "a" * 64,
            "  e:",
            "    image: MySQL",
        ]
    )
    found = compose_technologies(text)
    assert [(n, t, i, line) for n, t, i, line in found] == [
        ("Postgres", "SystemSoftware", "postgres:16", 3),
        ("Redis", "SystemSoftware", "docker.io/library/redis:7-alpine", 5),
        ("RabbitMQ", "SystemSoftware", "registry.local:5000/team/rabbitmq:3.13-management", 7),
        ("Apache Kafka", "SystemSoftware", "bitnami/kafka@sha256:" + "a" * 64, 9),
        ("MySQL", "SystemSoftware", "MySQL", 11),
    ]


def test_an_image_that_is_not_in_the_table_is_ignored_and_each_product_is_named_once():
    from app.connectors.github_model import compose_technologies

    text = (
        "services:\n  web:\n    image: ghcr.io/acme/postgres-exporter:1\n  api:\n    image: nginx:1\n"
        "  one:\n    image: postgres:16\n  two:\n    image: postgres:15\n  three:\n    build: .\n"
    )
    assert compose_technologies(text) == [("Postgres", "SystemSoftware", "postgres:16", 7)]
    assert compose_technologies("not: [valid") == []
    assert compose_technologies("") == []
    assert compose_technologies("services: nothing") == []


# --------------------------------------------------------------------- #
# Proposals and approval                                                 #
# --------------------------------------------------------------------- #


def test_each_technology_is_proposed_with_its_repository_file_line_and_image(db_session, org_a, synced_databases):
    assert synced_databases["success"] is True, synced_databases
    assert synced_databases["technologies_proposed"] == 3
    proposals = [_a for _p, _a in _by_repo(org_a.id, "repository_technology")]
    payloads = sorted((p["from"], p["technology"], p["file"], p["line"], p["image"]) for p, _a in _by_repo(org_a.id, "repository_technology"))
    assert payloads == [
        ("acme/customer-portal", "Postgres", "docker-compose.yml", 5, "postgres:16"),
        ("acme/customer-portal", "Redis", "docker-compose.yml", 7, "redis:7"),
        ("acme/payments-service", "Postgres", "docker-compose.yml", 6, "docker.io/library/postgres:15.4"),
    ]
    for approval in proposals:
        body = json.loads(approval.operation_payload)
        assert body["commit"] and len(body["commit"]) == 40, "no proposal without the commit it was read at"
        assert body["file"] in approval.summary and "line %s" % body["line"] in approval.summary


def test_a_second_read_proposes_nothing_again(db_session, org_a, synced_databases, connect, sync):
    again = sync(org_a, connect(org_a, token=TOKEN))
    assert again["technologies_proposed"] == 0
    assert len(_by_repo(org_a.id, "repository_technology")) == 3


def test_approving_adds_one_postgres_element_serving_both_applications_with_the_citation(
    db_session, org_a, people, synced_databases, tenant_ctx
):
    from app.models import ArchiMateElement, ArchiMateRelationship

    result = _approve_all(tenant_ctx, db_session, org_a, people.admin)
    assert result["failed"] == [], result
    postgres = ArchiMateElement.query.filter_by(organization_id=org_a.id, name="Postgres").all()
    assert len(postgres) == 1 and postgres[0].type == "SystemSoftware" and postgres[0].layer.lower() == "technology"
    served = ArchiMateRelationship.query.filter_by(organization_id=org_a.id, source_id=postgres[0].id).all()
    assert sorted(r.type for r in served) == ["Serving", "Serving"]
    by_target = {r.target_id: json.loads(r.source_ref) for r in served}
    portal, payments = _element(org_a.id, "Customer Portal"), _element(org_a.id, "Payments Service")
    assert set(by_target) == {portal.id, payments.id}
    assert by_target[portal.id]["repository"] == "acme/customer-portal"
    assert by_target[payments.id]["repository"] == "acme/payments-service"
    for origin in by_target.values():
        assert origin["file"] == "docker-compose.yml" and len(origin["commit"]) == 40
    for rel in served:
        assert rel.source_kind == "connector" and "docker-compose.yml line" in (rel.description or "")
        assert rel.custom_properties["github"]["line"] in (5, 6)
        assert rel.custom_properties["github"]["image"] in ("postgres:16", "docker.io/library/postgres:15.4")
    redis = ArchiMateElement.query.filter_by(organization_id=org_a.id, name="Redis").one()
    assert [r.target_id for r in ArchiMateRelationship.query.filter_by(source_id=redis.id)] == [portal.id]


def test_an_element_already_in_the_model_with_that_name_is_matched_not_duplicated(
    db_session, org_a, people, synced_databases, tenant_ctx
):
    from app.models import ArchiMateElement
    from app.services.archimate_backbone import create_backbone_element

    existing = create_backbone_element(element_type="SystemSoftware", layer="Technology", name="Redis", organization_id=org_a.id)
    db_session.commit()
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    assert ArchiMateElement.query.filter_by(organization_id=org_a.id, name="Redis").count() == 1
    from app.models import ArchiMateRelationship

    assert ArchiMateRelationship.query.filter_by(organization_id=org_a.id, source_id=existing.id).count() == 1


def test_the_frozen_application_technology_table_is_not_written(db_session, org_a, people, synced_databases, tenant_ctx):
    from sqlalchemy import text

    def rows():
        return db_session.execute(text("SELECT count(*) FROM application_technology_mapping")).scalar()

    before = rows()
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    assert rows() == before


# --------------------------------------------------------------------- #
# The answer                                                             #
# --------------------------------------------------------------------- #


def test_what_breaks_if_postgres_fails_lists_both_applications_and_cites_the_files(
    db_session, org_a, people, synced_databases, tenant_ctx
):
    from app.models import ArchiMateElement
    from app.modules.intelligence.services.query_service import IntelligenceQueryService

    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    postgres = ArchiMateElement.query.filter_by(organization_id=org_a.id, name="Postgres").one()
    with tenant_ctx(org_a.id):
        answer = IntelligenceQueryService.cross_layer_impact(postgres.id, include_derived=False, max_depth=1)
    names = {answer["elements"][str(r["element_id"])]["name"]: r for r in answer["rows"]}
    assert {"Customer Portal", "Payments Service"} <= set(names)
    for name in ("Customer Portal", "Payments Service"):
        origins = names[name]["relation"]["origins"]
        (origin,) = origins.values()
        assert origin["file"] == "docker-compose.yml" and origin["repository"].startswith("acme/")
        assert "docker-compose.yml line" in origin["detail"] and "postgres" in origin["detail"]


# --------------------------------------------------------------------- #
# Two organisations                                                      #
# --------------------------------------------------------------------- #


def test_two_organisations_running_postgres_get_two_elements_and_see_nothing_of_each_other(
    db_session, org_a, org_b, people, synced_databases, connect, sync, tenant_ctx
):
    from app.connectors.github import pending_review
    from app.models import ArchiMateElement, ArchiMateRelationship
    from tests.test_connector_approval_flow import _make_admin

    admin_b = _make_admin(db_session, org_b.id, "Other")
    _approve_all(tenant_ctx, db_session, org_a, people.admin)
    with tenant_ctx(org_b.id):
        assert pending_review(org_b.id)["technologies"] == []
    assert _count(db_session, "archimate_elements", org_b.id) == 0

    sync(org_b, connect(org_b, token="placeholder-other-token"))
    db_session.commit()
    _approve_all(tenant_ctx, db_session, org_b, admin_b)

    with tenant_ctx(org_a.id):
        a_postgres = ArchiMateElement.query.filter_by(name="Postgres").one()
        a_ids = {e.id for e in ArchiMateElement.query.all()}
        a_links = ArchiMateRelationship.query.all()
    with tenant_ctx(org_b.id):
        b_postgres = ArchiMateElement.query.filter_by(name="Postgres").one()
        b_ids = {e.id for e in ArchiMateElement.query.all()}
        b_links = ArchiMateRelationship.query.all()
    assert a_postgres.id != b_postgres.id
    assert not a_ids & b_ids
    assert a_postgres.organization_id == org_a.id and b_postgres.organization_id == org_b.id
    for links, own in ((a_links, a_ids), (b_links, b_ids)):
        assert links
        for rel in links:
            assert rel.source_id in own and rel.target_id in own, "a link crossed organisations"
