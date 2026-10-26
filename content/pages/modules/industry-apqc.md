---
wave: 3
page_family: module
module_label: "Industry Reference Frameworks"
endpoint: industry_apqc.industry_apqc_dashboard
source: app/modules/industry_apqc/routes/industry_apqc_routes.py, app/services/industry_apqc_service.py, app/models/industry_apqc.py, app/templates/industry_apqc/dashboard.html + framework_detail.html + recommendations.html, app/modules/modules_directory/routes.py + app/utils/role_access.py, read 2026-10-12
state: on_main
answers_use_cases:
  - {id: UC-S4-02, segment: S4}
capture_status: live
cta: plans
description: "Browse six APQC process classification framework profiles by industry, rated for regulatory intensity, digital maturity and process complexity in Entelim."
icon: library
---

# APQC process classification framework

An APQC process classification framework is a structured, industry-specific list of the processes a business runs, used as a common reference rather than a company's own organisation chart. Entelim holds six of these as framework profiles — Cross-Industry, Manufacturing, Banking & Financial Services, Healthcare Provider, Pharmaceutical & Life Sciences and Retail — each rated for regulatory intensity, typical digital maturity and process complexity, browsable from the Industry APQC dashboard. Viewing them is free on every Entelim plan; seeding them for your own organisation needs a platform administrator.

## What an APQC process classification framework is

APQC, the American Productivity & Quality Center, publishes the Process Classification Framework (PCF): a standard description of what a business does, organised into broad categories and then into named processes, written so that two different companies can compare how they work without first agreeing on a shared vocabulary. Alongside the cross-industry PCF, APQC publishes variants for sectors whose processes and regulatory load differ enough to warrant their own version, such as banking, healthcare or manufacturing.

Entelim stores each of these as an industry framework: a named profile with its own PCF version number, a regulatory-intensity rating (low to very high), a typical digital-maturity score out of five, a process-complexity rating, and two lists of named processes — key differentiators and industry-unique process areas — that describe what sets the industry apart from the cross-industry baseline.

This matters before you have recorded a single process of your own. Comparing a pharmaceutical company's profile (highly complex, very high regulatory intensity) against a retailer's (moderate complexity, low regulatory intensity) tells you, in under a minute, how much governance and process detail to expect before anyone on your team starts recording the real thing.

## Who it is for

- **A platform administrator** setting up a new Entelim organisation, who seeds an industry profile as a reference point before anyone records real processes.
- **An enterprise architect or portfolio manager** who wants a quick read on how regulated and digitally mature a sector typically is, before scoping how much process governance a project needs.
- **A consultant or EA lead** comparing more than one industry's typical process complexity ahead of a multi-business-unit engagement.

## How it works in Entelim

1. **Open it from the modules directory.** Industry APQC is not on any one role's own sidebar; open it from the Modules page, under "More tools", or go straight to the Industry APQC dashboard.
2. **Seed the shared frameworks.** On a new organisation the dashboard shows "No Frameworks Available" with a Seed Default Frameworks button. A platform administrator clicks it to create six framework records in one step: Cross-Industry, Manufacturing, Banking & Financial Services, Healthcare Provider, Pharmaceutical & Life Sciences and Retail. Anyone else who clicks it sees an error, because seeding is a platform-administrator action.
3. **Read the dashboard.** Four counts across the top total what has been seeded: Industry Frameworks, Total Processes, Industry-Unique Processes and Regulatory Processes. Below them, a card per framework shows its name, its code and its PCF version, and a Framework Statistics table lists each framework's process counts and its process-complexity rating.
4. **Open a framework.** Clicking a card opens its own page: four stats (Total Processes, Industry Unique, Regulatory, Maturity Target), a Framework Info panel (PCF version, regulatory intensity, process complexity and active status), a Processes by Category breakdown, and a table of up to 20 named processes with their code, name and category, for any process actually recorded against that framework.
5. **Check Recommendations.** The View Recommendations screen lists process-improvement suggestions, filterable by industry and by quick wins, each with a recommendation type, an impact score, an effort score and a priority score out of ten.

## The six frameworks available today

