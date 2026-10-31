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
