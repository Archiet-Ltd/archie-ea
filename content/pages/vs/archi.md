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
| Plain-language questions over an element | Not part of the tool's own stated scope | Built in — pick an element, get a fixed set of questions (what breaks, who's accountable, what's at risk and more) answered directly |
| Governance workflows | Not part of the tool's own stated scope | Built in |
| Pricing | Free | Published at /pricing; free to self-host under AGPL |

## What Entelim already does

- **Import your existing model.** ArchiMate Open Exchange and .archimate import, with a full element
  browser across every layer — the same Open Group exchange format Archi itself produces.
- **Derivation with provenance.** Entelim shows its reasoning for every connection it works out, in a
  proof drawer you can open, not a number you have to trust.
- **Find what you're paying for twice.** Duplicate detection across your application list, answered
  directly from a plain-language question.
- **A business case built from your own figures**, never invented to fill a gap.

## Where Entelim is headed next

ArchiMate Open Exchange is the working path across; since Archi's own format is ArchiMate-based
already, that tends to be a more direct path than for tools built on a proprietary notation.

## Frequently asked

**Is Archi the same kind of product as Entelim?**
No. Archi, by its own description, is a free and open source editor for creating and sketching
ArchiMate models and diagrams — you build the model by hand. Entelim is a platform: it derives
relationships from a model, runs governance workflows over it, and answers plain-language questions
about it.

**Do we have to publish our code if we use Entelim?**
No. Self-hosting is free under AGPL, the same free and open-source basis Archi itself is built on.
A commercial licence is available for organisations that don't want AGPL's obligations.
