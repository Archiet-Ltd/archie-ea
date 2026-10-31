---
wave: 2
page_family: module
module_label: "Digital Twin"
endpoint: intelligence_ui.ask
source: app/modules/intelligence/routes/ui.py, api.py, history_routes.py + templates intelligence/ask.html, twin_map.html, history_as_of.html, history_changes.html, read 2026-10-11
state: on_main
answers_use_cases:
  - {id: UC-S1-06, segment: S1}
  - {id: UC-S2-01, segment: S2}
  - {id: UC-S2-08, segment: S2}
  - {id: UC-S3-04, segment: S3}
  - {id: UC-S3-08, segment: S3}
  - {id: UC-S3-09, segment: S3}
capture_status: live
cta: plans
description: "A digital twin of an organisation is one model of what you run, who owns it and what it costs, that you can question in the AI chat and see on the twin map. Start free."
icon: radar
---

# Digital twin of an organisation

A digital twin of an organisation is one model of what you run, what depends on what, who owns it and what it costs, that you can question rather than take on trust. In Entelim you ask in plain words on the Ask screen and get a table you can check, with the reason behind every connection it worked out for you, a twin map to see it, and the model as it stood on any past date. It is for the architect, CTO or risk owner who needs an answer today, not a diagram someone drew last year.

## What a digital twin of an organisation is

A digital twin of an organisation is a model that mirrors the real one: every application, process, capability and piece of technology you run, and the relationships between them — this system depends on that platform, this capability is realised by that application, this risk threatens that process. Most organisations hold the pieces of this picture in separate places: a spreadsheet of applications, a diagram from a past project, a risk register nobody has reopened since the audit that produced it. None of those on its own can answer a question that crosses more than one of them, such as what actually happens if one system goes down, because the dependency was never written down anywhere a computer can read.

Entelim's twin is built from two kinds of connection. An explicit relationship is one you recorded or imported directly, in [ArchiMate](/modules/architecture-model) — the open modelling notation behind the model, with element types such as application component, business process or node, and relationship types such as serves, realises or depends on, across the business, application and technology layers. A derived relationship is a further connection Entelim works out, rather than one anyone recorded by hand, from a chain of explicit relationships, by a named rule, rechecked whenever the model changes. On screen, Entelim calls this "what we worked out".

## Who it is for

- **An [enterprise architect](/roles/enterprise-architects)** who has to answer "what does this touch" without redrawing a diagram first.
- **A CTO or risk owner** who needs to know what a failure, a change or a new risk actually reaches before the meeting it gets discussed in, not after it.
- **A portfolio manager or compliance lead** who wants the same model to answer a cost, a programme or a control question, instead of a different tool for each one.

## How it works in Entelim

1. **Ask a question about one element.** Open Ask and choose one of its lenses — Entelim's name for a ready-made question it can answer about any element: what breaks if it fails, what it costs and how it is rationalised, what could hurt it, what it is trying to achieve, what is changing around it, what data it holds, or which controls apply to it. Type a name and pick the application, process or other element the question is about from the matches.
2. **Read the answer.** The impact answer lists every element reached, each marked as explicit or worked out, with its recorded owner and a link straight to the twin map. The risk answer lists each risk with its likelihood-times-impact score and what it touches. The strategy and programme answers list the initiatives and work packages against the element, each with how much of the model it reaches. The data answer lists what the element holds or produces, who stewards it and where it flows. The compliance answer lists the controls it is mapped to and any with no recorded evidence.
3. **Open the proof.** Any "why?" button on a worked-out row opens the provenance drawer: a plain-terms explanation, the chain of explicit relationships the answer was built from — each link resolved to its two elements, the rule behind it, and who recorded it and when — and any decision recorded against the elements in that chain. A link that no longer exists in the model says so, rather than being dropped silently from the chain.
4. **Switch to the twin map.** The same element opens on a graph of what it connects to, banded by architecture layer, with a hop-depth slider running from one to five connections out and a toggle to include what Entelim worked out alongside what you recorded. Select any node for its own panel, with the same owner field and "why?" drawer, and a table underneath the graph listing the same rows.
5. **Compare the model over time.** Model as of shows every element and relationship exactly as they stood on a date you choose. Changes between two dates lists what was added, updated or removed in between, who changed it and the reason, if one was recorded, with the version before and after each change.

