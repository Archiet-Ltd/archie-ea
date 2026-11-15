"""The review board, renewals and applications pages say what the product does today."""

import re

import pytest

PAGES = {
    "board": "/use-cases/architecture-review-board",
    "renewals": "/use-cases/contract-renewals",
    "applications": "/modules/applications",
    "features": "/features",
}


def _text(client, path):
    response = client.get(path)
    assert response.status_code == 200, path
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", response.get_data(as_text=True)))


@pytest.fixture()
def client(app):
    return app.test_client()


def test_board_title_matches_what_the_body_describes(client):
    text = _text(client, PAGES["board"])
    assert "with its evidence pinned to the decision" in text
    assert "with the impact evidence attached" not in text
    assert "Impact analysis runs on its own screen" not in text
    assert "Impact analysis sits beside the review" in text


@pytest.mark.parametrize("path", ["/roles/enterprise-architects", "/roles/solution-architects", "/use-cases/programme-tracking"])
def test_pages_linking_the_board_use_its_current_title(client, path):
    assert "impact evidence attached" not in _text(client, path)


def test_renewals_page_joins_contract_to_dependants_in_the_chat(client):
    text = _text(client, PAGES["renewals"])
    assert "Entelim does not join the two" not in text
    assert "AI chat" in text
    assert "Vendor Catalogue" not in text
    assert not re.search(r"\bon Ask\b|open \*?\*?Ask", text)


def test_vendor_screenshot_caption_reads_your_vendors(client):
    text = _text(client, PAGES["features"])
    assert "Your vendors, with each vendor" in text
    assert "The vendor catalogue" not in text


def test_applications_page_leads_with_connecting_sources(client):
    text = _text(client, PAGES["applications"])
    assert "built from your repositories or imported, not typed in" in text
    assert re.search(r"1\. ?Connect GitHub or import a file|Connect GitHub or import a file", text)
    assert "no discovery agent" not in text.lower()
    assert "does not run a discovery agent" not in text
