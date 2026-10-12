---
page_family: comparison
competitor: Archi (archimatetool.com)
url_slug: archiet.ai/vs/archi
routing: >
  New page, placed at its archiet.ai address to match the rest of the /vs comparison cluster rather
  than entelim.org directly, consistent with the 2026-09-23 peer ruling covering the existing three
  comparison pages. Cross-links to entelim.org's own comparison hub.
sources:
  - url: https://www.archimatetool.com/
    read_date: 2026-10-04
    fact: "Archi's own site describes it as 'the Open Source modelling toolkit for creating ArchiMate models and sketches'"
  - url: https://github.com/archimatetool/archi
    read_date: 2026-10-04
    fact: "GitHub repository: 'Archi® is a free, open source, cross-platform tool and editor to create ArchiMate models,' targeted at all levels of enterprise architecture practice"
compliance_note: >
  Comparative claims only where sourced and linked. Both products are free and open source at
  their core; the comparison is about what each one does, not price. No superlative not backed by
  a specific, checkable fact. Nominative use of the Archi trademark only.
---

# Entelim vs Archi

*A factual comparison for teams evaluating enterprise architecture tools.*

## The short answer

Archi is a free, open source, cross-platform editor for creating and sketching ArchiMate models by
hand — confirmed directly on its own site and its GitHub repository. It is a modelling tool: you
draw the model yourself, element by element. Entelim is also free and open source (AGPL) at its
core, but it is a platform built on top of ArchiMate modelling that derives relationships from what
you've modelled, runs governance workflows over it, and answers a fixed set of plain-language
questions about any element you pick — rather than a canvas you edit by hand. Entelim's own
[plans and pricing](/pricing) are published directly for the organisations that want the commercial
licence instead of AGPL.

## What each product actually is

| | Archi | Entelim |
|---|---|---|
| Vendor | Open source community project (archimatetool.com) | Archiet Ltd |
| Licence | Free, open source | Open source, AGPL, plus a commercial licence |
| What it is | A modelling editor — you create and sketch ArchiMate models and diagrams by hand | A platform — modelling, plus derivation, governance workflows and plain-language questions over any element you pick |
| Self-hostable | Desktop application, runs locally | Yes, self-hosted or managed |
| Derivation / impact analysis | Not part of the tool's own stated scope | Built in — shows its reasoning for every connection it works out |
| Plain-language questions over an element | Not part of the tool's own stated scope | Built in — pick an element, get a fixed set of questions (what breaks and who gets called, what's at risk, what we're trying to achieve and more) answered directly |
| Governance workflows | Not part of the tool's own stated scope | Built in |
| Pricing | Free | Published on the [pricing page](/pricing); free to self-host under AGPL |

## What Entelim already does

- **Import your existing model.** ArchiMate Open Exchange and .archimate import, with a full element
  browser across every layer — the same Open Group exchange format Archi itself produces.
- **Derivation with provenance.** Entelim shows its reasoning for every connection it works out, in a
  proof drawer you can open, not a number you have to trust.
- **Find what you're paying for twice.** Run duplicate detection across your whole application list,
  review the overlapping groups it finds, and add them to a consolidation plan.
- **A business case built from your own figures**, never invented to fill a gap.

## Bringing your model across

ArchiMate Open Exchange is the working path across; since Archi's own format is ArchiMate-based
already, that tends to be a more direct path than for tools built on a proprietary notation.

## Frequently asked questions

### Is Archi the same kind of product as Entelim?

No. Archi describes itself as a free, open source editor for creating ArchiMate models and sketches, so you build the model by hand. Entelim is a platform on top of ArchiMate: it derives relationships from your model, runs governance workflows over it, and answers a fixed set of questions about any element you pick.

### How do we move from Archi to Entelim, and how much work is it?

Archi produces ArchiMate Open Exchange files, and Entelim imports both those and Archi files, so there is no conversion step. Import the file with batch import, then check the element counts and relationships in the import history. If you want it done for you, the [architecture health check](/architecture-health-check) costs a fixed $4,500.

### Will we lose data moving from Archi?

Elements, relationships and properties come across in the file. Nothing is removed from Archi by importing, and your Archi file is unchanged, so you can compare the two element by element. Keep the Archi file as your original.

### What would Entelim cost us compared with Archi?

Archi is free, and so is the Entelim Community plan for up to three people, as is self-hosting under AGPL. The difference appears when you want the platform around the model: Startup is $49 a month for up to ten people, and Team is $29 per editor a month, or $290 billed annually, with read-only members free.

### What would we give up by leaving Archi?

Nothing, if you do not have to: you can keep drawing in Archi and import the file whenever you want the analysis. What Archi gives you is a desktop editor for drawing, and you would only give that up if your team stops using it for modelling and works in Entelim's browser tools instead.

### What would we gain by adding Entelim to Archi?

Derived connections with a proof drawer that shows the reasoning, questions anyone can ask by picking an element, such as what breaks and who gets called, duplicate detection across your application list, a business case from your own figures and a review board workflow. Several people can work in one shared model.

### Can we run Archi and Entelim side by side?

Yes. Model in Archi, import the Open Exchange or Archi file into Entelim, and ask your questions there. Re-import after changes; the import history shows what came in and when.

### Do we have to publish our code if we use Entelim?

No. Self-hosting is free under AGPL, and a commercial licence is available for organisations that do not want AGPL's obligations.

## Sources

The facts about Archi on this page come from the public pages below, each read on the date shown.

- [archimatetool.com](https://www.archimatetool.com/), read 4 October 2026. Describes Archi as the open source modelling toolkit for creating ArchiMate models and sketches.
- [Archi repository on GitHub](https://github.com/archimatetool/archi), read 4 October 2026. Describes Archi as a free, open source, cross-platform tool and editor to create ArchiMate models, for all levels of enterprise architecture practice.
