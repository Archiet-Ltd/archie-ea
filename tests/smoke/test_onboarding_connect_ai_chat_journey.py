"""Connect the AI chat you already use, and ask the same question there.

An architect asks what breaks if one of their systems fails and reads the
cited answer on screen. The checklist then offers "Ask this in your own AI
chat"; they open the connect page, copy the connection address, and the AI
chat -- standing in here for a real client -- calls the same endpoint with an
issued credential and receives the same dependents and citations the screen
showed. A second organisation's credential returns none of the first
organisation's names. "Skip, ask here instead" lands back on Ask with the
question filled.

Walked at 1440 and 390, light and dark; the screenshots are saved under the
walk folder. Fails on main (no connect panel, no checklist item, no Ask
prefill), passes on the branch.
"""

from __future__ import annotations

import os
import urllib.parse

import pytest
import requests
from playwright.sync_api import expect

from tests.smoke.conftest import PAGE_TIMEOUT, PASSWORD
from tests.smoke.test_mcp_consent import (
    _CallbackCapture,  # noqa: F401  (imported for parity with consent helpers)
    _authorize_url,
    _login,
    _pkce_pair,
    _register_test_client,
    local_callback_server,  # noqa: F401  (fixture)
)

pytestmark = [pytest.mark.smoke, pytest.mark.journey]

WALK_DIR = "/home/ubuntu/verify/seats/onb04/walk"


def _shoot(page, name):
    os.makedirs(WALK_DIR, exist_ok=True)
    page.screenshot(path=os.path.join(WALK_DIR, name + ".png"), full_page=True)


def _mint_token(page, live_server, local_callback_server, email=None):
    """Complete the consent flow for the current (or a freshly signed-in)
    session, returning a bearer access token. ``email`` signs in first; when
    None the already-signed-in session is used as it stands."""
    client_id = _register_test_client(local_callback_server, live_server)
    verifier, challenge = _pkce_pair()
    if email is not None:
        _login(page, live_server, email)
    page.goto(
        _authorize_url(live_server, client_id, local_callback_server, challenge, "mcp:read"),
        wait_until="domcontentloaded", timeout=PAGE_TIMEOUT,
    )
    page.click('button[name="decision"][value="allow"]')
    page.wait_for_url(lambda url: "code=" in url, timeout=PAGE_TIMEOUT)
    code = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)["code"][0]
    resp = requests.post(
        live_server + "/oauth/token",
        data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": local_callback_server, "client_id": client_id,
            "code_verifier": verifier, "resource": live_server + "/mcp",
        },
        timeout=10,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _ask_impact_over_mcp(live_server, access_token, element_id):
    """Call ask_impact on the MCP endpoint with an issued credential, exactly
    as a connected AI chat would, and return the answer envelope."""
    import json

    resp = requests.post(
        live_server + "/mcp",
        json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "ask_impact", "arguments": {"element_id": element_id}},
        },
        headers={"Authorization": "Bearer " + access_token},
        timeout=15,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    payload = json.loads(body["result"]["content"][0]["text"])
    return payload.get("result", payload), body


def _second_org_with_linked_data(live_server):
    """A genuinely separate organisation, its own admin, and its own linked
    pair of elements, so the isolation check runs against two real orgs."""
    from app import create_app, db

    from tests.smoke.fresh_org import create_fresh_org

    out = create_fresh_org("solution_architect", org_admin=True)
    app = create_app("testing")
    with app.app_context():
        from app.models.archimate_core import ArchiMateElement, ArchiMateRelationship

        org_id = out["org_id"]
        source = ArchiMateElement(
            name="Second org source %s" % out["suffix"], type="application_component",
            layer="application", organization_id=org_id,
        )
        target = ArchiMateElement(
            name="Second org target %s" % out["suffix"], type="application_component",
            layer="application", organization_id=org_id,
        )
        db.session.add_all([source, target])
        db.session.commit()
        db.session.add(ArchiMateRelationship(
            type="serving", source_id=source.id, target_id=target.id, organization_id=org_id,
        ))
        db.session.commit()
        out["source_id"] = source.id
        out["target_name"] = target.name
        db.session.remove()
    return out


