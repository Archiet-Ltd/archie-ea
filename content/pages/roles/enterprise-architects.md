---
page_family: role
role_group: "Architecture"
role_label: "Enterprise architects"
state: held
capture_status: awaiting_capture
cta: plans
description: "Enterprise architect tools from Entelim: one model for Ask, roadmaps, portfolio, diagrams and the review board. Start free, see pricing today."
source: app/utils/role_access.py (ROLE_ENTERPRISE_ARCHITECT, SIDEBAR_ZONES, _MY_WORK_LINKS),
  content/pages/modules/{portfolio,roadmaps,diagrams,arb,architecture-model}.md,
  content/pages/function-per-segment/{uc-s2-01-what-breaks,uc-s3-04-value-streams-at-risk,
  uc-s3-09-programme-tracking,uc-s3-07-review-board}.md, app/services/billing_plans.py,
  read 11 October 2026
---

# Enterprise architect tools for one model, not three disconnected ones

Enterprise architect tools exist to answer one question honestly: what does this change touch, and
who is accountable for it? Entelim answers that from a single shared model — ask on the Ask screen,
trace the result on the twin map, and carry it through a roadmap's gap register to a review board
decision, all against the same data. It is built for enterprise architecture teams and the technology
leaders they report to, and a team can start on the free Community plan.

**[Start free](/register)** or **[see pricing](/pricing)**.

## What an enterprise architect does

An enterprise architect is the person accountable for how an organisation's business, application and
technology layers fit together, and for the model that records it: what exists, what depends on what,
and what still separates today's estate from the target state. The role is needed the moment a change
is proposed that nobody can yet say is safe — a platform migration, a new regulation, a merger — and
the question stops being "what does this do" and becomes "what does this touch". Software for
enterprise architects earns its place by making that second question answerable from a model you can
question directly, rather than a diagram that has to be redrawn and re-checked by hand every time.

## The questions you get asked

- [What breaks if this service or platform fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Which value streams are at risk because the capabilities under them are immature?](/use-cases/value-streams-at-risk)
- [Is the programme on time and on budget, and what does each project touch?](/use-cases/programme-tracking)
- [How do I take this change through the review board with the impact evidence attached?](/use-cases/architecture-review-board)

## The screens you will use

- **Ask**, first, and the twin map it opens into: ask a plain question about an element and trace
  what it affects, including the model as it stood on an earlier date — see
  [Digital twin of your organisation](/modules/digital-twin).
- **Roadmaps**, with the gap register between today's stages and the target state and the projects
  closing each gap — see [Roadmaps](/modules/roadmaps).
- **Portfolio**, where a value stream's maturity gaps and a transformation programme's cost and
  schedule sit against the same capabilities — see [Portfolio](/modules/portfolio).
- **Composer**, opening straight into the enterprise-wide architecture diagram rather than a blank
  canvas, and the saved diagram library behind it — see [Diagrams & Composer](/modules/diagrams).
- **Tech radar**, classifying the technology already in your model as adopt, trial, assess or hold.
- **The review board**, where a change proposal, its sessions and its decision are held against the
  impact evidence that was run before the meeting — see [Architecture Review Board](/modules/arb).
- **The architecture model**, the element browser every imported model lands in, across the business,
  application and technology layers — see [Architecture Model](/modules/architecture-model).

Technology leaders see the same review board from their own workspace; read how CTOs and CIOs use it
on [CTOs and CIOs](/roles/cto-and-cio).

## A week with Entelim

1. Bring your existing model in: an ArchiMate Open Exchange or .archimate export from Archi or
   another tool lands in the [architecture model](/modules/architecture-model) browser with its
   elements, relationships and properties intact.
2. Ask a question on the Ask screen — what breaks if this application fails, which value streams sit
   on an immature capability — and read the traced answer, with the twin map to see it and the
   model as it stood on an earlier date to check what changed.
3. Check the [roadmap](/modules/roadmaps)'s gap register for the stage the change affects, and the
   [transformation programme](/modules/portfolio) it belongs to, with cost and schedule shown as
   genuinely missing where nobody has entered it, never a silent zero.
4. Open Composer to see the change in the enterprise-wide diagram, and save the view for anyone who
   needs it next.
5. Take the proposal to the [review board](/modules/arb) with the impact evidence already attached,
   and record the decision — approved, approved with conditions, rejected or deferred — against the
   exact proposal it reviewed.

## What you hand over

A roadmap's gap register with the projects closing each gap; a programme's dates, status and cost per
work package, with any missing figure shown as missing rather than guessed; a review board decision
with its rationale and conditions, locked to the proposal it reviewed; and a diagram or a twin-map
trace that shows exactly what a change touches, built from the model rather than redrawn for the
meeting.

## Works with

An imported Archi or Open Exchange model gets everything onto one model from day one. From there,
[connect an AI assistant](/connectors/ai-assistants) to carry these same questions into a conversation
you are already having, read-only and scoped to your own data, and pull configuration items from
ServiceNow's CMDB into the application inventory the model already links to.

## Limits

The model answers from what is actually recorded in it; it does not discover systems or dependencies
on its own, so an import or a recorded element has to exist before a question about it can be
answered. The review board's decision is still made by the people on it — Entelim holds the evidence
and the record, not the judgement. Tech radar classifies technology already in the model; it does not
add technology to the model by itself.

## Frequently asked questions

### How long until the first answer?

As soon as your model is imported. An ArchiMate Open Exchange or .archimate export brings elements,
relationships and properties across intact, and the Ask screen reads that same import straight away.

### Can I import from Archi or LeanIX exports?

Yes for Archi: Entelim imports ArchiMate Open Exchange and .archimate files directly, with elements,
relationships and properties intact and an import history of what came in and when. Compare the two
tools on [Entelim vs Archi](/vs/archi).

### Who needs a paid seat?

Anyone editing the model needs an editor seat on the Team plan, priced per editor; people who only
ask questions are free. The Community plan covers one organisation of up to three people at no cost.

### Does it support ArchiMate 3.2?

Yes. The model is built on the ArchiMate 3.2 standard across the business, application and technology
layers, and imports and diagrams use the same notation.

### How is it different from a drawing tool?

A drawing tool stores a picture; Entelim stores the elements and relationships behind it, so a
question such as "what breaks if this fails" is answered by tracing the model, not by someone reading
a diagram and guessing. Composer still gives you the diagram, opened from the same elements.

Entelim gives every other enterprise architecture role — from solution architects to procurement and
finance — its own workspace over this same shared model.

## Start free

Start on the free Community plan for one organisation of up to three people, with every question, the
twin map and the review board included. A growing enterprise architecture team moves to Team, priced
per editor, where people who only ask questions stay free. For a guided first assessment of your
estate, see the [architecture health check](/architecture-health-check); for a paid annual plan with
onboarding included, see [Team annual onboarding](/team-annual-onboarding). [See full pricing](/pricing).
