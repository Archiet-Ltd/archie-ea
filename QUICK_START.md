# QUICK_START.md — Expert Agent Startup (5 steps)
## For agents that already know the codebase

> ## ⚠️ OUT OF DATE — DO NOT FOLLOW IN THIS REPOSITORY
>
> This document describes the upstream private repository. Every file and command it
> references is **absent from this open-source extract**: `DOMAIN.md`,
> `DATA_REALITY.md`, `docs/design_system/pattern_registry.json`,
> `docs/design_system/token_map.json`, and all of `scripts/guardrails/` — so the
> claim/release task workflow in Step 5 cannot be run here. It also cites production
> SSH access and data counts (844 applications, 720 ArchiMate elements) that describe
> a database this repo does not ship.
>
> **Use [`CLAUDE.md`](CLAUDE.md) instead**, and `python scripts/verify.py` for the
> verification steps this document gestures at. `DESIGN.md` is the live UI contract.
>
> Kept for historical reference only.

---

This is the fast path. If you are new to this codebase, read CLAUDE.md instead.

---

### Step 1: Data context

Read **DOMAIN.md** sections 4 (data counts) and 5 (existing APIs).
- 844 applications, 720 ArchiMate elements, 358 vendors, 460 products
- Search APIs: `/api/enterprise/applications?search=`, `/api/archimate/elements/search?q=`

### Step 2: Actual field values

Read **DATA_REALITY.md** (actual production field distributions).
- `lifecycle_status` uses Abacus codes: "2.1 STRATEGIC", "5. DECOMMISSIONED", etc.
- `deployment_status` is "development" for all 844 rows (not useful)
- 13 fields are 100% NULL (business_criticality, application_owner, costs, risks)

### Step 3: UI patterns

Read **`app/static/css/shadcn_tokens.css`** and **`docs/reuse-register.yml`** (canonical UI tokens and patterns).
- Every modal, card, table, button, badge has one correct implementation
- Do not invent new patterns; use existing ones

### Step 4: Verify assumptions

Before writing any filter, query, or mapping:
```bash
# Profile a table to see actual values
flask data-profile --table application_components --fields lifecycle_status,deployment_status

# Run a read-only SQL query
flask db-query "SELECT lifecycle_status, count(*) FROM application_components GROUP BY lifecycle_status ORDER BY count(*) DESC"

# Or SSH to production
ssh root@127.0.0.1 "cd /opt/archie && source venv/bin/activate && python3 -c \"...\""
```

### Journey → codegen (expectations)

Read **docs/ARCHITECTURE_JOURNEY_CODEGEN_ASSURANCE.md** before changing journey, ACM properties, UML enrichment, or codegen. User-facing copy should promise **traceability and previewable output verified against criteria**, not unbounded accuracy percentages.

### Step 5: Build, commit, release

1. Claim: `python scripts/guardrails/claim_task.py --task <ID> --role <ROLE> --agent <NAME>`
2. Implement (follow role boundaries — fixer edits source, tester edits tests)
3. Run guardrails: `python scripts/guardrails/check_ui_task_compliance.py --task <ID>`
4. Commit: stage specific files only (`git add <file>`)
5. Release: `python scripts/guardrails/release_task.py --status <TARGET>`

---

### Feature branch mode (multi-task work)

When working on multiple related tasks on a single feature branch:
```bash
python scripts/guardrails/claim_task.py --task RATA-001 --role fixer --agent my-agent \
  --mode feature-branch --tasks RATA-001,RATA-002,RATA-003
```
This combines all file_paths into one allowed_files list so you can work across tasks
without re-claiming.

---

**Full governance docs:** See CLAUDE.md for the complete mandatory startup sequence,
absolute rules, WCTG protocol, and all guardrail commands.
