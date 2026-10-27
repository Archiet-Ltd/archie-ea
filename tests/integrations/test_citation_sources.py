"""One citation builder: a link cites where a connector read it.

A link an applied integration fact wrote records itself on that fact, so its
citation names the system and item it came from; a link the GitHub connector
stamped with its origin cites the repository, commit and file. The answer's
citations and the impact rows' origins read the same builder, fenced to the
organisation.
"""
import json
from datetime import datetime

from app import db
from app.integrations.fact_store import apply_fact, sources_for_relationships
from app.models.integration_fact import IntegrationFact, IntegrationFactStatus

READ_AT = datetime(2026, 10, 20, 9, 30)


def _fact(org_id, path, fact_type, data, external_id="77"):
    fact = IntegrationFact(
        organization_id=org_id,
        system="github",
        external_id=external_id,
        path=path,
        fact_type=fact_type,
        fact_data=json.dumps(data),
        source_url="https://github.com/acme/shop",
        source_item_type="repository",
        source_read_at=READ_AT,
        status=IntegrationFactStatus.PENDING,
    )
    db.session.add(fact)
    db.session.flush()
    return fact


def _applied_link(org_id):
    """Two elements and the link between them, each applied from a fact of one repository."""
    shop = _fact(org_id, "element", "element", {"archimate_type": "ApplicationComponent", "name": "shop"})
    api = _fact(org_id, "service", "element", {"archimate_type": "ApplicationService", "name": "shop api"})
    link = _fact(org_id, "relationships.0", "relationship", {
        "type": "realization", "source_path": "element", "target_path": "service", "description": None,
        "properties": {},
    })
    for fact in (shop, api, link):
        assert apply_fact(fact, commit=False) is True
    return shop, api, link


def test_an_applied_link_cites_the_repository_it_was_read_from(app, db_session, make_org):
    org, other = make_org("cite-a"), make_org("cite-b")
    _shop, _api, link = _applied_link(org.id)

    assert link.applied_relationship_id is not None
    found = sources_for_relationships({link.applied_relationship_id}, org.id)
    assert found == {link.applied_relationship_id: {
        "system": "github", "item": "shop", "url": "https://github.com/acme/shop", "read_at": READ_AT.isoformat(),
    }}
    # Another organisation is told nothing about the link.
    assert sources_for_relationships({link.applied_relationship_id}, other.id) == {}


def test_a_team_owner_fact_records_the_assignment_it_wrote(app, db_session, make_org):
    from app.models import ArchiMateRelationship

    org = make_org("cite-team")
    shop = _fact(org.id, "element", "element", {"archimate_type": "ApplicationComponent", "name": "shop"})
    owner = _fact(org.id, "owners.0", "owner", {
        "element_path": "element", "owner_name": "payments-team", "owner_email": None,
        "ownership_type": "primary", "is_team": True,
    })
    assert apply_fact(shop, commit=False) and apply_fact(owner, commit=False)

    assignment = db.session.get(ArchiMateRelationship, owner.applied_relationship_id)
    assert (assignment.type, assignment.source_id, assignment.target_id) == (
        "assignment", owner.applied_element_id, shop.applied_element_id)
    assert sources_for_relationships({assignment.id}, org.id)[assignment.id]["item"] == "shop"


def test_a_link_github_stamped_cites_the_repository_commit_and_file(app, db_session, make_org):
    from app.models import ArchiMateElement, ArchiMateRelationship

    org = make_org("cite-stamp")
    a, b = (ArchiMateElement(name=n, type="ApplicationComponent", layer="application", organization_id=org.id)
            for n in ("consumer", "supplier"))
    db.session.add_all([a, b])
    db.session.flush()
    commit = "ab" * 20
    link = ArchiMateRelationship(
        type="Serving", source_id=b.id, target_id=a.id, organization_id=org.id, source_kind="connector",
        source_ref=json.dumps({"system": "github", "repository": "acme/consumer", "commit": commit,
                               "file": "go.mod", "detail": "go.mod names github.com/acme/supplier"}),
        source_label="GitHub acme/consumer @ abababa (go.mod)", source_recorded_at=READ_AT,
    )
    drawn = ArchiMateRelationship(type="Serving", source_id=a.id, target_id=b.id, organization_id=org.id,
                                  source_kind="manual")
    db.session.add_all([link, drawn])
    db.session.flush()

    found = sources_for_relationships({link.id, drawn.id}, org.id)

    assert set(found) == {link.id}, "a drawn link has no connector source"
    source = found[link.id]
    assert (source["system"], source["item"], source["repository"], source["commit"], source["file"]) == (
        "github", "acme/consumer", "acme/consumer", commit, "go.mod")
    assert source["url"] == f"https://github.com/acme/consumer/blob/{commit}/go.mod"
    assert source["read_at"] == READ_AT.isoformat()


def test_the_answer_cites_a_link_by_the_fact_that_wrote_it(app, db_session, make_org, tenant_ctx):
    from app.modules.intelligence.services.answer_envelope import KIND_IMPACT, _citations

    org = make_org("cite-answer")
    with tenant_ctx(org.id):
        shop, api, link = _applied_link(org.id)
        row = {
            "element_id": api.applied_element_id,
            "relation": {
                "kind": "explicit",
                "chain": [link.applied_relationship_id],
                "chain_elements": [shop.applied_element_id, api.applied_element_id],
            },
        }
        citations = _citations(KIND_IMPACT, [row])

    by_key = {(c["type"], c["id"]): c for c in citations}
    assert by_key[("relationship", link.applied_relationship_id)]["source"]["item"] == "shop"
    assert by_key[("element", api.applied_element_id)]["source"]["system"] == "github"