def test_connect_ai_chat_answers_the_same_cited_question_scoped_to_the_org(
    page, live_server, seeded, local_callback_server  # noqa: F811
):
    base = live_server
    source_id = seeded["ids"]["impact_source_element"]
    target_id = seeded["ids"]["impact_target_element"]
    target_name = "Smoke impact target"

    # 1. The architect reads the cited answer on screen.
    _login(page, base, seeded["emails"]["solution_architect"])
    page.add_init_script("localStorage.setItem('archie_onboarding_ts', '1')")
    page.goto(base + "/intelligence/ask", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"askSurface()\"]');"
        " return !!(el && el._x_dataStack); }",
        timeout=PAGE_TIMEOUT,
    )
    page.locator("#ask-question-impact").click()
    box = page.locator("#ask-picker-input")
    expect(box).to_be_focused()
    box.press_sequentially("Smoke impact source", delay=15)
    page.locator("#ask-picker-listbox [role=option]", has_text="Smoke impact source").first.click()
    rows = page.locator("[data-ask-row]")
    expect(rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(rows.filter(has_text=target_name).first).to_be_visible()
    on_screen_names = [r.strip() for r in rows.all_inner_texts()]
    assert any(target_name in n for n in on_screen_names), on_screen_names

    # 2. The checklist offers connecting the AI chat; open the connect page.
    page.goto(base + "/account/manage/connected-assistants?element=%d" % source_id,
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    panel = page.locator('[data-testid="connect-ai-chat"]')
    expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)

    # 3. Copy the address: the copy button carries the exact address it copies,
    #    and it is this server's /mcp origin.
    copy_button = page.locator('[data-testid="copy-connection-address"]')
    copied = copy_button.get_attribute("data-copy-text")
    assert copied == base + "/mcp", copied
    shown = page.locator('[data-testid="connection-address"]').inner_text().strip()
    assert shown == base + "/mcp", shown
    # Every listed client reads as a real line, not a dash or a blank.
    client_lines = page.locator('[data-testid="supported-clients"] li')
    assert client_lines.count() >= 1
    for i in range(client_lines.count()):
        text = client_lines.nth(i).inner_text().strip()
        assert text and text not in ("—", "-", "–"), text

    # 4. The AI chat calls the endpoint with an issued credential (org A) and
    #    gets the same dependents and citations the screen showed.
    token_a = _mint_token(page, base, local_callback_server)
    env_a, body_a = _ask_impact_over_mcp(base, token_a, source_id)
    # The same dependent the screen named comes back over the connector: the
    # target element by id, and its name in the answer's element map.
    mcp_target_ids = [r["element_id"] for r in env_a["rows"]]
    assert target_id in mcp_target_ids, env_a
    assert target_name in str(body_a), body_a
    assert env_a["citations"], env_a  # the same answer carries its sources

    # 5. A second organisation's credential returns none of the first org's names.
    second = _second_org_with_linked_data(base)
    page.goto(base + "/account/logout", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    token_b = _mint_token(page, base, local_callback_server, second["emails"]["solution_architect"])
    env_b, body_b = _ask_impact_over_mcp(base, token_b, source_id)
    # org A's element is not org B's to see: no rows for it, and none of org
    # A's names anywhere in the reply.
    assert not env_b.get("rows"), env_b
    assert target_name not in str(body_b), body_b
    assert "Smoke impact source" not in str(body_b), body_b
    # ...and org B's own element still answers for org B, with its own target.
    env_b_own, body_b_own = _ask_impact_over_mcp(base, token_b, second["source_id"])
    assert env_b_own["rows"], env_b_own
    assert second["target_name"] in str(body_b_own), body_b_own

    # 6. "Skip, ask here instead" lands on Ask with the question filled.
    page.goto(base + "/account/logout", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    _login(page, base, seeded["emails"]["solution_architect"])
    page.goto(base + "/account/manage/connected-assistants?element=%d" % source_id,
              wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.locator('[data-testid="skip-ask-here"]').click()
    page.wait_for_url("**/intelligence/ask?*", timeout=PAGE_TIMEOUT)
    assert "question=impact" in page.url
    assert "element=%d" % source_id in page.url
    page.wait_for_function(
        "() => { const el = document.querySelector('[x-data=\"askSurface()\"]');"
        " return !!(el && el._x_dataStack); }",
        timeout=PAGE_TIMEOUT,
    )
    back_rows = page.locator("[data-ask-row]")
    expect(back_rows.first).to_be_visible(timeout=PAGE_TIMEOUT)
    expect(back_rows.filter(has_text=target_name).first).to_be_visible()


def test_walk_the_connect_page_at_both_widths_light_and_dark(
    page, live_server, seeded  # noqa: F811
):
    """The walk a reviewer repeats: the connect page is legible at 1440 and 390,
    in light and dark, with the address, a copy button and the client lines
    present and not clipped."""
    base = live_server
    _login(page, base, seeded["emails"]["solution_architect"])
    source_id = seeded["ids"]["impact_source_element"]
    url = base + "/account/manage/connected-assistants?element=%d" % source_id

    for width, tag in ((1440, "wide"), (390, "narrow")):
        for theme in ("light", "dark"):
            page.emulate_media(color_scheme=theme)
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            panel = page.locator('[data-testid="connect-ai-chat"]')
            expect(panel).to_be_visible(timeout=PAGE_TIMEOUT)
            address = page.locator('[data-testid="connection-address"]')
            expect(address).to_be_visible()
            box = address.bounding_box()
            assert box and box["width"] > 40, (width, theme, box)
            expect(page.locator('[data-testid="copy-connection-address"]')).to_be_visible()
            expect(page.locator('[data-testid="try-it"]')).to_be_visible()
            _shoot(page, "connect-%s-%s" % (tag, theme))
