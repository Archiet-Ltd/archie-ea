# Compliance reference library

One JSON file per standard. The seed (`flask --app manage seed-framework-catalogue`, run on every
deploy) loads them into the shared `regulatory_frameworks` and `compliance_controls` tables.

## Control kind

Every control carries `kind`, the ArchiMate 3.2 element it becomes in an organisation's model:

- `"Constraint"`: the control restricts how something may be realised, or forbids an action
  (for example GDPR Art. 22, the right not to be subject to solely automated decisions;
  PCI DSS Req. 7, access restricted by need to know).
- `"Requirement"`: the control states a need or an outcome to achieve (for example GDPR Art. 32,
  security of processing; every NIST CSF 2.0 outcome).

Each pack also states `default_kind` (`"Requirement"` in all five packs). It applies only when a
control has no `kind`, or a value other than the two above. A control with no kind anywhere
(a sample framework that has no pack) reads as a Requirement. The kind is never worked out from
the wording of a control.

## When the seed writes

The seed writes a shared row only when it is new, or when its pack's content hash differs from the
hash recorded on the framework the last time it was written. A re-seed of an unchanged pack changes
nothing. An organisation's own records (adoptions and their notes, control mappings, evidence,
model elements and links) are never read or written by the seed.
