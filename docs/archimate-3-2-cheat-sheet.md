# ArchiMate 3.2 Cheat Sheet

A one-page reference for the **ArchiMate 3.2** modeling language: the layers, the core
elements in each, the aspects, and the relationships. ArchiMate is an open standard from
The Open Group and is fully aligned with TOGAF — use it to *visualize* the architecture
that TOGAF's method develops.

## The layers (and their colors)

| Layer | Default color | What it models |
|---|---|---|
| **Strategy** | Light orange | Resources, capabilities, courses of action |
| **Business** | Yellow | Actors, roles, processes, services, products |
| **Application** | Blue (cyan) | Application components, services, interfaces, data |
| **Technology** | Green | Nodes, devices, system software, networks, artifacts |
| **Physical** | Green | Equipment, facilities, materials, distribution networks |
| **Implementation & Migration** | Pink/rose | Work packages, deliverables, plateaus, gaps |

The **Motivation** extension (purple) cuts across all layers: stakeholders, drivers,
assessments, goals, outcomes, principles, requirements, constraints.

## Core elements by layer

**Strategy:** Resource · Capability · Course of Action · Value Stream

**Business:** Business Actor · Business Role · Business Collaboration · Business Process ·
Business Function · Business Interaction · Business Service · Business Object · Contract ·
Representation · Product

**Application:** Application Component · Application Collaboration · Application Interface ·
Application Function · Application Process · Application Service · Data Object

**Technology:** Node · Device · System Software · Technology Collaboration · Technology
Interface · Path · Communication Network · Technology Function · Technology Service ·
Artifact

**Physical:** Equipment · Facility · Distribution Network · Material

**Implementation & Migration:** Work Package · Deliverable · Implementation Event ·
Plateau · Gap

**Motivation:** Stakeholder · Driver · Assessment · Goal · Outcome · Principle ·
Requirement · Constraint · Meaning · Value

## The three aspects (columns)

ArchiMate organizes active behavior into three aspects, which gives each layer a
recognizable structure:

- **Active structure** — *who/what acts* (actor, component, node).
- **Behavior** — *what happens* (process, function, service).
- **Passive structure** — *what is acted on* (business object, data object, artifact).

A useful rule of thumb: an **active-structure** element is *assigned to* a **behavior**
element, which *realizes* or *serves* a **service** that is *accessed* by a **passive**
element.

## Relationships (most-used)

**Structural**
- **Composition** — whole/part (filled diamond); the part can't exist without the whole.
- **Aggregation** — grouping (open diamond); parts can exist independently.
- **Assignment** — allocation of active structure to behavior (e.g., a role performs a
  process).
- **Realization** — a more concrete element realizes a more abstract one (a component
  realizes a service).

**Dependency**
- **Serving** — one element provides functionality to another (formerly "used by").
- **Access** — behavior reads/writes a data or business object.
- **Influence** — a soft, possibly negative effect (used heavily in motivation).

**Dynamic**
- **Triggering** — temporal/causal flow between behaviors.
- **Flow** — transfer of information or value between behaviors.

**Other**
- **Specialization** — is-a (one element is a kind of another).
- **Association** — a generic relationship when nothing more specific applies.
- **Junction** — splits/joins relationships (and/or).

## Viewpoints

ArchiMate defines standard **viewpoints** — pre-selected element/relationship sets for a
specific stakeholder concern. Common ones:

- **Application Cooperation** — how applications interact (the most-used architecture view).
- **Technology Usage** — which technology supports which applications.
- **Layered** — a top-to-bottom slice across business → application → technology.
- **Service Realization** — how services are realized by underlying behavior.
- **Goal Realization / Motivation** — how requirements trace to drivers and goals.

## Modeling tips

- **Model services, not just components.** The service layer between active structure and
  consumers is what makes a model analyzable.
- **Use realization and serving deliberately** — they carry most of the meaning.
- **Don't over-model.** A view should answer one stakeholder's question, not show
  everything.
- **Name consistently.** Naming standards are what make a model queryable later.

## How Entelim helps (optional)

[Entelim](https://github.com/Anioko/archie-ea) implements ArchiMate 3.2 across all layers
with the standard relationships and viewpoints, and adds an AI architect that generates
candidate models — grounded in your real application portfolio — and a governance layer
on top.

## FAQ

**What is ArchiMate 3.2?**
An open enterprise-architecture modeling language from The Open Group, aligned with TOGAF,
with elements organized into Strategy, Business, Application, Technology, Physical,
Implementation & Migration, and a cross-cutting Motivation extension.

**Is ArchiMate the same as TOGAF?**
No. TOGAF is a *method* (the ADM) for developing architecture; ArchiMate is a *language*
for describing it. They're complementary — see
[TOGAF ADM with ArchiMate](togaf-adm-with-archimate.md).

**What's the difference between serving and realization?**
*Serving* means one element provides functionality to another; *realization* means a
concrete element makes an abstract one real (a component realizes a service).

---
See also: [TOGAF ADM with ArchiMate](togaf-adm-with-archimate.md) ·
[How to run an ARB](architecture-review-board.md)
