"""The public pages tell the product's story as it is: the first step is connecting GitHub,
people ask in the AI chat, the first reader is a startup or scale-up CTO, and every link a
reader can follow lands on a page with content."""

from __future__ import annotations

import re

import pytest

QUESTION_PAGES = [
    "/use-cases/what-breaks-and-who-gets-called",
    "/use-cases/what-happens-if-a-supplier-fails",
    "/use-cases/risk-blast-radius",
    "/use-cases/risk-and-control-gaps",
    "/use-cases/value-streams-at-risk",
    "/use-cases/duplicate-software-spend",
    "/use-cases/what-i-own",
    "/use-cases/single-point-of-failure",
]

# Wording that sends a reader to the retired Ask page or quotes its fixed questions.
ASK_PAGE_WORDING = [
    "the Ask page",
    "On Ask",
    "on Ask",
    "Open Ask",
    "open **Ask**",
    "the Ask screen",
    "Ask a question",
    "Ask, risk question",
    "Ask, compliance question",
    "fails or is under-resourced",
    "Library section",
]


def _article(client, path: str) -> str:
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200, f"{path} answered {response.status_code}"
    html = response.get_data(as_text=True)
    match = re.search(r"<article[\s\S]*?</article>", html)
    return match.group(0) if match else html


def _text(html: str) -> str:
    html = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _internal_links(html: str) -> list[str]:
    links = []
    for href in re.findall(r'href="([^"]+)"', html):
        if href.startswith("/") and not href.startswith(("//", "/static/")):
            path = href.split("#")[0]
            if path and path not in links:
                links.append(path)
    return links


def assert_every_link_lands_on_content(client, html: str, source: str) -> None:
    links = _internal_links(html)
    assert links, f"{source} links to nothing"
    for path in links:
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200, f"{source} links to {path}, which answered {response.status_code}"
        body = _text(response.get_data(as_text=True))
        assert len(body.split()) > 25, f"{source} links to {path}, which has no content"


@pytest.fixture
def client(app):
    with app.test_client() as signed_out:
        yield signed_out


@pytest.mark.parametrize("path", QUESTION_PAGES)
def test_question_page_asks_in_the_chat_and_every_link_lands(client, path):
    article = _article(client, path)
    text = _text(article)
    for wording in ASK_PAGE_WORDING:
        assert wording not in text and wording not in article, f"{path} still says {wording!r}"
    assert not re.search(r"\bthe Ask\b|\bAsk page\b", text), f"{path} still names the Ask page"
    assert_every_link_lands_on_content(client, article, path)


@pytest.mark.parametrize(
    "path",
    [
        "/use-cases/what-breaks-and-who-gets-called",
        "/use-cases/what-happens-if-a-supplier-fails",
        "/use-cases/risk-blast-radius",
        "/use-cases/risk-and-control-gaps",
        "/use-cases/value-streams-at-risk",
        "/use-cases/what-i-own",
    ],
)
def test_question_steps_name_the_ai_chat(client, path):
    text = _text(_article(client, path))
    assert "AI chat" in text, f"{path} does not tell the reader to ask in the AI chat"


@pytest.mark.parametrize(
    "path",
    [
        "/use-cases/what-breaks-and-who-gets-called",
        "/use-cases/what-happens-if-a-supplier-fails",
        "/use-cases/single-point-of-failure",
    ],
)
def test_first_step_connects_github_or_a_file(client, path):
    text = _text(_article(client, path))
    assert "Connect GitHub" in text, f"{path} does not start from connecting GitHub"


def test_features_opens_on_the_model_and_the_chat_and_every_delivered_link_lands(client):
    article = _article(client, "/features")
    text = _text(article)
    opening = text[:1200]
    assert "GitHub" in opening and "AI chat" in opening
    for gone in ("the Ask page", "twin map page", "vendor catalogue", "pick any element and ask four questions"):
        assert gone not in text, f"/features still says {gone!r}"
    assert "agents are listed in a register" not in text
    assert "read the model securely from Q1 2027" not in text
    delivered = re.findall(r"Delivered today by:([\s\S]*?)</p>", article)
    assert len(delivered) >= 10
    for block in delivered:
        assert_every_link_lands_on_content(client, block, "/features delivered-by")
    # The business case is described as a document, and the vendor image says whose vendors.
    assert "make the case for a change as one document" in text
    assert "Your vendors, with the systems they supply" in text


def test_canvas_pages_carry_no_caveats_or_old_locations(client):
    for path in ("/modules/business-model-canvas", "/use-cases/business-model-canvas-on-one-page"):
        text = _text(_article(client, path))
        for gone in (
            "plain text area",
            "does not read your capability data",
            "Library section",
            "Nothing here yet",
            "do not carry the text you typed",
            "come out empty",
        ):
            assert gone not in text, f"{path} still says {gone!r}"
    assert_every_link_lands_on_content(
        client, _article(client, "/use-cases/business-model-canvas-on-one-page"), "canvas use case"
    )


def test_connectors_hub_meets_github_first_and_states_each_step(client):
    article = _article(client, "/connectors")
    text = _text(article)
    assert text.index("GitHub") < 200
    for step in ("Sign up free", "Connect GitHub", "Approve", "Ask in the AI chat"):
        assert step in text
    assert "/connectors/ai-assistants" in article
    assert_every_link_lands_on_content(client, article, "/connectors")


def test_stop_typing_page_tells_the_github_story(client):
    article = _article(client, "/use-cases/import-from-jira-and-github")
    text = _text(article)
    assert text.index("GitHub") < text.index("ServiceNow")
    assert "does not pull issues" not in text and "not yet pulled" not in text
    assert "links back to the repository" in text
    assert_every_link_lands_on_content(client, article, "stop-typing page")


def test_roles_hub_starts_with_the_startup_cto_then_the_roles(client):
    article = _article(client, "/roles")
    text = _text(article)
    assert text.index("Startup and scale-up CTOs") < text.index("Business architects")
    for role in ("Business architects", "Enterprise architects", "Portfolio managers", "Compliance leads"):
        assert role in text
    links = _internal_links(article)
    assert links.index("/roles/scale-up-cto-and-vp-engineering") < links.index("/roles/business-architects")
    assert_every_link_lands_on_content(client, article, "/roles")


def test_use_cases_hub_leads_startups_with_connecting_github(client):
    article = _article(client, "/use-cases")
    startups = article.split("<h2>Startups</h2>")[1].split("<h2>")[0]
    first_link = re.search(r'href="([^"]+)"[^>]*>([^<]+)<', startups)
    assert first_link.group(1) == "/use-cases/import-from-jira-and-github"
    assert first_link.group(2).startswith("Connect GitHub and ask what breaks")
    assert_every_link_lands_on_content(client, article, "/use-cases")


def test_role_pages_do_not_send_readers_to_the_ask_page(client):
    for path in (
        "/roles/scale-up-cto-and-vp-engineering",
        "/roles/enterprise-architects",
        "/roles/solution-architects",
        "/roles/it-portfolio-managers",
        "/roles/cto-and-cio",
        "/roles/chief-architects",
        "/roles/security-architects",
        "/roles/data-architects",
    ):
        text = _text(_article(client, path))
        for gone in ("the Ask screen", "on Ask", "in Ask", "Open Ask", "from Ask", "the Ask data"):
            assert gone not in text, f"{path} still says {gone!r}"
        assert not re.search(r"(^|[ .])Ask, ", text), f"{path} still names Ask as a place"
