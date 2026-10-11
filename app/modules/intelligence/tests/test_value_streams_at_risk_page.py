"""The Value streams at risk page: who can open it, what it renders, and that
its rows are the intelligence API's own rows.

The page computes nothing on the server. Its rows are built in the browser
from ``GET /api/v1/intelligence/value-streams-at-risk``, so the agreement test
below seeds one tenant, asks the API through the real client, and runs the
page's own row builder (``value_streams_at_risk.js``) over that answer in
Node, the same engine the shipped script is checked with.

Fixtures (app, db_session, make_org, client, login_as) come from this
directory's conftest, which re-exports the shared ones.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

PAGE = "/intelligence/value-streams-at-risk"
API = "/api/v1/intelligence/value-streams-at-risk"

REPO_ROOT = Path(__file__).resolve().parents[4]
TEMPLATE = (
    REPO_ROOT / "app" / "modules" / "intelligence" / "templates" / "intelligence"
    / "value_streams_at_risk.html"
)
TABLE_TEMPLATE = (
    REPO_ROOT / "app" / "modules" / "intelligence" / "templates" / "intelligence"
    / "_value_streams_at_risk_table.html"
)
SCRIPT = REPO_ROOT / "app" / "static" / "js" / "intelligence" / "value_streams_at_risk.js"


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _user(db_session, org_id, role="business_architect"):
    from app.models.user import User

    user = User(
        email=f"vsrp-{uuid.uuid4().hex[:10]}@example.com",
        first_name="Vsr",
        last_name="Page",
        organization_id=org_id,
        confirmed=True,
        enterprise_role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _seed(db_session, org_id, names=None):
    """Two value streams: one depends on a capability below maturity 3 on two
    stages and on one with no maturity recorded; the other only on a mature
    capability. A third has no capability linked at all."""
    from app.models.unified_capability import (
        CapabilityValueStreamMapping,
        UnifiedCapability,
        ValueStream,
        ValueStreamStage,
    )

    s = _suffix()
    names = {
        "onboard": "Onboard customer",
        "settle": "Settle claim",
        "retire": "Retire product",
        "weak": "Identity checks",
        "unknown": "Document capture",
        "strong": "Payments",
        **(names or {}),
    }

    def vs(name):
        row = ValueStream(name=name, code=f"VSRP-{name[:4].upper()}-{s}", organization_id=org_id)
        db_session.add(row)
        db_session.flush()
        return row

    def stage(stream, name, order):
        row = ValueStreamStage(
            name=name, value_stream_id=stream.id, stage_order=order, organization_id=org_id
        )
        db_session.add(row)
        db_session.flush()
        return row

    def cap(name, current, target):
        row = UnifiedCapability(
            name=name,
            code=f"VSRP-{name[:5].upper()}-{s}",
            organization_id=org_id,
            scope="tenant",
            level=1,
            current_maturity_level=current,
            target_maturity_level=target,
        )
        db_session.add(row)
        db_session.flush()
        return row

    def link(capability, stream, stage_row):
        db_session.add(
            CapabilityValueStreamMapping(
                capability_id=capability.id,
                value_stream_id=stream.id,
                value_stream_stage_id=stage_row.id,
                organization_id=org_id,
                support_type="primary",
                support_level=3,
                impact_level="high",
            )
        )
        db_session.flush()

    onboard = vs(names["onboard"])
    settle = vs(names["settle"])
    vs(names["retire"])
    apply_stage = stage(onboard, "Apply", 1)
    verify_stage = stage(onboard, "Verify", 2)
    pay_stage = stage(settle, "Pay", 1)
    weak = cap(names["weak"], 1, 4)
    unknown = cap(names["unknown"], None, None)
    strong = cap(names["strong"], 4, 4)
    link(weak, onboard, apply_stage)
    link(weak, onboard, verify_stage)
    link(unknown, onboard, apply_stage)
    link(strong, settle, pay_stage)
    db_session.commit()
    return {
        "names": names,
        "onboard": onboard.id,
        "settle": settle.id,
        "weak": weak.id,
    }


# --- the route ---------------------------------------------------------------


def test_the_page_renders_its_own_template_for_a_signed_in_business_architect(
    app, db_session, make_org, client, login_as
):
    from flask import template_rendered

    org = make_org("vsrp-render")
    user = _user(db_session, org.id)
    seen = []

    def record(sender, template, context, **extra):
        seen.append(template.name)

    template_rendered.connect(record, app)
    try:
        login_as(client, user)
        response = client.get(PAGE)
    finally:
        template_rendered.disconnect(record, app)

    assert response.status_code == 200
    assert "intelligence/value_streams_at_risk.html" in seen
    html = response.get_data(as_text=True)
    assert 'aria-label="Breadcrumb"' in html
    assert '<h1 class="text-2xl font-bold text-foreground">Value streams at risk</h1>' in html
    assert 'x-data="valueStreamsAtRisk()"' in html
    assert "js/intelligence/value_streams_at_risk.js" in html


def test_the_page_is_not_served_to_an_anonymous_visitor(app, client):
    response = client.get(PAGE)
    assert response.status_code in (301, 302, 401)
    if response.status_code in (301, 302):
        assert "/account/login" in response.headers["Location"]


@pytest.mark.parametrize(
    "query, expected",
    [("", 3), ("?threshold=1", 1), ("?threshold=5", 5), ("?threshold=4", 4),
     ("?threshold=0", 3), ("?threshold=6", 3), ("?threshold=abc", 3)],
)
def test_the_threshold_in_the_address_bar_is_the_one_the_page_opens_on(
    app, db_session, make_org, client, login_as, query, expected
):
    org = make_org("vsrp-threshold")
    user = _user(db_session, org.id)
    login_as(client, user)
    html = client.get(PAGE + query).get_data(as_text=True)

    assert f'data-threshold="{expected}"' in html
    checked = re.findall(r'<input type="radio" name="threshold" value="(\d)"[^>]*?(?<![:\w-])checked(?![=\w])', html, re.S)
    assert checked == [str(expected)]
    offered = re.findall(r'<input type="radio" name="threshold" value="(\d)"', html)
    assert offered == ["1", "2", "3", "4", "5"]


@pytest.mark.parametrize(
    "query, expected_threshold",
    [
        ("", "3"),
        ("?threshold=0", "3"),
        ("?threshold=6", "3"),
        ("?threshold=abc", "3"),
    ],
)
def test_invalid_or_missing_threshold_is_canonicalised_to_three_in_the_address_bar(
    app, db_session, make_org, client, login_as, query, expected_threshold
):
    org = make_org("vsrp-canonical")
    user = _user(db_session, org.id)
    login_as(client, user)

    html = client.get(PAGE + query).get_data(as_text=True)

    match = re.search(r'x-data="valueStreamsAtRisk\(\)"[^>]*data-threshold="(\d)"', html)
    assert match and match.group(1) == "3"
    result = _run_init("http://example.test" + PAGE + query)
    assert result["threshold"] == 3
    assert result["loaded"] is True
    assert result["replaceStateCalls"] == [PAGE + "?threshold=" + expected_threshold]


def test_the_business_architect_sidebar_carries_the_page_and_no_other_persona_does():
    from app.utils.role_access import SIDEBAR_ZONES

    holders = sorted(
        role
        for role, zones in SIDEBAR_ZONES.items()
        for zone in zones
        for link in zone["links"]
        if link["endpoint"] == "intelligence_ui.value_streams_at_risk"
    )
    assert holders == ["business_architect"]


def test_the_rendered_sidebar_links_to_the_page_for_a_business_architect(
    app, db_session, make_org, client, login_as
):
    org = make_org("vsrp-nav")
    user = _user(db_session, org.id)
    login_as(client, user)
    html = client.get(PAGE).get_data(as_text=True)
    assert f'href="{PAGE}"' in html
    assert "Value Streams at Risk" in html


def test_the_page_reuses_the_shared_table_component_via_its_macro():
    page = TEMPLATE.read_text(encoding="utf-8")
    table_macro = TABLE_TEMPLATE.read_text(encoding="utf-8")

    assert "from 'intelligence/_value_streams_at_risk_table.html' import value_streams_at_risk_table" in page
    assert "{{ value_streams_at_risk_table() }}" in page
    assert "<table class=\"w-full text-sm\" data-vsr-table>" not in page
    assert "from 'components/table.html' import table" in table_macro
    assert "{% call table() %}" in table_macro


# --- the page's rows are the API's rows ---------------------------------------


_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
process.stdout.write(JSON.stringify(sandbox.window.ValueStreamsAtRisk.buildRows(payload)));
"""


