"""The academic address matcher: which institution an address belongs to, and when it must not."""
import pytest

from app.services import academic_domains


@pytest.mark.parametrize(
    "address, expected",
    [
        ("l.marti@uda.ad", "University of Andorra"),  # exact domain
        ("l.marti@cs.uda.ad", "University of Andorra"),  # subdomain
        ("l.marti@a.b.cs.uda.ad", "University of Andorra"),  # deeper subdomain
        ("L.Marti@CS.UDA.AD", "University of Andorra"),  # upper case
        ("  l.marti@uda.ad.  ", "University of Andorra"),  # stray space and trailing dot
        ("l.marti@xuda.ad", None),  # not a label boundary
        ("l.marti@uda.ad.example.com", None),  # the list domain is not the registrable end
        ("l.marti@alumni.uda.ad", None),  # alumni
        ("l.marti@alum.uda.ad", None),
        ("l.marti@alumnae.uda.ad", None),
        ("l.marti@alumni.cs.uda.ad", None),
        ("l.marti@ame.ru", None),  # an excluded country
        ("l.marti@gmail.com", None),  # public mailbox
        ("l.marti@0-mail.com", None),  # disposable
        ("l.marti", None),
        ("", None),
        (None, None),
    ],
)
def test_the_matcher_table(address, expected):
    found = academic_domains.match(address)
    assert (found.name if found else None) == expected


def test_a_match_carries_the_institution_and_country():
    found = academic_domains.match("x@cs.uda.ad")
    assert (found.institution_id, found.country, found.domain) == ("00w7mf142", "AD", "uda.ad")


def test_no_listed_domain_is_in_an_excluded_country():
    assert all(i.country not in academic_domains.EXCLUDED_COUNTRIES for i in academic_domains._domains().values())
