"""Canvas export and share.

Covers: (1) the format export of an empty canvas names every box with its
configured reason (``canvas_box_empty`` for a plain entry zone,
``canvas_box_not_derived`` for a composed one); (2) a canvas whose saved
diagram carries an entry lists it, and that zone drops out of the reasons
list; (3) the three interchange formats (mermaid, lucid, archi) each carry
the reasons in their own notes mechanism, and a fourth format is refused;
(4) a foreign canvas id at the export route 404s rather than leaking;
(5) the existing in-tenant saved-diagram share returns the same missing-id
bytes for a foreign id as for a missing one; (6) a static walk of the URL
map proves every canvas or saved-diagram route is login-guarded and none
sits under a public path.

Export runs over the record's saved diagram when the record already carries
a link, else the tenant's own saved diagram of that template's kind — there
is no projection yet, so an entry zone is proven here through the same
``acm_properties`` profile tag the design uses for a plain zone, not through
flags, attribute totals or the risk register (out of scope for this file).
"""
from __future__ import annotations

import re
import uuid

from app.config.archimate_viewpoints import CANVAS_TEMPLATES


def _make_user(db_session, org_id, label):
    from app.models.user import User

    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"{label.lower()}-{suffix}@example.com",
        organization_id=org_id,
        enterprise_role="enterprise_architect",
        confirmed=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _bmc(db_session, org_id, name="Canvas"):
    from app.models.business_model import BusinessModelCanvas

    canvas = BusinessModelCanvas(name=name, organization_id=org_id)
    db_session.add(canvas)
    db_session.flush()
    return canvas


def _case(db_session, org_id, title="Case"):
    from app.models.business_case import BusinessCase

    case = BusinessCase(title=title, organization_id=org_id)
    db_session.add(case)
    db_session.flush()
    return case


# -- The format export of an empty canvas names every box's reason ----------


class TestFormatExportNamesEveryEmptyBox:
    def test_business_model_canvas_mermaid_names_every_box(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("cv5-bmc-empty")
        user = _make_user(db_session, org.id, "BmcExportOwner")
        canvas = _bmc(db_session, org.id, "Empty BMC")
        canvas_id = canvas.id

        login_as(client, user)
        resp = client.get(f"/business-model/{canvas_id}/export?format=mermaid")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)

        zones = CANVAS_TEMPLATES["business_model_canvas"]["zones"]
        assert len(zones) > 0
        for zone in zones:
            reason = zone.get("empty_reason") or "canvas_box_empty"
            line = f"{zone['box_key']}: {reason}"
            assert line in body, f"missing {line!r} in export:\n{body}"

    def test_business_case_mermaid_names_every_box_including_composed(
        self, app, db_session, make_org, client, login_as
    ):
        org = make_org("cv5-case-empty")
        user = _make_user(db_session, org.id, "CaseExportOwner")
        case = _case(db_session, org.id, "Empty Case")
        case_id = case.id

        login_as(client, user)
        resp = client.get(f"/business-case/{case_id}/export?format=mermaid")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)

        zones = CANVAS_TEMPLATES["business_case"]["zones"]
        for zone in zones:
            reason = zone.get("empty_reason") or "canvas_box_empty"
            line = f"{zone['box_key']}: {reason}"
            assert line in body, f"missing {line!r} in export:\n{body}"

        # The two composed zones report canvas_box_not_derived, never
        # canvas_box_empty — a composed box is never claimed to be simply
        # empty, matching the page's own rendering.
        assert "executive_summary: canvas_box_not_derived" in body
        assert "investment_appraisal: canvas_box_not_derived" in body


# -- A canvas with an entry lists it, and that zone drops from reasons ------


