---
wave: 1
page_family: role
role_group: technology-leadership
state: on_main
capture_status: live
cta: plans
description: "Entelim's CIO dashboard for CTOs: a scorecard, rationalisation, roadmaps and the review board built on one shared model. Start free, see pricing today."
source: app/utils/role_access.py (ROLE_CTO, SIDEBAR_ZONES, _MY_WORK_LINKS, _BOARD_ROLES),
  app/modules/architecture/services/cto_scorecard_service.py,
  app/modules/dashboard/v2/routes/dashboard_views.py (health_scorecard),
  app/modules/applications/routes/coverage_routes.py, app/modules/capabilities/routes/ownership_routes.py,
  content/pages/modules/{rationalization,roadmaps,business-case}.md,
  content/pages/function-per-segment/{uc-s2-03-duplicate-spend,uc-s2-08-risk-blast-radius,
  uc-s3-05-business-case-for-the-cio}.md, content/pages/site/architecture-health-check.md,
  app/services/billing_plans.py, read 11 October 2026
---

# CIO dashboard software for CTOs and technology leaders

A CIO dashboard brings the state of the whole technology estate onto one screen: what you run, what
it costs, what's overdue for a decision and what two teams are quietly paying for twice. Entelim
builds that dashboard from a single shared model, with a CTO scorecard, a health scorecard and an
application rationalisation view that score the estate the same way every time you open it. A CTO or
CIO can start on the free Community plan and see a first picture as soon as the inventory is in.

**[Start free](/register)** or **[see pricing](/pricing)**.

## What a CTO or CIO does

A CTO or CIO is accountable for what the organisation's technology costs, what it risks and where it
is heading, and answers for all three to the board and the finance function. The job turns from
opinion to evidence the moment someone asks a question with a number attached: how much are we
paying for the same thing twice, which systems nobody owns, what a proposed investment actually
returns. A CIO dashboard earns its place by answering those questions from the same model every
time, rather than a slide rebuilt by hand before each board meeting.

## The questions you get asked

- [What are we paying for twice?](/use-cases/duplicate-software-spend)
- [Which risks sit on our revenue-critical path, and who owns the mitigation?](/use-cases/risk-blast-radius)
- [What is the business case for this investment?](/use-cases/business-case-for-the-cio)

## The screens you will use

- **The AI chat**, the one place every role asks in plain words, for a quick check before
  you open a dashboard.
- **The CTO scorecard and the health scorecard**, together giving the share of your applications
  still on a supported vendor version, the oldest open architecture exceptions to escalate, and one
  score built from risk counts, the review board's pipeline and how far your change programmes have
  moved through their phases.
- **Rationalisation** — see [Rationalisation](/modules/rationalization) — scoring every application
  Tolerate, Invest, Migrate or Eliminate, and turning the result into the consolidation list that
  answers what you're paying for twice.
- **Ownership coverage and capabilities with no owner**, broken down by business unit, so a gap in
  accountability is visible before an auditor finds it.
- **Roadmaps** — see [Roadmaps](/modules/roadmaps) — the gap register between today's stages and the
  target state, and the projects closing each one.
- **Tech radar**, classifying the technology already in your model as adopt, trial, assess or hold.
- **The review board** — see [Architecture Review Board](/modules/arb) — where a change goes through
  sessions and a recorded decision before it ships.

Enterprise and solution architects do the day-to-day modelling behind these screens; see how they
work on [enterprise architect tools](/roles/enterprise-architects).

## A week with Entelim

1. Open the health scorecard and the CTO scorecard for one read of risk, the review board's pipeline
   and which applications still run a supported vendor version, with the oldest open exceptions ready
   to escalate.
2. Check rationalisation's consolidation list for what you're paying for twice, and ownership
   coverage for which business unit has an application nobody has claimed.
3. Open the roadmap's gap register for the stage a proposed change sits in, and the business case
   behind it, with the capex, annual opex, three-year TCO, ROI, payback and NPV recorded on it. The
   costs of the capability, initiative or solution it's linked to can fill blank cost fields and are
   shown beside what was entered as a cross-check.
4. Send the change to the review board, and decide it once its sessions and evidence are recorded.
5. Set direction on the tech radar for anything newly adopted, trialled, assessed or put on hold.

## What you hand over

The CTO scorecard's oldest open exceptions, escalated; a consolidation list with a target date and an
estimated annual saving against every entry; a roadmap's gap register; a business case with capex,
opex, three-year TCO, ROI, payback and NPV; and a review board decision with its conditions attached.

## Works with

Sign your whole technology leadership team in the way they already sign in everywhere else: connect
[single sign-on](/connectors/single-sign-on) over OpenID Connect or SAML, included from the Team
plan. Carry the same questions into a conversation you're already having by connecting an
[AI assistant](/connectors/ai-assistants) such as Claude, read-only and scoped to your own
organisation's data, on every plan including the free one.

## Limits

The scorecards answer from what is recorded against an application: one with no vendor product
linked counts toward neither a supported nor an unsupported share, rather than being guessed into
one. A business case's benefit and
payback figures are the ones you enter, not ones Entelim estimates for you. A review board decision
is still made by the people sitting on it; Entelim holds the evidence and the record, not the
judgement.

## Frequently asked questions

### What does it cost for a whole IT team?

Community is free for one organisation of up to three people, with the CTO scorecard, health
scorecard and rationalisation all included. A growing technology team moves to Team, priced at $29
per editor a month, where anyone who only reads a dashboard or asks a question stays free. Enterprise
is sold by contract, from $24,000 a year, and adds SAML single sign-on and unlimited editors.

### Can the board see the numbers without a paid seat?

Yes. Anyone who only reads a dashboard or asks a question in the AI chat is free, on every plan
including Enterprise. An editor seat is only needed to change the model itself: updating a roadmap,
recording a rationalisation decision, or taking a case through the review board.

### How fast can we have a first picture?

As soon as your applications are imported. An ArchiMate Open Exchange or .archimate export, or a CSV
or Excel list, brings your inventory in with a history of what arrived and when. The CTO scorecard
and health scorecard read that same import immediately, and rationalisation's scoring and duplicate
detection run on demand, so the first pass can be done the same day.

### Do you offer an assessment before we commit?

Yes. The [architecture health check](/architecture-health-check) is a fixed-price assessment, run
on your own application data rather than a demonstration, that delivers a written report within two
weeks of kickoff covering ownership gaps, duplicate spend, rationalisation scores and contract
renewals, plus a read-out call to walk your team through what it found.

### Do I need to learn ArchiMate to use the CIO dashboard?

No. The CTO and CIO workspace is built around dashboards you read and a plain-language AI chat,
not a modelling canvas. Architects on your team keep working in ArchiMate underneath; you see the
scorecards, the consolidation list and the roadmap it produces, not the notation behind it.

## Start free

**[Start free](/register)** on the Community plan for one organisation of up to three people, with
the CTO scorecard, health scorecard and rationalisation included from day one. A technology team that
outgrows it moves to Team, priced per editor, where everyone who only reads a dashboard stays free.
For a guided first assessment, see the [architecture health check](/architecture-health-check).
[See full pricing](/pricing).
