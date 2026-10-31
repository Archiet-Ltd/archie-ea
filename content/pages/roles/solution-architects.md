---
wave: 5
page_family: role
role_group: architecture
state: on_main
capture_status: live
cta: plans
description: "Solution architect tools from Entelim: ADM kanban, review board decisions and impact analysis on one shared model. Start free, see pricing today."
source: app/utils/role_access.py (ROLE_SOLUTION_ARCHITECT, SIDEBAR_ZONES, _MY_WORK_LINKS),
  app/models/adm_kanban.py, app/modules/architecture/routes/arb_routes.py,
  app/modules/interface_register/services/interface_register_service.py,
  app/modules/solutions_strategic/v2/routes/{journey_v2_routes,solution_design_routes,strategic_routes}.py,
  app/services/jira_connector_service.py, app/services/m365_service.py,
  app/modules/solutions_strategic/v2/routes/solution_export_routes.py, app/services/billing_plans.py,
  read 11 October 2026
---

# Solution architect tools for a design the review board can actually decide on

Solution architect tools earn their place by answering three questions every design raises before it
can ship: what it touches, whether the review board has approved it and under which conditions, and
which interfaces change. Entelim answers all three from the same model your enterprise architects
already work in, carries a design across an ADM kanban board, and records the review board's decision
against it rather than in a separate document nobody checks afterwards. A solution architect can start
on the free Community plan.

**[Start free](/register)** or **[see pricing](/pricing)**.

## What a solution architect does

A solution architect turns an approved direction — a capability gap, a target-state decision, a new
integration — into a design detailed enough to build: which applications and interfaces are involved,
what changes in each, and who owns the pieces. The job turns from drafting to defending the moment
someone outside the design asks what it actually touches, or whether it was ever approved. Solution
architect tools earn their place by answering both from the model the design is built in, not a
diagram and a decision log kept in two places that quietly drift apart.

## The questions you get asked

- [What does this design touch?](/modules/digital-twin)
- [Has the review board approved it, and with which conditions?](/use-cases/architecture-review-board)

## The screens you will use

- **The AI chat**, first, for a quick question about one element, and the impact analysis to
  trace what a design touches — see [Digital twin of your organisation](/modules/digital-twin).
- **Architecture Journey and Solutions**, where a design is built: its ArchiMate elements, the APQC
  processes it affects, and the owner, sponsor and technical lead recorded against it.
- **ADM Kanban**, which moves a design across the stages of TOGAF's Architecture Development Method —
  the standard cycle of phases a change in enterprise architecture passes through, from the first
  statement of intent through business, information systems and technology architecture to migration
  planning and governance — with each stage's governance checkpoints attached to the card.
- **The review board**, where a design goes as a change proposal, is discussed in a session, and
  comes back with a decision — approved, approved with conditions, rejected or deferred — see
  [Architecture Review Board](/modules/arb).
- **Programmes**, where a design's delivery sits against the same roadmap stage it is closing, with
  dates, status and cost per work package — see [is the programme on time and on
  budget](/use-cases/programme-tracking) within [Portfolio](/modules/portfolio).
- **Interface Register**, recording each interface's type, protocol, criticality and status against
  the roadmap initiative it belongs to, and flagging the gaps between one initiative's interfaces and
  the next — see the Interface Register screen.
- **AI Chat**, carrying the same questions into a conversation instead of a search through the model —
  see [AI Chat](/modules/ai-chat).

Enterprise architects set the target state these designs move toward; read how they work on
[enterprise architect tools](/roles/enterprise-architects).

## A week with Entelim

1. Open a design in Solutions and attach its ArchiMate elements, APQC processes, owner, sponsor and
   technical lead.
2. Move it across the ADM kanban board as each phase's inputs are met, with that phase's governance
   checkpoints attached to the card.
3. Ask what the design touches before you submit it — what depends on the elements it changes.
4. Check the interface register for anything the design adds, changes or retires, scoped to its
   roadmap initiative.
5. Submit it to the review board with that evidence attached, and record the decision against the
   design itself.

## What you hand over

A design's blueprint, exported as a PowerPoint file and, where Microsoft 365 is connected, uploaded
straight to a SharePoint folder. A review board decision with its rationale and any conditions
attached. An interface register entry for each interface the design touches, with its type, protocol
and criticality recorded. And the model itself, updated once the design is approved, so the next
person who asks what changed gets the real answer.

## Works with

An imported Archi or Open Exchange model puts a design on the same model your enterprise architects
use from day one. From there, connect Jira (see [the connectors](/connectors)) to carry a design's work into the
tracker your delivery team already uses, and connect Microsoft 365 and
Teams to send a design's exported blueprint to SharePoint and
post the decision to a Teams channel as soon as the review board approves it.

## Limits

The review board's decision is still made by the people sitting on it; Entelim holds the evidence and
the record, not the judgement. The interface register holds what is recorded against a roadmap
initiative; it does not discover interfaces on its own. ADM Kanban tracks a design's progress through
TOGAF's phases; whether that design is methodologically sound is still a question for the review
board.

## Frequently asked questions

### Does it follow TOGAF?

Yes. The ADM kanban board tracks a design through the stages of TOGAF's Architecture Development
Method, with each stage's inputs, outputs and governance checkpoints attached to the card, and the
underlying model is built on the ArchiMate 3.2 standard across the business, application, technology
and motivation layers.

### Can I submit a design to the review board from it?

Yes. A design in Solutions goes to the review board as a change proposal with its evidence pinned
to the decision, is discussed in a recorded session, and comes back with a decision — approved, approved
with conditions, rejected or deferred — recorded against that same design rather than a separate
document.

### Can I export a design document?

Yes. A design's blueprint exports as a PowerPoint file you can present directly, and if Microsoft 365
is connected, Entelim uploads the same file straight to a SharePoint folder, so a reviewer who was not
in the room still sees exactly what the board decided on.

### How is it different from a diagramming tool?

A diagramming tool stores a picture; Entelim stores the elements and relationships a design is made
of, so "what does this touch" is answered by tracing the model, not by someone reading a diagram and
guessing. The diagram is still there when you need it, generated from the same elements rather than
redrawn by hand.

### Who needs a paid seat?

Anyone editing a design — moving it across ADM Kanban, submitting it to the review board, or
recording an interface — needs an editor seat on the Team plan, priced at $29 per editor a month.
People who only ask questions in the AI chat are free, on every plan, and Community covers a whole
organisation of up to three people at no cost at all.

## Start free

[Start free](/register) on the Community plan for one organisation of up to three people, with
Solutions, the ADM kanban board and the review board all included from day one. A growing team moves
to Team, priced per editor, where people who only ask questions stay free. [See full
pricing](/pricing).