class TestFormatExportListsEntries:
    def test_entry_zone_is_listed_and_excluded_from_reasons(
        self, app, db_session, make_org, client, login_as, tenant_ctx
    ):
        from app.models.archimate_core import ArchiMateElement, SavedDiagram, SavedDiagramElement

        org = make_org("cv5-bmc-entries")
        user = _make_user(db_session, org.id, "BmcEntryOwner")
        canvas = _bmc(db_session, org.id, "BMC With An Entry")
        canvas_id = canvas.id

        with tenant_ctx(org.id):
            element = ArchiMateElement(
                name="Early adopters", type="Stakeholder", layer="motivation",
                organization_id=org.id, acm_properties={"profile": "customer_segment"},
            )
            db_session.add(element)
            db_session.flush()

            diagram = SavedDiagram(
                name="BMC diagram", viewpoint_type="business_model_canvas",
                organization_id=org.id,
            )
            db_session.add(diagram)
            db_session.flush()
            db_session.add(SavedDiagramElement(diagram_id=diagram.id, element_id=element.id))
            db_session.flush()

        login_as(client, user)
        resp = client.get(f"/business-model/{canvas_id}/export?format=mermaid")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_data(as_text=True)

        # The entry is listed as an element in the export...
        assert "Early adopters" in body
        # ...its zone (customer_segments, a type_profile zone) no longer
        # carries a reason...
        assert "customer_segments: canvas_box_empty" not in body
        # ...while a zone with no entry still does.
        assert "channels: canvas_box_empty" in body


# -- Each of the three interchange formats carries the reasons -------------


class TestEveryFormatCarriesReasons:
    def test_lucid_export_carries_notes(self, app, db_session, make_org, client, login_as):
        import io
        import json
        import zipfile

        org = make_org("cv5-bmc-lucid")
        user = _make_user(db_session, org.id, "BmcLucidOwner")
        canvas = _bmc(db_session, org.id, "Lucid BMC")
        canvas_id = canvas.id

        login_as(client, user)
        resp = client.get(f"/business-model/{canvas_id}/export?format=lucid")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        with zipfile.ZipFile(io.BytesIO(resp.get_data())) as archive:
            document = json.loads(archive.read("document.json"))
        notes = document.get("notes") or []
        assert any(n.startswith("channels: canvas_box_empty") for n in notes), notes
        # The reasons are metadata only — no shape was fabricated for them.
        shape_texts = [
            ta.get("text", "")
            for page in document.get("pages", [])
            for shape in page.get("shapes", [])
            for ta in shape.get("textAreas", [])
        ]
        assert "canvas_box_empty" not in " ".join(shape_texts)

    def test_archi_export_carries_reason_properties(self, app, db_session, make_org, client, login_as):
        import xml.etree.ElementTree as ET

        org = make_org("cv5-case-archi")
        user = _make_user(db_session, org.id, "CaseArchiOwner")
        case = _case(db_session, org.id, "Archi Case")
        case_id = case.id

        login_as(client, user)
        resp = client.get(f"/business-case/{case_id}/export?format=archi")
        assert resp.status_code == 200, resp.get_data(as_text=True)

        root = ET.fromstring(resp.get_data(as_text=True))
        props = {p.get("key"): p.get("value") for p in root.findall("property")}
        assert props.get("reason:problem_statement") == "canvas_box_empty"
        assert props.get("reason:executive_summary") == "canvas_box_not_derived"


# -- No fourth format is offered to a canvas ---------------------------------


class TestNoFourthFormat:
    def test_archimate_exchange_format_is_refused(self, app, db_session, make_org, client, login_as):
        org = make_org("cv5-bmc-noformat")
        user = _make_user(db_session, org.id, "BmcFormatOwner")
        canvas = _bmc(db_session, org.id, "Format-limited BMC")
        canvas_id = canvas.id

        login_as(client, user)
        resp = client.get(f"/business-model/{canvas_id}/export?format=archimate_exchange")
        assert resp.status_code == 400


# -- Foreign canvas ids at the export route 404, not leak -------------------


