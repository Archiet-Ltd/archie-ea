"""composer-diagram-link-mismatches bucket (D1-D4).

Four sites across the codebase passed a numeric SavedDiagram.id as
``?viewpoint=`` -- a param read only as a named STANDARD_VIEWPOINTS key
server-side -- instead of ``?viewpoint_id=``, the param
``loadSavedViewpoint`` (composer.js) actually reads to load a saved diagram
by id. Passing the wrong one does not error: it silently falls through to
the 'basic' viewpoint, landing the user on the wrong canvas.

D1 (dashboard card) and full browser walkthroughs of D3 live in
tests/smoke/test_composer_link_mismatches.py. This file covers:
  - D2: composer.js linkSubDiagram's URL-building (source assertion, since
    a canvas sub-diagram state is not reachable from a fresh smoke seed).
  - D3: architect_viewpoints() writes SavedDiagram (not ViewpointView), never
    fabricates a ?viewpoint=0 placeholder link, and the response shape uses
    saved_diagram_id.
  - D4: archimate_composer_service.create_diagram(), the create-diagram-from-
    elements route, and chat_workflows.py's solution-diagram redirect_url all
    build viewpoint_id= links.
"""
import re
import uuid

import pytest


def test_composer_js_link_sub_diagram_uses_viewpoint_id():
    """D2: linkSubDiagram must build ?viewpoint_id=<SavedDiagram.id>, not
    ?viewpoint=<id> (which silently falls through to the 'basic' viewpoint
    server-side)."""
    from pathlib import Path

    js = Path("app/static/js/archimate/composer.js").read_text(encoding="utf-8")
    match = re.search(r"linkSubDiagram:\s*function[\s\S]{0,600}", js)
    assert match, "linkSubDiagram function not found in composer.js"
    body = match.group(0)

    assert "viewpoint_id=" in body, (
        "linkSubDiagram must build a ?viewpoint_id= URL to open an existing "
        "sub-diagram -- D2 regressed"
    )
    assert "'/archimate/composer?viewpoint=' + existingId" not in body, (
        "linkSubDiagram still builds the broken ?viewpoint=<numeric id> URL"
    )


def test_create_diagram_returns_viewpoint_id_url(app, db_session, make_org):
    """D4: archimate_composer_service.create_diagram() -- the helper feeding
    Slack, Teams and structured-deliverable links -- must return
    ?viewpoint_id=, not ?viewpoint=<numeric>."""
    from app.models.archimate_core import ArchiMateElement
    from app.services.archimate_composer_service import create_diagram

    org = make_org("create-diagram")
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        el = ArchiMateElement(
            name=f"D4 Probe {uuid.uuid4().hex[:6]}", type="ApplicationComponent",
            layer="application", organization_id=org.id,
        )
        db_session.add(el)
        db_session.flush()

        url = create_diagram([el.id], name="D4 test diagram")

    assert url is not None
    assert "viewpoint_id=" in url, f"create_diagram returned a broken link: {url!r}"
    assert re.search(r"\?viewpoint=\d", url) is None, (
        f"create_diagram still builds a numeric ?viewpoint= link: {url!r}"
    )


def test_create_diagram_accepts_viewpoint_type(app, db_session, make_org):
    """D3 depends on create_diagram() accepting an optional viewpoint_type
    kwarg, backward-compatible with its other four callers."""
    from app.models.archimate_core import ArchiMateElement, SavedDiagram
    from app.services.archimate_composer_service import create_diagram

    org = make_org("create-diagram-vptype")
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        el = ArchiMateElement(
            name=f"D3 vptype probe {uuid.uuid4().hex[:6]}", type="ApplicationComponent",
            layer="application", organization_id=org.id,
        )
        db_session.add(el)
        db_session.flush()

        url = create_diagram([el.id], name="vptype test", viewpoint_type="application")

    assert url is not None
    diagram_id = int(url.rsplit("=", 1)[-1])
    diagram = SavedDiagram.query.get(diagram_id)
    assert diagram.viewpoint_type == "application"


