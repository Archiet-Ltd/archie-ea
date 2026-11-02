"""A "Depends On" cell in an import file becomes relationships inside the importing organisation only."""

import uuid

from app import db


def _system(org, name):
    from app.models import ArchiMateElement
    from app.models.application_layer import ApplicationComponent

    element = ArchiMateElement(name=name, type="ApplicationComponent", layer="application", organization_id=org.id)
    db.session.add(element)
    db.session.flush()
    component = ApplicationComponent(name=name, organization_id=org.id, archimate_element_id=element.id)
    db.session.add(component)
    db.session.flush()
    return element


def _links(org):
    from app.models import ArchiMateRelationship

    return db.session.execute(
        db.select(ArchiMateRelationship).where(ArchiMateRelationship.organization_id == org.id)
    ).scalars().all()


def test_a_depends_on_cell_links_the_system_relied_on_to_the_one_that_relies_on_it(app, db_session, make_org, tenant_ctx):
    from app.modules.applications.services import declared_dependencies

    mine, other = make_org("deps-a"), make_org("deps-b")
    tag = uuid.uuid4().hex[:6]
    api, database, cache = (_system(mine, "Api " + tag), _system(mine, "Postgres " + tag), _system(mine, "Redis " + tag))
    stranger = _system(other, "Api " + tag)  # same name in another organisation
    db_session.commit()

    with tenant_ctx(mine.id):
        added = declared_dependencies.record(
            [("Api " + tag, "Postgres %s; Redis %s; Unknown %s" % (tag, tag, tag))], "estate.xlsx"
        )
        again = declared_dependencies.record([("Api " + tag, "Postgres " + tag)], "estate.xlsx")
        links = _links(mine)

    assert added == 2 and again == 0, "two links the first time, none on a repeat"
    assert {(lk.source_id, lk.target_id, lk.type) for lk in links} == {
        (database.id, api.id, "Serving"),
        (cache.id, api.id, "Serving"),
    }
    assert all(lk.source_kind == "import" and lk.source_label == "estate.xlsx" for lk in links)
    with tenant_ctx(other.id):
        assert _links(other) == [] and stranger.id not in {lk.target_id for lk in _links(other)}
