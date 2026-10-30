"""The founder sees the company's facts at step 3 without waiting on the model (crawl05, QA-026).

Pass 1 reads the pages with no model and proposes what they state outright. Pass 2 hands the pages read to the
model in ONE batched call and adds what it finds. The model here takes "30 seconds" a call, scaled to a short real
delay so the test stays fast; the assertions are about how many calls were made and what was proposed before them.
"""

from __future__ import annotations

import time

import pytest

from tests.test_website_crawl import _by_field, _crawl, _html, _org, _Site, fake_model, internal_hosts_allowed, other_site, site  # noqa: F401

pytestmark = pytest.mark.usefixtures("db_session")

SCALED_SLOW_CALL = 0.3  # stands in for a model that takes 30 s a call


class SlowModel:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        time.sleep(SCALED_SLOW_CALL)
        return fake_model(prompt)


def test_pass_one_proposes_the_name_without_the_model_and_pass_two_asks_the_model_once(make_org, site, monkeypatch):
    from app.modules.organization import website_crawl
    from app.modules.organization.website_crawl import service

    model = SlowModel()
    monkeypatch.setattr(website_crawl, "default_model", lambda *a, **k: model)
    org = _org(make_org, site.url)

    started = time.monotonic()
    first = service.run_website_crawl_job({"organization_id": org.id})
    pass_one_seconds = time.monotonic() - started
    assert model.prompts == [], "pass 1 waited for the model"
    assert pass_one_seconds < 10, "the first facts took %.1f s" % pass_one_seconds
    found = _by_field(org.id)
    assert [r["value"] for r in found["company_name"]] == ["Acme Robotics Ltd"]
    assert "contact_email" in found and "linkedin_url" in found
    assert "category" not in found, "a model-only fact was proposed before the model was asked"
    assert first["more_to_come"] is True and first["pages_read"] >= 5

    service.run_website_retry_job({"organization_id": org.id, "retry_attempt": 0})
    assert len(model.prompts) == 1, "the model was asked %d times; it should be asked once over the pages read" % len(model.prompts)
    for path in ("/about", "/products", "/contact"):
        assert path in model.prompts[0], "%s was not in the one batched call" % path
    found = _by_field(org.id)
    assert found["category"][0]["value"] == "Transportation & Logistics", "the model's reading was not added"
    assert [r["value"] for r in found["company_name"]] == ["Acme Robotics Ltd"], "the name was proposed twice"


def test_thirty_pages_are_read_and_the_model_is_called_exactly_once(make_org):
    links = " ".join('<a href="/p%d">page %d</a>' % (i, i) for i in range(45))
    pages = {"/": _html("<html><head><title>Big Co | Things</title></head><body>%s</body></html>" % links),
             "/robots.txt": (404, "text/plain", "")}
    for i in range(45):
        pages["/p%d" % i] = _html("<html><body><p>page %d with warehouse robots text</p></body></html>" % i)
    big = _Site(pages)
    try:
        org = _org(make_org, big.url)
        model = SlowModel()
        summary = _crawl(org, llm=model)
        assert summary["pages_read"] == 30
        assert len(model.prompts) == 1, "%d model calls for 30 pages" % len(model.prompts)
        assert len(model.prompts[0]) <= 42000
        assert _by_field(org.id)["company_name"][0]["value"] == "Big Co"
    finally:
        big.stop()


def test_each_pass_has_its_own_time_limit(make_org, site, monkeypatch):
    """A read that used its whole limit in pass 1 still gets the model its own, full time to answer."""
    from app.modules.organization import website_crawl
    from app.modules.organization.website_crawl import service

    given = []
    monkeypatch.setattr(service, "default_model", lambda timeout=service.MODEL_TIMEOUT_SECONDS: given.append(timeout) or fake_model)
    monkeypatch.setattr(website_crawl, "default_model", service.default_model)
    org = _org(make_org, site.url)
    service.run_website_crawl_job({"organization_id": org.id})
    service.run_website_retry_job({"organization_id": org.id, "retry_attempt": 0})
    assert given and given[-1] == service.MODEL_TIMEOUT_SECONDS
    assert service.TIME_LIMIT_SECONDS == 120.0
