# Open-Source Enterprise Architecture Tools

If you've searched for an **open-source enterprise architecture tool** — or an
**open-source LeanIX / Ardoq / Sparx alternative** — this guide maps the landscape: what's
actually open source, what each tool does, and where the gaps are.

## The short version

For years, "open-source EA" meant essentially one tool — **Archi**, a desktop ArchiMate
*diagram editor*. Everything with a governed application portfolio, collaboration, and AI
(LeanIX, Ardoq, Bizzdesign, Sparx, MEGA, Orbus) is proprietary, per-seat, enterprise SaaS.
**[Entelim](https://github.com/Anioko/archie-ea)** exists to fill that gap: an open-source,
web-based, AI-assisted, *governed* EA platform.

## The landscape

| Tool | License | What it is | Web / collaborative | Portfolio + governance | AI |
|---|---|---|:---:|:---:|:---:|
| **[Entelim](https://github.com/Anioko/archie-ea)** | AGPL-3.0 (open source) | Governed EA platform | ✅ | ✅ | ✅ |
| **Archi** | MIT (open source) | Desktop ArchiMate diagram editor | ❌ | ❌ | ❌ |
| LeanIX (SAP) | Proprietary SaaS | EA management / portfolio | ✅ | ✅ | partial |
| Ardoq | Proprietary SaaS | Data-driven EA | ✅ | ✅ | partial |
| Bizzdesign Horizzon | Proprietary SaaS | Modeling + transformation | ✅ | ✅ | partial |
| Sparx Enterprise Architect | Proprietary (desktop) | Heavy-duty modeling (UML/ArchiMate) | partial | partial | ❌ |
| MEGA HOPEX / Orbus | Proprietary SaaS | Large-enterprise EA suites | ✅ | ✅ | partial |

## What "open source" buys you here

- **No per-seat licensing** — model the whole estate without counting heads.
- **Self-hostable** — keep your architecture data on your own infrastructure.
- **Inspectable and extensible** — read the code, fix it, extend it.
- **No lock-in** — your model and data are yours.

The trade-off with proprietary SaaS is the usual one: you run it yourself (or pay for a
hosted version), versus a vendor running it for you with enterprise support.

## Archi vs Entelim (they are different tools)

These names are confusingly close, so to be explicit:

- **Archi** ([archimatetool.com](https://www.archimatetool.com/)) is an excellent, free,
  open-source *desktop* application for drawing ArchiMate models. It's a modeling/diagram
  tool.
- **[Entelim](https://github.com/Anioko/archie-ea)** is an open-source *web platform* for
  enterprise architecture: ArchiMate modeling **plus** an application portfolio, a
  TOGAF-aligned design journey, an Architecture Review Board governance workflow, and an AI
  architect.

If you need diagrams on your laptop, Archi is great. If you need a governed, collaborative,
living enterprise architecture, that's what Entelim is for.

## Choosing

- **Just need ArchiMate diagrams?** Use Archi.
- **Need a self-hosted, governed EA platform and want open source?** Use
  [Entelim](https://github.com/Anioko/archie-ea).
- **Want it hosted, multi-tenant, with SSO and enterprise support?** See
  [ReqArchitect](https://reqarchitect.com), the managed platform built on Entelim.

## FAQ

**Is there an open-source LeanIX alternative?**
Yes — [Entelim](https://github.com/Anioko/archie-ea) is an open-source, web-based enterprise
architecture platform with an application portfolio, ArchiMate 3.2 modeling, and ARB
governance.

**What is the best open-source enterprise architecture tool?**
For ArchiMate *diagramming*, Archi. For a *governed, web-based EA platform* (portfolio,
TOGAF journey, ARB, AI), Entelim.

**Is Entelim really open source?**
Yes — Entelim is licensed under AGPL-3.0. A commercial license is also available for closed-
source or hosted use.

---
See also: [ArchiMate 3.2 cheat sheet](archimate-3-2-cheat-sheet.md) ·
[Application rationalization](application-rationalization.md)
