---
wave: 1
page_family: site
title: "Features"
page_role: "The eighteen capabilities of Enterprise Intelligence Management, grouped by the question each one answers, each linked to the module pages and use cases that deliver it today."
source: docs/eim-category-positioning-v1.md section 2; docs/artifacts/feature-status-register.yml
---

# The eighteen capabilities of Enterprise Intelligence Management

Every role starts with the same two things. Entelim builds one model of what you run from the tools
you already use: connect GitHub or Jira, or import a file, and every fact in the model links back to
the source it came from. Then you ask about that model in the AI chat and get answers that cite the
records behind every line.

Eighteen capabilities build on those two, grouped into five questions every enterprise asks: set
direction, know what you run, stay in control, get answers before you need them, and connect and
trust. Start with [what connects](/connectors) and [the AI chat](/modules/ai-chat).

## Set direction

*Where are we going, and is the money following the plan?*

<h3 id="strategy-management">Strategy Management</h3>

Turn what you intend into goals, objectives and the capabilities that deliver them, and see whether
the strategy is actually being carried out.

**Today:** browse your business capabilities as a map, map your value streams stage by stage to the capabilities behind them, see which value
streams are at risk, lay out drivers and goals in a motivation model, and capture the business model
on one canvas.

**Early access:** tracing capability ownership and goals to what realises them, and objectives and key results with measured results. [Join the waiting list](/early-access/objectives-and-key-results).

Delivered today by: [Business Model Canvas](/modules/business-model-canvas),
[Value Streams at Risk](/use-cases/value-streams-at-risk).

<figure class="public-figure mt-10">
<picture>
<img src="/static/img/modules/business-model-canvas.webp" alt="Screenshot of the Business Model Canvas module's canvas library." width="1200" height="750" loading="lazy" decoding="async" class="rounded-lg border border-border shadow-sm">
</picture>
<figcaption class="mt-2 text-sm text-muted-foreground">The Canvases page, listing each business model canvas.</figcaption>
</figure>

<h3 id="portfolio-management">Portfolio Management</h3>

Keep one current portfolio of applications, technology, products and initiatives, each with an
owner, a health picture and a disposition you can defend with data.

[[if live PG-APP]]**Today:** keep your application inventory with its interfaces and dependencies, and see the
applications you own on one page.

Every impact answer shows the owners of the applications it reaches, with their health, so each affected system has a name beside it.

Delivered today by: [Applications](/modules/applications), [What I Own](/use-cases/what-i-own).
[[/if]]

[[if live PG-OVR]]One overview shows the health of the estate and how far each figure in the model can be trusted.
[[/if]]

[[if live PG-PLAN]]Scoring applications for rationalisation, one consolidation list, and one portfolio list of
initiatives and programmes, each with its budget, spend, health and progress, work today.

Delivered today by: [Portfolio](/modules/portfolio), [Rationalization](/modules/rationalization).
[[else]]**Early access:** scoring applications for rationalisation, one consolidation list, and one portfolio list of initiatives and programmes with their budget, spend, health and progress. [Join the waiting list](/early-access/gaps-work-packages-portfolio-rationalisation-and-investment).
[[/if]]

<figure class="public-figure mt-10">
<picture>
<img src="/static/img/modules/portfolio.webp" alt="Screenshot of the Portfolio module's dashboard." width="1200" height="750" loading="lazy" decoding="async" class="rounded-lg border border-border shadow-sm">
</picture>
<figcaption class="mt-2 text-sm text-muted-foreground">Every initiative and programme in one list, with its budget, spend, health and progress.</figcaption>
</figure>

<h3 id="transformation-management">Transformation Management</h3>

Plan change from where you are to where you want to be, through gaps, roadmaps, programmes and work
packages, and prove the outcome when it lands.

[[if live PG-ROAD]]**Today:** generate a roadmap of plateaus from the model, in the same model as the estate it
changes, and ask the AI chat what any change touches.
[[/if]]

[[if live PG-SOL]]Solutions and programmes, from idea to approved design, work today.
[[else]]**Early access:** taking a solution from idea to approved design, and following the programme that delivers it. [Join the waiting list](/early-access/solutions-programmes-and-the-architecture-journey).
[[/if]]

**Early access:** stage evidence and gate conformance, and benefits tracked to measured outcomes. [Join the waiting list](/early-access/stage-evidence-and-benefits-tracking).

Delivered today by: [Roadmaps](/modules/roadmaps), [Programme Tracking](/use-cases/programme-tracking).

<h3 id="investment-and-financial-management">Investment and Financial Management</h3>

Give every cost one home in one reporting currency, appraise what to fund, and tie spend to the
capabilities and outcomes it pays for.

