"""The social profiles a website reading proposes are the company's own accounts, one per network."""

import os
from collections import Counter

import pytest

from app.modules.organization.website_crawl.extraction import extract_static, social_profile_root
from app.modules.organization.website_crawl.service import _facts_from

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "site_social")

OWN = {
    "posthog": {"Twitter/X": "https://x.com/posthog", "LinkedIn": "https://www.linkedin.com/company/posthog",
                "GitHub": "https://github.com/PostHog", "YouTube": "https://www.youtube.com/@posthog"},
    "linear": {"Twitter/X": "https://x.com/linear", "LinkedIn": "https://www.linkedin.com/company/linear-app",
               "GitHub": "https://github.com/linear", "YouTube": "https://www.youtube.com/@linear"},
    "supabase": {"Twitter/X": "https://x.com/supabase", "LinkedIn": "https://www.linkedin.com/company/supabase",
                 "GitHub": "https://github.com/supabase", "YouTube": "https://www.youtube.com/@supabase"},
    "calcom": {"Twitter/X": "https://x.com/calcom", "LinkedIn": "https://www.linkedin.com/company/cal-com",
               "GitHub": "https://github.com/calcom", "YouTube": "https://www.youtube.com/@calcom"},
    "plausible": {"Twitter/X": "https://x.com/plausiblehq", "LinkedIn": "https://www.linkedin.com/company/plausible-analytics",
                  "GitHub": "https://github.com/plausible"},
}
SITES = {"posthog": "https://posthog.com/", "linear": "https://linear.app/", "supabase": "https://supabase.com/",
         "calcom": "https://cal.com/", "plausible": "https://plausible.io/"}


def _proposed(name, analysis=None):
    url = SITES[name]
    with open(os.path.join(FIXTURES, name + ".html")) as handle:
        html = handle.read()
    page = {"url": url, "fetched_at": "2026-10-30T00:00:00", "static": extract_static(url, html, url),
            "analysis": analysis or {}}
    return [r["value"] for r in _facts_from([page]).ordered() if r["field"] == "social_profile"]


@pytest.mark.parametrize("name", sorted(SITES))
def test_only_the_companys_own_account_is_proposed_once_per_network(name):
    proposed = _proposed(name)
    assert Counter(p["platform"] for p in proposed).most_common(1)[0][1] == 1
    assert {p["platform"]: p["url"] for p in proposed} == OWN[name]


def test_a_model_reading_cannot_add_a_person_a_post_or_a_second_account():
    proposed = _proposed("linear", {"social_media": [
        {"url": "https://www.linkedin.com/in/karrisaarinen"}, {"url": "https://twitter.com/someoneelse"},
        {"url": "https://x.com/linear/status/1"}, {"url": "https://github.com/linear/linear/tree/main"}]})
    assert {p["platform"]: p["url"] for p in proposed} == OWN["linear"]


@pytest.mark.parametrize("address", [
    "https://www.linkedin.com/in/someone", "https://x.com/someone/status/12", "https://twitter.com/intent/tweet?text=a",
    "https://www.youtube.com/watch?v=abc", "https://github.com/acme/repo", "https://github.com/acme/repo/blob/main/a",
    "https://www.facebook.com/sharer/sharer.php?u=a", "https://www.instagram.com/p/abc/"])
def test_addresses_that_are_not_a_profile_root_are_refused(address):
    assert social_profile_root(address) is None


def test_a_profile_root_is_normalised():
    assert social_profile_root("https://www.LinkedIn.com/company/acme/?trk=x")[1] == "https://www.linkedin.com/company/acme"


def _page(url, body, header="", footer="", analysis=None):
    html = "<html><head><title>%s</title></head><body><header>%s</header>%s<footer>%s</footer></body></html>" % (
        url, header, body, footer)
    return {"url": url, "fetched_at": "2026-10-30T00:00:00", "static": extract_static(url, html, url),
            "analysis": analysis or {}}


def _facts(page):
    facts = _facts_from([page]).ordered()
    return ([r["value"] for r in facts if r["field"] == "social_profile"],
            [r["value"] for r in facts if r["field"] == "linkedin_url"])


def _link(address):
    return '<a href="%s">link</a>' % address


def test_a_short_piece_of_the_name_is_not_the_companys_handle():
    body = "".join(_link(a) for a in ("https://x.com/post", "https://github.com/hog", "https://www.youtube.com/@o",
                                      "https://x.com/p", "https://x.com/posthog"))
    profiles, _ = _facts(_page("https://posthog.com/", body))
    assert [p["url"] for p in profiles] == ["https://x.com/posthog"]


def test_a_handle_must_spell_the_name_as_a_whole_word():
    body = "".join(_link(a) for a in ("https://github.com/acmeville", "https://x.com/notacme", "https://x.com/acme-hq"))
    profiles, _ = _facts(_page("https://acme.com/", body))
    assert [p["url"] for p in profiles] == ["https://x.com/acme-hq"]


def test_the_linkedin_address_is_the_linkedin_profile_chosen_by_the_same_rule():
    body = _link("https://www.linkedin.com/company/bigbank") + _link("https://www.linkedin.com/company/acme")
    profiles, linkedin = _facts(_page("https://acme.com/", body))
    assert linkedin == ["https://www.linkedin.com/company/acme"]
    assert [p["url"] for p in profiles if p["platform"] == "LinkedIn"] == linkedin


def test_a_customers_linkedin_page_is_neither_a_profile_nor_the_linkedin_address():
    profiles, linkedin = _facts(_page("https://acme.com/", _link("https://www.linkedin.com/company/bigbank")))
    assert profiles == [] and linkedin == []


def test_a_model_given_linkedin_address_is_checked_too():
    page = _page("https://acme.com/", "", analysis={"company": {"linkedin_url": "https://www.linkedin.com/company/other"}})
    assert _facts(page)[1] == []


def test_a_powered_by_link_in_the_footer_is_not_the_companys_account():
    profiles, _ = _facts(_page("https://acme.com/", "", footer=_link("https://x.com/vercel") + _link("https://x.com/acme")))
    assert [p["url"] for p in profiles] == ["https://x.com/acme"]
    assert _facts(_page("https://acme.com/", "", footer=_link("https://x.com/vercel")))[0] == []
