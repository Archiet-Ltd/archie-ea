"""The Ask and Twin map pages: who can open them, what they render, and the
rules their markup and scripts keep.

Two kinds of test live here. The first drives the real pages through the test
client (signed in, signed out, workspace empty, populated, counts unreadable).
The second reads the page templates and scripts as text, for the properties that
must hold for every render rather than for one: one disclosure control, a single
element search endpoint, no layer or type asked for, no sentence written in the
browser, colour only from the layer tokens.
"""

from __future__ import annotations

import re
import sys
import uuid
from html.parser import HTMLParser
from pathlib import Path

from app.datetime_helpers import utcnow

import pytest

# Fixtures (app, db_session, make_org, client, login_as) come from this
# directory's conftest, which re-exports the shared ones.

PAGES = ["/intelligence/ask", "/intelligence/twin-map"]

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATE_DIR = REPO_ROOT / "app" / "modules" / "intelligence" / "templates" / "intelligence"
SCRIPT_DIR = REPO_ROOT / "app" / "static" / "js" / "intelligence"


def _text_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _templates() -> dict[str, str]:
    return {p.name: _text_of(p) for p in sorted(TEMPLATE_DIR.glob("*.html"))}


def _scripts() -> dict[str, str]:
    return {p.name: _text_of(p) for p in sorted(SCRIPT_DIR.glob("*.js"))}


