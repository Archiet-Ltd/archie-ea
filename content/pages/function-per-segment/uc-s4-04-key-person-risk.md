---
wave: 2
page_family: function-per-segment
use_case_id: UC-S4-04
segment_id: S4
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/key-person-risk
icon: user-cog
---

# Which of my people is a single point of failure?

**For owner-operators and managing directors.**

Entelim shows you who is recorded as accountable for each system, one system at a time. On Ask, choose "Who's accountable for ___, and can they take on more?" and pick a system. You get a card for every distinct recorded owner. The Org Chart and the Enterprise RACI matrix show the wider picture of who answers for what. Entelim does not total up how much each person holds, so a name that keeps turning up is something you spot, not something it ranks for you.

## The question

A key person resigns, and suddenly nobody can list what they actually held. That gap shows up at the worst moment, usually after they have gone. The question is not who is on the org chart. It is which systems this person is the only name against, and what you would have to cover by Friday.

## What Entelim answers

On the **Ask** page, open the accountability question and pick a system. Each distinct recorded owner appears as a card.

The cards cover the system itself and the things it affects downstream. Each distinct owner appears once. Under the cards, every answer carries the line "Capacity and availability data is not yet connected." When no ownership is recorded for the system or anything it affects, the page says "Ownership data is not yet connected." It does not name anyone on your behalf.

The **Org Chart** (Organization, then Org Chart) draws departments, teams, business units and partners from the actors and links already in your model. The **Enterprise RACI** matrix puts stakeholders down one side and capabilities across the other. Click a cell to cycle it through R, A, C and I (Responsible, Accountable, Consulted, Informed). A cell nobody has set stays blank, so an empty column shows a capability nobody has assigned.

The person themselves can open **My Applications** to see the applications they are recorded as owning.

## Who asks it

An owner-operator or managing director who has just had a resignation, or who wants to know before the next one how much rests on a single person. The same question comes from an investor asking how dependent the business is on its founders.

## How to get your answer

1. Record owners against your applications and capabilities, with an ownership type for each.
2. Open **Ask**, choose the accountability question and pick the system the person looks after.
3. Read the cards. If the person is the only card, and there is no Backup, you have found a dependency.
4. Repeat for the systems that matter most, and note any name that keeps appearing.
5. Open the **Enterprise RACI** matrix to see the same names against capabilities.

## Doing this without Entelim

You ask each manager what their people hold and write it down. It takes a week and it ages the day you finish. It also misses what nobody thought to mention, which is usually the thing that breaks.

## Limits

- Ask answers one system at a time. It does not count the systems each person owns across the estate, and it does not rank people.
- There is no capacity or availability data. "Can they take on more?" is not answered.
- It reads the owners recorded in Entelim. It does not guess one for a system with none.
- The Org Chart draws only the structure in your model. With no composition or aggregation links between actors, it shows a flat list grouped by actor type.
- The matrix shows what has been set. Blank means nobody has set it.

## Frequently asked questions

### Can Entelim tell me who is a single point of failure?

It shows who is recorded as accountable for each system and whether a Backup is recorded. It does not rank people across the estate.

### Does it know how busy someone is?

No. Capacity and availability are not connected, and the Ask page says so on every answer.

### What if nobody is recorded as owner?

The page says ownership data is not yet connected. That is the gap to close first.

### Where do the owners come from?

From the ownership records you add against applications and capabilities in Entelim.

## Related questions and modules

- [Org Chart & RACI](/modules/org-chart)
- [My Applications](/modules/my-applications)
- [Which systems have no owner, and which owner is a single point of failure?](/use-cases/systems-with-no-owner)
- [What happens to my business if this person, supplier or system underperforms?](/use-cases/what-happens-if-a-supplier-fails)
