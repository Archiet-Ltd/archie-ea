---
wave: 1
page_family: function-per-segment
use_case_id: UC-S2-02
segment_id: S2
state: in_review
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/systems-with-no-owner
icon: user-x
---

# Which systems have no owner, and which owner is a single point of failure?

**For scale-up CTOs and VPs of Engineering.**

Pick any system on Ask and ask who is accountable for it. Entelim lists every recorded owner of that
system, and of the systems it affects downstream, one card per distinct owner. If nothing is recorded
anywhere in that chain, the answer says so instead of naming someone. The
Org Chart and the Enterprise RACI matrix sit next to it for the wider picture of who answers for
what.

## The question

A SOC 2 or ISO 27001 audit asks for an asset inventory with owners attached. Half the systems in a
typical scale-up either have no recorded owner, or one person's name is on so many of them that the
person is a risk in their own right. The question is not "who is on the org chart" but "for this
system, who is on the hook, and is anyone?"

## What Entelim answers

On the **Ask** page, choose "Who's accountable for ___, and can they take on more?", pick a system
with the picker, and the answer appears as one card per distinct owner.

The list starts with the system's own owners and then adds the owners of the elements it affects,
walking up to three steps downstream. Each distinct owner appears once. When neither the system nor
anything it affects has an owner, the page reads "Ownership data is not yet connected" rather than inventing one.

Two other screens cover the gap side directly. **Capabilities with no owner** lists every capability in
your organisation with nobody recorded as its owner, with a user search beside each row so you can
assign one on the spot; it is on the CTO and portfolio manager sidebars, and when every capability has
an owner it says so. The **Org Chart** (Organization, then Org Chart) draws the structure of your
departments, teams, business units and partners from the actors and composition and aggregation links
already in your model, and the **Enterprise RACI** matrix (Organization, then Enterprise RACI) puts
stakeholders — actors, roles or users — down one side and your capabilities across the other. A click
on a cell cycles it through Responsible, Accountable, Consulted and Informed, and a cell nobody has
set stays blank.

## Who asks it

A CTO or VP of Engineering preparing for a SOC 2 or ISO 27001 audit, or answering an investor's
due-diligence request for "your systems and who owns them". The same question comes from a head of
operations who has just lost a long-serving engineer and wants to know what that person was holding.

## How to find your gaps

1. Open **Ask** and choose the accountability question.
2. Pick a system. Read the cards: each is an owner recorded against the system or something it affects.
3. If the page reads "Ownership data is not yet connected" for that system and everything it affects, you have found a
   gap. If cards appear, check whether they belong to the system itself or only to something downstream. Open the application, go to its Ownership section, search for a user, choose Primary, Backup,
   Technical or Business, and click Add owner. Then ask again.
4. Run the same question for the systems you most depend on, and note any name that keeps appearing
   across them.
5. Open **Capabilities with no owner** for the same check at capability level, and **Organization**
   for the Org Chart and the RACI matrix behind those names.

## Doing this without Entelim

Export the asset list from wherever it lives, add an owner column by hand, and chase each blank with
whoever might know. It works for one audit and goes stale the week after, because nothing links the
owner column to the dependencies that make an owner matter.

## Limits

- Ask reads the owners that are recorded. It does not guess an owner for a system that has none.
- It lists owners one system at a time. It does not yet total up how many systems each person owns, so
  spotting an over-loaded owner means noticing the same name across several answers.
- Capacity and availability data is not connected. The page says so on every answer, and "can they
  take on more?" is not answered.
- The Org Chart draws only the hierarchy that exists in your model. With no composition or aggregation
  links between actors, it falls back to a flat list grouped by actor type.

## Frequently asked questions

### Does Entelim assign an owner when none is recorded?

No. When neither the system nor anything it affects has an owner, the page reads "Ownership data is not yet connected" instead of naming someone. Be aware that a system with no owner of its own can still show cards for owners of things downstream of it, so a card is not proof that the system itself is owned.
Naming someone by guesswork would hide exactly the gap an auditor asks about.

### Can it tell me which person is a single point of failure?

It shows you the owners of a system and of what it affects, so a name that appears again and again is
visible. It does not count or rank owners across the whole estate, and it holds no data on anyone's
workload or availability.

### Where do owners come from?

From the ownership records you add to your applications and capabilities in Entelim, each with an
ownership type. The legacy owner text
fields on an application are read-only and are not what Ask reads.

### What is the difference between the Org Chart and the RACI matrix?

The Org Chart shows how the organisation is structured. The RACI matrix shows who is Responsible,
Accountable, Consulted or Informed for each capability. They answer related but different questions,
which is why both sit under Organization.

## Related questions and modules

- [Org Chart & RACI](/modules/org-chart)
- [Which of my people is a single point of failure?](/use-cases/key-person-risk)
- [What breaks if this service fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