def test_architect_viewpoints_creates_saved_diagrams_not_viewpoint_views(
    app, db_session, make_org, login_as
):
    """D3: the route must create SavedDiagram rows (option (b) from the
    task's resolved design), never construct a ViewpointView, and return
    saved_diagram_id / composer_url with viewpoint_id= links."""
    from app.models.archimate_core import ArchiMateElement, SavedDiagram
    from app.models.archimate_viewpoint import ViewpointView
    from app.models.solution_archimate_element import SolutionArchiMateElement
    from app.models.solution_models import Solution
    from app.models.user import User

    org = make_org("architect-viewpoints")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"architect-vp-{suffix}@example.com",
        first_name="Architect", last_name="VP",
        organization_id=org.id, confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)

    solution = Solution(name=f"D3 Solution {suffix}", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()

    el = ArchiMateElement(
        name=f"D3 Element {suffix}", type="ApplicationComponent",
        layer="application", organization_id=org.id,
    )
    db_session.add(el)
    db_session.flush()
    db_session.add(SolutionArchiMateElement(solution_id=solution.id, element_id=el.id))
    db_session.flush()

    before_viewpoint_view_count = ViewpointView.query.count()

    client = app.test_client()
    login_as(client, user)

    resp = client.post(
        "/ai-chat/architect/viewpoints",
        json={"solution_id": solution.id},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    payload = resp.get_json()
    assert payload["success"], payload

    viewpoints = payload["viewpoints"]
    assert len(viewpoints) == 4, viewpoints

    # No new ViewpointView rows -- the dead store gains no new writer.
    assert ViewpointView.query.count() == before_viewpoint_view_count, (
        "architect_viewpoints() must not construct ViewpointView rows -- D3 "
        "resolved design is option (b), SavedDiagram only"
    )

    saw_real_link = False
    for vp in viewpoints:
        assert "viewpoint_view_id" not in vp, (
            "response must not carry the retired viewpoint_view_id field"
        )
        assert "saved_diagram_id" in vp
        if vp["composer_url"] is not None:
            saw_real_link = True
            assert "viewpoint_id=" in vp["composer_url"], vp
            assert re.search(r"\?viewpoint=\d", vp["composer_url"]) is None, (
                f"fabricated placeholder link returned: {vp['composer_url']!r}"
            )
            diagram = SavedDiagram.query.get(vp["saved_diagram_id"])
            assert diagram is not None, "composer_url references a diagram that was never created"
            assert diagram.organization_id == org.id
        else:
            # Never a placeholder link -- null means null.
            assert vp["saved_diagram_id"] is None, vp

    assert saw_real_link, "at least one of the 4 viewpoints should match the seeded element"


def test_architect_viewpoints_zero_match_returns_null_not_placeholder(
    app, db_session, make_org, login_as
):
    """A viewpoint matching zero elements must return composer_url: None --
    never the old fabricated ?viewpoint=0 link the tech-lead flagged as a
    fabricated-data violation."""
    from app.models.solution_models import Solution
    from app.models.user import User

    org = make_org("architect-viewpoints-empty")
    suffix = uuid.uuid4().hex[:8]
    user = User(
        email=f"architect-vp-empty-{suffix}@example.com",
        first_name="Architect", last_name="Empty",
        organization_id=org.id, confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)

    solution = Solution(name=f"Empty Solution {suffix}", organization_id=org.id)
    db_session.add(solution)
    db_session.flush()

    client = app.test_client()
    login_as(client, user)

    resp = client.post(
        "/ai-chat/architect/viewpoints",
        json={"solution_id": solution.id},
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    for vp in payload["viewpoints"]:
        assert vp["element_count"] == 0
        assert vp["composer_url"] is None, (
            f"empty viewpoint must return composer_url: None, got {vp['composer_url']!r}"
        )
        assert vp["saved_diagram_id"] is None


def test_viewpoint_view_model_carries_deprecation_comment():
    """D3 constraint: leave a comment where ViewpointView is defined so the
    next reader does not re-adopt a dead, non-tenant-scoped store."""
    from pathlib import Path

    src = Path("app/models/archimate_viewpoint.py").read_text(encoding="utf-8")
    assert "DEPRECATED" in src and "ViewpointView" in src
    assert "SavedDiagram" in src


@pytest.mark.parametrize(
    "path,needle",
    [
        (
            "app/modules/architecture/routes/archimate_routes.py",
            '"composer_url": f"/archimate/composer?viewpoint_id={diagram.id}"',
        ),
        (
            "app/modules/ai_chat/routes/chat_workflows.py",
            'redirect_url = f"/archimate/composer?viewpoint_id={diag.id}',
        ),
        (
            "app/services/archimate_composer_service.py",
            'url = f"/archimate/composer?viewpoint_id={diagram.id}"',
        ),
    ],
)
def test_d4_sites_use_viewpoint_id(path, needle):
    """D4: the three additional sites the tech-lead found must all use
    viewpoint_id=, matching the already-correct pattern elsewhere in each
    file."""
    from pathlib import Path as _P

    src = _P(path).read_text(encoding="utf-8")
    assert needle in src, f"{path} does not contain the fixed viewpoint_id= link"


def test_commands_js_guards_on_composer_url_not_dead_field():
    """The AI-chat renderer must guard the anchor on composer_url (which can
    be null on a genuine miss) rather than the retired viewpoint_view_id
    field."""
    from pathlib import Path

    js = Path("app/static/js/ai_chat/commands.js").read_text(encoding="utf-8")
    assert "vp.viewpoint_view_id" not in js
    assert "vp.composer_url" in js


# ── 2026-09-19 refuter follow-up: broader param-name mismatch class ─────────
#
# The D1-D4 re-grep only checked for `?viewpoint=<numeric>`. A refuter pass
# found the real bug class is broader: "a composer link sets a query
# parameter the receiving end never reads" -- of which the numeric
# `?viewpoint=` typo was only one instance. These tests cover the remaining
# five sites found by the corrected, receiver-contract-based audit.


def test_composer_js_defaults_to_layered_viewpoint_when_only_layer_given():
    """FIX 1: composer.js's init logic must not silently drop `initialLayer`
    when no `initialViewpoint` is present -- a layer-only link (e.g.
    traceability_chain.html's "+ Add" buttons) must still open something
    sensible (the 'layered' viewpoint, pre-filtered to that layer) instead
    of falling through to the generic blank-canvas branch."""
    from pathlib import Path

    js = Path("app/static/js/archimate/composer.js").read_text(encoding="utf-8")
    match = re.search(
        r"Check for initial viewpoint from URL[\s\S]{0,1600}",
        js,
    )
    assert match, "initial viewpoint init block not found in composer.js"
    body = match.group(0)
    assert "initialVp = 'layered'" in body, (
        "composer.js must default initialVp to 'layered' when only "
        "initialLayer is set -- FIX 1 regressed"
    )


def test_traceability_chain_add_buttons_carry_layer_only():
    """FIX 1: the 8 traceability_chain.html '+ Add' buttons pass a bare
    ?layer=<X> with no ?viewpoint= -- documenting the exact shape the
    composer.js receiver-side fix (above) exists to handle."""
    from pathlib import Path

    src = Path("app/templates/archimate/traceability_chain.html").read_text(encoding="utf-8")
    layers = re.findall(r"composer_page'\)\s*\}\}\?layer=(\w+)", src)
    assert set(layers) == {"Motivation", "Business", "Application", "Technology"}, layers
    assert len(layers) == 8, f"expected all 8 '+ Add' buttons, found {len(layers)}"


def test_blueprint_js_uses_solution_id_param_not_solution():
    """FIX 2: blueprint.js's openComposer must use ?solution_id=, the param
    composer_page actually reads, not ?solution= -- and must not invent a
    ?section= scroll mechanism the composer has no way to honour."""
    from pathlib import Path

    js = Path("app/static/js/solutions/blueprint.js").read_text(encoding="utf-8")
    match = re.search(r"openComposer:\s*function[\s\S]{0,500}", js)
    assert match, "openComposer not found in blueprint.js"
    body = match.group(0)
    assert "solution_id=" in body
    assert "?solution=" not in body
    assert "section=" not in body


@pytest.mark.parametrize(
    "path,dead_needle",
    [
        ("app/templates/archimate/traceability_chain.html", "?element_id=' + editElement.id"),
    ],
)
def test_fix3_sites_no_longer_build_unread_params(path, dead_needle):
    """FIX 3 (original round): element_id/process query params the composer
    never read must not appear on these links -- either routed through
    create_diagram() for a real viewpoint_id, or made honestly generic.

    architecture/elements.html was in this list too, until the composer
    gained a real `element` parameter (2026-09-21, see
    composer_page()'s and composer.js's _selectInitialElement's own
    docstrings/comments): `?element=` moved from "dead, must not appear" to
    "correct, must appear" for that site and two traceability_chain.html
    sites -- see test_element_param_sites_use_the_now-live_param below,
    which is that test's mirror image, not this one's contradiction.
    ai_chat/commands.js's process gap link was removed outright in the same
    round: process_id is an APQCProcess.id, not an ArchiMateElement.id, so
    there was no element param honest or otherwise to give it."""
    from pathlib import Path

    src = Path(path).read_text(encoding="utf-8")
    assert dead_needle not in src, f"{path} still builds the unread param: {dead_needle!r}"
    assert "/archimate/composer" in src, f"{path} should still link to the composer generically"


@pytest.mark.parametrize(
    "path,live_needle",
    [
        ("app/templates/architecture/elements.html", "?element=' + selectedElement.id"),
        ("app/templates/archimate/traceability_chain.html", "?element=' + editElement.id"),
    ],
)
def test_element_param_sites_use_the_now_live_param(path, live_needle):
    """2026-09-21: the composer gained a real `element` query parameter
    (composer_page() + composer.js's _selectInitialElement -- selects and
    centres that element once its viewpoint data has loaded). These two
    sites previously linked generically because `element` was unread; they
    now use it, which is what makes their "Open/Edit in Composer" links
    actually open the element clicked, not just the composer in general."""
    from pathlib import Path

    src = Path(path).read_text(encoding="utf-8")
    assert live_needle in src, f"{path} should use the now-live element param: {live_needle!r}"


def test_fix4_composer_url_helper_uses_create_diagram_not_elements_param():
    """FIX 4: the two archimate_routes.py sites that built a literal
    ?elements=<ids> composer_url (never read by anything) must route through
    create_diagram() for a real ?viewpoint_id= link instead."""
    from pathlib import Path

    src = Path("app/modules/architecture/routes/archimate_routes.py").read_text(encoding="utf-8")
    assert 'f"/archimate/composer?elements=' not in src, (
        "a literal, unread ?elements= composer_url is still being built"
    )
    assert "_composer_url_for_elements" in src
    assert "from app.services.archimate_composer_service import create_diagram" in src


def test_composer_url_for_elements_helper(app, db_session, make_org):
    """The FIX 4 helper returns a real viewpoint_id= URL for real elements,
    and None for an empty id list -- never a fabricated placeholder."""
    from app.models.archimate_core import ArchiMateElement
    from app.modules.architecture.routes.archimate_routes import _composer_url_for_elements

    org = make_org("fix4-composer-url-helper")
    with app.test_request_context("/"):
        from flask import g

        g.current_org_id = org.id
        el = ArchiMateElement(
            name=f"FIX4 Probe {uuid.uuid4().hex[:6]}", type="ApplicationComponent",
            layer="application", organization_id=org.id,
        )
        db_session.add(el)
        db_session.flush()

        url = _composer_url_for_elements([el.id], "FIX4 test diagram")
        assert url is not None
        assert "viewpoint_id=" in url
        assert "elements=" not in url

        assert _composer_url_for_elements([], "empty") is None