[[if live PG-BC]]**Today:** make the case for a change as one document: the problem, the options considered, the
recommendation, costs and benefits with payback and net present value, and the risks, linked to the
work it funds.
[[else]]**Early access:** business cases, each one document holding the problem, the options considered, the recommendation, costs and benefits with payback and net present value, and the risks. [Join the waiting list](/early-access/business-cases).
[[/if]]

**Early access:** one home for every cost, totalled in your reporting currency, with financial what-if and budget-constrained portfolio optimisation. [Join the waiting list](/early-access/financial-what-if-and-portfolio-optimisation).

[[if live PG-BC]]Delivered today by: [Business Case](/modules/business-case).
[[/if]]

<figure class="public-figure mt-10">
<picture>
<img src="/static/img/modules/business-case.webp" alt="Screenshot of the Business Case module's list of business cases." width="1200" height="750" loading="lazy" decoding="async" class="rounded-lg border border-border shadow-sm">
</picture>
<figcaption class="mt-2 text-sm text-muted-foreground">Business cases with their status, three-year TCO and return on investment.</figcaption>
</figure>

## Know what you run

*What does the business actually run on, and who owns it?*

<h3 id="architecture-management">Architecture Management</h3>

Model the whole enterprise across business, applications, data and technology in ArchiMate 3.2,
design solutions against your standards, and keep the model healthy.

[[if live PG-BL]]**Today:** build the model from GitHub or a spreadsheet, and record the decision tables your
architecture follows.
[[/if]]

[[if live PG-DIA]]Working on the model in the composer, browsing every view in the diagram library and importing an
Open Exchange or Archi file work today.
[[else]]**Early access:** the composer, the diagram library, and import and export of Open Exchange and Archi files. [Join the waiting list](/early-access/composer-diagrams-model-exchange-and-traceability).
[[/if]]

[[if live PG-ELE]]Opening any element to see everything it connects to, and browsing the model layer by layer, work
today.
[[else]]**Early access:** a page for every element showing everything it connects to, and browsing the model layer by layer. [Join the waiting list](/early-access/element-pages-and-browse-by-layer).
[[/if]]

**Early access:** identities that survive renames when you re-import a model, model health findings and design-to-delivery traceability. [Join the waiting list](/early-access/model-health-and-traceability).

[[if live PG-DIA]]Delivered today by: [Architecture Model](/modules/architecture-model),
[Diagrams and Composer](/modules/diagrams), [Industry Reference Frameworks](/modules/industry-apqc).
[[/if]]

<figure class="public-figure mt-10">
<picture>
<img src="/static/img/modules/architecture-model.webp" alt="Screenshot of the Architecture Model module's ArchiMate element browser." width="1200" height="750" loading="lazy" decoding="async" class="rounded-lg border border-border shadow-sm">
</picture>
<figcaption class="mt-2 text-sm text-muted-foreground">The ArchiMate element browser, spanning the business, application, technology and motivation layers.</figcaption>
</figure>

<h3 id="data-management">Data Management</h3>

Know what data you hold, where each piece is mastered, how it moves from system to system and who
looks after it.

[[if live PG-DATA]]**Today:** keep an enterprise data model, trace lineage, assign stewards and declare the system of
record for each master data domain.
[[else]]**Early access:** an enterprise data model with stewards and the system of record for each master data domain. [Join the waiting list](/early-access/data-architecture-lineage-and-governance).
[[/if]]

**Early access:** lineage across every hop, with sensitivity carried forward to every consumer, and data quality rules and trends. [Join the waiting list](/early-access/data-lineage-and-quality).

<h3 id="people-and-organisation-management">People and Organisation Management</h3>

Connect people, teams, roles and skills to the capabilities and systems they own and run, so every
question has a name attached.

[[if live PG-PEOPLE]]**Today:** keep your organisation chart with roles and responsibilities, and map your stakeholders.
[[else]]**Early access:** your organisation chart with roles and responsibilities, and a map of your stakeholders. [Join the waiting list](/early-access/organisation-chart-raci-stakeholders).
[[/if]]

**Early access:** ownership across the whole model, and skills and capacity planning. [Join the waiting list](/early-access/skills-and-capacity-planning).

[[if live PG-PEOPLE]]Delivered today by: [Org Chart and RACI](/modules/org-chart).
[[/if]]

<h3 id="operations-and-service-management">Operations and Service Management</h3>

Bring the live state of the business into the model (services, incidents, changes, service levels
and telemetry) so the company brain reflects what is happening now, not what was drawn last quarter.

