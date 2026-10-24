"""_get_display_fields formats a model's raw column values for the ArchiMate
element detail page (app/templates/archimate_crud/detail.html).

A screenshot from a Fortune-500-fitness review showed this page rendering a
literal Python dict repr -- {'source_model': 'Risk'} -- and a mis-capitalised
"Acm Properties" label, because the introspection loop called str(value) on
whatever type a column happened to hold. This pins the fix: dict/list values
are rendered as readable text, empty dict/list columns are skipped like any
other empty value, and known acronyms are capitalised correctly.
"""
from sqlalchemy import Column, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

from app.modules.architecture.routes.archimate_crud.routes import _get_display_fields

Base = declarative_base()


class _FakeElement(Base):
    __tablename__ = "test_display_fields_fake_element"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    acm_properties = Column(JSONB)
    custom_properties = Column(JSONB)
    tags = Column(JSONB)


def _make(acm_properties=None, custom_properties=None, tags=None):
    row = _FakeElement()
    row.id = 1
    row.name = "Test Element"
    row.acm_properties = acm_properties
    row.custom_properties = custom_properties
    row.tags = tags
    return row


def test_empty_dict_column_is_skipped_not_rendered_as_braces():
    row = _make(acm_properties={}, custom_properties={"source_model": "Risk"})
    fields = {f["label"]: f["value"] for f in _get_display_fields(row, _FakeElement)}

    assert "Acm properties" not in fields
    assert "ACM Properties" not in fields


def test_populated_dict_renders_as_readable_pairs_not_python_repr():
    row = _make(custom_properties={"source_model": "Risk"})
    fields = {f["label"]: f["value"] for f in _get_display_fields(row, _FakeElement)}

    value = fields["Custom properties"]
    assert value == "Source model: Risk"
    assert "{" not in value and "}" not in value
    assert "'" not in value


def test_known_acronym_is_capitalised_correctly():
    row = _make(acm_properties={"tier": "0"})
    fields = {f["label"]: f["value"] for f in _get_display_fields(row, _FakeElement)}

    assert "ACM properties" in fields
    assert "Acm properties" not in fields


def test_list_column_renders_as_comma_joined_text():
    row = _make(tags=["urgent", "cutover"])
    fields = {f["label"]: f["value"] for f in _get_display_fields(row, _FakeElement)}

    assert fields["Tags"] == "urgent, cutover"


def test_none_and_empty_string_are_still_skipped():
    row = _make()
    row.name = ""
    fields = _get_display_fields(row, _FakeElement)

    labels = [f["label"] for f in fields]
    assert "Acm Properties" not in labels
    assert "Custom properties" not in labels
    assert "Tags" not in labels
