---
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

On the Ask page, choose "What breaks if ___ fails or is under-resourced?", pick the service or platform, and Entelim lists everything connected to it, up to three steps away. Each line names its owner, or says "Not recorded" rather than guessing one. Lines Entelim worked out from other connections are labelled as such, and a Why? button shows the links behind them.

## The question

An outage happens. The first ten minutes are spent figuring out who owns the failing component
before anyone can even start fixing it. That gap — not the outage itself — is usually the part
that hurts.

## What Entelim answers

Pick a system with the search box ("Type a team, service or system name") and the **Results** list fills in, closest connections first. An explicit connection, one somebody recorded, comes before a worked-out one at the same distance. Each line shows:

- the **name** of the connected element
- **Owner**: the names of the people recorded as owners, or a "Not recorded" badge
- **Health**, which reads "Not recorded yet", because the answer carries no health signal
- "We worked this out — nobody drew it directly." on a connection Entelim derived from others
- a **Why?** button, which opens a panel with the sentence in plain terms, the chain of links it came from, who drew each link and when, and any decisions recorded about those elements
- a **View on twin map** link, which opens the map centred on that element
- a **Full detail** control with the ArchiMate type, layer and depth and, for worked-out connections, the rule and confidence

If nothing is recorded as depending on the system, the page says "Nothing is recorded as depending on this yet". If your indirect connections have never been calculated, it says "We haven't worked out the indirect connections for your model yet" and offers **Work them out now**. If some may be out of date, a notice says so and offers the same button. The **Indirect connections** panel at the foot of Ask shows when they were last worked out and how many may be out of date.

The owner on each line belongs to that connected element, not to the system you picked. To find who to call for the failing system itself, open the question "Who's accountable for ___, and can they take on more?" and pick the same system. It lists the recorded owners of that system and of what it affects, one card per distinct owner.

The **Impact Analysis** page, under the Strategy Layer, is a separate tool for planned change. Pick an element, choose a change type (Full Replacement, Upgrade / Version Change, Decommission / Retirement or Minor Modification) and click **Analyze Impact**. The report gives a severity, counts of Capabilities, Processes and TCO at Risk, and a table of affected elements with Name, Type, Application, Owner, Health and Relationship.

## Who asks it

The CTO or head of platform who is asked "what depends on this?" before a migration, or during an incident review.

## How to get your answer

1. Record your applications and platforms, the relationships between them, and their owners. Import them, or add them by hand in Applications.
2. Open **Ask a question** from the sidebar and choose the first question.
3. Search for the service and pick it from the list.
4. Read the lines from the top. Open **Why?** on any worked-out line before you rely on it.
5. Pick the same system under the accountability question to see who owns it.

## Doing this without Entelim

A dependency diagram and a service catalogue spreadsheet. In an incident the diagram is out of date and the spreadsheet names someone who has moved. The cost is the time spent checking.

## Limits

- Owners come from the ownership records on applications and capabilities. Other element types have no owner record, so their lines read "Not recorded".
- The answer follows relationships in one direction, away from the system you pick, to three steps.
- Health is not measured by this screen.
- Worked-out connections need to have been calculated. Until then you see only the connections somebody recorded.
- This screen does not page anyone or open an incident. It tells you who is recorded as responsible.

## Frequently asked questions

### What if nobody owns the system that failed?

The accountability question does not name anyone for an owner that is not recorded, but it also lists owners of the systems downstream, so cards can still appear. Open the system's own Ownership section to see whether it has an owner itself.

### Does it read live monitoring?

No. It reads the model you keep in Entelim. Incident and monitoring feeds are not connected to this answer.

### How do I know a worked-out connection is right?

Open **Why?**. It lists the links the connection came from, who drew them and when. If one of those is wrong, correct it in the model and work the connections out again.

## Related questions and modules

- [Digital Twin](/modules/digital-twin)
- [Applications](/modules/applications)
- [Which systems have no owner, and which owner is a single point of failure?](/use-cases/systems-with-no-owner)
- [If our one cloud platform or payment provider goes down, what stops?](/use-cases/single-point-of-failure)
- [Which risks sit on our revenue-critical path?](/use-cases/risk-blast-radius)