**Early access:** service mapping and reconciliation with your configuration records, and incidents, changes, telemetry and service levels on the model. [Join the waiting list](/early-access/service-mapping-and-operations).

## Stay in control

*What could hurt us, what must we prove, and what are we committed to?*

<h3 id="risk-management">Risk Management</h3>

Hold one risk register, see risk that travels through dependencies rather than only the risk written
on an element, and measure exposure against your appetite.

[[if live PG-RISK]]**Today:** keep one risk register and ask the AI chat which risks sit on the path to any system.
[[/if]]

**Early access:** risk that propagates through every dependency, appetite, key risk indicators and scenario-based resilience tests. [Join the waiting list](/early-access/risk-appetite-and-resilience-tests).

Delivered today by: [Risk Register](/modules/risk-register).

<figure class="public-figure mt-10">
<picture>
<img src="/static/img/modules/risk-register.webp" alt="Screenshot of the Risk Register module." width="1200" height="750" loading="lazy" decoding="async" class="rounded-lg border border-border shadow-sm">
</picture>
<figcaption class="mt-2 text-sm text-muted-foreground">The risk register, with likelihood, impact and a mitigation plan recorded for each risk.</figcaption>
</figure>

<h3 id="compliance-and-governance">Compliance and Governance</h3>

Map obligations to controls and evidence, run architecture review and gates, and give auditors
complete evidence without a scramble.

[[if live PG-GOV]]**Today:** run an architecture review board from submission to decision, record conditions and
waivers, define governance gates, monitor policy, handle data subject requests and query, export and
verify the audit log.
[[/if]]

**Early access:** a control library with evidence that proves each control operates. [Join the waiting list](/early-access/control-library).

Delivered today by: [Architecture Review Board](/modules/arb),
[Compliance Frameworks](/modules/compliance-frameworks).

<figure class="public-figure mt-10">
<picture>
<source media="(max-width: 1099px)" srcset="/static/img/modules/arb-mobile.webp" width="302" height="624">
<img src="/static/img/modules/arb.webp" alt="Screenshot of the Architecture Review Board hub showing total and pending reviews, approval rate, cycle time and the review status chart." width="952" height="808" loading="lazy" decoding="async" class="rounded-lg border border-border shadow-sm">
</picture>
<figcaption class="mt-2 text-sm text-muted-foreground">The Architecture Review Board hub: review volume, pending decisions, approval rate and cycle time.</figcaption>
</figure>

<h3 id="vendor-and-procurement-management">Vendor and Procurement Management</h3>

Know every supplier, contract and licence, what each one serves and what it costs, and never miss
the last day to cancel.

[[if live PG-VEN]]**Today:** keep every vendor your systems and contracts already name in one list, with the
applications each one supplies, and keep contracts, licences, renewals and spend in one procurement
workspace tied to those applications.
[[else]]**Early access:** every vendor in one list with the applications each one supplies, and contracts, licences, renewals and spend in one procurement workspace. [Join the waiting list](/early-access/vendors-contracts-licences-and-spend).
[[/if]]

**Early access:** renewal alerts that show the last day to cancel, and licence usage that is measured rather than typed in. [Join the waiting list](/early-access/renewal-alerts-and-licence-usage).

[[if live PG-VEN]]Delivered today by: [Vendors](/modules/vendors), [Procurement](/modules/procurement).
[[/if]]

<figure class="public-figure mt-10">
<picture>
<img src="/static/img/modules/vendors.webp" alt="Screenshot of the Vendors module's list of your vendors." width="1200" height="750" loading="lazy" decoding="async" class="rounded-lg border border-border shadow-sm">
</picture>
<figcaption class="mt-2 text-sm text-muted-foreground">Your vendors, with each vendor's type, products and contract status.</figcaption>
</figure>

<h3 id="ai-management">AI Management</h3>

Inventory, classify, evaluate and govern every AI system in the enterprise, including Entelim's own,
and manage what AI costs you.

**Today:** Entelim governs its own AI the way you will govern yours: every action its assistant takes
passes one permission check, and anything it proposes to change waits in your approvals queue until a
person approves it.

**Early access:** your own AI system register, with a proposed EU AI Act risk class for each system, and AI usage and cost by provider. [Join the waiting list](/early-access/ai-systems-register).

## Get answers before you need them

*What does it mean, what happens next, and what should we do?*

<h3 id="enterprise-intelligence">Enterprise Intelligence</h3>

Ask the model a question and get an answer that cites its sources, explains itself and shows what it
does not know. Then let the platform raise what matters before you ask.

[[if live PG-ANS]]**Today:** ask in plain words in the AI chat: what breaks if this fails, which goals it serves, what
is changing it and which risks reach it. Every answer lists the records it came from and how each
connection was derived, and you can see the model as it stood on any past date.
[[/if]]

