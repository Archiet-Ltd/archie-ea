"""Impact Analysis: the element picker finds elements by name, whatever layer or type spelling.

The page used to search ``/architecture/api/layer/<layer>/elements`` with a layer chosen first. That
listing keeps only ArchiMate rows whose ``type`` is in a CamelCase list (``ApplicationComponent``), so an
element stored as ``application_component`` was never returned, although Ctrl+K
(``/api/sidebar/search``) returned it.

The picker now calls the canonical element search, ``GET /archimate/api/elements/search``, with the
layer as an optional narrowing. These tests read the request the page really sends (its endpoint and
limit are declared in the rendered page), so a page that goes back to a layer-scoped listing fails them.

What the picker matches: an element's name, the way the global search does. The old listing also matched
words in an element's description; the picker no longer does, on purpose.

How many it lists: at most the page's limit, in name order. Any text that matches that many elements or
fewer lists every match, so the picker then contains every ArchiMate element the global search returns
for the same text on the same tenant. A text that matches more lists the first ones by name and the page
says so (the browser tests cover the note). The global search reads ``%`` and ``_`` as wildcards and the
picker matches them literally, so the comparison is made on texts without them.
"""

from __future__ import annotations

import re
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("db_session")

PAGE = "/strategic/impact-analysis"
GLOBAL_SEARCH = "/api/sidebar/search"


def _make_user(db_session, org, role="solution_architect"):
    from app.models.user import User

    user = User(
        email=f"impact-picker-{uuid.uuid4().hex[:8]}@example.com",
        first_name="Impact",
        last_name="Picker",
        organization_id=org.id,
        confirmed=True,
        enterprise_role=role,
    )
    user.password = "Sup3rSecret!23"
    db_session.add(user)
    db_session.flush()
    return user


def _element(db_session, org, name, type_, layer):
    from app.models.archimate_core import ArchiMateElement

    row = ArchiMateElement(name=name, type=type_, layer=layer, organization_id=org.id)
    db_session.add(row)
    db_session.flush()
    return row


def _picker_request(client, login_as, user):
    """The endpoint and limit the rendered page declares for its picker."""
    login_as(client, user)
    response = client.get(PAGE)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    endpoint = re.search(r"const IMPACT_PICKER_ENDPOINT = '([^']+)'", html)
    limit = re.search(r"const IMPACT_PICKER_LIMIT = (\d+)", html)
    assert endpoint and limit, "the page does not declare the endpoint and limit its picker requests"
    return endpoint.group(1), int(limit.group(1)), html


def _picker_rows(client, login_as, user, endpoint, limit, text, layer=None):
    params = {"q": text, "limit": limit}
    if layer:
        params["layer"] = layer
    login_as(client, user)
    response = client.get(endpoint, query_string=params)
    assert response.status_code == 200
    return response.get_json()["data"]


def _global_element_ids(client, login_as, user, text):
    login_as(client, user)
    response = client.get(GLOBAL_SEARCH, query_string={"q": text})
    assert response.status_code == 200
    return {r["id"] for r in response.get_json()["results"] if r["type"] == "archimate_element"}


def test_picker_uses_the_canonical_element_search_not_a_layer_listing(
    app, db_session, make_org, client, login_as
):
    org = make_org("impact-picker-endpoint")
    user = _make_user(db_session, org)

    endpoint, limit, html = _picker_request(client, login_as, user)

    assert endpoint == "/archimate/api/elements/search"
    assert limit == 50, "each keystroke asks for at most this many rows"
    script = html[html.index("function impactAnalysis()"):html.index("window.impactAnalysis = impactAnalysis;")]
    assert "/architecture/api/layer/" not in script, "the picker must not read the layer-scoped listing"
    # "All layers" is the default, and it sends no layer at all.
    assert '<option value="">All layers</option>' in html
    assert re.search(r"selectedType: '',", script), "the layer control must default to All layers"


