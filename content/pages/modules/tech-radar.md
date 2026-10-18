---
page_family: module
module_label: "Tech Radar"
endpoint: tech_radar.index
source: app/modules/tech_radar/routes.py, service.py, app/templates/tech_radar/index.html, app/models/tech_radar.py, read 18 October 2026
state: on_main
answers_use_cases: []
capture_status: live
cta: plans
description: "A technology radar sorts the technology you already run into adopt, trial, assess and hold. Entelim's Tech Radar does it on your architecture model. Start free."
icon: radar
---

# Technology radar

A technology radar is a short, shared list of which technologies your organisation should adopt, trial, assess or put on hold, so every team builds on the same decisions. Entelim's Tech Radar sorts the technology already in your architecture model into those four rings, with the reason and a review date beside each call. It is for the enterprise architect or CTO who sets technology direction, and it is part of every plan, including the free Community plan.

## What a technology radar is

A technology radar is a record of technology decisions in four rings. **Adopt** means use it as the default. **Trial** means use it on a real project to learn whether it fits. **Assess** means look into it, without building on it yet. **Hold** means do not start anything new with it. Organisations use the format because it turns "which database should we use?" from a debate held on every project into a decision made once and visible to everyone.

Most radars are drawn as a slide or a page on a wiki. They go out of date the day after they are published, and nothing ties a blip on the chart to the system it describes. When a team asks whether a message broker is approved, the answer is in someone's memory.

Entelim keeps the radar on the architecture model instead. The technologies it classifies are the ones already recorded in the model's technology layer, so a ring is attached to a real element that other screens can already see, not to a name typed into a separate list.

## Who it is for

- **An [enterprise architect](/roles/enterprise-architects)** who sets standards and wants the answer to "is this approved?" to live somewhere other than a slide.
- **A CTO or CIO** who needs to see how much of the estate runs on technology marked adopt, and how much on technology marked hold.
- **A security or solution architect** checking whether a design relies on something the organisation has decided to move away from.

## How it works in Entelim

1. **Record the technology in the model.** The radar classifies the technology layer of the [architecture model](/modules/architecture-model): nodes, devices, system software and technology services. If your infrastructure is already recorded, directly or through an application's technology stack, it is already on the radar as unclassified. If nothing is recorded yet, the radar says so and tells you what it needs.
2. **Open the radar.** Tech Radar shows a count for each ring across the top, then one list per ring, then a list of everything not yet classified.
3. **Classify a technology.** Pick a ring for any unclassified item. Before you save, you can open the details panel to add a rationale, a review date and the requesting initiative, the strategic initiative that asked for the call, found by searching your initiatives and programmes.
4. **Read the decision.** Each classified technology shows its ArchiMate type, its rationale, the date it is due for review and the initiative that requested the call. Under it sits a short list of the other elements of the same type in the model, each with its ring or "not classified", so you can see what else exists for the same job before choosing.
5. **Move it as decisions change.** Every classified item has a ring selector and a Move button. Moving a technology from trial to adopt leaves its rationale, review date and requesting initiative as recorded, until you change them.

## What you get

- A count and a list for each of the four rings: adopt, trial, assess and hold.
- A rationale, a review-by date and a requesting initiative on every call, so a decision can be questioned later.
- A list of every technology in the model that has not been classified, so the gaps in the radar are visible.
- Peer comparison for each technology: the other elements of the same ArchiMate type and the ring each one has.
- The same data as JSON from the radar's API route, with the total number of candidates, how many are classified and the rings with their entries.

## Limits

The radar classifies technology that is already in your model; it does not scan your network or cloud accounts to discover what is running, and it cannot create a technology that is not recorded. "Same purpose" is answered as far as the model records it: other elements of the same ArchiMate type, such as another piece of system software for a piece of system software. The model holds no finer description of purpose, so the radar claims none.

An item with no decision stays "not classified" and is never placed on a ring by default. The rings are lists, not a plotted chart: Entelim shows each ring as a list with its entries beneath. Architects and administrators can classify and move technologies; other signed-in users can read the radar. Pair it with [Applications](/modules/applications) and [Rationalization](/modules/rationalization) when the decision is about which applications to keep.

## Frequently asked questions

### What is a technology radar?

A technology radar is a list of technologies sorted into adopt, trial, assess and hold, with the reasoning for each. It gives every team the same answer to which technologies the organisation backs, which it is testing and which it is moving away from.

### What do adopt, trial, assess and hold mean?

Adopt means use it as the default. Trial means use it on a real project to learn whether it fits. Assess means investigate it without building on it. Hold means start nothing new with it. Entelim uses exactly these four rings, and each call carries its rationale and a review date.

### Can I add a technology that is not in the model?

No. The radar only classifies technology already recorded in the model's technology layer, so every entry is tied to a real element. Record the node, device, system software or technology service in the architecture model first, and it appears on the radar as unclassified.

### Who can change the radar?

Architects and administrators can classify a technology and move it between rings. Other signed-in users can open the radar and read every decision, including its rationale, review date and requesting initiative.

### Is the Tech Radar in the free plan?

Yes. The Tech Radar is part of the free Community plan, for one organisation and up to three people, and of every paid plan above it. See [Entelim pricing](/pricing) for Startup, Team and Enterprise, or self-host the open-source edition under AGPL-3.0.

## Related modules

- [Architecture Model](/modules/architecture-model)
- [Applications](/modules/applications)
- [Rationalization](/modules/rationalization)
- [Digital twin of your organisation](/modules/digital-twin)

## Plan

The Tech Radar is part of every plan, including the free Community plan for up to three people. [See Entelim pricing](/pricing) for Startup, Team and Enterprise, browse [all modules](/features), or see how Entelim compares with [other enterprise architecture tools](/vs).