_INIT_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const calls = [];
const sandbox = {
  URL,
  window: {
    location: { href: payload.href },
    history: {
      replaceState: function (_state, _title, url) {
        calls.push(url);
      }
    }
  }
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox);
const component = sandbox.window.valueStreamsAtRisk();
component.$el = { getAttribute: function (name) { return name === 'data-threshold' ? payload.dataThreshold : null; } };
component.load = function () { this.loaded = true; };
component.init();
process.stdout.write(JSON.stringify({
  threshold: component.threshold,
  loaded: component.loaded === true,
  replaceStateCalls: calls
}));
"""


def _page_rows(payload):
    node = shutil.which("node")
    if not node:
        pytest.fail("Node.js is required to run the page's own row builder")
    proc = subprocess.run(
        [node, "-e", _HARNESS, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _run_init(href: str, data_threshold: str = "3"):
    node = shutil.which("node")
    if not node:
        pytest.fail("Node.js is required to run the page's own init path")
    proc = subprocess.run(
        [node, "-e", _INIT_HARNESS, str(SCRIPT)],
        input=json.dumps({"href": href, "dataThreshold": data_threshold}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("threshold", [2, 3, 5])
def test_the_pages_rows_equal_the_apis_rows_for_the_same_seed(
    app, db_session, make_org, client, login_as, threshold
):
    org = make_org("vsrp-agree")
    user = _user(db_session, org.id)
    ids = _seed(db_session, org.id)

    login_as(client, user)
    response = client.get(f"{API}?threshold={threshold}")
    assert response.status_code == 200
    answer = response.get_json()["data"]
    rows = _page_rows(answer)

    # One page row per API row, in the API's order, with the API's counts.
    assert [r["id"] for r in rows] == [r["value_stream"]["id"] for r in answer["rows"]]
    assert [r["name"] for r in rows] == [r["value_stream"]["name"] for r in answer["rows"]]
    assert [r["atRiskCount"] for r in rows] == [
        r["at_risk_capability_count"] for r in answer["rows"]
    ]
    for page_row, api_row in zip(rows, answer["rows"]):
        api_caps = {c["id"]: c for c in api_row["capabilities"]}
        assert [c["id"] for c in page_row["capabilities"]] == list(dict.fromkeys(api_caps))
        assert page_row["capabilityCount"] == len(api_caps)
        assert page_row["atRisk"] == (api_row["at_risk_capability_count"] > 0)
        for cap in page_row["capabilities"]:
            api_cap = api_caps[cap["id"]]
            assert cap["atRisk"] == (api_cap["at_risk"] is True)
            assert cap["current"] == (
                "—" if api_cap["current_maturity"] is None else str(api_cap["current_maturity"])
            )
        assert page_row["unassessedCount"] == sum(
            1 for c in api_caps.values() if c["current_maturity"] is None
        )

    # The page's summary counts are the answer's own: the same seed read at
    # a different threshold moves the at-risk count, and the page follows.
    at_risk_rows = [r["id"] for r in rows if r["atRisk"]]
    if threshold == 2:
        assert at_risk_rows == [ids["onboard"]]
    elif threshold == 3:
        assert at_risk_rows == [ids["onboard"]]
    else:
        assert at_risk_rows == [ids["onboard"], ids["settle"]]
    onboard = next(r for r in rows if r["id"] == ids["onboard"])
    weak = next(c for c in onboard["capabilities"] if c["id"] == ids["weak"])
    assert weak["stages"] == ["Apply", "Verify"]


def test_the_page_only_builds_rows_for_the_signed_in_organisations_value_streams(
    app, db_session, make_org, client, login_as
):
    ours = make_org("vsrp-page-ours")
    other = make_org("vsrp-page-other")
    user = _user(db_session, ours.id)
    our_ids = _seed(
        db_session,
        ours.id,
        names={
            "onboard": "Order to cash Alpha",
            "settle": "Claims handling Alpha",
            "retire": "Retire service Alpha",
        },
    )
    other_ids = _seed(
        db_session,
        other.id,
        names={
            "onboard": "Order to cash Beta",
            "settle": "Claims handling Beta",
            "retire": "Retire service Beta",
        },
    )

    login_as(client, user)
    page = client.get(PAGE)
    assert page.status_code == 200
    answer = client.get(f"{API}?threshold=3")
    assert answer.status_code == 200
    rows = _page_rows(answer.get_json()["data"])
    names = [row["name"] for row in rows]

    assert names == [
        our_ids["names"]["onboard"],
        our_ids["names"]["settle"],
        our_ids["names"]["retire"],
    ]
    assert other_ids["names"]["onboard"] not in names
    assert other_ids["names"]["settle"] not in names
    assert other_ids["names"]["retire"] not in names
    html = page.get_data(as_text=True)
    assert other_ids["names"]["onboard"] not in html
    assert other_ids["names"]["settle"] not in html
    assert other_ids["names"]["retire"] not in html


def test_the_page_asks_the_existing_api_and_nothing_else():
    source = SCRIPT.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    assert set(re.findall(r"'(/[a-z0-9_/.-]*)'", code)) == {API}
    assert "Platform.fetch.get(API_URL" in code
    assert not re.search(r"(?<![.\w])fetch\(", code)
    assert "global.history.replaceState" in code
    template = TEMPLATE.read_text(encoding="utf-8")
    assert "/api/" not in template