- **Cross-Industry** (PCF 8.0.0, low regulatory intensity, moderate complexity): the baseline PCF that applies to every industry, with no industry-specific differentiators of its own.
- **Manufacturing** (PCF 7.3.0, medium regulatory intensity, complex): differentiated by supply chain and logistics optimisation and production planning and scheduling, with shop floor management and material requirements planning (MRP) named as unique process areas.
- **Banking & Financial Services** (PCF 7.2.0, very high regulatory intensity, highly complex): differentiated by regulatory compliance (SOX, Basel III, Dodd-Frank) and customer onboarding and KYC/AML, with credit risk modelling and regulatory reporting (CCAR, DFAST) named as unique process areas.
- **Healthcare Provider** (PCF 7.1.0, very high regulatory intensity, highly complex): differentiated by patient care delivery and safety and HIPAA compliance, with clinical decision support and utilisation management named as unique process areas.
- **Pharmaceutical & Life Sciences** (PCF 7.0.0, very high regulatory intensity, highly complex): differentiated by FDA compliance (21 CFR Part 11) and clinical trial management, with batch record management and adverse event reporting named as unique process areas.
- **Retail** (PCF 7.2.0, low regulatory intensity, moderate complexity): differentiated by omnichannel customer experience and merchandise planning, with assortment planning and last-mile delivery named as unique process areas.

## What you get

- Six seeded industry framework profiles, each with a PCF version, a regulatory-intensity rating and a digital-maturity score out of five.
- A key-differentiators list and a unique-process-areas list per framework, naming the processes that set the industry apart from the cross-industry baseline.
- Four dashboard counts: frameworks, total processes, industry-unique processes and regulatory processes.
- A framework detail page with a processes-by-category breakdown and a table of named processes, for any framework that has processes recorded against it.
- A recommendations list, filterable by industry and by quick wins, scoring each suggestion for impact, effort and priority.

## Limits

Seeding the six default frameworks creates their profiles and benchmark ratings; it does not add any processes underneath them. A freshly seeded framework reads zero total processes until individual processes are recorded against it, and Entelim has no screen today that adds a process to a framework. Use [the ArchiMate model import](/modules/architecture-model) to build and record your own process and capability model instead.

Seeding is a platform-administrator action, shared across your whole organisation rather than set per person. Everyone signed in can read what has already been seeded.

Entelim's industry code list recognises more sectors than it seeds by default — insurance, telecommunications, utilities, government, education and six more carry a code but no seeded profile yet. Only the six listed above are created when Seed Default Frameworks is run.

The recommendations list reads suggestions already generated from a maturity assessment; it does not include an on-screen form to run that assessment, and with no processes seeded under the default frameworks there is nothing yet to assess.

## Frequently asked questions

### What is an APQC process classification framework?

It is a structured, standardised list of the processes a business runs, published by APQC and organised from broad categories down to named processes, so that different organisations can compare how they work. Entelim stores a version of this list, called a framework, for each of six industries plus a cross-industry baseline.

### Which industries can I see in Entelim today?

Six: Cross-Industry, Manufacturing, Banking & Financial Services, Healthcare Provider, Pharmaceutical & Life Sciences and Retail. Each carries its own PCF version, regulatory-intensity rating and digital-maturity score. Entelim's industry code list recognises more sectors, including insurance, telecommunications and utilities, but only these six have a seeded profile you can open today.

### Does seeding a framework add processes to my own model?

No. Seeding creates the framework's profile — its PCF version, regulatory-intensity rating and differentiator lists — not a set of process records underneath it. A newly seeded framework shows zero total processes. Record your own processes through [the ArchiMate model import](/modules/architecture-model) instead.

### Who can seed the default frameworks?

A platform administrator. The Seed Default Frameworks button is visible to anyone who opens the dashboard, but clicking it without platform-administrator access returns an error rather than creating anything. Once seeded, the six frameworks are shared across the whole organisation, so everyone signed in can open and read them straight away.

### Is there a free plan?

Yes. The Community plan is free with no time limit, for one organisation and up to three people, and includes the Industry APQC dashboard. You can also self-host the open-source edition under AGPL-3.0. See [Entelim pricing](/pricing) for Startup, Team and Enterprise.

## Related modules

- [Architecture model](/modules/architecture-model)
- [Vendors](/modules/vendors)

## Plan

Viewing the industry frameworks is included on every Entelim plan, from the free Community plan upward; seeding frameworks for your own organisation needs a platform administrator. See [Entelim pricing](/pricing), or browse [all modules](/features).