def test_all_layers_request_returns_every_layer_without_a_layer_or_type_parameter(
    app, db_session, make_org, client, login_as
):
    org = make_org("impact-picker-all-layers")
    user = _make_user(db_session, org)
    tag = uuid.uuid4().hex[:8]
    app_el = _element(db_session, org, f"Probe {tag} billing", "ApplicationComponent", "application")
    biz_el = _element(db_session, org, f"Probe {tag} onboarding", "business_process", "business")
    tech_el = _element(db_session, org, f"Probe {tag} gateway", "Node", "technology")

    endpoint, limit, _ = _picker_request(client, login_as, user)
    rows = _picker_rows(client, login_as, user, endpoint, limit, f"Probe {tag}")

    by_id = {r["id"]: r for r in rows}
    assert {app_el.id, biz_el.id, tech_el.id} <= set(by_id)
    assert {by_id[app_el.id]["layer"], by_id[biz_el.id]["layer"], by_id[tech_el.id]["layer"]} == {
        "application", "business", "technology",
    }
    # The page reads the key "type", and it is the stored value, whatever its spelling.
    assert by_id[biz_el.id]["type"] == "business_process"
    assert by_id[app_el.id]["type"] == "ApplicationComponent"


def test_a_chosen_layer_narrows_the_results_and_never_gates_the_query(
    app, db_session, make_org, client, login_as
):
    org = make_org("impact-picker-narrowing")
    user = _make_user(db_session, org)
    tag = uuid.uuid4().hex[:8]
    app_el = _element(db_session, org, f"Narrow {tag} billing", "application_component", "application")
    biz_el = _element(db_session, org, f"Narrow {tag} onboarding", "business_process", "business")

    endpoint, limit, _ = _picker_request(client, login_as, user)
    everything = {r["id"] for r in _picker_rows(client, login_as, user, endpoint, limit, f"Narrow {tag}")}
    business_only = {
        r["id"] for r in _picker_rows(client, login_as, user, endpoint, limit, f"Narrow {tag}", layer="business")
    }

    assert everything == {app_el.id, biz_el.id}
    assert business_only == {biz_el.id}


def test_elements_stored_under_either_type_spelling_are_both_found(
    app, db_session, make_org, client, login_as
):
    """application_component (as the smoke fixtures store it) and ApplicationComponent."""
    org = make_org("impact-picker-spellings")
    user = _make_user(db_session, org)
    tag = uuid.uuid4().hex[:8]
    snake = _element(db_session, org, f"Spelling {tag} snake", "application_component", "application")
    camel = _element(db_session, org, f"Spelling {tag} camel", "ApplicationComponent", "application")

    endpoint, limit, _ = _picker_request(client, login_as, user)
    for layer in (None, "application"):
        rows = _picker_rows(client, login_as, user, endpoint, limit, f"Spelling {tag}", layer=layer)
        assert {r["id"] for r in rows} == {snake.id, camel.id}, (
            "both spellings must be listed (layer=%r)" % layer
        )


def test_picker_lists_every_archimate_element_the_global_search_returns_when_the_matches_fit_the_limit(
    app, db_session, make_org, client, login_as
):
    """For a text of two or more characters that matches no more than the page's limit, the picker rows
    across layers contain the global search's ArchiMate element group on the same tenant.

    The global search returns at most five of these, unordered; the elements below give it more than
    five matches in mixed layers and spellings so the assertion is not satisfied by a short list.
    """
    org = make_org("impact-picker-vs-global")
    other_org = make_org("impact-picker-vs-global-other")
    user = _make_user(db_session, org)
    tag = uuid.uuid4().hex[:6]
    specs = [
        ("application_component", "application", "billing"),
        ("ApplicationComponent", "application", "ledger"),
        ("business_process", "business", "onboarding"),
        ("BusinessCapability", "business", "collections"),
        ("Node", "technology", "gateway"),
        ("technology_service", "technology", "queue"),
        ("Goal", "motivation", "growth"),
        ("Capability", "strategy", "expansion"),
    ]
    created = [
        _element(db_session, org, f"Global {tag} {suffix}", type_, layer) for type_, layer, suffix in specs
    ]
    foreign = _element(db_session, other_org, f"Global {tag} foreign", "application_component", "application")

    endpoint, limit, _ = _picker_request(client, login_as, user)

    for text in (f"Global {tag}", tag, "Global", "gl", f"Global {tag} b", "ledger"):
        assert len(text) >= 2
        global_ids = _global_element_ids(client, login_as, user, text)
        picker_ids = {r["id"] for r in _picker_rows(client, login_as, user, endpoint, limit, text)}
        assert global_ids, "the comparison would be vacuous: the global search returned no element for %r" % text
        missing = global_ids - picker_ids
        assert not missing, "picker misses %s that the global search returns for %r" % (sorted(missing), text)
        assert foreign.id not in picker_ids and foreign.id not in global_ids, "another tenant's element leaked"

    # More matches than the global search's five, so the comparison above is not trivially the whole set.
    assert len(created) > 5
    assert {c.id for c in created} <= {
        r["id"] for r in _picker_rows(client, login_as, user, endpoint, limit, f"Global {tag}")
    }