def _code(source: str) -> str:
    """A script with its comments removed, for checks about what it does."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//[^\n]*$", "", source)


def _everything() -> dict[str, str]:
    files = {f"templates/{n}": t for n, t in _templates().items()}
    files.update({f"js/{n}": t for n, t in _scripts().items()})
    return files


def _user(db_session, org_id):
    from app.models.user import User

    user = User(
        email=f"ui-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Ui",
        last_name="Tester",
        organization_id=org_id,
        confirmed=True,
        enterprise_role="enterprise_architect",
    )
    db_session.add(user)
    db_session.flush()
    return user


class _Visible(HTMLParser):
    """The text a person reads: text nodes only, skipping scripts and styles."""

    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.chunks.append(data.strip())


def _main_html(html: str) -> str:
    match = re.search(r'<main id="main-content".*?</main>', html, re.S)
    assert match, "the page has no main region"
    return match.group(0)


def _visible_text(html: str) -> str:
    parser = _Visible()
    parser.feed(html)
    return " ".join(parser.chunks)


def _rendered_templates(app):
    from flask import template_rendered

    seen: list[str] = []

    def record(sender, template, context, **extra):
        seen.append(template.name)

    template_rendered.connect(record, app)
    return seen, lambda: template_rendered.disconnect(record, app)


# --- the routes -------------------------------------------------------------


@pytest.mark.parametrize("path,template", [
    ("/intelligence/ask", "intelligence/ask.html"),
    ("/intelligence/twin-map", "intelligence/twin_map.html"),
])
def test_page_renders_for_a_signed_in_user_with_its_own_template(
    app, db_session, make_org, client, login_as, path, template
):
    org = make_org("ui-render")
    user = _user(db_session, org.id)
    seen, stop = _rendered_templates(app)
    try:
        login_as(client, user)
        response = client.get(path)
    finally:
        stop()
    assert response.status_code == 200
    assert template in seen


@pytest.mark.parametrize("path", PAGES)
def test_page_is_not_served_to_an_anonymous_visitor(app, client, path):
    response = client.get(path)
    assert response.status_code in (301, 302, 401)
    if response.status_code in (301, 302):
        assert "/account/login" in response.headers["Location"]


def test_the_read_only_pages_are_the_only_routes_this_blueprint_serves(app):
    rules = {
        rule.rule: sorted(rule.methods - {"HEAD", "OPTIONS"})
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("intelligence_ui.")
    }
    assert rules == {
        "/intelligence/ask": ["GET"],
        "/intelligence/twin-map": ["GET"],
        "/intelligence/value-streams-at-risk": ["GET"],
        "/intelligence/traceability": ["GET"],
        "/intelligence/history/as-of": ["GET"],
        "/intelligence/history/changes": ["GET"],
        "/intelligence/api/history/as-of": ["GET"],
        "/intelligence/api/history/changes": ["GET"],
        "/intelligence/api/history/element/<int:element_id>": ["GET"],
    }
    assert not [r for r in rules if r.startswith("/api/")]


def test_pages_have_a_breadcrumb_and_the_page_shell_wrapper(
    app, db_session, make_org, client, login_as
):
    org = make_org("ui-shell")
    user = _user(db_session, org.id)
    for path, title in (("/intelligence/ask", "Ask"), ("/intelligence/twin-map", "Twin map")):
        login_as(client, user)
        html = client.get(path).get_data(as_text=True)
        assert 'aria-label="Breadcrumb"' in html
        assert f'<h1 class="text-2xl font-bold text-foreground">{title}</h1>' in html
        assert "p-6 space-y-6" in html
        assert "container mx-auto" not in _main_html(html)


def test_twin_map_hands_a_whole_number_element_to_the_page(
    app, db_session, make_org, client, login_as
):
    org = make_org("ui-handoff")
    user = _user(db_session, org.id)
    login_as(client, user)
    html = client.get("/intelligence/twin-map?element=42").get_data(as_text=True)
    assert 'data-initial-element="42"' in html


@pytest.mark.parametrize("value", ["abc", "", "4 2", "1e3", "-"])
def test_twin_map_ignores_an_element_that_is_not_a_whole_number(
    app, db_session, make_org, client, login_as, value
):
    org = make_org("ui-bad-element")
    user = _user(db_session, org.id)
    login_as(client, user)
    response = client.get("/intelligence/twin-map", query_string={"element": value})
    assert response.status_code == 200
    assert 'data-initial-element=""' in response.get_data(as_text=True)


def test_ui_blueprint_registration_is_non_fatal_and_leaves_the_api_registered(monkeypatch):
    """A failure to load the UI blueprint costs the two pages and nothing else."""
    monkeypatch.setitem(sys.modules, "app.modules.intelligence.routes.ui", None)
    from app.modules.intelligence import register

    class _Logger:
        def __init__(self):
            self.errors = []

        def exception(self, msg, *args):
            self.errors.append(msg)

        def __getattr__(self, name):
            return lambda *a, **k: None

    class _App:
        def __init__(self):
            self.blueprints = {}
            self.logger = _Logger()

        def register_blueprint(self, bp, **kwargs):
            self.blueprints[bp.name] = bp

    stub = _App()
    register(stub)  # must not raise
    assert set(stub.blueprints) == {"intelligence_api"}
    assert any("UI blueprint" in message for message in stub.logger.errors)


# --- the empty workspace ----------------------------------------------------


def _patch_counts(monkeypatch, counts):
    from app._bootstrap import context_processors

    if isinstance(counts, Exception):
        def fail(org_id, ttl=None):
            raise counts

        monkeypatch.setattr(context_processors, "compute_nav_counts", fail)
    else:
        monkeypatch.setattr(
            context_processors, "compute_nav_counts", lambda org_id, ttl=None: dict(counts)
        )


@pytest.mark.parametrize("path", PAGES)
def test_an_empty_workspace_shows_the_setup_state_instead_of_the_picker(
    app, db_session, make_org, client, login_as, monkeypatch, path
):
    _patch_counts(monkeypatch, {"applications": 0, "elements": 0, "capabilities": 0, "vendors": 0})
    org = make_org("ui-empty")
    user = _user(db_session, org.id)
    login_as(client, user)
    html = _main_html(client.get(path).get_data(as_text=True))
    text = _visible_text(html)
    assert "Nothing is modelled yet" in text
    assert "Add your systems and teams, and this is where you will ask questions about them." in text
    assert "Set up your workspace" in text
    assert 'href="/dashboard/overview"' in html
    assert 'role="combobox"' not in html
    assert 'role="status"' in html
    assert not re.search(r"connector", text, re.I)
    assert "onboarding" not in html.lower()


@pytest.mark.parametrize("path", PAGES)
def test_the_gate_reads_counts_taken_now_not_a_cached_empty_workspace(
    app, db_session, make_org, client, login_as, path
):
    """A new organisation opens a page while empty, then models its first
    element: the very next Ask or Twin map page must see it."""
    org = make_org("ui-fresh-counts")
    user = _user(db_session, org.id)
    login_as(client, user)
    # Open the page first, as the new organisation would, so any count read
    # before the element exists has had its chance to be kept and reused.
    assert client.get(path).status_code == 200
    from app.models.archimate_core import ArchiMateElement

    db_session.add(ArchiMateElement(
        name="Fresh element %s" % uuid.uuid4().hex[:6], type="ApplicationComponent",
        layer="application", organization_id=org.id,
    ))
    db_session.flush()
    html = _main_html(client.get(path).get_data(as_text=True))
    assert 'role="combobox"' in html
    assert "Nothing is modelled yet" not in html


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("counts", [
    {"applications": 1, "elements": 0, "capabilities": 0, "vendors": 0},
    {"applications": 0, "elements": 5, "capabilities": 0, "vendors": 0},
    {"applications": 0, "elements": 0, "capabilities": 2, "vendors": 0},
])
def test_a_populated_workspace_shows_the_picker_and_no_setup_state(
    app, db_session, make_org, client, login_as, monkeypatch, path, counts
):
    _patch_counts(monkeypatch, counts)
    org = make_org("ui-populated")
    user = _user(db_session, org.id)
    login_as(client, user)
    html = _main_html(client.get(path).get_data(as_text=True))
    assert 'role="combobox"' in html
    assert "Nothing is modelled yet" not in html


@pytest.mark.parametrize("path", PAGES)
def test_global_vendor_counts_do_not_hide_the_empty_workspace_state(
    app, db_session, make_org, client, login_as, monkeypatch, path
):
    _patch_counts(monkeypatch, {"applications": 0, "elements": 0, "capabilities": 0, "vendors": 3})
    org = make_org("ui-vendors-only")
    user = _user(db_session, org.id)
    login_as(client, user)
    html = _main_html(client.get(path).get_data(as_text=True))
    text = _visible_text(html)
    assert "Nothing is modelled yet" in text
    assert 'role="combobox"' not in html


@pytest.mark.parametrize("path", PAGES)
def test_unreadable_counts_never_read_as_an_empty_workspace(
    app, db_session, make_org, client, login_as, monkeypatch, path
):
    """The shell answers a failed count with zeros; the pages must not take that
    for an empty workspace."""
    _patch_counts(monkeypatch, RuntimeError("counts unavailable"))
    org = make_org("ui-counts-down")
    user = _user(db_session, org.id)
    login_as(client, user)
    response = client.get(path)
    assert response.status_code == 200
    html = _main_html(response.get_data(as_text=True))
    assert 'role="combobox"' in html
    assert "Nothing is modelled yet" not in html


def test_unknown_organisation_makes_workspace_counts_unavailable(app):
    from flask import g

    from app.modules.intelligence.routes.ui import _workspace_counts_available

    with app.test_request_context("/intelligence/ask"):
        g.current_org_id = None
        assert _workspace_counts_available() is False


# --- what a person reads ----------------------------------------------------

# Page copy is written for the people who use the page. It carries no status
# label (an upper-case tag such as a status stamp) and no placeholder wording.
STATUS_LABELS = re.compile(r"\b(?:VERIFIED|UNVERIFIED|TBC|TODO)\b|(?i:to be confirmed)")
# ...and nothing that reads as a file, a path, a hash or a tracker id.
FORBIDDEN_SHAPES = re.compile(
    r"(\bapp/|\bdocs/|\bscripts/|\btests?/|\.py\b|\.html\b|\.md\b|\.json\b|"
    r"\b[0-9a-f]{7,40}\b|\b[A-Z]{1,3}-\d+\b)"
)


@pytest.mark.parametrize("path", PAGES)
def test_page_text_carries_no_status_labels_paths_or_ids_outside_full_detail(
    app, db_session, make_org, client, login_as, path
):
    org = make_org("ui-copy")
    user = _user(db_session, org.id)
    login_as(client, user)
    html = _main_html(client.get(path).get_data(as_text=True))
    html = re.sub(r"<[^>]*data-full-detail-region[^>]*>.*?</dl>\s*</div>", "", html, flags=re.S)
    text = _visible_text(html)
    assert text, "the page rendered no text at all"
    assert STATUS_LABELS.findall(text) == []
    assert FORBIDDEN_SHAPES.findall(text) == []


def test_script_strings_carry_no_status_labels():
    strings = []
    for name, source in _scripts().items():
        strings.extend(re.findall(r"'([^'\n]{4,})'", source))
        strings.extend(re.findall(r'"([^"\n]{4,})"', source))
    readable = [s for s in strings if " " in s]
    assert readable, "no readable strings found: the scan is not looking at the scripts"
    offenders = [s for s in readable if STATUS_LABELS.search(s)]
    assert offenders == []


# --- one entry point, no layer, one endpoint --------------------------------


def test_only_the_archimate_element_search_endpoint_is_used_and_nothing_layer_scoped():
    for name, source in _everything().items():
        assert "/architecture/api/layer/" not in source, name
        assert "/architecture/decisions/api/element-search" not in source, name
        for found in re.findall(r"/archimate/api/elements/[A-Za-z0-9_<>{}/-]*", source):
            assert found == "/archimate/api/elements/search", (name, found)
    assert "/archimate/api/elements/search" in _scripts()["core.js"]


def test_the_picker_asks_for_no_layer_type_or_vocabulary_term():
    for name, source in _templates().items():
        assert "<select" not in source, name
    # The picker and the search request carry only what was typed. (The pages
    # read an element's layer from the answer to place it on the map; that is
    # a value shown, never one collected.)
    assert not re.search(r"layer|archimate_type|vocabulary", _code(_scripts()["picker.js"]), re.I)
    picker_markup = re.sub(r"\{#.*?#\}", "", _templates()["_entity_picker.html"], flags=re.S)
    assert not re.search(r"layer|<select", picker_markup, re.I)
    core = _scripts()["core.js"]
    call = re.search(r"Platform\.fetch\.get\(SEARCH_URL, (\{[^}]*\})", core)
    assert call and set(re.findall(r"(\w+):", call.group(1))) == {"q", "limit"}


def test_names_come_only_from_the_impact_answers_element_map():
    for name, source in _everything().items():
        assert "/detail" not in source, name
        assert "localStorage" not in source and "sessionStorage" not in source, name
    # This allowlist drifted out of sync with reality some time before this
    # fix -- the L3/L5/L6 briefs each added a fetch URL to core.js (risk,
    # portfolio, programme) without updating it. Found while adding the L2
    # brief's own strategy URL; corrected to the real, current set rather
    # than bumped by one on top of a stale base. L7 (Data lens) adds its
    # own endpoint which now returns an elements map for name lookups.
    # Compliance (under L6) adds its own endpoint the same way.
    urls = set(re.findall(r"'(/[a-z0-9_/.-]*)'", _scripts()["core.js"]))
    assert urls == {
        "/archimate/api/elements/search",
        "/api/v1/intelligence/impact/",
        "/api/v1/intelligence/derivation/recompute",
        "/api/v1/intelligence/risk/",
        "/api/v1/intelligence/portfolio/",
        "/api/v1/intelligence/programme/",
        "/api/v1/intelligence/strategy/",
        "/api/v1/intelligence/accountability/",
        "/api/v1/intelligence/derived/",
        "/api/v1/intelligence/data/",
        "/api/v1/intelligence/compliance/",
    }


def test_every_network_call_goes_through_platform_fetch():
    for name, source in _scripts().items():
        assert not re.search(r"(?<![.\w])fetch\(", source), name
        assert "XMLHttpRequest" not in source, name
        assert "console." not in source, name
    for name, source in _templates().items():
        assert "console." not in source, name


# --- one disclosure control -------------------------------------------------


def test_full_detail_is_defined_once_and_called_from_exactly_three_places():
    templates = _templates()
    definitions = [n for n, t in templates.items() for _ in re.findall(r"\{%\s*macro\s+full_detail\(", t)]
    assert definitions == ["_full_detail.html"]
    calls = [n for n, t in templates.items() for _ in re.findall(r"\{\{\s*full_detail\(", t)]
    assert sorted(calls) == ["_provenance_drawer.html", "ask.html", "twin_map.html"]


def test_the_disclosure_label_is_full_detail_and_it_starts_collapsed():
    source = _templates()["_full_detail.html"]
    assert re.search(r"data-full-detail-toggle.*?aria-expanded=\"false\"", source, re.S)
    assert "x-data=\"{ open: false }\"" in source
    assert re.search(r"\bFull detail\b", source)
    # A native button that changes nothing in the address bar.
    assert re.search(r"<button type=\"button\" data-full-detail-toggle", source)
    assert "href=" not in source and "window.open" not in source


def test_no_second_show_more_affordance_exists():
    banned = re.compile(
        r"<details|<summary|role=\"tab\"|role=\"tablist\"|x-collapse|Show more|Show less|"
        r"See more|Read more|More questions|Expand all|Collapse all",
        re.I,
    )
    for name, source in _templates().items():
        assert not banned.search(source), name
    for name, source in _scripts().items():
        assert not re.search(r"Show more|More questions", source, re.I), name
    # The only controls that carry aria-expanded: the one disclosure control,
    # the combobox, the question card that opens the picker, the button
    # that collapses the Twin map's side panel, and the Value streams at risk
    # row toggle that opens one value stream's own capabilities.
    owners = {}
    for name, source in _templates().items():
        for tag in re.findall(r"<(?:button|input)[^>]*aria-expanded[^>]*>", source, re.S):
            key = ("full-detail" if "data-full-detail-toggle" in tag else
                   "combobox" if 'role="combobox"' in tag else
                   "question" if "ask-question-" in tag else
                   "rail" if "twin-rail-toggle" in tag else
                   "vsr-row" if "data-vsr-toggle" in tag else "OTHER")
            owners.setdefault(key, []).append(name)
    assert set(owners) == {"full-detail", "combobox", "question", "rail", "vsr-row"}, owners
    assert owners["vsr-row"] == ["_value_streams_at_risk_table.html"], owners


def test_the_map_table_is_present_without_a_toggle():
    source = _templates()["twin_map.html"]
    region = re.search(r"<div ([^>]*)data-map-table-region([^>]*)>", source)
    assert region, "the table region is missing from the Twin map page"
    attributes = region.group(1) + region.group(2)
    assert not re.search(r"x-show|x-if|x-cloak|hidden|:class|@click", attributes)
    table = _templates()["_map_table.html"]
    assert "<caption" in table and "<thead" in table
    assert re.search(r"tabindex", table) is None


def test_level_three_terms_appear_only_inside_full_detail():
    """The ArchiMate type, layer, rule, confidence, chain, depth, time worked out
    and engine version are named in the disclosure macro and nowhere else."""
    terms = ["ArchiMate type", "Engine version", "Derived record", "Worked out at", "Rule"]
    for name, source in _templates().items():
        if name == "_full_detail.html":
            continue
        visible = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
        for term in terms:
            assert not re.search(r">\s*" + re.escape(term) + r"\s*<", visible), (name, term)
        for key in ("detail.type", "detail.layer", "detail.ruleId", "detail.confidence",
                    "detail.engineVersion", "detail.computedAt", "detail.derivedId", "detail.chain"):
            assert key not in visible or name in {"_full_detail.html"}, (name, key)


# --- the sentence is the server's --------------------------------------------


def test_no_script_or_template_writes_the_plain_terms_sentence_or_formats_confidence():
    signatures = [
        r"worked\s+this\s+out\s+because", r"hops\s+away", r"(very|fairly)\s+confident",
        r"less\s+confident", r"second\s+look",
    ]
    # Scoped to confidence: L2/L5's cost and budget variance rows legitimately
    # format a percentage client-side (``wp.costVariancePct.toFixed(1)``), which
    # has nothing to do with confidence. Only a percentage computed from
    # something named "confidence" is the client-side math this test forbids.
    confidence_math = re.compile(
        r"confidence[^\n]{0,40}(toFixed|Math\.round|\*\s*100\b)"
        r"|(toFixed|Math\.round|\*\s*100\b)[^\n]{0,40}confidence",
        re.I,
    )
    for name, source in _everything().items():
        for pattern in signatures:
            assert not re.search(pattern, source, re.I), (name, pattern)
        assert not re.search(r"confidence\s*(>=|<=|>|<)", source), name
        assert not confidence_math.search(source), name


def test_the_drawer_renders_the_supplied_sentence_in_one_paragraph_and_nothing_else():
    source = _templates()["_provenance_drawer.html"]
    assert len(re.findall(r"data-plain-terms", source)) == 1
    assert re.search(r'<p [^>]*data-plain-terms x-text="drawer\.plainTerms"></p>', source)
    assert 'x-show="drawer.plainTerms"' in source


def test_programme_and_strategy_cards_use_server_formatted_variance_text():
    source = _templates()["ask.html"]
    assert "wp.costVarianceText" in source
    assert "initiative.budgetVarianceText" in source
    assert "costVariancePct" not in source
    assert "budgetVariancePct" not in source


# --- colour and copy of the map ---------------------------------------------


def test_no_hard_coded_colour_values_and_no_per_domain_palette():
    for name, source in _everything().items():
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", re.sub(r"&#\d+;", "", source)), name
        assert not re.search(r"\.domain-[a-z]+", source), name
        assert not re.search(r"\brgba?\(|\bhsla?\(\s*\d", source), name
    graph = _scripts()["graph.js"]
    for layer in ("motivation", "strategy", "business", "application", "technology", "implementation"):
        assert f"layer-{layer}" in graph
    assert "layer-risk" not in graph


def test_derived_edges_are_dashed_and_badged_and_explicit_edges_are_solid():
    graph = _scripts()["graph.js"]
    assert "var DASH = '5,5'" in graph
    assert re.search(r"stroke-dasharray.*d\.edge\.kind === 'derived' \? DASH : null", graph)
    assert "Intelligence.WORKED_OUT" in graph
    assert "var WORKED_OUT = 'Worked out';" in _code(_scripts()["core.js"])


def test_the_map_text_is_written_with_text_never_html():
    graph = _scripts()["graph.js"]
    assert ".html(" not in graph
    assert "innerHTML" not in graph
    for name, source in _scripts().items():
        assert "innerHTML" not in source and "insertAdjacentHTML" not in source, name


def test_the_hop_depth_control_is_a_native_range_input_from_one_to_five():
    source = _templates()["twin_map.html"]
    control = re.search(r"<input type=\"range\"[^>]*>", source, re.S)
    assert control
    tag = control.group(0)
    for attribute in ('id="twin-hop-depth"', 'min="1"', 'max="5"', 'step="1"'):
        assert attribute in tag
    assert re.search(r'<label for="twin-hop-depth"[^>]*>Hop depth</label>', source)


def test_the_page_scripts_are_top_level_factories_not_alpine_data_registrations():
    for name in ("ask.js", "twin_map.js"):
        source = _code(_scripts()[name])
        assert "Alpine.data(" not in source, name
    assert "window.askSurface = askSurface;" in _scripts()["ask.js"]
    assert "window.twinMapSurface = twinMapSurface;" in _scripts()["twin_map.js"]
    assert 'x-data="askSurface()"' in _templates()["ask.html"]
    assert 'x-data="twinMapSurface()"' in _templates()["twin_map.html"]


def test_the_shipped_macros_are_the_named_ones_with_the_right_keywords():
    joined = "\n".join(_templates().values())
    assert "from 'components/empty_state.html' import empty_state" in joined
    assert "from 'macros/page_shell.html' import empty_state" not in joined
    assert "from 'components/drawer.html' import drawer with context" in joined
    assert "from 'components/skeleton.html' import" in joined
    for call in re.findall(r"empty_state\((.*?)\) \}\}", joined, re.S):
        assert "headline=" not in call and "body=" not in call
    assert "animate-spin" not in joined and "loader" not in joined


# --- what the pages are built on ----------------------------------------------


def _element(db_session, org_id, name, layer="application", kind="ApplicationComponent"):
    from app.models import ArchiMateElement

    row = ArchiMateElement(name=name, type=kind, layer=layer, organization_id=org_id)
    db_session.add(row)
    db_session.flush()
    return row


def _relationship(db_session, org_id, source, target):
    from app.models import ArchiMateRelationship

    row = ArchiMateRelationship(
        source_id=source.id, target_id=target.id, type="Serving", organization_id=org_id
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_the_impact_answer_carries_the_names_and_derived_fields_the_pages_read(
    app, db_session, make_org, client, login_as
):
    """The pages read four things from the impact answer and invent none of them:
    an element map whose entries hold exactly id, name, type and layer, and on a
    worked-out row its record id, engine version and sentence."""
    from app.modules.intelligence.models.derived_relationship import DerivedRelationship

    org = make_org("ui-contract")
    user = _user(db_session, org.id)
    a = _element(db_session, org.id, "Alpha")
    b = _element(db_session, org.id, "Beta", layer="business", kind="BusinessActor")
    c = _element(db_session, org.id, "Gamma", layer="technology", kind="Node")
    first = _relationship(db_session, org.id, a, b)
    second = _relationship(db_session, org.id, b, c)
    db_session.add(DerivedRelationship(
        organization_id=org.id, source_element_id=a.id, target_element_id=c.id,
        derived_type="Serving", rule_id="serving-through-serving",
        chain=[first.id, second.id], chain_element_ids=[a.id, b.id, c.id], depth=2,
        confidence=0.82, provenance="derivation", engine_version="1.0",
        computed_at=utcnow(), stale=False,
    ))
    db_session.flush()

    login_as(client, user)
    response = client.get(
        f"/api/v1/intelligence/impact/{a.id}?include_derived=true&max_depth=3&with_owner=true"
    )
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert set(data) >= {"rows", "summary", "reasons", "elements"}
    assert set(data["elements"]) == {str(a.id), str(b.id), str(c.id)}
    for entry in data["elements"].values():
        assert set(entry) == {"id", "name", "type", "layer"}
    assert data["elements"][str(b.id)] == {
        "id": b.id, "name": "Beta", "type": "BusinessActor", "layer": "business",
    }

    derived = [r for r in data["rows"] if r["relation"]["kind"] == "derived"]
    explicit = [r for r in data["rows"] if r["relation"]["kind"] == "explicit"]
    assert len(derived) == 1 and len(explicit) == 2
    relation = derived[0]["relation"]
    assert isinstance(relation["derived_id"], int)
    assert relation["engine_version"] == "1.0"
    assert relation["plain_terms"].startswith("We worked this out because")
    for row in explicit:
        assert row["relation"]["derived_id"] is None
        assert row["relation"]["engine_version"] is None
        assert row["relation"]["plain_terms"] is None


def test_the_accessibility_audit_this_work_extends_checks_wcag_22_and_fails_rather_than_skips():
    """The pages are added to an audit that reads the 2.2 tag set and refuses to skip:
    without both, a clean result there would say nothing."""
    source = (REPO_ROOT / "tests" / "smoke" / "test_accessibility_audit.py").read_text(encoding="utf-8")
    tags = re.search(r"^TAGS = \[(.*?)\]", source, re.M | re.S)
    assert tags and '"wcag22aa"' in tags.group(1)
    assert not re.search(r"^(?!\s*#).*importorskip\(", source, re.M)
    assert re.search(r"def axe_module\(browser\):.*?pytest\.fail\(", source, re.S)
    assert '"target-size": "wcag22aa"' in source
    assert '("solution_architect", "/intelligence/ask")' in source
    assert '("enterprise_architect", "/intelligence/twin-map")' in source
    baseline = (REPO_ROOT / "tests" / "smoke" / "a11y_baseline.json").read_text(encoding="utf-8")
    assert "/intelligence/" not in baseline


# --- a model that has gone out of date; what a worked-out row is called ------------


def test_worked_out_connections_are_asked_for_together_with_out_of_date_ones():
    """Wherever the pages ask for worked-out connections they ask for the ones that
    have gone out of date too; without that the answer leaves them out and only its
    summary says so."""
    core = _code(_scripts()["core.js"])
    request = re.search(r"Platform\.fetch\.get\(IMPACT_URL \+ elementId, (\{.*?\})", core, re.S)
    assert request, "the impact request was not found"
    assert re.search(r"include_derived:\s*options\.includeDerived", request.group(1))
    assert re.search(r"include_stale:\s*options\.includeDerived", request.group(1))
    # Both pages go through that one request.
    for name in ("ask.js", "twin_map.js"):
        assert "Intelligence.fetchImpact(" in _code(_scripts()[name]), name


def test_an_out_of_date_answer_gets_a_notice_that_is_text_and_a_clock_with_the_action():
    states = _templates()["_impact_states.html"]
    notice = re.search(r"<div x-show=\"stale\"[^>]*data-stale-notice.*?</div>\s*(?=\{% endmacro|<div|$)", states, re.S)
    assert notice, "the out-of-date notice is missing"
    assert 'role="status"' in notice.group(0)
    assert 'data-lucide="clock"' in notice.group(0)
    assert 'x-text="staleNotice"' in notice.group(0)
    # The same action as the not-worked-out state, from one definition.
    assert len(re.findall(r"\{\{ recompute_action\(\) \}\}", states)) == 2
    assert len(re.findall(r"Work them out now", states)) == 1
    for name in ("ask.js", "twin_map.js"):
        source = _code(_scripts()[name])
        assert "answerState(" in source and "this.stale = answer.stale" in source, name


def test_the_row_kind_in_the_drawer_title_is_true_of_the_row_and_adds_no_badge_kind():
    drawer = _code(_scripts()["drawer.js"])
    assert re.search(r"drawer\.kind = row\.name === null \? 'missing' : \(row\.derived \? 'derived' : 'measured'\)", drawer)
    title = _templates()["_provenance_drawer.html"]
    assert re.search(r"drawer\.kind === 'derived'.*?data-lucide=\"waypoints\".*?>Worked out</span>", title, re.S)
    # The title words for a worked-out row are plain markup; the provenance badge
    # macro is only ever called with the four kinds it already has.
    kinds = set()
    for name, source in _templates().items():
        kinds.update(re.findall(r"provenance_badge\('([a-z]+)'\)", source))
    assert kinds <= {"measured", "missing", "unavailable"}, kinds
    assert "provenance_badge('derived')" not in title


def test_the_map_badge_and_table_say_when_a_worked_out_connection_may_be_out_of_date():
    graph = _code(_scripts()["graph.js"])
    assert "WORKED_OUT_STALE" in graph and "GLYPH_CLOCK" in graph
    core = _code(_scripts()["core.js"])
    assert "var WORKED_OUT_STALE = 'Worked out, may be out of date';" in core
    table = _templates()["_map_table.html"]
    assert "stale_clock('row.stale')" in table and 'x-text="row.kindLabel"' in table


def test_the_status_region_reads_results_for_a_search_and_connections_for_a_choice():
    core = _code(_scripts()["core.js"])
    assert "' result' : ' results'" in core
    assert "' connection' : ' connections'" in core
    assert "resultsText(this.options.length, typed)" in _code(_scripts()["picker.js"])
    for name in ("ask.js", "twin_map.js"):
        source = _code(_scripts()[name])
        assert "connectionsText(" in source, name
        assert "results for" not in source, name


def test_the_side_panel_control_is_named_for_the_panel():
    twin = _templates()["twin_map.html"]
    assert re.search(r'id="twin-rail-toggle".*?Selected element\s*</button>', twin, re.S)
    assert 'aria-label="Selected element"' in twin
    assert ">Details<" not in twin and "Details</button>" not in twin


# --- nav-cache invalidation -------------------------------------


def test_nav_counts_cache_invalidated_on_element_write(db_session, make_org):
    """After priming the cache for an empty organisation, adding an
    ArchiMateElement through the ORM and flushing makes compute_nav_counts
    return the new count immediately (after_flush listener evicted the
    stale entry)."""
    from app._bootstrap.context_processors import compute_nav_counts, _nav_counts_cache
    from app.models.archimate_core import ArchiMateElement

    org = make_org()

    first = compute_nav_counts(org.id)
    assert first["elements"] == 0
    assert org.id in _nav_counts_cache

    db_session.add(ArchiMateElement(
        name="Post-Flush Element",
        type="ApplicationComponent",
        organization_id=org.id,
    ))
    db_session.flush()

    second = compute_nav_counts(org.id)
    assert second["elements"] == 1


def test_nav_counts_cache_expires_empty_result_on_other_worker(
    db_session, make_org, monkeypatch
):
    """After priming the cache for an empty organisation, inserting an element
    through raw SQL (no after_flush in this process) and advancing time past the
    5-second empty-result TTL makes compute_nav_counts recompute from the DB.

    Also proves that a non-empty result is still served from the cache for
    nearly 300 seconds and only recomputes after the TTL expires."""
    import time

    from sqlalchemy import text

    from app._bootstrap.context_processors import compute_nav_counts, _nav_counts_cache

    # compute_nav_counts memoises on the request environ when a request context
    # is active, and only uses the process cache outside one. This test is about
    # the process cache, so take that path even if an earlier test in the same
    # process left a request context pushed -- otherwise the second call returns
    # the first call's memo and the assertion below reads 0.
    monkeypatch.setattr("flask.has_request_context", lambda: False)

    org = make_org()

    # Prime cache with empty result — stored with 5-second TTL.
    first = compute_nav_counts(org.id)
    assert first["elements"] == 0
    assert org.id in _nav_counts_cache

    # Insert an element via raw SQL: no ORM session.new tracking, so the
    # after_flush listener does not fire — simulating another worker.
    db_session.execute(
        text(
            "INSERT INTO archimate_elements (name, organization_id) "
            "VALUES (:name, :org_id)"
        ),
        {"name": "Other-worker Element", "org_id": org.id},
    )
    db_session.flush()

    # Advance time past the 5-second empty-result TTL.
    original_time = time.time
    monkeypatch.setattr(time, "time", lambda: original_time() + 6)

    # Cache should have expired — recomputes from DB and finds 1 element.
    second = compute_nav_counts(org.id)
    assert second["elements"] == 1

    # Now prime the cache with a non-empty result (1 element).
    third = compute_nav_counts(org.id)
    assert third["elements"] == 1
    assert org.id in _nav_counts_cache

    # Insert a second element via raw SQL — listener does not fire.
    db_session.execute(
        text(
            "INSERT INTO archimate_elements (name, organization_id) "
            "VALUES (:name, :org_id)"
        ),
        {"name": "Second raw element", "org_id": org.id},
    )
    db_session.flush()

    # Advance time by 66 seconds from the priming timestamp (still well
    # within 300 s TTL) — cache must still serve the old count of 1,
    # proving the non-empty entry was NOT recomputed.
    monkeypatch.setattr(time, "time", lambda: original_time() + 66)
    fourth = compute_nav_counts(org.id)
    assert fourth["elements"] == 1, "expected stale cached value at +66 s"
    assert org.id in _nav_counts_cache

    # Advance time past the 300-second TTL — cache must now recompute and
    # find both elements.
    monkeypatch.setattr(time, "time", lambda: original_time() + 307)
    fifth = compute_nav_counts(org.id)
    assert fifth["elements"] == 2


def test_nav_counts_cache_evicts_two_orgs_in_one_flush(
    db_session, make_org, monkeypatch
):
    """A single flush that adds tracked records for two different organisations
    evicts both organisations' cache entries."""
    from app._bootstrap.context_processors import compute_nav_counts, _nav_counts_cache
    from app.models.archimate_core import ArchiMateElement
    from app.models.business_capabilities import BusinessCapability

    org_a = make_org()
    org_b = make_org()

    # Prime both caches.
    first_a = compute_nav_counts(org_a.id)
    first_b = compute_nav_counts(org_b.id)
    assert first_a["elements"] == 0
    assert first_b["capabilities"] == 0
    assert org_a.id in _nav_counts_cache
    assert org_b.id in _nav_counts_cache

    # One flush: element for org A, capability for org B.
    db_session.add(ArchiMateElement(
        name="OrgA Element",
        type="ApplicationComponent",
        organization_id=org_a.id,
    ))
    db_session.add(BusinessCapability(
        name="OrgB Capability",
        organization_id=org_b.id,
    ))
    db_session.flush()

    # Both caches must be evicted — recompute returns the new values.
    after_a = compute_nav_counts(org_a.id)
    after_b = compute_nav_counts(org_b.id)
    assert after_a["elements"] == 1
    assert after_b["capabilities"] == 1


def test_ask_page_updates_after_element_write(
    app, db_session, make_org, client, login_as
):
    """A signed-in user on an empty organisation sees the empty-workspace gate.
    After adding one element through the ORM and flushing, the next GET shows
    the populated page body."""
    from app.models.archimate_core import ArchiMateElement

    org = make_org("ui-flush-test")
    user = _user(db_session, org.id)
    login_as(client, user)

    # First read: empty workspace gate.
    html = client.get("/intelligence/ask").get_data(as_text=True)
    main = _main_html(html)
    text = _visible_text(main)
    assert "Nothing is modelled yet" in text

    # Write one element through the ORM and flush.
    db_session.add(ArchiMateElement(
        name="Route-Test Element",
        type="ApplicationComponent",
        organization_id=org.id,
    ))
    db_session.flush()

    # Second read: populated page with combobox and no gate text.
    html = client.get("/intelligence/ask").get_data(as_text=True)
    assert 'role="combobox"' in html
    assert "Nothing is modelled yet" not in html
