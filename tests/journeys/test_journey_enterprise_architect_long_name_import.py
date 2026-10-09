"""Journey: can the enterprise_architect persona import a model and reopen a
long-named element whole?

Level 9, docs/TESTING_STANDARD.md. The persona imports an Open Exchange Format
model on the one import screen, then opens an element whose name is 150
characters long, reloads the page, and still sees the full name. On main the
name column is 100 wide, so the 150-character name is truncated and the full
name never appears; on this branch it is stored whole and rendered whole.

The user carries role_name="Architect", not "Administrator". An admin session
normalises to the "admin" role and would satisfy every require_roles guard on
the way through, hiding exactly the authorisation defect this level exists to
find (TESTING_STANDARD rule 4).
"""

import io
import uuid

import pytest

from .conftest import login, make_org, make_user

pytestmark = pytest.mark.journey

def _long_name():
    # A unique 150-character name so a re-run of this module never collides
    # with a row a previous run left behind (the journey app fixture does not
    # drop the database between runs). uuid4 hex is 32 chars; repeating it and
    # truncating to 150 keeps the unique prefix and lands exactly on 150.
    return (uuid.uuid4().hex * 5)[:150]


def _oef(name_150):
    return """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.opengroup.org/xsd/archimate/3.0/"
       xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" identifier="id-journey-long">
  <name xml:lang="en">Journey long-name model</name>
  <elements>
    <element identifier="E1" xsi:type="BusinessService">
      <name xml:lang="en">{name_150}</name>
      <documentation>A service with a 150-character name.</documentation>
    </element>
  </elements>
  <relationships/>
</model>
""".format(name_150=name_150)


def _import(client, xml):
    return client.post(
        "/architecture/import/oef",
        data={"oef_file": (io.BytesIO(xml.encode("utf-8")), "model.xml"),
              "strategy": "skip_duplicates"},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )


def test_enterprise_architect_imports_and_reopens_a_long_named_element(app, client):
    """Import a 150-char name on the import screen, open the element, reload,
    and see the full name every time."""
    from app import db
    from app.models.archimate_core import ArchiMateElement

    with app.app_context():
        org_id = make_org(db, "EAImport")
        ea_id = make_user(
            db, org_id, "eaimport", enterprise_role="enterprise_architect",
            role_name="Architect",
        )

    login(client, ea_id)

    long_name = _long_name()

    # IMPORT on the one import screen.
    response = _import(client, _oef(long_name))
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    payload = response.get_json()
    assert payload.get("created") == 1, payload

    # PERSISTED WHOLE. Read the database, not the response body the route built.
    with app.app_context():
        db.session.expunge_all()
        row = db.session.execute(
            db.select(ArchiMateElement).filter_by(name=long_name)
        ).scalar_one_or_none()
        assert row is not None, "150-char element was not stored"
        assert len(row.name) == 150
        element_id = row.id

    # OPEN the element detail page and see the full name.
    page = client.get("/architecture/business/BusinessService/%d" % element_id)
    assert page.status_code == 200, page.status_code
    html = page.get_data(as_text=True)
    assert long_name in html, "full 150-char name missing from the detail page"

    # RELOAD and see the full name again.
    reloaded = client.get("/architecture/business/BusinessService/%d" % element_id)
    assert reloaded.status_code == 200, reloaded.status_code
    assert long_name in reloaded.get_data(as_text=True), (
        "full 150-char name missing after reload"
    )
