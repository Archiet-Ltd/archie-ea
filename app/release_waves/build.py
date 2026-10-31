"""Builds app/release_waves/wave_map.json: the wave of every registered route rule.

The wave plan's page list (wave_plan_pages.json, a copy of the release plan's
page-to-wave map) names the pages. A route rule that is not a page (a form post,
a data call) takes the wave of the page it belongs to. Every rule gets an entry
and a stated basis; a rule no page of the plan reaches is wave ``X`` ("not in any
planned wave"): it is closed in every wave, never open by default.

Basis values: ``page`` (the rule is a page of the plan), ``override`` (this module
names it), ``prefix`` (it sits under a page of the plan), ``api-prefix`` (the same
after the /api prefix is removed), ``blueprint`` (every mapped rule of its blueprint
is in one wave), ``unplanned`` (nothing maps it; closed).
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLAN_PATH = HERE / "wave_plan_pages.json"
MAP_PATH = HERE / "wave_map.json"

# Waves that are never gated by the switch: infrastructure (A), release foundation (W0) and
# operations (W6, platform administrators only through their own permission checks).
PASS_WAVES = ("A", "W0", "W6")

_PARAM = re.compile(r"<[^>]*>")


def norm(rule: str) -> str:
    """A route rule with every parameter written <x>, so <int:id> and <id> are one rule."""
    return _PARAM.sub("<x>", rule)


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


# Rules that are not pages of the plan but are named here, in priority order (first match wins).
# (prefix, wave, group, why)
PREFIX_OVERRIDES: tuple = (
    ("/static/", "A", "infra", "static files"),
    ("/health", "A", "infra", "health checks"),
    ("/version", "A", "infra", "build version"),
    ("/robots.txt", "A", "infra", "crawler rules"),
    ("/sitemap.xml", "A", "infra", "sitemap (lists released pages only)"),
    ("/llms.txt", "A", "infra", "llms.txt (lists released pages only)"),
    ("/llms-full.txt", "A", "infra", "llms-full.txt (lists released pages only)"),
    ("/favicon.ico", "A", "infra", "icon"),
    ("/status", "A", "infra", "service status"),
    ("/apispec.json", "W6", "PG-OPS", "API documentation, platform administrators"),
    ("/apidocs", "W6", "PG-OPS", "API documentation, platform administrators"),
    ("/flasgger_static", "W6", "PG-OPS", "API documentation assets"),
    ("/oauth2-redirect.html", "W6", "PG-OPS", "API documentation sign-in"),
    ("/api/client-error", "A", "infra", "browser error reports"),
    ("/csp-report", "A", "infra", "browser security reports"),
    ("/early-access", "A", "infra", "the early-access pages themselves"),
    ("/oauth/", "W1", "PG-CON", "assistant sign-in, consent, token and registration: the connector stays on at wave 1 for signed-in users (lead, 26 Oct)"),
    ("/.well-known/", "W1", "PG-CON", "assistant discovery documents (connector stays on at wave 1)"),
    ("/mcp", "W1", "PG-CON", "the assistant connection (MCP): on at wave 1 for signed-in users, advertised from wave 2"),
    ("/roles", "W1", "PG-PUB", "public roles hub"),
    ("/connectors", "W1", "PG-PUB", "public connectors hub"),
    ("/admin/signups", "W6", "PG-OPS", "where sign-ups came from: the platform team only"),
    ("/intelligence/answers/element/", "W2", "PG-ANS", "the answer for one element, beside the other answer pages"),
    # Reads the assistant connection and token clients make (all GET): the connector is on at wave 1.
    ("/api/v1/elements", "W1", "PG-CON", "element reads for token clients and the assistant connection"),
    ("/archimate/api/elements/search$", "W1", "PG-CON", "element search the assistant connection calls"),
    ("/archimate/api/elements/<int:element_id>/detail$", "W1", "PG-CON", "element detail the assistant connection calls"),
    ("/api/v1/intelligence/accountability/", "W1", "PG-CON", "the accountability answer the assistant connection calls"),
    ("/api/v1/intelligence/compliance/", "W1", "PG-CON", "the compliance answer the assistant connection calls"),
    ("/api/v1/intelligence/data/", "W1", "PG-CON", "the data answer the assistant connection calls"),
    ("/api/v1/intelligence/impact/", "W1", "PG-CON", "the impact answer the assistant connection calls"),
    ("/api/v1/intelligence/portfolio/", "W1", "PG-CON", "the portfolio answer the assistant connection calls"),
    ("/api/v1/intelligence/programme/", "W1", "PG-CON", "the programme answer the assistant connection calls"),
    ("/api/v1/intelligence/risk/", "W1", "PG-CON", "the risk answer the assistant connection calls"),
    ("/api/v1/intelligence/strategy/", "W1", "PG-CON", "the strategy answer the assistant connection calls"),
    ("/api/system-settings", "W6", "PG-OPS", "platform settings page data, platform administrators"),
    # Calls a wave-1 screen makes (found by walking the wave-1 preview; "$" means this address only).
    ("/api/notifications", "W1", "PG-ACC", "the notification bell in the signed-in shell"),
    ("/api/bu-scope$", "W1", "PG-ACC", "business-unit scope read by the signed-in shell"),
    ("/api/archimate/viewpoints/<viewpoint_key>/diagram$", "W1", "PG-APP", "the application cooperation diagram on the applications page"),
    ("/dashboard/api/archimate-elements/<int:element_id>/correct$", "W1", "PG-APP", "correct an element's type from the application page"),
    ("/api/ea-workflows/instances$", "W1", "PG-APP", "workflow list shown on the application page"),
    ("/offers/inquire", "W1", "PG-PUB", "public enquiry and waiting-list form"),
    ("/t/plan-click", "W1", "PG-PUB", "public plan click counter"),
    ("/signup", "W1", "PG-ACC", "sign-up address"),
    ("/register", "W1", "PG-ACC", "sign-up address"),
    ("/login", "W1", "PG-ACC", "sign-in address"),
    ("/account/", "W1", "PG-ACC", "account, sign-in, two-factor and the first sit-down"),
    ("/admin/billing/", "W1", "PG-ORG", "plan checkout, cancel and billing (NP-07, NP-08)"),
    ("/architecture/sample-company/", "W1", "PG-NEW", "start the sample company (NP-06)"),
    ("/dashboard/api/onboarding-complete", "W1", "PG-NEW", "first sit-down progress (NP-05)"),
)

# Rules whose path is ambiguous after normalisation (two rules read /<x>): named by endpoint.
ENDPOINT_OVERRIDES: dict = {
    "main.public_legal_page": ("W1", "PG-PUB", "legal pages"),
    "main.public_site_page": ("W1", "PG-PUB", "public site pages"),
    "main.indexnow_key_file": ("A", "infra", "search engine key file"),
    "main.index": ("W1", "PG-PUB", "home"),
    "main.public_module": ("W1", "PG-PUB", "module pages; the page's own wave decides what it shows"),
}

# Plan pages whose title names the screen, not an address: the address is in the table.
# Plan pages moved to another wave by the lead: NP-13 connected assistants and NP-14 the consent page stay on at wave 1.
PAGE_WAVE_OVERRIDES: dict = {"NP-13": "W1", "NP-14": "W1"}

PAGE_ADDRESS_OVERRIDES: dict = {
    "NP-05": "/account/welcome",
    "NP-06": "/architecture/sample-company/load",
    "NP-07": "/admin/billing/",
    "NP-08": "/admin/billing/cancel",
    "NP-09": "/account/welcome",
}

GROUP_FALLBACK = {
    "PG-RT": ("This page", "This address has moved and the page it moves to opens in a later release."),
    "PG-EA": ("This capability", "Entelim is building this. Join the waiting list and we will tell you the day it opens."),
    "PG-OPS": ("Platform operations", "Operator tools for the platform team."),
    "infra": ("This page", "This page opens in a later release."),
}


# What a customer reads for a group: plain capability names, never plan wording. Applied last, to every group.
GROUP_TEXT_OVERRIDES: dict = {
    "PG-CON": ("Connectors and integrations", None),
    "PG-ELE": (None, "Open any element and see what depends on it."),
    "PG-EA": ("This capability", "Entelim is building this. Join the waiting list and we will tell you the day it opens."),
    "PG-RT": ("This page", "This address has moved and the page it moves to opens in a later release."),
    "PG-RTP": ("This page", "This address has moved and the page it moves to opens in a later release."),
    "PG-NEW": ("This capability", "Entelim is building this. Join the waiting list and we will tell you the day it opens."),
    "PG-PUB": ("This page", "This page opens in a later release."),
    "PG-OPS": ("Platform tools", "Tools for the platform team."),
}


def _title_of(code: str, text: str) -> tuple[str, str]:
    head, _, tail = text.partition(":")
    if not tail:
        return GROUP_FALLBACK.get(code, (head.strip().rstrip("."), ""))
    tail = tail.strip()
    return head.strip(), tail[:1].upper() + tail[1:].rstrip(".") + "."


def _humanise(blueprint: str) -> str:
    words = blueprint.replace("_api", "").replace("_bp", "").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def load_plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def _plan_pages(plan: dict) -> dict:
    """norm(address) -> plan item, for plan items that name an address."""
    pages: dict = {}
    for item in plan["items"]:
        title = item["title"]
        if item["id"] in PAGE_ADDRESS_OVERRIDES:
            title = PAGE_ADDRESS_OVERRIDES[item["id"]]
        else:
            m = re.search(r"\((/[^)]*)\)\s*$", title)
            if m and not title.startswith("/"):
                title = m.group(1)
        if not title.startswith("/"):
            continue
        title = title.split(" (")[0]
        # A title written with a descriptive slug list stands for the public site pages; those are
        # named by endpoint above.
        if "<legal slug" in title or "<site slug" in title:
            continue
        pages.setdefault(norm(title), item)
    return pages


def build(rules: list[tuple[str, str]]) -> dict:
    """rules: [(rule string, endpoint)] for every rule of the url_map. Returns the map document."""
    plan = load_plan()
    pages = _plan_pages(plan)
    page_segs = {k: _segments(k) for k in pages}

    groups: dict = {}
    for code, text in plan["reason_codes"].items():
        title, blurb = _title_of(code, text)
        groups[code] = {"title": title, "blurb": blurb}
    for code, (title, blurb) in GROUP_FALLBACK.items():
        groups.setdefault(code, {"title": title, "blurb": blurb})

    def longest_prefix(path: str) -> str | None:
        segs = _segments(path)
        best = None
        for key, ks in page_segs.items():
            if ks and len(ks) <= len(segs) and segs[: len(ks)] == ks and any(not s.startswith("<") for s in ks):
                if best is None or len(ks) > len(page_segs[best]):
                    best = key
        return best

    def from_item(item: dict, basis: str, inherit: bool = False) -> list:
        wave = PAGE_WAVE_OVERRIDES.get(item["id"], item["wave"])
        to = item.get("to") if not inherit else None
        ships = item.get("ships") if not inherit else None
        group = item["reason"]
        if wave == "RT":
            dest = (item.get("to") or "").split(" (")[0].split("?")[0]
            dest_item = pages.get(norm(dest)) if dest.startswith("/") else None
            group = dest_item["reason"] if dest_item and dest_item["reason"] != "PG-RT" else "PG-RT"
            if to:
                to = to.split(" (")[0]
        return [wave, group, basis, to, ships]

    entries: dict = {}
    unresolved: list = []
    resolved_bp: dict = collections.defaultdict(collections.Counter)

    def put(key: str, entry: list, endpoint: str) -> None:
        old = entries.get(key)
        if old is not None and old[0] != entry[0]:
            # two rules read alike but sit in different waves: the later wave closes both
            order = {"A": 0, "W0": 0, "W1": 1, "W2": 2, "W3": 3, "W4": 4, "W5": 5, "W6": 0, "EA": 7, "RT": 8, "X": 9}
            if order.get(old[0], 9) >= order.get(entry[0], 9):
                return
        entries[key] = entry

    for rule, endpoint in rules:
        key = norm(rule)
        blueprint = endpoint.split(".")[0]
        if endpoint in ENDPOINT_OVERRIDES:
            wave, group, _why = ENDPOINT_OVERRIDES[endpoint]
            entries[f"endpoint:{endpoint}"] = [wave, group, "override", None, None]
            continue
        item = pages.get(key)
        if item is not None:
            put(key, from_item(item, "page"), endpoint)
            resolved_bp[blueprint][item["wave"]] += 1
            continue
        hit = None
        for prefix, wave, group, _why in PREFIX_OVERRIDES:
            if prefix.endswith("$"):
                matched = rule == prefix[:-1]
            else:
                matched = rule == prefix or rule.startswith(prefix.rstrip("/") + "/")
            if matched:
                hit = (wave, group)
                break
        if hit:
            put(key, [hit[0], hit[1], "override", None, None], endpoint)
            resolved_bp[blueprint][hit[0]] += 1
            continue
        best = longest_prefix(key)
        basis = "prefix"
        if best is None:
            stripped = re.sub(r"^/api(/v\d+)?", "", key) or "/"
            if stripped != key:
                best = longest_prefix(stripped)
                basis = "api-prefix"
        if best is not None:
            put(key, from_item(pages[best], basis, inherit=True), endpoint)
            resolved_bp[blueprint][pages[best]["wave"]] += 1
            continue
        unresolved.append((rule, endpoint))

    for rule, endpoint in unresolved:
        key = norm(rule)
        blueprint = endpoint.split(".")[0]
        waves = set(resolved_bp.get(blueprint, {}))
        if len(waves) == 1:
            wave = next(iter(waves))
            put(key, [wave, f"BP:{blueprint}", "blueprint", None, None], endpoint)
        else:
            put(key, ["X", f"BP:{blueprint}", "unplanned", None, None], endpoint)
        groups.setdefault(f"BP:{blueprint}", {
            "title": _humanise(blueprint),
            "blurb": "Entelim is building this. Join the waiting list and we will tell you the day it opens.",
        })

    for code, (title, blurb) in GROUP_TEXT_OVERRIDES.items():
        if code in groups:
            if title is not None:
                groups[code]["title"] = title
            if blurb is not None:
                groups[code]["blurb"] = blurb
    for code in groups:
        if code.startswith("BP:"):
            groups[code]["title"] = "This capability"

    # a group a rule uses must exist
    for entry in entries.values():
        groups.setdefault(entry[1], {"title": "This capability", "blurb": ""})

    return {
        "version": 1,
        "source": "WAVE-PLAN-items-pages-v2 (copied to wave_plan_pages.json), built by scripts/build_wave_map.py",
        "pass_waves": list(PASS_WAVES),
        "groups": {k: groups[k] for k in sorted(groups)},
        "rules": {k: entries[k] for k in sorted(entries)},
    }


def dump(doc: dict) -> str:
    """The map as text: one rule per line, so a change to one rule is one line in a diff."""
    head = {k: v for k, v in doc.items() if k != "rules"}
    lines = ["{"]
    for key in sorted(head):
        lines.append(json.dumps(key) + ":" + json.dumps(head[key], sort_keys=True, ensure_ascii=False, separators=(",", ":")) + ",")
    lines.append('"rules":{')
    rules = doc["rules"]
    keys = sorted(rules)
    for i, key in enumerate(keys):
        comma = "," if i < len(keys) - 1 else ""
        lines.append(json.dumps(key, ensure_ascii=False) + ":" + json.dumps(rules[key], ensure_ascii=False, separators=(",", ":")) + comma)
    lines.append("}")
    lines.append("}")
    return "\n".join(lines) + "\n"
