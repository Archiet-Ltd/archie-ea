"""A preview must write nothing, and must predict exactly what the commit does.

Both halves matter and the second is the one that gets broken.

A preview that writes is obviously wrong. A preview that reports a number the
commit then contradicts is subtler and worse: the architect approves 80 elements
and gets 74, and the next thing they stop trusting is the preview. This is the
same defect class as the "720 elements" headline - a number stated with
confidence that nothing computed.

Both regressions happened while building this and are pinned below:

  * Preview cannot see duplicates WITHIN one diagram. The commit path finds the
    second INTERNET by querying the row the first one just inserted; preview
    inserts nothing, so it counted all five. Predicted 80, committed 74.

  * The first fix keyed relationship de-duplication on (source_id, target_id),
    which in preview are both None for not-yet-created elements. Every edge
    after the first of each type looked like a duplicate: predicted 5, committed
    94. Endpoints must be identified by what they will BECOME.
"""

import uuid

import pytest

pytestmark = pytest.mark.journey


@pytest.fixture(scope="module")
def app():
    import os

    os.environ.setdefault("SECRET_KEY", "x" * 32)
    from app import create_app, db

    application = create_app("testing")
    with application.app_context():
        db.create_all()
    return application


@pytest.fixture
def org_id(app):
    """A fresh, empty organisation - so every element in the payload is new."""
    from app import db
    from app.models.organization import Organization

    marker = uuid.uuid4().hex[:8]
    with app.app_context():
        org = Organization(name="Preview %s" % marker, slug="preview-%s" % marker)
        db.session.add(org)
        db.session.commit()
        created = org.id

    yield created

    with app.app_context():
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        ArchiMateRelationship.query.filter_by(organization_id=created).delete()
        ArchiMateElement.query.filter_by(organization_id=created).delete()
        db.session.commit()


def _payload():
    """A diagram containing a deliberate duplicate - the case that broke both times.

    "INTERNET" appears three times, exactly as a real landscape diagram draws
    one network box per zone. Under name-type de-duplication all three become
    one element, and the edges into them collapse with them.
    """
    from app.services.lucid_archimate_transformer import LucidArchiMateTransformer

    def shape(shape_id, name, box):
        x, y, w, h = box
        return {"id": shape_id, "class": "ArchiMate3ComponentBoxBlock",
                "textAreas": [{"label": "Text", "text": name}],
                "boundingBox": {"x": x, "y": y, "w": w, "h": h}}

    def line(line_id, source, target, style="Arrow"):
        return {"id": line_id,
                "endpoint1": {"connectedTo": source, "style": "None"},
                "endpoint2": {"connectedTo": target, "style": style}}

    shapes = [
        shape("app1", "Business Central", (0, 0, 160, 60)),
        shape("app2", "MuleSoft", (400, 0, 160, 60)),
        shape("net1", "INTERNET", (0, 200, 120, 60)),
        shape("net2", "INTERNET", (200, 200, 120, 60)),
        shape("net3", "INTERNET", (400, 200, 120, 60)),
    ]
    lines = [
        line("l1", "app1", "net1"),
        line("l2", "app1", "net2"),   # collapses onto l1 once the nets merge
        line("l3", "app2", "net3"),
    ]
    return LucidArchiMateTransformer().transform_document({
        "title": "Duplicate Networks",
        "pages": [{"id": "p1", "title": "P1", "items": {"shapes": shapes, "lines": lines}}],
    })


def _counts(app, org_id):
    from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

    with app.app_context():
        return (
            ArchiMateElement.query.filter_by(organization_id=org_id).count(),
            ArchiMateRelationship.query.filter_by(organization_id=org_id).count(),
        )


def test_preview_writes_nothing(app, org_id):
    from app.services.lucid_import_service import import_payload

    before = _counts(app, org_id)
    with app.app_context():
        import_payload(_payload(), org_id=org_id, preview=True)
    after = _counts(app, org_id)

    assert before == after == (0, 0), (
        "preview wrote to the database: %s -> %s. Nothing may be persisted "
        "before the architect approves it." % (before, after))


def test_preview_predicts_the_commit_exactly(app, org_id):
    """The number shown before is the number that happens after."""
    from app.services.lucid_import_service import import_payload

    payload = _payload()
    with app.app_context():
        preview = import_payload(payload, org_id=org_id, preview=True)
    with app.app_context():
        commit = import_payload(payload, org_id=org_id, preview=False)

    predicted = (preview["counts"]["elements"].get("created", 0),
                 preview["counts"]["relationships"].get("created", 0))
    actual = (commit["counts"]["elements"].get("created", 0),
              commit["counts"]["relationships"].get("created", 0))
    in_database = _counts(app, org_id)

    assert predicted == actual, (
        "preview promised %s and the commit did %s. An architect approves the "
        "preview; if it over-promises they stop trusting it, and if it "
        "under-promises they are surprised by their own estate." % (predicted, actual))
    assert actual == in_database, (
        "the commit reported %s but the database holds %s" % (actual, in_database))


def test_duplicates_within_one_diagram_collapse(app, org_id):
    """Three INTERNET boxes are one network, and preview must know that.

    Predicted 80 elements against 74 committed until this was handled.
    """
    from app.services.lucid_import_service import import_payload

    with app.app_context():
        preview = import_payload(_payload(), org_id=org_id, preview=True)

    created = preview["counts"]["elements"].get("created", 0)
    assert created == 3, (
        "expected 3 distinct elements (Business Central, MuleSoft, INTERNET) - "
        "got %d, so the repeated INTERNET boxes were counted separately" % created)

    duplicates = [e for e in preview["elements"]
                  if e["detail"] == "duplicate within this diagram"]
    assert len(duplicates) == 2, (
        "expected the 2 repeat INTERNET boxes to be reported as duplicates, "
        "got %d" % len(duplicates))


def test_relationship_prediction_is_not_collapsed_by_null_endpoints(app, org_id):
    """The regression that reported 5 relationships where 94 were created.

    Keying on (source_id, target_id) while both are None in preview makes every
    edge of a given type look identical.
    """
    from app.services.lucid_import_service import import_payload

    with app.app_context():
        preview = import_payload(_payload(), org_id=org_id, preview=True)

    created = preview["counts"]["relationships"].get("created", 0)
    assert created == 2, (
        "expected 2 distinct relationships once the INTERNET boxes merge "
        "(Business Central→INTERNET, MuleSoft→INTERNET) - got %d. All three "
        "edges share a type, so a key that ignores the endpoints collapses "
        "them to 1." % created)


def test_a_second_import_creates_nothing(app, org_id):
    """Re-importing an unchanged diagram must be a no-op, and say so."""
    from app.services.lucid_import_service import import_payload

    payload = _payload()
    with app.app_context():
        import_payload(payload, org_id=org_id, preview=False)
    first = _counts(app, org_id)

    with app.app_context():
        again = import_payload(payload, org_id=org_id, preview=False)
    second = _counts(app, org_id)

    assert first == second, (
        "re-importing the same diagram changed the estate: %s -> %s" % (first, second))
    assert again["counts"]["elements"].get("created", 0) == 0, (
        "the second import created elements that already existed")
