"""capability_scope: the organisation's own capabilities are the count; the shared catalogue is one sentence."""
from app.models.unified_capability import UnifiedCapability
from app.modules.capabilities.services.capability_count_service import capability_scope


def _rows(db_session, count, label, organization_id, scope):
    db_session.add_all(
        UnifiedCapability(name="%s %d" % (label, n), code="%s-%d" % (label[:3].upper(), n), level=1,
                          organization_id=organization_id, scope=scope)
        for n in range(count)
    )
    db_session.flush()


def _catalogue_size():
    return UnifiedCapability.query.filter(UnifiedCapability.scope == "reference",
                                          UnifiedCapability.organization_id.is_(None)).count()


def test_no_own_capabilities_with_catalogue_loaded(app, db_session, make_org):
    org = make_org("empty")
    _rows(db_session, 4, "Catalogue", None, "reference")
    scope = capability_scope(org.id)
    shared = _catalogue_size()
    assert shared >= 4
    assert scope["own"] == 0
    assert scope["shared"] == shared
    assert scope["catalogue_sentence"] == "%d from the shared catalogue, not yet adopted" % shared


def test_three_own_capabilities(app, db_session, make_org):
    org = make_org("three")
    _rows(db_session, 3, "Mine", org.id, "tenant")
    scope = capability_scope(org.id)
    assert scope["own"] == 3
    assert scope["total"] == 3 + scope["shared"]


def test_two_organisations_never_count_each_others_capabilities(app, db_session, make_org):
    mine, theirs = make_org("mine"), make_org("theirs")
    _rows(db_session, 3, "Mine", mine.id, "tenant")
    _rows(db_session, 5, "Theirs", theirs.id, "tenant")
    assert capability_scope(mine.id)["own"] == 3
    assert capability_scope(theirs.id)["own"] == 5
    assert capability_scope(mine.id)["shared"] == capability_scope(theirs.id)["shared"]

