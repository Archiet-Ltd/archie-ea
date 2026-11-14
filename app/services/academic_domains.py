"""Which university or college an email address belongs to.

The list is ``app/data/academic_email_domains.csv`` (see its NOTICE), read once.
An address matches when its domain is a listed domain or a subdomain of one at a
label boundary, so ``cs.uda.ad`` matches ``uda.ad`` and ``xuda.ad`` does not.
Alumni addresses, public mailboxes, disposable domains and institutions in the
countries below never match. The public mailbox list and the disposable list are
the product's existing ones; this module holds no copy of either.
"""

import csv
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional

_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "academic_email_domains.csv")

EXCLUDED_COUNTRIES = frozenset({"RU", "BY", "IR", "SY", "KP"})
ALUMNI_LABELS = frozenset({"alumni", "alum", "alumnae"})


@dataclass(frozen=True)
class Institution:
    institution_id: str
    name: str
    country: str
    domain: str


@lru_cache(maxsize=1)
def _domains() -> Dict[str, Institution]:
    found: Dict[str, Institution] = {}
    with open(_FILE, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            domain = (row.get("domain") or "").strip().lower()
            country = (row.get("country_iso2") or "").strip().upper()
            if not domain or country in EXCLUDED_COUNTRIES:
                continue
            found.setdefault(domain, Institution(row["institution_id"], row["institution"], country, domain))
    return found


def match(address: Optional[str]) -> Optional[Institution]:
    """The institution an address belongs to, or None."""
    from app.services.signup_guard import is_disposable
    from app.services.sso_service import PUBLIC_MAILBOX_DOMAINS

    text = (address or "").strip().lower()
    if "@" not in text:
        return None
    host = text.rsplit("@", 1)[-1].strip(". ")
    if not host or host in PUBLIC_MAILBOX_DOMAINS or is_disposable(text):
        return None
    labels = host.split(".")
    listed = _domains()
    for start in range(len(labels) - 1):
        found = listed.get(".".join(labels[start:]))
        if found is not None:
            # A label that names an alumni mailbox, left of the matched domain, ends the match.
            if any(label in ALUMNI_LABELS for label in labels[:start]):
                return None
            return found
    return None
