"""A company with a confirmed profile, public signals, and two named competitors, and a model that answers from them.

Used by the unit tests, the two browser journeys and the walk. Nothing here reaches the internet: ``FakeStrategyModel``
is a stand-in that reads the numbered sources in the prompt it is handed and cites only those, the way a careful
reader would. It also does what a careless one does, on purpose, so the critique can be seen working:

- a strength that cites a source number that does not exist (the 40% market-share claim);
- a statement that cites nothing;
- a statement that cites a real source but claims more than it says ("Fabricated award"), which the second reading removes.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Dict, List

COMPANY = "Northwind Freight"
SITE = "https://northwind-freight.example"
HARBOR = "Harbor Logistics"
SWIFTLINE = "Swiftline Cargo"
RIVALS = [HARBOR, SWIFTLINE]

INVENTED_SHARE = "Owns 40% of the European cold chain market"
NO_SOURCE = "Plans an initial public offering next year"
FABRICATED = "Fabricated award for routing research"

NOW = "2026-10-26T09:00:00"

PAGES = {
    "/": ("Northwind Freight | Refrigerated freight", [
        ("tagline", "Refrigerated freight that arrives on time"),
        ("category", "Transportation & Logistics"),
        ("business_model", "Subscription freight capacity for food producers"),
        ("target_market", "Food producers and grocers"),
    ]),
    "/products": ("Products", [
        ("product", {"name": "ColdRoute planning"}),
        ("value_proposition", "Routes planned around refrigeration needs, not only distance"),
    ]),
}

SIGNALS = {
    COMPANY: [
        ("news", "NewsAPI", "Northwind opens a Rotterdam depot", "Northwind Freight opened a depot in Rotterdam.",
         "https://techdaily.example/northwind-rotterdam", "2026-10-20"),
        ("filing", "SEC EDGAR", "Northwind Freight 10-Q filing", "Quarterly filing.",
         "https://www.sec.gov/Archives/edgar/data/1234567/nwf-10q.htm", "2026-08-05"),
        ("news", "NewsAPI", "Northwind adds a route planning tool", "ColdRoute planning cuts empty running.",
         "https://logweekly.example/northwind-coldroute", "2026-10-02"),
    ],
    HARBOR: [
        ("news", "NewsAPI", "Harbor Logistics expands its depot network", "Harbor Logistics now runs 31 regional depots.",
         "https://freightnews.example/harbor-depots", "2026-10-18"),
        ("news", "NewsAPI", "Harbor customers report slow online booking", "Shippers say booking is still by phone and email.",
         "https://freightnews.example/harbor-booking", "2026-09-30"),
    ],
    SWIFTLINE: [
        ("news", "NewsAPI", "Swiftline Cargo raises a funding round", "Swiftline Cargo raised capital to enter refrigerated freight.",
         "https://freightnews.example/swiftline-funding", "2026-10-12"),
        ("news", "NewsAPI", "Swiftline Cargo prices undercut rivals", "Swiftline lists lower per-pallet prices.",
         "https://freightnews.example/swiftline-prices", "2026-10-05"),
    ],
}


def seed_company(org_id: int, user_id: int, *, company: str = COMPANY, rivals: List[str] = None,
                 site: str = SITE, signals: Dict[str, list] = None, profile_rivals: List[str] = ()) -> Dict[str, object]:
    """Record a confirmed company profile, its signals and its named competitors for one organisation."""
    from sqlalchemy.orm.attributes import flag_modified

    from app import db
    from app.models.organization import Organization
    from app.models.organization_signal import OrganizationSignal

    rivals = list(RIVALS if rivals is None else rivals)
    org = db.session.get(Organization, org_id)
    profile = {"company_name": {"value": company, "source": "website", "source_url": site + "/", "fetched_at": NOW,
                                "confidence": 0.9, "corrected": False, "confirmed_by": user_id,
                                "confirmed_by_name": "Casey Founder", "confirmed_at": NOW}}
    for path, (_title, facts) in PAGES.items():
        for field, value in facts:
            entry = {"value": value, "source": "website", "source_url": site + path, "fetched_at": NOW,
                     "confidence": 0.8, "corrected": False, "confirmed_by": user_id,
                     "confirmed_by_name": "Casey Founder", "confirmed_at": NOW}
            if field in ("product", "value_proposition"):
                profile.setdefault(field, []).append(entry)
            else:
                profile[field] = entry
    if profile_rivals:
        profile["competitor"] = [{"value": r, "source": "website", "source_url": site + "/", "fetched_at": NOW,
                                  "confidence": 0.6, "corrected": False, "confirmed_by": user_id,
                                  "confirmed_by_name": "Casey Founder", "confirmed_at": NOW} for r in profile_rivals]
    settings = dict(org.settings or {})
    settings["company_profile"] = profile
    settings["onboarding"] = {"website": {"url": site, "consent_at": NOW, "consent_by": user_id}}
    settings["public_signals"] = {"competitors": rivals}
    org.settings = settings
    flag_modified(org, "settings")
    org.name = company
    wanted = signals if signals is not None else {
        company: SIGNALS[COMPANY], **{r: SIGNALS.get(r, []) for r in rivals}}
    for subject, rows in wanted.items():
        for kind, label, title, summary, url, day in rows:
            db.session.add(OrganizationSignal(
                organization_id=org_id, kind=kind, source=label.lower().replace(" ", "_"), source_label=label,
                subject=subject, title=title, summary=summary, url=url, published_at=datetime.fromisoformat(day),
                fetched_at=datetime.fromisoformat(NOW), checksum=uuid.uuid4().hex))
    db.session.commit()
    return {"company": company, "rivals": rivals, "site": site}


_SOURCE_LINE = re.compile(r"^\[(S\d+)\] (.*?)(?: about (.+?))? \((.*?), read (.*?)\)$", re.M)


class FakeStrategyModel:
    """Answers the three analyses and the critique from the numbered sources in the prompt it is given."""

    def __init__(self, fail=()):
        self.prompts: List[str] = []
        self.fail = set(fail)

    # -- reading the prompt
    @staticmethod
    def _by_subject(prompt: str) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for sid, _title, subject, _publisher, _day in _SOURCE_LINE.findall(prompt):
            out.setdefault(subject or "", []).append(sid)
        return out

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "hostile filter" in prompt:
            return self._critique(prompt)
        if "competitive intelligence analyst" in prompt:
            if "competitors" in self.fail:
                raise RuntimeError("the provider is busy")
            return self._competitors(prompt)
        if "SWOT analysis" in prompt:
            if "swot" in self.fail:
                raise RuntimeError("the provider is busy")
            return self._swot(prompt)
        if "PESTLE analysis" in prompt:
            if "pestle" in self.fail:
                raise RuntimeError("the provider is busy")
            return self._pestle(prompt)
        raise AssertionError("an unexpected prompt: " + prompt[:80])

    # -- answers
    def _competitors(self, prompt: str) -> str:
        sources = self._by_subject(prompt)
        names = re.findall(r"^- (.+)$", prompt.split("## Competitors", 1)[1].split("##", 1)[0], re.M)
        company = [s for k, v in sources.items() if k and k not in names and k != "" for s in v]
        out = []
        for i, name in enumerate(names):
            own = sources.get(name, [])
            first, last = (own[0], own[-1]) if own else (None, None)
            if not first:
                continue
            item = {
                "name": name,
                "position": {"text": ("Established regional carrier with a large depot network" if i == 0
                                       else "Funded newcomer entering refrigerated freight"), "sources": [first]},
                "threat": {"level": "high" if i == 0 else "medium", "sources": [first]},
                "strengths": [{"text": ("Large depot network across the region" if i == 0 else "Fresh funding to grow quickly"),
                               "sources": [first]}],
                "weaknesses": ([{"text": "Online booking is still slow", "sources": [last]}] if i == 0 else []),
                "differences": [{"text": "We plan routes around refrigeration needs",
                                 "sources": [company[0], first] if company else [first]}],
            }
            if i == 0:
                item["strengths"].append({"text": INVENTED_SHARE, "sources": ["S99"]})
                item["strengths"].append({"text": NO_SOURCE, "sources": []})
                item["strengths"].append({"text": FABRICATED, "sources": [first]})
            out.append(item)
        return json.dumps({"competitors": out})

    def _swot(self, prompt: str) -> str:
        sources = self._by_subject(prompt)
        mine = sources.get(COMPANY, [])
        harbor = sources.get(HARBOR, [])
        swift = sources.get(SWIFTLINE, [])
        return json.dumps({
            "strengths": [{"text": "ColdRoute planning cuts empty running", "sources": mine[-1:] or mine[:1]},
                          {"text": FABRICATED + " in SWOT", "sources": mine[:1]}],
            "weaknesses": [{"text": "Booking still depends on a single depot hub", "sources": mine[:1]}],
            "opportunities": [{"text": "Harbor customers want faster online booking", "sources": harbor[-1:]}],
            "threats": [{"text": "Swiftline is funded to enter refrigerated freight", "sources": swift[:1]},
                        {"text": NO_SOURCE + " (threat)", "sources": []}],
        })

    def _pestle(self, prompt: str) -> str:
        sources = self._by_subject(prompt)
        mine = sources.get(COMPANY, [])
        harbor = sources.get(HARBOR, [])
        return json.dumps({
            "political": [],
            "economic": [{"text": "Rivals compete on per-pallet price", "sources": sources.get(SWIFTLINE, [])[-1:]}],
            "social": [],
            "technological": [{"text": "Route planning tools are becoming standard", "sources": mine[-1:]}],
            "legal": [{"text": "Quarterly filings disclose the company's finances", "sources": mine[1:2]}],
            "environmental": [{"text": "Depot growth raises the carbon footprint of freight", "sources": harbor[:1]}],
        })

    def _critique(self, prompt: str) -> str:
        gone = [m.group(1) for m in re.finditer(r"^(s\d+) \|[^\n]*Fabricated[^\n]*$", prompt, re.M)]
        return json.dumps({"remove": gone})