**Early access:** sharing an answer with a link, insights the platform raises without being asked, and forecasts and recommendations tracked to outcomes. [Join the waiting list](/early-access/recommendations).

Delivered today by: [AI Chat](/modules/ai-chat), [Digital Twin](/modules/digital-twin),
[What breaks if this fails, and who gets called](/use-cases/what-breaks-and-who-gets-called).

<figure class="public-figure mt-10">
<picture>
<img src="/static/img/modules/ai-chat.webp" alt="Screenshot of the AI Chat module answering a question about Lantern Quay Systems' architecture." width="1200" height="750" loading="lazy" decoding="async" class="rounded-lg border border-border shadow-sm">
</picture>
<figcaption class="mt-2 text-sm text-muted-foreground">The AI chat answering a question from the organisation's own model, with the records behind the answer.</figcaption>
</figure>

<h3 id="simulation-and-scenario-planning">Simulation and Scenario Planning</h3>

Branch the model, change it, and see what happens to cost, risk, resilience and capacity before you
commit money.

**Early access:** scenario branches with every assumption visible, and portfolio and roadmap optimisation. [Join the waiting list](/modules/scenario-modelling).

**Today:** the impact question shows exactly what one failure would break.

<h3 id="agentic-automation">Agentic Automation</h3>

Let governed agents and your own AI assistants read the model, propose changes and, within limits you
delegate, act on it, with one permission check, one approval queue and a full audit trail.

[[if live PG-CHAT]]**Today:** ask from the AI chat inside Entelim, or connect the AI assistant your team already uses.
[[/if]]
Both read the same model, with read-only access scoped to your organisation, and anything either
proposes to change waits in your approvals queue.

**Early access:** multi-step agent workflows. [Join the waiting list](/early-access/multi-step-agent-workflows).

Delivered today by: [AI Chat](/modules/ai-chat),
[Connect your AI assistant](/connectors/ai-assistants).

## Connect and trust

*Does it fit how we work, and can we trust it with our data?*

<h3 id="collaboration-and-communication">Collaboration and Communication</h3>

Make every answer shareable, reviewable and deliverable, and give every person one place for their
work.

**Today:** every answer lives in your chat history, and the audit trail exports now.[[if live PG-DIA]] Diagrams
export too.[[/if]]

**Early access:** sharing an answer with a link, with comments and reviews. [Join the waiting list](/early-access/answer-and-artefact-sharing).

<h3 id="integration-and-interoperability">Integration and Interoperability</h3>

Open the model to the rest of your stack through a secure API, events and connectors, with every
incoming change matched by identifier and approved before it lands.

[[if live PG-CON]]**Today:** connect GitHub or Jira when you sign up and Entelim proposes the services, dependencies and
owners it finds; import spreadsheet files; pull applications from ServiceNow; connect your own AI
assistant; read your model through the API with a scoped token; and send signed event notifications
to Slack, Microsoft Teams or any webhook.
[[/if]]

**Early access:** developer kits and more endpoints beyond the read access a token gives today, and more connectors that write through one approval queue, such as your identity provider. [Join the waiting list](/early-access/developer-kits-and-more-connectors).

Delivered today by: [Connectors](/connectors),
[[if live PG-DIA]][Architecture Model](/modules/architecture-model) (file import),
[[/if]][Connect your AI assistant](/connectors/ai-assistants).

<h3 id="administration-and-trust">Administration and Trust</h3>

Enterprise sign-in, access control, proven tenant isolation, audit and billing, so the platform
passes the checks your security team runs first.

[[if live PG-ORG]]**Today:** every record scoped to your organisation, a verifiable audit log, and plans you can buy,
change and cancel yourself.
[[/if]]

[[if live PG-SSO]]Single sign-on through SAML or OpenID Connect works today.
[[else]]**Early access:** single sign-on through SAML or OpenID Connect, managed from the administration pages. [Join the waiting list](/early-access/single-sign-on-administration).
[[/if]]

For certifications and what we do and do not claim today, see [Security](/security).

---

Every capability above is part of the offering. Those marked early access have a waiting list, and each one reaches Startup and Team at no extra charge when it opens. See [how
it's priced](/pricing) or [try it](/account/register).

<h2 id="coming-soon">Early access</h2>

These are in early access and not open to everyone yet. Open one to join its waiting list and we will tell you the day it opens.

- [Scenarios](/modules/scenario-modelling): compare what-if plans side by side before you commit.
- [Software and tool discovery](/modules/software-and-tool-discovery): discover the software and tools you actually pay for and use.
