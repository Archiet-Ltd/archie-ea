"""The SysML early-access page can be found by search, and an enquiry from it is counted against it.

A page that says plainly it is early access is a demand test, and search is how demand arrives, so it is indexed
whether the release switch is on or off. The two early-access pages whose copy is not yet reviewed stay held.
"""

import re
import uuid

import pytest

PAGE = "/modules/sysml-to-archimate"
# Scenarios is released with the switch off and then serves its released page, so it is held only with the switch on.
HELD = {"off": ("/modules/software-and-tool-discovery",), "on": ("/modules/software-and-tool-discovery", "/modules/scenario-modelling")}
NOINDEX = re.compile(r'<meta[^>]+name="robots"[^>]+noindex', re.I)


@pytest.fixture(params=["off", "on"])
def switch(request, app, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1" if request.param == "on" else None)
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    return request.param


def _indexable(response):
    return response.status_code == 200 and not NOINDEX.search(response.get_data(as_text=True)) and "noindex" not in response.headers.get("X-Robots-Tag", "")


def test_the_sysml_page_is_indexed_and_in_the_sitemap_and_the_two_briefed_pages_are_not(app, switch):
    client = app.test_client()
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    page = client.get(PAGE)
    assert _indexable(page)
    assert "Bring your SysML models" in page.get_data(as_text=True)
    assert PAGE in sitemap
    for held in HELD[switch]:
        assert not _indexable(client.get(held)), held
        assert held not in sitemap, held


def _form_fields(html):
    return dict(re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', html))


def test_talk_to_us_from_the_page_records_the_enquiry_against_the_page(app):
    from app import db
    from app.models.product_inquiry import ProductInquiry

    client = app.test_client()
    html = client.get(PAGE).get_data(as_text=True)
    assert 'href="/contact?from=sysml-to-archimate"' in html
    assert "book a conversation" not in html.lower()
    contact = client.get("/contact?from=sysml-to-archimate")
    assert contact.status_code == 200
    fields = _form_fields(contact.get_data(as_text=True))
    assert fields["source"] == "sysml-to-archimate"
    email = f"sysml.enquiry.{uuid.uuid4().hex[:8]}@example.com"
    offer = fields["offer"]
    try:
        sent = client.post("/offers/inquire", data={**fields, "email": email, "consent": "1"})
        assert sent.status_code == 200
        with app.app_context():
            rows = ProductInquiry.query.filter_by(email=email).all()
            assert [r.offer for r in rows] == ["sales_enquiry:sysml-to-archimate"]
    finally:
        with app.app_context():
            ProductInquiry.query.filter_by(email=email, offer=offer).delete()
            db.session.commit()


def test_a_source_that_is_not_a_waiting_list_page_is_ignored(app):
    client = app.test_client()
    for bad in ("nonsense", "architecture-model", "../etc"):
        fields = _form_fields(client.get(f"/contact?from={bad}").get_data(as_text=True))
        assert fields["source"] == "" and fields["offer"] == "sales_enquiry", bad


def test_the_hub_lists_each_capability_once_in_one_alphabetical_list(app, monkeypatch):
    monkeypatch.setitem(app.config, "RELEASE_WAVE", "1")
    monkeypatch.delenv("RELEASE_WAVE", raising=False)
    html = app.test_client().get("/early-access").get_data(as_text=True)
    titles = re.findall(r'<li><a href="[^"]+">([^<]+)</a>', html)
    assert "Bring your SysML models" in titles
    assert not ("Scenarios" in titles and "Simulation scenarios" in titles)
    assert len(titles) == len(set(titles))
    assert titles == sorted(titles, key=str.lower)
    blurb = re.search(r'Bring your SysML models</a>: ([^<]+)<', html).group(1)
    assert "bring" not in blurb.lower() and "sysml" not in blurb.lower()
