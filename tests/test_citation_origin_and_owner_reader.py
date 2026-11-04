"""The two readers behind "a citation lands on its fact" and "one owner on every screen".

* ``relationship_origins`` gives the relationships list the source, the date in words and the
  confidence the answer rows cite, from the same origin reader.
* ``ApplicationOwner.derived_owners_for_elements`` is the one reader of the owner a repository's code-owner file names,
  fenced to the organisation, worded the same on every screen.
"""

from app import db
from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship
from app.models.application_owner import ApplicationOwner
from app.modules.intelligence.services.query_service import relationship_origins


def code_owner_display(element_ids, org_id):
    """The owner reader's wording, as every screen shows it."""
    return {
        eid: found["label"]
        for eid, found in ApplicationOwner.derived_owners_for_elements(element_ids, org_id).items()
    }


def _element(org, name, props=None):
    el = ArchiMateElement(name=name, type="ApplicationComponent", layer="Application", organization_id=org.id)
    el.acm_properties = props
    db.session.add(el)
    db.session.flush()
    return el


def test_code_owner_is_worded_once_and_fenced_to_the_organisation(db_session, make_org):
    mine, theirs = make_org("mine"), make_org("theirs")
    team = _element(mine, "Customer Portal", {"github_owning_team": {"value": "@acme/web-team"}})
    people = _element(mine, "Billing", {"github_code_owners": {"value": ["@a", "@b"]}})
    bare = _element(mine, "Gateway", {})
    foreign = _element(theirs, "Other", {"github_owning_team": {"value": "@rival/team"}})

    shown = code_owner_display([team.id, people.id, bare.id, foreign.id], mine.id)

    assert shown == {
        team.id: "@acme/web-team (from GitHub code owners; assign an owner)",
        people.id: "@a, @b (from GitHub code owners; assign an owner)",
    }
    assert code_owner_display([], mine.id) == {}


def test_a_relationship_origin_reads_source_date_in_words_and_confidence(db_session, make_org):
    org = make_org("origin")
    a, b = _element(org, "A"), _element(org, "B")
    imported = ArchiMateRelationship(
        type="Serving", source_id=a.id, target_id=b.id, organization_id=org.id,
        source_kind="import", source_label="acme/billing-api pom.xml",
    )
    unrecorded = ArchiMateRelationship(type="Flow", source_id=b.id, target_id=a.id, organization_id=org.id)
    db_session.add_all([imported, unrecorded])
    db_session.flush()

    found = relationship_origins([imported, unrecorded], org.id)

    assert found[imported.id]["text"] == "Imported from acme/billing-api pom.xml"
    assert found[imported.id]["confidence"] == "Recorded"
    assert found[imported.id]["recorded_on"], "a recorded relationship shows when it was recorded"
    assert "T" not in found[imported.id]["recorded_on"], found[imported.id]["recorded_on"]
    assert found[unrecorded.id]["text"] == "Origin not recorded"