## What you get

- An impact answer for any element: every system, process or capability it reaches, each marked explicit or worked out, with its recorded owner and a link to the twin map.
- A risk, strategy, programme, data and compliance answer for the same element, each reached from the same Ask screen.
- A provenance drawer on every worked-out row: the chain of explicit relationships behind it, who recorded each link and when, and any decision recorded against it.
- A twin map of what any element connects to, grouped by architecture layer, with hop depth and a worked-out-connections toggle you control, plus a table view of the same rows.
- A status panel on Ask showing when connections were last worked out, how many exist, and how many may be out of date and why.
- The model exactly as it stood on a past date, and a list of what changed between any two dates, by whom and why.

## Limits

Ask and the twin map answer only from what is in your model. An element with no recorded relationship shows no connections, and an application with no recorded owner shows that plainly, rather than a guess at either. Entelim does not scan your network or your cloud estate to find what is running; what the twin map shows is what was recorded or imported.

Accountability — who's accountable for an element — is recorded ownership on the application record and in [Org Chart & RACI](/modules/org-chart), not a separate answer on Ask today.

A worked-out connection is rechecked whenever the model changes; it can briefly be out of date between a change and the next check, and Ask's own status panel states how many connections may be out of date and why. Budget and cost figures on the strategy and programme answers are visible only to roles with budget authority; every other role sees the same row without the figure. Pair the twin map with [Architecture Model](/modules/architecture-model) for the elements and relationships it reads, and the formats you can import them from.

## Questions it answers

- [If our one cloud platform or payment provider goes down, what stops?](/use-cases/single-point-of-failure)
- [What breaks if this service or platform fails, and who gets called?](/use-cases/what-breaks-and-who-gets-called)
- [Which risks sit on our revenue-critical path, and who owns the mitigation?](/use-cases/risk-blast-radius)
- [Which value streams are at risk because the capabilities under them are immature?](/use-cases/value-streams-at-risk)
- [Is the programme on time and on budget, and what does each project touch?](/use-cases/programme-tracking)
- [Which risks and control gaps touch this goal, and who owns the mitigation?](/use-cases/risk-and-control-gaps)

## Frequently asked questions

### What is a digital twin of an organisation?

A digital twin of an organisation is one model of what you run, what depends on what, who owns it and what it costs, built from the relationships you recorded plus the further connections Entelim works out from them. Ask a question in plain words and you get a checkable answer, not a diagram drawn from memory.

### How is it different from a CMDB?

A configuration management database usually records what exists. Entelim's model adds why each connection exists: the chain of relationships an answer was worked out from, the rule behind it, and who recorded each link and when, so a question like what breaks if this fails comes with its proof, not only a list of affected items.

### Can I see how the model looked last quarter?

Yes. Open Model as of and pick any past date to see every element and relationship exactly as they were recorded then. Changes between two dates lists what was added, updated or removed in that period, with who changed it and the reason, if one was recorded.

### Can an AI assistant query it?

Yes. Once you connect an assistant under your own account, it reads your model through a read-only connection and can answer what stops if something fails, which risks threaten it, and which programmes and initiatives it serves. See [connecting an AI assistant](/connectors/ai-assistants) for how it is set up and what it can and cannot do.

### Is Ask included in the free plan?

Yes. Ask and the twin map are part of the free Community plan, for one organisation and up to three people, and of every paid plan above it. See [Entelim pricing](/pricing) for Startup, Team and Enterprise, or self-host the open-source edition under AGPL-3.0.

## Related modules

- [Architecture Model](/modules/architecture-model)
- [Rationalization](/modules/rationalization)
- [Risk Register](/modules/risk-register)
- [Applications](/modules/applications)

## Plan

Ask and the twin map are part of every plan, including the free Community plan for up to three people. [See Entelim pricing](/pricing) for Startup, Team and Enterprise, browse [all modules](/features), or see how Entelim compares with [other enterprise architecture tools](/vs).