def test_a_text_matching_exactly_the_limit_lists_every_match_including_the_global_search_hits(
    app, db_session, make_org, client, login_as
):
    org = make_org("impact-picker-at-limit")
    user = _make_user(db_session, org)
    tag = uuid.uuid4().hex[:6]
    endpoint, limit, _ = _picker_request(client, login_as, user)
    layers = ("application", "business", "technology", "strategy", "motivation")
    created = [
        _element(db_session, org, f"Fit {tag} {n:03d}", "application_component" if n % 2 else "ApplicationComponent",
                 layers[n % len(layers)])
        for n in range(limit)
    ]

    rows = _picker_rows(client, login_as, user, endpoint, limit, f"Fit {tag}")
    assert {r["id"] for r in rows} == {c.id for c in created}, "every one of the %d matches must be listed" % limit

    global_ids = _global_element_ids(client, login_as, user, f"Fit {tag}")
    assert global_ids, "the comparison would be vacuous: the global search returned no element"
    assert global_ids <= {r["id"] for r in rows}


def test_a_text_matching_more_than_the_limit_lists_the_first_ones_by_name(
    app, db_session, make_org, client, login_as
):
    """Above the limit the list is capped, exactly, and is the head of the name order."""
    org = make_org("impact-picker-over-limit")
    user = _make_user(db_session, org)
    tag = uuid.uuid4().hex[:6]
    endpoint, limit, _ = _picker_request(client, login_as, user)
    # Zero-padded numbers give one name order under any database collation.
    names = [f"Over {tag} {n:03d}" for n in range(limit + 11)]
    for name in names:
        _element(db_session, org, name, "application_component", "application")

    rows = _picker_rows(client, login_as, user, endpoint, limit, f"Over {tag}")
    assert len(rows) == limit
    assert [r["name"] for r in rows] == sorted(names)[:limit]

    # A narrower text goes back under the limit and lists all of its matches.
    narrow = _picker_rows(client, login_as, user, endpoint, limit, f"Over {tag} 05")
    assert [r["name"] for r in narrow] == [n for n in sorted(names) if n.startswith(f"Over {tag} 05")]


def test_percent_underscore_and_backslash_can_be_matched_literally(
    app, db_session, make_org, client, login_as
):
    """The page escapes backslash, percent and underscore before it asks; the database's default LIKE
    escape is the backslash, so the escaped text finds exactly the element that spells it and no
    look-alike. The unescaped text is a wildcard pattern, which is why the page escapes it."""
    bs = chr(92)  # one backslash
    org = make_org("impact-picker-literal")
    user = _make_user(db_session, org)
    tag = uuid.uuid4().hex[:6]
    endpoint, limit, _ = _picker_request(client, login_as, user)
    underscore = _element(db_session, org, f"W{tag}_Billing", "application_component", "application")
    look_alike = _element(db_session, org, f"W{tag}XBilling", "application_component", "application")
    percent = _element(db_session, org, f"Rate {tag} 50% off", "application_component", "application")
    other_percent = _element(db_session, org, f"Rate {tag} 500 off", "application_component", "application")
    backslash = _element(db_session, org, f"Path {tag} A{bs}B", "application_component", "application")

    def ids(text):
        return {r["id"] for r in _picker_rows(client, login_as, user, endpoint, limit, text)}

    assert ids(f"W{tag}{bs}_Billing") == {underscore.id}
    assert ids(f"W{tag}_Billing") == {underscore.id, look_alike.id}, "unescaped, _ is a wildcard"
    assert ids(f"Rate {tag} 50{bs}%") == {percent.id}
    assert ids(f"Rate {tag} 50%") == {percent.id, other_percent.id}, "unescaped, % is a wildcard"
    assert ids(f"Path {tag} A{bs}{bs}B") == {backslash.id}
    assert ids(f"{bs}_{bs}_") == set(), "two escaped underscores match no name with two underscores in a row"
