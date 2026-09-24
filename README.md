<!--
Canonical description (use verbatim on every surface — repo About, PyPI, docs, blog, social):
"Entelim is an open-source, AI-native enterprise architecture platform for TOGAF 9.2 and
ArchiMate 3.2 — model your application portfolio, run an AI-assisted solution-design
journey, and govern designs through an Architecture Review Board (ARB)."
-->

# Entelim — Open-Source Enterprise Architecture Platform (TOGAF 9.2 · ArchiMate 3.2)

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Commercial license available](https://img.shields.io/badge/Commercial%20license-available-success.svg)](COMMERCIAL-LICENSE.md)

**Entelim is an open-source, AI-native enterprise architecture platform for TOGAF 9.2 and
ArchiMate 3.2** — model your application portfolio, run an AI-assisted solution-design
journey, and govern designs through an **Architecture Review Board (ARB)**.

> **Entelim vs Archi:** [Archi](https://www.archimatetool.com/) gives you ArchiMate
> *diagrams* on the desktop. **Entelim** gives you a *governed, AI-driven, web-based
> enterprise architecture* — portfolio, capabilities, vendors, an ARB workflow, and an
> AI architect — and it's open source. If you've searched for an **open-source LeanIX or
> Ardoq alternative**, this is it.

*Formerly archie-ea.*

---

## What it looks like

<!-- Regenerate with scripts/capture_screenshots.py against a seeded demo
     database. These are real screenshots of the running application, not
     mock-ups, so they drift when the UI does -- which is the point. -->

| | |
|---|---|
| **Dashboard** — architecture health, portfolio insight and the work waiting on you, with a lens per persona (CTO, CFO, Architect, Business). <br><br> ![Dashboard](docs/screenshots/dashboard.png) | **Capability map** — business capabilities across domains and architecture layers, with application coverage and gap lenses. <br><br> ![Capability map](docs/screenshots/capability-map.png) |
| **Application portfolio** — lifecycle, criticality, cost and ownership for every application in the estate. <br><br> ![Application portfolio](docs/screenshots/applications.png) | **Architecture Review Board** — submissions, review queue and recorded decisions, with separation of duties enforced server-side. <br><br> ![ARB](docs/screenshots/arb.png) |
| **Value streams** — the business flows your capabilities serve. <br><br> ![Value streams](docs/screenshots/value-streams.png) | **ArchiMate model** — the 3.2 element and relationship model that everything else is derived from. <br><br> ![ArchiMate](docs/screenshots/archimate.png) |

---

## Why Entelim

The open-source EA landscape has one well-known tool — Archi — and it's a desktop
*diagram editor*. Everything with governance, an application portfolio, and AI
(LeanIX, Ardoq, Bizzdesign, Sparx, MEGA) is expensive proprietary SaaS. **Entelim is the
first open-source, web-based, AI-assisted, governed EA platform.**

| | Entelim | Archi | LeanIX / Ardoq / Bizzdesign |
|---|:---:|:---:|:---:|
| Open source | ✅ AGPL-3.0 | ✅ | ❌ |
| Web-based / collaborative | ✅ | ❌ desktop | ✅ |
| ArchiMate 3.2 | ✅ | ✅ | ✅ |
| TOGAF ADM workflow | ✅ | partial | ✅ |
| Application portfolio mgmt | ✅ | ❌ | ✅ |
| Architecture Review Board (ARB) governance | ✅ | ❌ | partial |
| AI architect / design assistant | ✅ | ❌ | partial |
| Self-hostable | ✅ | ✅ | ❌ |

## Features

- **ArchiMate 3.2** across all layers (Strategy, Business, Application, Technology,
  Physical, Motivation, Implementation).
- **TOGAF-aligned solution-design journey** — capture drivers, goals, constraints,
  requirements, risks and options; link real applications, vendors and capabilities.
- **Architecture Review Board (ARB) governance** — maturity scoring, a readiness gate,
  and a submit-to-ARB workflow with an audit trail.
- **AI architect ("Entelim")** — clarifies the problem, generates candidate architectures,
  and flags risks, grounded in *your* portfolio data.
- **Application Portfolio Management** — rationalization, dependencies, lifecycle.

## Open source vs. Commercial

Entelim's **core is free and open source (AGPL-3.0)** — self-host it forever. When you need
to *not* run it yourself, or need enterprise features, there are two hosted products built
on Entelim:

| | **Entelim** (this repo) | **ReqArchitect** (hosted EA) | **Archiet** (spec→code) |
|---|---|---|---|
| What | Self-hosted EA platform | Managed multi-tenant EA SaaS, SSO, real-time collaboration, premium AI, connectors | PRD → production code across 12+ stacks + 7 compliance frameworks |
| For | Architects who self-host | Enterprises that want governed EA without ops | Founders/CTOs shipping compliant apps |
| Link | — | **[reqarchitect.com](https://reqarchitect.com)** | **[archiet.com](https://archiet.com)** |

Need to embed Entelim in a proprietary product or offer it as a service without AGPL
obligations? A **[commercial license](COMMERCIAL-LICENSE.md)** is available.

## Quick start

**Requires PostgreSQL.** (For the AI/embedding features, enable the `vector` extension:
`CREATE EXTENSION IF NOT EXISTS vector;`)

```bash
# 1. Install
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
#    edit .env: SECRET_KEY, DATABASE_URL (postgresql://…), ADMIN_EMAIL, ADMIN_PASSWORD
#    (leave FLASK_CONFIG=development)

# 3. Create the schema + first admin user
flask --app manage init-db
python create_admin.py

# 4. Run
flask --app manage run                               # dev server on :5000
# production:  gunicorn -c gunicorn.conf.py "manage:app"
```

Open http://127.0.0.1:5000 and sign in with the `ADMIN_EMAIL` / `ADMIN_PASSWORD` from your
`.env`. Ships with an **empty schema and no customer data**.

> A `docker-compose.yml` (app + PostgreSQL + Redis) is also included for a containerized
> setup.

## FAQ

**Is there an open-source LeanIX / Ardoq alternative?**
Yes — Entelim is an open-source, web-based enterprise architecture platform with
application portfolio management, ArchiMate 3.2 and ARB governance.

**How is Entelim different from Archi?**
Archi is a desktop ArchiMate *diagram* editor. Entelim is a *governed, AI-driven,
web-based EA platform* (portfolio, ARB workflow, AI architect) — and open source.

**Does Entelim support TOGAF and ArchiMate?**
Yes — ArchiMate 3.2 across all layers, with a TOGAF-aligned ADM solution-design journey
and an Architecture Review Board governance workflow.

**Can I use it commercially?**
Yes, under AGPL-3.0. To avoid AGPL's copyleft obligations (embedding in a proprietary
product, offering it as a hosted service), buy a [commercial license](COMMERCIAL-LICENSE.md).

## Guides

Vendor-neutral enterprise architecture guides (useful with any tool):

- [How to run an Architecture Review Board (ARB)](docs/architecture-review-board.md)
- [ArchiMate 3.2 cheat sheet](docs/archimate-3-2-cheat-sheet.md)
- [Application portfolio rationalization (TIME & 6R)](docs/application-rationalization.md)
- [TOGAF ADM with ArchiMate](docs/togaf-adm-with-archimate.md)
- [Open-source enterprise architecture tools](docs/open-source-enterprise-architecture-tools.md)

## Stack
Python / Flask · PostgreSQL (SQLAlchemy, `db.create_all`) · Tailwind / shadcn-style UI ·
optional LLM providers for the AI assistant · optional Abacus/Avolution portfolio connector.

## License
**AGPL-3.0** — see [`LICENSE`](LICENSE). A **[commercial license](COMMERCIAL-LICENSE.md)**
is available for closed-source/SaaS use. © Anioko.

---
*Topics: `enterprise-architecture` `togaf` `archimate` `ea` `architecture-governance`
`application-portfolio-management` `leanix-alternative` `ardoq-alternative` `archimate-3-2`
`togaf-9-2` `ai-architect`*
