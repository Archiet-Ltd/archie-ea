---
wave: 2
page_family: function-per-segment
use_case_id: UC-S2-01
segment_id: S2
state: on_main
source: docs/artifacts/icp-use-case-register.yml (v3, 2026-09-23)
capture_status: awaiting_capture
url_slug: /use-cases/what-breaks-and-who-gets-called
icon: phone-call
---

# What breaks if this service or platform fails, and who gets called?

**For scale-up CTOs and VPs of Engineering, 50-500 people.**

Ask the AI chat in your own words: "what breaks if the payments service fails, and who gets called?" You get everything connected to it, closest first, up to three steps away, each with its owner and the links behind it. A line with no recorded owner says so rather than guessing one.

## The question

An outage happens. The first ten minutes are spent figuring out who owns the failing component
before anyone can even start fixing it. That gap — not the outage itself — is usually the part
that hurts.

## What Entelim answers

The chat answers with a card for the answer and one card for each connection, closest first. An explicit connection, one somebody recorded, comes before a worked-out one at the same distance. Each connection card shows:

- the **name** of the connected element, which opens it in the Composer
- its **type** and its **health**, or "Health not recorded"
- the **owner**: the names of the people recorded as owners, or a plain "No owner recorded"
- the **source** of the connection: the repository, file and commit it was read from, or "Worked out from the model"
- **The links behind this** on a connection Entelim worked out from others, with the chain of links, who drew each one and when, and any decisions recorded about those elements
- **Where this link came from** on a recorded connection, with a link to its relationship record

If nothing is recorded as depending on the system, the answer says "Nothing is recorded as depending on this yet". If your indirect connections have never been worked out, it says "We haven't worked out the indirect connections for your model yet" and offers **Work them out now**. If some may be out of date, a notice says so and offers the same button. **Open this answer** opens the answer at its own address.

The owner on each line belongs to that connected element, not to the system you picked. To find who to call for the failing system itself, ask the chat "who is accountable for the payments service?" and name the same system. It lists the recorded owners of that system and of what it affects.

The **Impact Analysis** page is the screen for planned change. Pick an element, choose a change type (Full Replacement, Upgrade / Version Change, Decommission / Retirement or Minor Modification) and click **Analyze Impact**. The report gives a severity, counts of Capabilities, Processes and TCO at Risk, and a table of affected elements with Name, Type, Application, Owner, Health and Relationship.

## Who asks it

The CTO or head of platform who is asked "what depends on this?" before a migration, or during an incident review.

## How to get your answer

1. Connect GitHub or import a file. Entelim reads your repositories and proposes the services, dependencies and owners it finds, and you approve them. A model you already keep comes in as an Open Exchange, Archi or spreadsheet file.
2. Open the **AI chat** from the sidebar and ask what breaks if the service fails.
3. Read the cards from the top. Open **The links behind this** on any worked-out connection before you rely on it.
4. Ask who is accountable for the same system to see who owns it.

## Doing this without Entelim

A dependency diagram and a service catalogue spreadsheet. In an incident the diagram is out of date and the spreadsheet names someone who has moved. The cost is the time spent checking.

## Limits

- Owners come from the ownership records on applications and capabilities. Other element types have no owner record, so their lines read "Not recorded".
- The answer follows relationships in one direction, away from the system you pick, to three steps.
- Health is shown as recorded; the answer does not measure it.
- Worked-out connections need to have been calculated. Until then you see only the connections somebody recorded.
- The chat does not page anyone or open an incident. It tells you who is recorded as responsible.

## Frequently asked questions

### What if nobody owns the system that failed?

The accountability question does not name anyone for an owner that is not recorded, but it also lists owners of the systems downstream, so cards can still appear. Open the system's own Ownership section to see whether it has an owner itself.

### Does it read live monitoring?

No. It reads the model you keep in Entelim. Incident and monitoring feeds are not connected to this answer.

### How do I know a worked-out connection is right?

Open **The links behind this**. It lists the links the connection came from, who drew them and when. If one of those is wrong, correct it in the model and work the connections out again.

## Related questions and modules

- [Digital Twin](/modules/digital-twin)
- [Applications](/modules/applications)
- [Which systems have no owner, and which owner is a single point of failure?](/use-cases/systems-with-no-owner)
- [If our one cloud platform or payment provider goes down, what stops?](/use-cases/single-point-of-failure)
- [Which risks sit on our revenue-critical path?](/use-cases/risk-blast-radius)
