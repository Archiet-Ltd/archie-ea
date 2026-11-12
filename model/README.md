# Entelim system model

The model of what Entelim must do, how it is built and how each requirement is proven, in SysML v2 textual notation.

- `library/` the constructs used and what each means here (authored)
- `requirements/` needs, the twelve release-rule lines, and requirements (authored)
- `architecture/` parts, interfaces, the allocation of functions, constraints and the tests that verify (authored)
- `as-built/` what the code actually has, written by `scripts/sysml/as_built.py` from the routes and forms in `docs/component-inventory.json` (the one reader of both) and from every reader of the modelled fields under `app/` (generated, never edited; the `as-built-current` gate fails when it is stale)
- `schema/ifml.schema.json` the shape of the screen interaction model
- `scope.json` which slice is modelled (routes by address tail, screens, fields) — not which readers are read; `ratchet.json` the findings a check may not exceed

Run it (the parser is free and installs into one folder, no sudo):

    SYSML_HOME=<folder> scripts/sysml/install_parser.sh
    SYSML_HOME=<folder> python scripts/sysml/model_check.py check
    SYSML_HOME=<folder> python scripts/sysml/model_check.py check --root <another checkout of the code>
    SYSML_HOME=<folder> python scripts/sysml/model_check.py matrix
    python scripts/sysml/model_check.py impact --open <brief.md> --new <brief.md>
