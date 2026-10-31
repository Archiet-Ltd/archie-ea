---
wave: 5
page_family: role
role_group: architecture
state: on_main
capture_status: live
cta: plans
description: "Chief architect software from Entelim: architecture health, review board throughput, traceability and the technology radar in one model. Start free, see pricing today."
source: app/utils/role_access.py (ROLE_ENTERPRISE_ARCHITECT, SIDEBAR_ZONES, _MY_WORK_LINKS),
  app/modules/dashboard/v2/routes/dashboard_views.py (health_scorecard),
  app/modules/architecture/routes/{arb_routes,architecture_decision_routes}.py,
  app/modules/strategic (capability_health, traceability_matrix), app/modules/tech_radar,
  content/pages/modules/{arb,roadmaps,architecture-model,digital-twin}.md,
  app/services/billing_plans.py, read 18 October 2026 against the running product
---

# Chief architect software for an architecture function you can account for

Chief architect software has one job the enterprise architect's tools do not: showing whether the
architecture function itself is working. How much of the estate is modelled and connected, how long a
change waits for the review board and what it decided, which technology is adopted and which is on hold,
and where the model still has gaps. Entelim reads all of it from the same model the architects work in,
so the answer to "how is architecture doing" is a screen, not a slide assembled for the meeting. A chief
architect can start on the free Community plan.

**[Start free](/register)** or **[see pricing](/pricing)**.

## What a chief architect does

A chief architect is accountable for the architecture function: the standards the estate is built to, the
decisions that have been taken and why, and the review process every significant change goes through. Where
an [enterprise architect](/roles/enterprise-architects) works the model itself and a [solution
architect](/roles/solution-architects) takes one design from idea to approval, the chief architect answers
upward and across: to the CTO and the business for what architecture has delivered, and to the architects
for what good looks like. The job gets hard the moment someone asks for evidence — how many reviews are
waiting, how often the board approves, which part of the model nobody has connected — and the honest answer
lives in five different places.

## The questions you get asked

- [Is the review board keeping up, and what has it decided?](/use-cases/architecture-review-board)
- [What breaks if this platform fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Which value streams are at risk because the capabilities under them are immature?](/use-cases/value-streams-at-risk)

## The screens you will use

- **The health scorecard**, one place for the architecture's own measures: a health score weighted from risk
  posture, capability coverage and governance, the review pipeline by state, open risks and ArchiMate
  elements counted by layer. Where something has not been measured, the scorecard says so rather than
  showing a number.
- **The review board**, whose dashboard shows total reviews, how many are pending, the approval rate and the
  average cycle time, with every review listed by number, type, risk and state — see [Architecture Review
  Board](/modules/arb).
- **Architecture decisions**, the record of decisions taken: context, the decision, consequences and the
  alternatives considered, a status of proposed, accepted, deprecated or superseded, a review date, and the
  ArchiMate elements each decision affects.
- **The traceability matrix**, with a coverage and gap analysis showing how much of the model is connected
  from drivers through goals, requirements and capabilities down to applications — and how many elements are
  orphaned.
- **Capability health**, scoring each business capability on maturity, coverage and risk, with domain-level
  health, at-risk hotspots and the coverage and maturity shortfalls behind them.
- **The tech radar**, where each device, system software and technology service already in the model is
  classed adopt, trial, assess or hold, with a rationale, review date and requesting initiative recorded.
- **The AI chat**, for a plain question about one element and what it connects to; any element then opens in
  the Composer — see [Digital twin of your organisation](/modules/digital-twin).
- **Roadmaps**, with plateaus, the gap register and work packages on one timeline — see
  [Roadmaps](/modules/roadmaps).

## A week with Entelim

1. Open the health scorecard for the state of the model and the review pipeline.
2. Open the review board dashboard: what is pending, what was decided, how long it took.
3. Check the traceability matrix's coverage figures for the part of the model that is not yet connected, and
   capability health for where the shortfalls sit.
4. Record the decisions the board took in the architecture decisions register, linked to the elements they
   concern.
5. Classify any technology that has moved on the tech radar, with the reason and a review date.

## What you hand over

A health scorecard that reads from the live model. The review board's volume, approval rate and cycle time.
A register of architecture decisions with their status. A technology radar with a rationale and review date
on each classification. A coverage report from the traceability matrix showing what is connected and what is
not.

## Works with

An imported Archi or ArchiMate Open Exchange model brings the existing model in, so the scorecards read
from the work already done — see [import your model](/use-cases/import-archimate-model). [Connect an AI
assistant](/connectors/ai-assistants), read-only and scoped to your organisation's data, to ask the same
questions from a tool you already use.

## Limits

Every figure is read from what is recorded in the model: the health scorecard and the coverage analysis are
only as complete as the model behind them, and a measure with nothing recorded shows as not measured rather
than as zero. The review board's decision is made by the people who sit on it; Entelim holds the evidence
and the record, not the judgement.

## Frequently asked questions

### How is this different from the enterprise architect tools?

The enterprise architect works the model: elements, relationships, impact, roadmaps. A chief architect uses
the same model to account for the function — review board throughput, decisions on record, technology
standards and how much of the estate is connected. See [enterprise architect
tools](/roles/enterprise-architects) for the modelling side.

### Does it follow TOGAF and ArchiMate?

Yes. The model is built on ArchiMate 3.2 across the business, application, technology and motivation
layers, and design work is tracked through the stages of TOGAF's Architecture Development Method.

### Can the CTO see these numbers without a paid seat?

Yes. Anyone who only reads a dashboard or asks a question in the AI chat is free on every plan. An editor seat is
for people who change the model: recording a decision, classifying technology, taking a review forward.

### Who needs a paid seat?

Editors on the Team plan, priced at $29 per editor a month. Community covers one organisation of up to three
people at no cost, and Enterprise, from $24,000 a year, adds SAML sign-in and unlimited editors.

## Start free

[Start free](/register) on the Community plan for one organisation of up to three people. A larger
architecture team moves to Team, priced per editor, where people who only read and ask stay free. [See full
pricing](/pricing).
