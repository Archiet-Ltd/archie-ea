"""The diagram service keeps its stored-code type lines and 22-character cut
for callers that pass no naming preference (the whole-model export), and prints
plain type names and full names only when asked."""
from app.services.archimate_diagram_service import DiagramRenderService


class _El:
    def __init__(self, id, name, type, layer="business"):
        self.id, self.name, self.type, self.layer = id, name, type, layer


class _Rows:
    """Stands in for a model class: ``Model.query.filter(...).all()`` returns the rows."""

    source_id = target_id = id = None

    def __init__(self, rows):
        self.query = self
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _Db:
    @staticmethod
    def or_(*a):
        return None


def _render(monkeypatch, elements, **kw):
    import app.services.archimate_diagram_service as mod

    class _InCol:
        def in_(self, ids):
            return None

    rels = _Rows([])
    rels.source_id = rels.target_id = _InCol()
    els = _Rows(elements)
    els.id = _InCol()
    monkeypatch.setattr(mod, "db", _Db)
    monkeypatch.setattr(mod, "ArchiMateRelationship", rels)
    monkeypatch.setattr(mod, "ArchiMateElement", els)
    return DiagramRenderService().render_diagram([e.id for e in elements], "business", **kw)


def test_no_naming_preference_keeps_todays_labels(monkeypatch):
    long = "Handle customer checkout and returns"
    svg = _render(monkeypatch, [_El(1, long, "BusinessProcess")])
    assert "Handle customer checko…" in svg
    assert ">BusinessProcess</text>" in svg
    assert "<title>" not in svg


def test_naming_preference_prints_plain_type_and_full_name(monkeypatch):
    long = "Handle customer checkout and returns"
    svg = _render(monkeypatch, [_El(1, long, "BusinessProcess")], type_namer=lambda t: "Process")
    assert ">Process</text>" in svg
    assert "BusinessProcess" not in svg
    assert "…" not in svg
    assert "<title>%s</title>" % long in svg
