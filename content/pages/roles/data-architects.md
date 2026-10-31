---
wave: 5
page_family: role
role_group: architecture
state: on_main
capture_status: live
cta: plans
description: "Data architect tools from Entelim: data lineage on modelled data objects, evidence-based stewardship findings and impact tracing on one model. Start free, see pricing."
source: app/utils/role_access.py (ROLE_DATA_ARCHITECT, SIDEBAR_ZONES, _MY_WORK_LINKS),
  app/modules/architecture data lineage and data architecture routes, app/modules/solutions_strategic
  data stewardship routes, app/modules/intelligence/routes/ui.py, app/services/billing_plans.py,
  read 18 October 2026
---

# Data architect tools that tie every data flow to a real element in the model

Data architect tools earn their place by answering three questions every data change raises: where this data comes from and goes to, which of it is sensitive and still unclassified, and what a change to it would touch. Entelim answers them from the same architecture model your enterprise architects work in: a lineage view over the data objects already modelled, a stewardship review that names the evidence behind each finding, and the traceability and impact views that follow a data object to the systems and processes around it. A data architect can start on the free Community plan.

**[Start free](/register)** or **[see pricing](/pricing)**.

## What a data architect does

A data architect decides what data the organisation holds, where it is mastered, how it moves and how it is protected. The job turns from modelling to defending when someone outside the data team asks a plain question: where does this number come from, which systems hold personal data, and what breaks if this feed changes? Data architect tools earn their place by answering from the model the systems are described in, not from a lineage diagram drawn once for an audit and not updated since.

## The questions you get asked

- [What breaks if this service fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Which risks and control gaps touch this goal?](/use-cases/risk-and-control-gaps)
- [What has changed around this component since we last checked, and is anything out of date?](/use-cases/architecture-change-tracking)

## The screens you will use

- **Data Lineage**, which shows where data is mastered, copied and consumed across the data objects already in the ArchiMate model. It counts the data objects, the recorded lineage edges and the objects not yet traced, and lists each recorded flow as source to target. To record a flow you choose a source and a target from the existing data objects and a flow type, ETL, ELT, real-time or batch; both ends must be modelled data objects, so a flow cannot be recorded against a system that is not in the model. Flows already implied by access relationships in the model are listed with them.
- **Data Stewardship**, where a review of the data layer reads live across the data object catalogue, the application classifications and the integration flows. Each finding carries a severity, a category such as classification or lineage, and the evidence it rests on, for example a data object whose name suggests personal data. Each finding offers an action: escalate it to the Architecture Review Board, which records an escalation reference, or open the catalogue behind it. The classification finding can also apply a classification to every unclassified application in one step, marking those that hold personal data confidential and the rest internal.
- **Data Architecture**, a dashboard of the data layer: logical and physical model counts, lineage flows, and the data and application elements and relationships in the repository.
- **The AI chat**, where you ask what data an element holds or produces, who stewards it and where it flows, and it answers from the data objects and flows recorded against it. See [Digital twin of your organisation](/modules/digital-twin).
- **Traceability Matrix**, which follows elements from strategy and business down to technology, top-down or bottom-up, from a pivot element you choose, with a CSV export.
- **Architecture Model**, the element browser where the data objects themselves are kept, with their types, relationships and sources. See [Architecture Model](/modules/architecture-model).
- **Applications**, the portfolio the data objects belong to, with each application's owner and lifecycle stage. See [Applications](/modules/applications).

Enterprise architects set the direction the data model serves; read how they work on [enterprise architect tools](/roles/enterprise-architects).

## A week with Entelim

1. Open Data Lineage and read what is not yet traced: the data objects with no recorded flow.
2. Record the flows you know, choosing the source object, the target object and the flow type, and check they appear in the recorded list.
3. Open Data Stewardship and read each finding with its evidence; escalate the ones the review board should decide.
4. Before a change, trace the data object in the Traceability Matrix to see the processes and applications around it.
5. Take the escalated findings and the lineage view into the next review.

## What you hand over

A lineage view in which every row is a modelled data object, with the flow type recorded where you know it. A stewardship finding list with the evidence behind each item and an escalation reference for those sent to the board. A traceability export from a data object to what surrounds it. And a model other architects can query, so the next person who asks where a data object goes finds a recorded answer.

## Limits

Entelim records lineage that you or an import put into the model; it does not discover data flows by reading your databases or pipelines. Lineage is held between modelled data objects, so a system that is not modelled has to be modelled before a flow can be recorded against it. The stewardship review flags names that suggest personal data and applications with no classification; it does not read the data itself, so a data object with an innocuous name is not flagged. Where nothing is recorded against an element, the AI chat's data answer says so instead of guessing. Classification applied in one step is a starting point for review, not a legal assessment.

## Frequently asked questions

### Can I record data lineage between systems?

Yes, between data objects already in the model. You pick a source object, a target object and a flow type of ETL, ELT, real-time or batch, and the flow appears in the recorded lineage list. Both ends must be modelled data objects, so lineage never points at a free-text name.

### Does it find sensitive data automatically?

Partly. The stewardship review flags data objects whose names suggest personal data and applications with no classification, and shows the evidence for each. It does not scan the contents of your databases, so it supports a review by a data architect rather than replacing one.

### Can I send a data issue to the review board?

Yes. Each stewardship finding has an Escalate to ARB action, which records the escalation with a reference number so the review board can pick it up.

### How is it different from a data catalogue?

A catalogue lists datasets and their fields. Entelim's lineage sits in the architecture model, so a data object is tied to the applications, processes and technology around it, and a question such as what a change touches is answered by tracing the model rather than by searching a list.

### Is it free to start?

Yes. The Community plan is free for one organisation of up to three people; Startup, Team and Enterprise are the paid plans above it. See [Entelim pricing](/pricing), or self-host the open-source edition under AGPL-3.0.

## Start free

[Start free](/register) on the Community plan for one organisation of up to three people. A growing team moves to one of the paid plans. [See full pricing](/pricing).
