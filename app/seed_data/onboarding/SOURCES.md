# Where this data comes from

These five files are copied unchanged from Archiet's own organisation-setup reference data, so
Entelim's "Tell us more" offers the same industries, sectors, company sizes, regions, compliance
standards, frameworks, transformation templates and implementation types as Archiet does, from one
place.

| File | Copied from |
|---|---|
| `organization_metadata.json` | `reqarchi-dashboard/lib/mock-data/organization-metadata.json` — industries, sectors, company-size bands, regions (with each region's recommended compliance standards) and the five-level governance maturity scale. |
| `compliance_standards.json` | `reqarchi-dashboard/lib/mock-data/compliance-standards.json` — the twenty-one compliance standards, the region-to-standard recommendation map, and the industry-to-standard map. |
| `frameworks.json` | `reqarchi-dashboard/lib/mock-data/frameworks.json` — the ten frameworks (COBIT, ITIL, ISO 27001, NIST CSF, TOGAF, SAFe, Agile, DevOps, Lean Six Sigma, OKR) and their eight categories. |
| `digital_transformation_templates.json` | `reqarchi-dashboard/lib/mock-data/digital-transformation-templates.json` — the six transformation templates (CRM, ERP, data platform, cloud migration, digital workplace, e-commerce), each with its phases, and the four complexity levels. |
| `implementation_types.json` | `reqarchi-dashboard/lib/mock-data/implementation-types.json` — the four implementation types (enterprise platform, custom development, system integrations, governance-only) and the selection guidance by company size and industry. |
| `security_compliance_templates.json` | `reqarchi-core/data/security_compliance_templates.json` — Archiet's richer, API-backed compliance template set (control counts, publishers, tooling). Kept alongside `compliance_standards.json` for completeness; `reference_data.py` reads `compliance_standards.json` as the operative list because it is the one the recommended/common/other grouping algorithm (`organization-data-loader.ts`) actually runs over. |

The five-step maturity scale (Not Implemented / Planning / Partially Implemented / Substantially
Implemented / Fully Implemented) is copied from `compliance-maturity-step.tsx`'s `maturityLevelOptions`
and lives in `reference_data.py` directly, since it is a short, fixed list rather than a separate data
file in the source.

The recommended / common / other grouping in `reference_data.py`'s `grouped_compliance_standards()`
reproduces `organization-data-loader.ts`'s `getRecommendedComplianceStandards()` exactly: recommended is
a region's or industry's own named standards; common is everything else international or "all-region"
with a priority of 1 or 2; other is the rest.
