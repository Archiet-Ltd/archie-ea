"""The diagram service keeps its stored-code type lines and 22-character cut
for callers that pass no naming preference (the whole-model export), and prints
plain type names and full names only when asked."""
from app.services.archimate_diagram_service import DiagramRenderService


class _El:
    def __init__(self, id, name, type, layer="business"):
        self.id, self.name, self.type, self.layer = id, name, type, layer


def _render(monkeypatch, elements, **kw):
    svc = DiagramRenderService()
    import app.services.archimate_diagram_service as mod

    class _Q:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *a, **k):
            return self

        def all(self):
            return self.rows

    monkeypatch.setattr(mod.ArchiMateRelationship, "query", _Q([]), raising=False)
    monkeypatch.setattr(mod.ArchiMateElement, "query", _Q(elements), raising=False)
    monkeypatch.setattr(mod.ArchiMateRelationship, "source_id", mod.ArchiMateRelationship.source_id, raising=False)
    return svc.render_diagram([e.id for e in elements], "business", **kw)


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
