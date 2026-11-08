# -*- coding: utf-8 -*-
"""What each module picture says it shows, worked out from the screen the picture was taken of.

A description is written when the picture is made, from the text and elements in the cropped frame, and is
kept beside the picture in ``captures.json`` with the figures it was built from. Nothing here states a figure
of its own: a figure the frame does not show makes the picture refuse, so a description cannot outlive the data
it describes. The page reads the description back from ``captures.json``.
"""
from __future__ import annotations

import re

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
        "seventeen eighteen nineteen").split()
TENS = "x x twenty thirty forty fifty sixty seventy eighty ninety".split()


class CannotDescribe(RuntimeError):
    """The frame does not show a figure its description needs."""


def number_words(number: int) -> str:
    """0 to 99 in words, as the descriptions have always written them; larger numbers stay as digits."""
    if number < 20:
        return ONES[number]
    if number < 100:
        return TENS[number // 10] + ("-" + ONES[number % 10] if number % 10 else "")
    return str(number)


def window_words(days: int) -> str:
    return {180: "one hundred and eighty"}.get(days) or number_words(days)


def plural(count: int, noun: str) -> str:
    return "%s %s%s" % (number_words(count), noun, "" if count == 1 else "s")


def _find(text: str, pattern: str, what: str) -> re.Match:
    found = re.search(pattern, text)
    if not found:
        raise CannotDescribe("the frame does not show %s" % what)
    return found


def _per_cent(rate: str) -> str:
    return rate[:-2] if rate.endswith(".0") else rate


def _applications(variant, target, text):
    if variant == "phone":
        cards = target.locator("[data-app-id]").count()
        return ("The Applications list as cards, %s each with its status, owner, vendor, capability and a ring "
                "showing how complete its record is." % plural(cards, "system"), {"systems": cards})
    rows = target.locator("tbody tr").count()
    facts = {"systems": rows}
    line = ""
    complete = re.search(r"All (\d+) have an owner\D+All \1 have a capability\D+All \1 have a vendor", text)
    if complete:
        line = ", under a line saying every system has an owner, a capability and a vendor"
    return ("The Applications list showing %s with their type, lifecycle stage, owner, how complete each record is "
            "and the capability each supports%s." % (plural(rows, "system"), line), facts)


def _arb(variant, target, text):
    total = int(_find(text, r"Total reviews\s+(\d+)", "the total reviews").group(1))
    pending = int(_find(text, r"Pending\s+(\d+)", "the pending reviews").group(1))
    rate = _find(text, r"Approval rate\s+([\d.]+)%", "the approval rate").group(1)
    outside = _find(text, r"(\d+) of the (\d+) pending reviews? (?:was|were) raised before the queue",
                    "the pending reviews outside the queue")
    queue = target.locator("ul li").count() if variant == "phone" else target.locator("tbody tr").count()
    facts = {"total": total, "pending": pending, "approval_rate": float(rate), "outside_queue": int(outside.group(1)),
             "in_queue": queue}
    if int(outside.group(2)) != pending:
        raise CannotDescribe("the board's line counts %s pending reviews but its figure says %s"
                             % (outside.group(2), pending))
    line = "a line saying %s of the %s pending reviews are in the full review ledger" % (
        number_words(facts["outside_queue"]), number_words(pending))
    if variant == "phone":
        return ("The Architecture Review Board's four figures, %s, and the %s waiting for a decision, one card each."
                % (line, plural(queue, "review")), facts)
    return ("The Architecture Review Board showing %s, %s of them pending, an approval rate of %s per cent and the "
            "average cycle time, %s, and %s waiting for a decision, each with its subject, status and an Open "
            "review button." % (plural(total, "review"), number_words(pending), _per_cent(rate), line,
                                plural(queue, "review")), facts)


def _architecture_model(variant, target, text):
    found = _find(text, r"(\d+) elements?, (\d+) relationships?", "the count of elements and relationships")
    elements, relationships = int(found.group(1)), int(found.group(2))
    drawn = target.locator("g.joint-link").count()
    if drawn != relationships:
        raise CannotDescribe("the header counts %d relationships but %d lines are drawn" % (relationships, drawn))
    facts = {"elements": elements, "relationships": relationships}
    if variant == "phone":
        return ("The layered ArchiMate view on a narrow screen: %s stacked one above another in strategy, business, "
                "application and technology bands, with the %s between them drawn as lines."
                % (plural(elements, "element"), plural(relationships, "relationship")), facts)
    return ("A layered ArchiMate view of %s drawn in strategy, business, application and technology bands, with the "
            "%s between them drawn as arrows." % (plural(elements, "element"), plural(relationships, "relationship")),
            facts)


def _business_case(variant, target, text):
    title = target.locator("h1").first.inner_text().strip()
    if not title:
        raise CannotDescribe("the frame does not show the business case's title")
    return ("The business case to %s, with its options considered, the recommendation, and the capital cost, annual "
            "running cost, three-year cost, annual benefit, return and payback." % (title[0].lower() + title[1:]),
            {"title": title})


def _canvas(variant, target, text):
    if variant == "phone":
        items = target.locator("li").count()
        heading = _find(text, r"(Value Propositions)", "the value propositions heading").group(1)
        return ("One block of the business model canvas, the %s, listing the %s the demonstration company offers."
                % (heading.lower(), plural(items, "proposition")), {"propositions": items})
    blocks = target.locator("[id^='box-']").count()
    return ("A business model canvas with %s blocks filled in for the demonstration company: key partners, "
            "activities and resources, value propositions, customer relationships, channels and segments, cost "
            "structure and revenue streams." % number_words(blocks) if blocks == 9 else
            "A business model canvas with %s blocks filled in for the demonstration company." % number_words(blocks),
            {"blocks": blocks})


def _compliance(variant, target, text):
    frameworks = int(_find(text, r"Active frameworks\s+(\d+)", "the active frameworks").group(1))
    controls = int(_find(text, r"Controls\s+(\d+)", "the controls").group(1))
    mappings = int(_find(text, r"Application control mappings\s+(\d+)", "the control mappings").group(1))
    facts = {"frameworks": frameworks, "controls": controls, "mappings": mappings}
    if variant == "phone":
        return ("The compliance figures: %s, %s and %s application control mappings counted by status."
                % (plural(frameworks, "active framework"), plural(controls, "control"), number_words(mappings)), facts)
    rows = target.locator("tbody tr:visible").count()
    facts["findings_shown"] = rows
    return ("The compliance dashboard showing %s, %s and %s application control mappings by status, above the first "
            "%s not yet in place with the application and capability each belongs to."
            % (plural(frameworks, "active framework"), plural(controls, "control"), number_words(mappings),
               plural(rows, "control")), facts)


def _portfolio(variant, target, text):
    initiatives = int(_find(text, r"Initiatives\s+(\d+)", "the initiatives").group(1))
    health = _find(text, r"(\d+) red\s+(\d+) amber\s+(\d+) green", "the health count")
    facts = {"initiatives": initiatives, "red": int(health.group(1)), "amber": int(health.group(2)),
             "green": int(health.group(3))}
    if variant == "phone":
        line = ("the line saying every initiative serves an objective" if "Every initiative serves an objective" in text
                else "the count of initiatives serving no objective")
        return ("The portfolio figures: %s, the approved budget, the spend to date and a health count of red, amber and "
                "green, with %s." % (plural(initiatives, "initiative"), line), facts)
    rows = target.locator("tbody tr").count()
    if rows != initiatives:
        raise CannotDescribe("the figure counts %d initiatives but the table lists %d" % (initiatives, rows))
    return ("The portfolio dashboard showing %s, their approved budget and spend, a health count of red, amber and "
            "green, and a table with each initiative's phase, budget, spend, variance, completion, benefits and "
            "sponsor." % plural(initiatives, "initiative"), facts)


def _procurement(variant, target, text):
    windows = [int(d) for d in re.findall(r"Renew within (\d+) days", text)]
    counts = [int(c) for c in re.findall(r"(\d+)\s+Renew within \d+ days", text)]
    if len(windows) != 3 or len(counts) != 3:
        raise CannotDescribe("the frame does not show the three renewal windows")
    rows = target.locator("[data-testid=renewal-row]").count()
    facts = {"windows": windows, "contracts_in_windows": counts, "listed": rows}
    named = ", ".join("%s within %s" % (number_words(c), window_words(d)) for c, d in zip(counts[:-1], windows[:-1]))
    tiles = "%s and %s within %s days" % (named, number_words(counts[-1]), window_words(windows[-1]))
    if variant == "phone":
        return ("The renewals screen showing how many contracts renew in each window (%s), and the %s coming up "
                "within %s days." % (tiles, plural(rows, "contract"), window_words(windows[0])), facts)
    return ("The renewals screen showing how many contracts renew in each window (%s), and the %s due within %s days "
            "with its vendor, annual cost and days to renewal." % (tiles, plural(rows, "contract"),
                                                                    window_words(windows[0])), facts)


def _risk_register(variant, target, text):
    top = int(_find(text, r"Top risks \((\d+)\)", "the count of top risks").group(1))
    placed = sum(int(c) for c in target.locator("[data-testid=risk-cell]").evaluate_all(
        "(cells) => cells.map((cell) => cell.dataset.count || '0')"))
    if placed != top:
        raise CannotDescribe("the list counts %d risks and the matrix places %d" % (top, placed))
    facts = {"risks": top}
    if variant == "phone":
        return ("The risk register on a phone: a five-by-five matrix counting the register's %s by probability and "
                "impact, above the top risks ranked by the elements each reaches." % plural(top, "risk"), facts)
    return ("The risk register showing the top risks ranked by the elements each reaches, with their status and what "
            "they touch, beside a five-by-five matrix counting the register's %s by probability and impact."
            % plural(top, "risk"), facts)


def _vendors(variant, target, text):
    line = _find(text, r"(\d+) vendors? supply (\d+) of your (\d+) systems?\. (?:(\d+) renew|No vendor renews) within (\d+) days",
                 "the line counting vendors, systems and renewals")
    rows = target.locator("tbody tr").count()
    facts = {"vendors": int(line.group(1)), "systems_supplied": int(line.group(2)), "systems": int(line.group(3)),
             "renewing": int(line.group(4) or 0), "window_days": int(line.group(5)), "listed": rows}
    supplied = ("all %s" % plural(facts["systems"], "system") if facts["systems_supplied"] == facts["systems"]
                else "%s of the organisation's %s" % (number_words(facts["systems_supplied"]),
                                                      plural(facts["systems"], "system")))
    return ("The vendor register: a line saying %s supply %s and %s renew within %s "
            "days, above %s and the systems each supplies." % (
                plural(facts["vendors"], "vendor"), supplied,
                number_words(facts["renewing"]) if facts["renewing"] else "no vendors", number_words(facts["window_days"]),
                plural(rows, "vendor")), facts)


DESCRIBERS = {
    "applications": _applications,
    "arb": _arb,
    "architecture-model": _architecture_model,
    "business-case": _business_case,
    "business-model-canvas": _canvas,
    "compliance-frameworks": _compliance,
    "portfolio": _portfolio,
    "procurement": _procurement,
    "risk-register": _risk_register,
    "vendors": _vendors,
}


def describe(slug: str, variant: str, target, text: str):
    """(description, figures it was built from) for one picture of ``slug``, read from its cropped frame."""
    return DESCRIBERS[slug](variant, target, text)
