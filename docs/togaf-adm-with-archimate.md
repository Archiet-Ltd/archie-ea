# TOGAF ADM with ArchiMate

**TOGAF** and **ArchiMate** are the two most widely used open standards in enterprise
architecture, and they are designed to work together. The simplest way to remember the
relationship:

> **TOGAF is the *method*. ArchiMate is the *language*.**

TOGAF's **Architecture Development Method (ADM)** tells you *how to develop* an
architecture, step by step. ArchiMate gives you a standard *notation* to *describe* the
architecture the ADM produces. You can use either alone — but together, the method produces
artifacts and the language makes them precise and analyzable.

## The TOGAF ADM phases

The ADM is a cycle of phases, surrounded by ongoing requirements management:

| Phase | Purpose |
|---|---|
| **Preliminary** | Set up the architecture capability, principles, governance |
| **A — Architecture Vision** | Scope, stakeholders, drivers, goals, high-level vision |
| **B — Business Architecture** | Business capabilities, processes, organization |
| **C — Information Systems** | Application and Data architectures |
| **D — Technology Architecture** | Infrastructure, platforms, networks |
| **E — Opportunities & Solutions** | Options, work packages, transition planning |
| **F — Migration Planning** | Sequenced roadmap, plateaus, gaps |
| **G — Implementation Governance** | Govern delivery against the architecture (ARB) |
| **H — Architecture Change Management** | Manage change to the live architecture |
| **Requirements Management** | Central, ongoing — feeds every phase |

## Mapping ADM phases to ArchiMate

ArchiMate's layers map cleanly onto the ADM phases, which is what makes the two standards
complementary:

| ADM phase | ArchiMate layer / extension |
|---|---|
| A — Vision | **Motivation** (stakeholders, drivers, goals) + high-level **Strategy** |
| B — Business | **Business** layer + **Strategy** (capabilities) |
| C — Information Systems | **Application** layer (incl. Data Objects) |
| D — Technology | **Technology** + **Physical** layers |
| E/F — Opportunities, Migration | **Implementation & Migration** (work packages, plateaus, gaps) |
| G/H — Governance, Change | Realized through review and the model's audit trail |

So a single ArchiMate model, viewed through different viewpoints, can carry the output of
the entire ADM cycle — motivation in Phase A, application-cooperation views in Phase C,
technology-usage views in Phase D, and plateaus/gaps in Phases E–F.

## A worked thread

A typical solution-design thread through the ADM, expressed in ArchiMate:

1. **Phase A** — capture the **drivers**, **goals**, and **constraints** (Motivation).
2. **Phase B** — identify the **business capabilities** the solution must support
   (Strategy/Business).
3. **Phase C** — design the **application components** and **services**, and link the
   **data objects** (Application).
4. **Phase D** — place components on **nodes** and **technology services** (Technology).
5. **Phase E/F** — define **work packages** and **plateaus** for the roadmap.
6. **Phase G** — submit to the [ARB](architecture-review-board.md); govern delivery against
   the model.

## Requirements management is the spine

The ADM puts **Requirements Management** at the center for a reason: requirements flow into
and out of every phase. In ArchiMate, this is the **Motivation** extension — requirements,
constraints, goals, and drivers — traced via *realization* and *influence* relationships to
the architecture that satisfies them. Keeping that trace is what lets you answer "*why* does
this component exist?" later.

## Do you need both?

- **TOGAF without ArchiMate** — workable, but your artifacts are prose and ad-hoc diagrams;
  hard to analyze or keep consistent.
- **ArchiMate without TOGAF** — workable, but you have a language with no method telling you
  *what to model when*.
- **Both** — the method gives you the cadence and artifacts; the language makes them
  precise, queryable, and governable. This is the mainstream practice.

## How Entelim helps (optional)

[Entelim](https://github.com/Anioko/archie-ea) implements a TOGAF-aligned solution-design
journey on top of an ArchiMate 3.2 model: it walks Phases A–G — drivers and goals, business
capabilities, application and technology design, options, and an ARB governance gate — and
keeps the requirements trace intact throughout.

## FAQ

**What is the difference between TOGAF and ArchiMate?**
TOGAF is a method (the ADM) for *developing* enterprise architecture; ArchiMate is a
language for *describing* it. They are complementary open standards from The Open Group.

**Can you use ArchiMate to document TOGAF deliverables?**
Yes — ArchiMate's layers map onto the ADM phases, so a single ArchiMate model can carry the
outputs of the whole ADM cycle through different viewpoints.

**What are the phases of the TOGAF ADM?**
Preliminary, A (Vision), B (Business), C (Information Systems), D (Technology), E
(Opportunities & Solutions), F (Migration Planning), G (Implementation Governance), H
(Change Management), plus continuous Requirements Management.

---
See also: [ArchiMate 3.2 cheat sheet](archimate-3-2-cheat-sheet.md) ·
[How to run an ARB](architecture-review-board.md)