class TestForeignCanvasIdExportReturnsNotFound:
    def test_foreign_business_model_canvas_export_404s(
        self, app, db_session, make_org, client, login_as
    ):
        org_a = make_org("cv5-export-nf-a")
        org_b = make_org("cv5-export-nf-b")
        user_b = _make_user(db_session, org_b.id, "ExportViewerB")
        canvas = _bmc(db_session, org_a.id, "Org A BMC")
        canvas_id = canvas.id

        login_as(client, user_b)
        resp = client.get(f"/business-model/{canvas_id}/export?format=mermaid")
        assert resp.status_code == 404

    def test_foreign_business_case_export_404s(
        self, app, db_session, make_org, client, login_as
    ):
        org_a = make_org("cv5-export-nf-case-a")
        org_b = make_org("cv5-export-nf-case-b")
        user_b = _make_user(db_session, org_b.id, "CaseExportViewerB")
        case = _case(db_session, org_a.id, "Org A Case")
        case_id = case.id

        login_as(client, user_b)
        resp = client.get(f"/business-case/{case_id}/export?format=mermaid")
        assert resp.status_code == 404


# -- The existing in-tenant saved-diagram share -----------------------------


class TestForeignSavedDiagramReturnsMissingIdBytes:
    def test_foreign_saved_diagram_id_returns_same_bytes_as_a_missing_one(
        self, app, db_session, make_org, client, login_as, tenant_ctx
    ):
        from app.models.archimate_core import SavedDiagram

        org_a = make_org("cv5-share-a")
        org_b = make_org("cv5-share-b")
        user_b = _make_user(db_session, org_b.id, "ShareViewerB")

        with tenant_ctx(org_a.id):
            diagram = SavedDiagram(name="Org A canvas diagram", organization_id=org_a.id)
            db_session.add(diagram)
            db_session.flush()
            diagram_id = diagram.id

        login_as(client, user_b)
        own_missing = client.get("/archimate/api/saved-viewpoints/999999999")
        foreign = client.get(f"/archimate/api/saved-viewpoints/{diagram_id}")

        assert own_missing.status_code == 404
        assert foreign.status_code == 404
        assert foreign.get_data() == own_missing.get_data()


# -- No public or unauthenticated route serves a canvas or saved diagram ----


_CANVAS_PATH_PREFIXES = ("/business-model", "/business-case")
_SAVED_DIAGRAM_PATH_PREFIXES = ("/archimate/api/saved-viewpoints",)
_SHARE_DESTINATION_PATHS = ("/archimate/composer",)


def _matches_canvas_or_saved_diagram(path: str) -> bool:
    if path in _SHARE_DESTINATION_PATHS:
        return True
    return path.startswith(_CANVAS_PATH_PREFIXES) or path.startswith(_SAVED_DIAGRAM_PATH_PREFIXES)


def _concrete_path(rule) -> str:
    """Substitute every ``<converter:name>`` in a Werkzeug rule with ``1`` —
    good enough for a login-guard probe; the guard runs before any id lookup."""
    return re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", "1", str(rule))


class TestNoPublicRoute:
    def test_no_canvas_or_saved_diagram_route_sits_under_a_public_path(self, app):
        offenders = []
        for rule in app.url_map.iter_rules():
            path = str(rule)
            if not _matches_canvas_or_saved_diagram(path):
                continue
            if "/public" in path or "/share/public" in path:
                offenders.append(path)
        assert offenders == []

    def test_every_canvas_or_saved_diagram_route_is_login_guarded(self, app, client):
        checked = 0
        for rule in app.url_map.iter_rules():
            path = str(rule)
            if not _matches_canvas_or_saved_diagram(path):
                continue
            if "GET" not in (rule.methods or set()):
                continue
            checked += 1
            resp = client.get(_concrete_path(rule), follow_redirects=False)
            assert resp.status_code in (302, 401, 403), (
                f"{path} reachable without login (status {resp.status_code})"
            )
            if resp.status_code == 302:
                assert "/account/login" in resp.headers.get("Location", ""), path
        # Sanity: the walk actually found the routes this file is about —
        # an empty loop would pass every assertion above and prove nothing.
        assert checked >= 5, "the URL map walk matched fewer canvas/saved-diagram GET routes than expected"
