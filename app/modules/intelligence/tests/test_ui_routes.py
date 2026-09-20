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


def test_the_two_pages_are_the_only_routes_this_blueprint_serves(app):
    rules = {
        rule.rule: sorted(rule.methods - {"HEAD", "OPTIONS"})
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("intelligence_ui.")
    }
    assert rules == {
        "/intelligence/ask": ["GET"],
        "/intelligence/twin-map": ["GET"],
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
@pytest.mark.parametrize("counts", [
    {"applications": 1, "elements": 0, "capabilities": 0, "vendors": 0},
    {"applications": 0, "elements": 5, "capabilities": 0, "vendors": 0},
    {"applications": 0, "elements": 0, "capabilities": 2, "vendors": 0},
    {"applications": 0, "elements": 0, "capabilities": 0, "vendors": 3},
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
    urls = set(re.findall(r"'(/[a-z0-9_/.-]*)'", _scripts()["core.js"]))
    assert urls == {
        "/archimate/api/elements/search",
        "/api/v1/intelligence/impact/",
        "/api/v1/intelligence/derivation/recompute",
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
    # the combobox, the question card that opens the picker, and the button
    # that collapses the Twin map's side panel.
    owners = {}
    for name, source in _templates().items():
        for tag in re.findall(r"<(?:button|input)[^>]*aria-expanded[^>]*>", source, re.S):
            key = ("full-detail" if "data-full-detail-toggle" in tag else
                   "combobox" if 'role="combobox"' in tag else
                   "question" if "ask-question-" in tag else
                   "rail" if "twin-rail-toggle" in tag else "OTHER")
            owners.setdefault(key, []).append(name)
    assert set(owners) == {"full-detail", "combobox", "question", "rail"}, owners


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
    for name, source in _everything().items():
        for pattern in signatures:
            assert not re.search(pattern, source, re.I), (name, pattern)
        assert not re.search(r"confidence\s*(>=|<=|>|<)", source), name
        assert not re.search(r"toFixed|Math\.round|\*\s*100\b", source), name


def test_the_drawer_renders_the_supplied_sentence_in_one_paragraph_and_nothing_else():
    source = _templates()["_provenance_drawer.html"]
    assert len(re.findall(r"data-plain-terms", source)) == 1
    assert re.search(r'<p [^>]*data-plain-terms x-text="drawer\.plainTerms"></p>', source)
    assert 'x-show="drawer.plainTerms"' in source


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
    import datetime

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
        computed_at=datetime.datetime.utcnow(), stale=False,
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
