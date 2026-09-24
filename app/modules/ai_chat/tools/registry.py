"""
Tool registry for AI Chat agent mode.

Each schema defines one operation the LLM can invoke. The 'tier' field controls
execution behaviour:
  - 'auto'    : executed immediately, no user confirmation required
  - 'approve' : queued for explicit user confirmation before any DB write

Tools always accept names (not IDs) — the EntityResolver converts them.
"""

TOOL_SCHEMAS = [
    {
        "name": "create_solution",
        "mutates": True,
        "description": (
            "Create a new architectural solution in the repository. "
            "Use when the user asks to design, propose, plan, or create a new solution, "
            "programme, or initiative."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short, descriptive name for the solution (e.g. 'CRM Modernisation')",
                },
                "description": {
                    "type": "string",
                    "description": "Business problem the solution addresses",
                },
                "business_domain": {
                    "type": "string",
                    "description": "Primary business domain",
                    "enum": [
                        "customer", "finance", "hr", "operations",
                        "technology", "supply_chain", "risk", "legal",
                    ],
                },
                "solution_type": {
                    "type": "string",
                    "description": "Classification of the solution",
                    "enum": ["Platform", "Product", "Service", "Integration", "Migration"],
                },
                "allow_duplicate": {
                    "type": "boolean",
                    "description": (
                        "Only set true after the tool has reported a DUPLICATE_NAME "
                        "conflict and the user has confirmed a second solution with "
                        "that name is genuinely wanted. Default false."
                    ),
                },
            },
            "required": ["name", "description"],
        },
        "tier": "auto",
    },
    {
        "name": "link_capability_to_solution",
        "mutates": True,
        "description": (
            "Link a business capability to a solution to show what capabilities "
            "the solution delivers, enables, or affects. "
            "Use when the user wants to map capabilities to a solution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_name": {
                    "type": "string",
                    "description": "Name of the solution (fuzzy matched)",
                },
                "capability_name": {
                    "type": "string",
                    "description": "Name of the business capability (fuzzy matched)",
                },
                "support_level": {
                    "type": "string",
                    "description": "How the solution supports this capability",
                    "enum": ["primary", "secondary", "planned", "partial"],
                },
                "notes": {
                    "type": "string",
                    "description": "Optional rationale or notes for the mapping",
                },
            },
            "required": ["solution_name", "capability_name"],
        },
        "tier": "auto",
    },
    {
        "name": "link_application_to_capability",
        "mutates": True,
        "description": (
            "Map an application to a business capability it supports. "
            "Use when the user wants to record which applications cover a capability."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "application_name": {
                    "type": "string",
                    "description": "Name of the application (fuzzy matched)",
                },
                "capability_name": {
                    "type": "string",
                    "description": "Name of the business capability (fuzzy matched)",
                },
                "coverage_level": {
                    "type": "string",
                    "description": "Degree to which the application covers the capability",
                    "enum": ["full", "partial", "planned"],
                },
                "notes": {"type": "string"},
            },
            "required": ["application_name", "capability_name"],
        },
        "tier": "auto",
    },
    {
        "name": "create_archimate_element",
        "mutates": True,
        "description": (
            "Create a new ArchiMate element and optionally attach it to a solution. "
            "Use when the user asks to model a component, service, process, data object, "
            "or any other ArchiMate concept."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Element name (e.g. 'Customer Data Service')",
                },
                "type": {
                    "type": "string",
                    "description": (
                        "ArchiMate element type. Examples: ApplicationComponent, "
                        "ApplicationService, BusinessProcess, BusinessFunction, "
                        "DataObject, TechnologyService, SystemSoftware, Node"
                    ),
                },
                "layer": {
                    "type": "string",
                    "description": "ArchiMate layer",
                    "enum": ["business", "application", "technology", "motivation", "implementation"],
                },
                "description": {"type": "string"},
                "solution_name": {
                    "type": "string",
                    "description": "Solution to attach this element to (optional, fuzzy matched)",
                },
                "allow_duplicate": {
                    "type": "boolean",
                    "description": (
                        "Only set true after the tool has reported a DUPLICATE_NAME "
                        "conflict and the user has confirmed a second element of the "
                        "same name and type is genuinely wanted. Default false."
                    ),
                },
            },
            "required": ["name", "type", "layer"],
        },
        "tier": "auto",
    },
    {
        "name": "update_application_status",
        "mutates": True,
        "description": (
            "Update the deployment/lifecycle status of an application. "
            "Use when the user wants to mark an application as retiring, "
            "decommissioned, in production, strategic, etc. "
            "REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "application_name": {
                    "type": "string",
                    "description": "Name of the application (fuzzy matched)",
                },
                "new_status": {
                    "type": "string",
                    "description": "New lifecycle/deployment status",
                    "enum": [
                        "design", "development", "testing",
                        "production", "retiring", "decommissioned",
                    ],
                },
                "rationale": {
                    "type": "string",
                    "description": "Business reason for the status change",
                },
            },
            "required": ["application_name", "new_status", "rationale"],
        },
        "tier": "approve",
    },
    {
        "name": "submit_for_arb_review",
        "mutates": True,
        "description": (
            "Submit a solution for Architecture Review Board (ARB) governance review. "
            "REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_name": {
                    "type": "string",
                    "description": "Name of the solution to submit (fuzzy matched)",
                },
                "notes": {
                    "type": "string",
                    "description": "Additional context or questions for the ARB",
                },
            },
            "required": ["solution_name"],
        },
        "tier": "approve",
    },
    {
        "name": "query_capability_gaps",
        "mutates": False,
        "description": (
            "Find business capabilities with no supporting applications, "
            "or capabilities below a specified maturity threshold. "
            "Read-only — safe to execute without confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "max_maturity": {
                    "type": "integer",
                    "description": "Return capabilities AT or BELOW this maturity level (1-5). Default 2.",
                    "minimum": 1,
                    "maximum": 5,
                },
                "business_domain": {
                    "type": "string",
                    "description": "Filter by business domain (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default 20)",
                    "default": 20,
                    "maximum": 100,
                },
            },
        },
        "tier": "auto",
    },
    {
        "name": "find_applications",
        "mutates": False,
        "description": (
            "Search for applications by name, lifecycle status, or capability. "
            "Returns lifecycle_status (planning/development/testing/operational/"
            "deprecated/retired — matches the Applications UI and "
            "/applications/api/list exactly) for each row. "
            "Read-only — safe to execute without confirmation. "
            "Use when the user asks what applications exist, "
            "which apps support a capability, or wants to find a specific app."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name_contains": {
                    "type": "string",
                    "description": "Partial name to search for",
                },
                "lifecycle_status": {
                    "type": "string",
                    "description": "Filter by lifecycle status — the same value shown in the Applications UI and returned by /applications/api/list.",
                    "enum": [
                        "planning", "development", "testing",
                        "operational", "deprecated", "retired",
                    ],
                },
                "capability_name": {
                    "type": "string",
                    "description": "Return only apps linked to this capability",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default 15)",
                    "default": 15,
                    "maximum": 50,
                },
            },
        },
        "tier": "auto",
    },
    # ------------------------------------------------------------------ #
    # J1 CRUD tools                                                       #
    # ------------------------------------------------------------------ #
    {
        "name": "create_driver",
        "mutates": True,
        "description": (
            "Add a business driver to a solution (ArchiMate Motivation layer). "
            "Use when the user says a solution is motivated by cost pressure, compliance, "
            "market demand, or any other business force."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer", "description": "Solution ID (injected from context when on blueprint page)"},
                "name": {"type": "string", "description": "Short driver name, e.g. 'Regulatory Compliance Pressure'"},
                "description": {"type": "string", "description": "Explanation of the driver"},
                "driver_type": {
                    "type": "string",
                    "enum": ["technology", "stakeholder", "external", "internal"],
                    "description": "Category of driver",
                },
            },
            "required": ["solution_id", "name", "driver_type"],
        },
        "tier": "auto",
    },
    {
        "name": "create_goal",
        "mutates": True,
        "description": (
            "Add a goal to a solution (ArchiMate Motivation layer). "
            "Use when the user describes a desired outcome or success criterion."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "name": {"type": "string", "description": "Goal statement, e.g. 'Reduce TCO 20% in 12 months'"},
                "description": {"type": "string"},
                "priority": {"type": "integer", "description": "1 (highest) to 5 (lowest)", "minimum": 1, "maximum": 5},
            },
            "required": ["solution_id", "name"],
        },
        "tier": "auto",
    },
    {
        "name": "create_constraint",
        "mutates": True,
        "description": (
            "Add a constraint to a solution (ArchiMate Motivation layer). "
            "Use when the user mentions a hard limit: budget cap, regulatory requirement, timeline, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "name": {"type": "string", "description": "Constraint name, e.g. 'Budget cap £500k'"},
                "description": {"type": "string"},
                "constraint_type": {
                    "type": "string",
                    "enum": ["budget", "timeline", "resource", "compliance", "technical", "organizational"],
                },
                "severity": {"type": "integer", "description": "1 (soft) to 5 (hard limit)", "minimum": 1, "maximum": 5},
            },
            "required": ["solution_id", "name", "constraint_type"],
        },
        "tier": "auto",
    },
    {
        "name": "create_requirement",
        "mutates": True,
        "description": (
            "Add a functional or non-functional requirement to a solution. "
            "Use when the user specifies something the solution MUST do or achieve."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "name": {"type": "string", "description": "Requirement name, e.g. 'Support 1000 concurrent users'"},
                "description": {"type": "string"},
                "requirement_type": {
                    "type": "string",
                    "enum": ["functional", "quality", "constraint"],
                },
            },
            "required": ["solution_id", "name", "description", "requirement_type"],
        },
        "tier": "auto",
    },
    {
        "name": "create_risk",
        "mutates": True,
        "description": (
            "Add a risk to a solution risk register. "
            "Use when the user identifies a threat, concern, or uncertainty for the solution."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "risk_description": {"type": "string", "description": "Description of the risk"},
                "impact": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "probability": {"type": "string", "enum": ["low", "medium", "high"]},
                "mitigation": {"type": "string", "description": "Optional mitigation strategy"},
            },
            "required": ["solution_id", "risk_description", "impact", "probability"],
        },
        "tier": "auto",
    },
    {
        "name": "create_option",
        "mutates": True,
        "description": (
            "Add a Phase E solution option/recommendation. "
            "Use when the user describes an approach: buy a product, build custom, reuse existing, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "name": {"type": "string", "description": "Option name, e.g. 'Buy Vendor Solution'"},
                "option_type": {
                    "type": "string",
                    "enum": ["buy", "build", "reuse", "partner", "hybrid"],
                },
            },
            "required": ["solution_id", "name", "option_type"],
        },
        "tier": "auto",
    },
    {
        "name": "mark_option_recommended",
        "mutates": True,
        "description": (
            "Mark one solution option as the architect's recommended choice. "
            "Use when the user selects or endorses a specific option."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "option_name": {"type": "string", "description": "Name of the option to mark (fuzzy matched)"},
            },
            "required": ["solution_id", "option_name"],
        },
        "tier": "auto",
    },
    {
        "name": "link_application_to_solution",
        "mutates": True,
        "description": (
            "Link an existing application from the catalog to a solution. "
            "Use when the user says a solution involves, replaces, or integrates with an application."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "application_name": {"type": "string", "description": "Application name (fuzzy matched against 850 apps)"},
                "role": {
                    "type": "string",
                    "enum": ["primary", "supporting", "integrating"],
                    "description": "How the application relates to the solution",
                },
            },
            "required": ["solution_id", "application_name"],
        },
        "tier": "auto",
    },
    {
        "name": "link_vendor_product",
        "mutates": True,
        "description": (
            "Link a vendor product from the catalog to a solution (Phase E). "
            "Use when the user identifies a commercial product as part of the technology stack."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "vendor_product_name": {"type": "string", "description": "Vendor product name (fuzzy matched)"},
            },
            "required": ["solution_id", "vendor_product_name"],
        },
        "tier": "auto",
    },
    {
        "name": "run_inference_engine",
        # Real write, not read-only despite the "diagnose"-adjacent name:
        # _tool_run_inference_engine (tools/executor.py) defaults dry_run to
        # False from args.get("dry_run", False) and calls
        # engine.repair(link.element_id, dry_run=dry_run) unconditionally.
        # ArchiMateInferenceEngine.repair -> repair_chain ->
        # get_or_create_node/get_or_create_relationship
        # (architecture_graph_facade.py) call db.session.add + db.session.flush,
        # committed by the chat turn's later db.session.commit(). The tool's own
        # schema description below says as much: "If true, show what would be
        # created without writing to DB. Default false."
        "mutates": True,
        "description": (
            "Run the ArchiMate Inference Engine on a solution's elements to fill missing "
            "cross-layer chain elements (Motivation→Strategy→Business→Application→Technology→Implementation). "
            "Use when the user asks to complete, fill in, or repair the architecture chain."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, show what would be created without writing to DB. Default false.",
                },
            },
            "required": ["solution_id"],
        },
        "tier": "auto",
    },
    {
        "name": "generate_blueprint_narrative",
        "mutates": False,
        "description": (
            "Generate an AI narrative for a specific blueprint section. "
            "REQUIRES USER CONFIRMATION — this overwrites existing section text. "
            "section_id examples: sec-1 (Summary), sec-2 (Strategic), sec-3 (Business), "
            "sec-4 (Application), sec-5 (Options), sec-7 (Governance), sec-8 (Risks)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "section_id": {"type": "string", "description": "Blueprint section ID, e.g. 'sec-2'"},
            },
            "required": ["solution_id", "section_id"],
        },
        "tier": "approve",
    },
    # ------------------------------------------------------------------ #
    # ArchiMate Intelligence tools                                        #
    # ------------------------------------------------------------------ #
    {
        "name": "create_archimate_relationship",
        "mutates": True,
        "description": (
            "Create a typed ArchiMate relationship between two existing elements. "
            "Use when the user wants to model how elements connect."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_element_name": {"type": "string", "description": "Source element name (fuzzy matched)"},
                "target_element_name": {"type": "string", "description": "Target element name (fuzzy matched)"},
                "relationship_type": {
                    "type": "string",
                    "enum": ["Realization", "Serving", "Assignment", "Aggregation", "Composition",
                             "Association", "Influence", "Triggering", "Flow", "Specialization", "Access"],
                },
                "solution_id": {"type": "integer", "description": "Solution to attach to (optional)"},
            },
            "required": ["source_element_name", "target_element_name", "relationship_type"],
        },
        "tier": "auto",
    },
    {
        "name": "diagnose_chain",
        "mutates": False,
        "description": (
            "Show missing elements in an ArchiMate element's chain without repairing. "
            "Read-only. Use when the user asks what's incomplete or what's missing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "element_name": {"type": "string", "description": "Element name (fuzzy matched)"},
            },
            "required": ["element_name"],
        },
        "tier": "auto",
    },
    {
        "name": "explain_element",
        "mutates": False,
        "description": (
            "Explain why an ArchiMate element exists by tracing its upstream provenance chain. "
            "Read-only. Use when the user asks 'why does X exist?' or 'what drives X?'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "element_name": {"type": "string", "description": "Element name (fuzzy matched)"},
            },
            "required": ["element_name"],
        },
        "tier": "auto",
    },
    {
        "name": "simulate_impact",
        "mutates": False,
        "description": (
            "Show the blast radius if an ArchiMate element is retired or changed. "
            "Read-only. Returns all downstream dependents across all 6 layers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "element_name": {"type": "string", "description": "Element name (fuzzy matched)"},
            },
            "required": ["element_name"],
        },
        "tier": "auto",
    },
    # ------------------------------------------------------------------ #
    # Solution State tools                                                #
    # ------------------------------------------------------------------ #
    {
        "name": "get_solution_summary",
        "mutates": False,
        "description": (
            "Read the current state of a solution: maturity score, linked entity counts, "
            "ARB status, and completeness gaps. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
            },
            "required": ["solution_id"],
        },
        "tier": "auto",
    },
    {
        "name": "get_completeness_score",
        "mutates": False,
        "description": (
            "Get the blueprint completeness score with dimension breakdown "
            "(Elements %, Relationships %, Traceability %). Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
            },
            "required": ["solution_id"],
        },
        "tier": "auto",
    },
    {
        "name": "update_solution_fields",
        "mutates": True,
        "description": (
            "Update solution metadata: owner, business_sponsor, technical_lead, or description. "
            "Use when the user assigns roles or updates the solution description."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "solution_owner": {"type": "string"},
                "business_sponsor": {"type": "string"},
                "technical_lead": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["solution_id"],
        },
        "tier": "auto",
    },
    {
        "name": "update_solution_phase",
        "mutates": True,
        "description": (
            "Advance the solution's TOGAF ADM phase (A through H). "
            "Use when the user says they're done with a phase and ready to move on."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer"},
                "phase": {
                    "type": "string",
                    "enum": ["A", "B", "C", "D", "E", "F", "G", "H"],
                    "description": "Target ADM phase",
                },
            },
            "required": ["solution_id", "phase"],
        },
        "tier": "auto",
    },
    # ------------------------------------------------------------------ #
    # Capability Architect Phase 2-4 grounding tools                     #
    # ------------------------------------------------------------------ #
    {
        "name": "search_capabilities_by_problem",
        "mutates": False,
        "description": (
            "Semantic search over the business capability catalog to find which ones "
            "are most relevant to a stated problem or initiative. "
            "Use at the START of Phase 2 — before asking the user what capabilities "
            "they need. Returns capabilities ranked by relevance with maturity gaps "
            "and current application coverage count. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "problem_description": {
                    "type": "string",
                    "description": "The problem or initiative description to search against",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max capabilities to return (default 10, max 25)",
                    "default": 10,
                    "maximum": 25,
                },
            },
            "required": ["problem_description"],
        },
        "tier": "auto",
    },
    {
        "name": "find_applications_by_capability",
        "mutates": False,
        "description": (
            "Find all applications already mapped to a specific business "
            "capability. "
            "Use at Phase 4 (Application layer) to ground architecture in real "
            "existing systems rather than inventing application names. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "capability_name": {
                    "type": "string",
                    "description": "Business capability name (fuzzy matched)",
                },
            },
            "required": ["capability_name"],
        },
        "tier": "auto",
    },
    {
        "name": "find_technical_capabilities",
        "mutates": False,
        "description": (
            "Find technical capabilities from the ACM (Application Capability Model) taxonomy "
            "across 7 domains: USER-EXPERIENCE, APPLICATION-SERVICES, DATA-STORAGE, "
            "SECURITY-IDENTITY, DEVOPS-PLATFORM, AI-ANALYTICS, COMMUNICATION. "
            "Use at Phase 5 (Technology layer) BEFORE suggesting Nodes or SystemSoftware — "
            "grounds the technology architecture in the real capability taxonomy. "
            "Returns L1/L2 capabilities with how many apps already cover each one. "
            "Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "Filter by ACM domain (optional)",
                    "enum": [
                        "USER-EXPERIENCE", "APPLICATION-SERVICES", "DATA-STORAGE",
                        "SECURITY-IDENTITY", "DEVOPS-PLATFORM", "AI-ANALYTICS", "COMMUNICATION",
                    ],
                },
                "query": {
                    "type": "string",
                    "description": "Keyword search across capability names and descriptions (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max capabilities to return (default 15, max 30)",
                    "default": 15,
                    "maximum": 30,
                },
            },
        },
        "tier": "auto",
    },
    {
        "name": "search_archimate_elements",
        "mutates": False,
        "description": (
            "Search ArchiMate elements by name, layer, or type. Read-only. "
            "Use when the user asks what elements exist or wants to find a specific element."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name_contains": {"type": "string"},
                "layer": {
                    "type": "string",
                    "enum": ["motivation", "strategy", "business", "application", "technology", "implementation"],
                },
                "element_type": {"type": "string", "description": "e.g. ApplicationComponent, BusinessProcess"},
                "limit": {"type": "integer", "default": 15, "maximum": 50},
            },
        },
        "tier": "auto",
    },
    # ------------------------------------------------------------------ #
    # Gap-closure tools (AI-Architect capability expansion)              #
    # ------------------------------------------------------------------ #
    {
        "name": "verify_codegen",
        "mutates": False,
        "description": (
            "Verify that a solution's generated artifacts trace back to ArchiMate sources. "
            "Checks application-layer coverage, data-layer coverage, technology-layer presence, "
            "and element name quality. Returns a score (0-100), grade (A-F), and findings. "
            "USE when: codegen was run on a solution and the user asks if it's conformant; "
            "after building a solution architecture to check it's ready for generation; "
            "or when the user asks about codegen quality or ARB readiness for a solution. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {"type": "integer", "description": "Solution to verify"},
                "solution_name": {"type": "string", "description": "Solution name (fuzzy matched if solution_id not provided)"},
            },
        },
        "tier": "auto",
    },
    {
        "name": "propose_rationalization",
        "mutates": False,
        "description": (
            "Generate autonomous TIME (Tolerate/Invest/Migrate/Eliminate) rationalization proposals "
            "from portfolio data. Surfaces ELIMINATE candidates with no active programme, "
            "capability duplication clusters, on-premise migration backlog, and INVEST apps needing "
            "sponsorship — all with evidence and recommended next steps. "
            "USE when the user asks: 'what should we retire?', 'where are we duplicating capability?', "
            "'what's the rationalization pipeline?', or any portfolio optimisation question. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max proposals to return (default 10)",
                    "default": 10,
                    "maximum": 25,
                },
            },
        },
        "tier": "auto",
    },
    {
        "name": "build_architecture_plan",
        "mutates": False,
        "description": (
            "Build a multi-step architecture execution plan for a goal. "
            "Selects the right template (SAP transformation, rationalization, solution design, "
            "data governance, programme setup) and returns an ordered list of steps, each with "
            "the Entelim tool to call, dependency on previous steps, and a gate-check condition. "
            "USE when the user says 'help me plan', 'what are the steps to', 'sequence this work', "
            "or asks how to execute a transformation, design, or programme. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The architecture goal or work to plan (natural language)",
                },
                "solution_id": {
                    "type": "integer",
                    "description": "If the plan is for a specific solution, provide its ID",
                },
            },
            "required": ["goal"],
        },
        "tier": "auto",
    },
    {
        "name": "poll_infrastructure",
        "mutates": False,
        "description": (
            "Check configured infrastructure endpoints for reachability. "
            "Probes: Abacus API connector, LLM API endpoints, integration pattern URLs. "
            "Returns up/down status per endpoint, latency, and a delta summary of what's "
            "modelled in Entelim vs what's actually reachable. "
            "USE when the user asks about connectivity, 'is X reachable?', infrastructure health, "
            "or wants to know if configured integrations are live. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "include_abacus": {"type": "boolean", "default": True},
                "include_llm": {"type": "boolean", "default": True},
                "additional_urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra URLs to probe (max 20)",
                },
            },
        },
        "tier": "auto",
    },
    {
        "name": "infer_schema",
        "mutates": False,
        "description": (
            "Parse SQL DDL or OpenAPI 3.x JSON/YAML and infer ArchiMate DataObject elements. "
            "Returns a list of DataObject candidates with field attributes, ready to create "
            "via create_archimate_element. Does NOT write to DB — returns candidates for review. "
            "USE when the user pastes a CREATE TABLE statement, OpenAPI schema block, or asks "
            "'import this schema', 'create data objects from this DDL', or similar. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "input_text": {
                    "type": "string",
                    "description": "The DDL or OpenAPI JSON/YAML text to parse",
                },
                "format": {
                    "type": "string",
                    "enum": ["ddl", "openapi", "auto"],
                    "description": "Input format. 'auto' detects from content (default).",
                    "default": "auto",
                },
                "solution_id": {
                    "type": "integer",
                    "description": "If provided, the inferred DataObjects will be suggested for this solution",
                },
            },
            "required": ["input_text"],
        },
        "tier": "auto",
    },
    {
        "name": "validate_sap_clean_core",
        "mutates": False,
        "description": (
            "Validate a solution's architecture against the SAP RISE clean-core extension model. "
            "Detects Tier 3/4 violations (RFC/BAPI integrations, CMOD/SMOD modifications, direct SAP coupling, "
            "missing BTP mediation layer, legacy ECC/R3 systems) and returns a scored compliance report. "
            "USE THIS TOOL whenever the user asks about: SAP clean core, RISE compliance, S/4HANA upgrade "
            "readiness, SAP extension model, custom code risk, ABAP modifications, BTP architecture, "
            "or SAP transformation posture. Also use proactively when a solution contains SAP components "
            "and the user asks about its architecture quality or ARB readiness. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {
                    "type": "integer",
                    "description": "ID of the solution to validate",
                },
                "solution_name": {
                    "type": "string",
                    "description": "Name of the solution (used to resolve solution_id if not provided; fuzzy matched)",
                },
                "include_portfolio_scan": {
                    "type": "boolean",
                    "description": (
                        "If true, scan all solutions with SAP footprint and return a portfolio-level "
                        "compliance summary. Use when the user asks about the overall SAP estate or "
                        "programme-level clean-core posture."
                    ),
                    "default": False,
                },
            },
        },
        "tier": "auto",
    },
    {
        # ADR 0009 / 0010 — genome-as-substrate. The copilot proposes a
        # schema-validated, provenance-bearing PATCH to the enterprise genome
        # instead of doing direct CRUD. Proposing only QUEUES the patch for
        # approval (mutates=False w.r.t. the model); the real write happens in
        # the un-registered `apply_genome_patch` handler once a human approves.
        "name": "propose_genome_patch",
        "mutates": False,
        "description": (
            "Propose a change to the enterprise genome (the ArchiMate model) as a "
            "structured, provenance-bearing PATCH. Use when the user asks to propose "
            "a missing capability, a control, a driver, a requirement, or any "
            "motivation/architecture element. Do NOT create elements directly — emit "
            "a patch object and this tool validates it and queues it for human "
            "approval. The patch MUST include target.organization_id, target.domain, "
            "operation (add|modify), element (archimate_type, layer, name), and "
            "provenance (proposed_by, rationale, archimate_anchor)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "What the user asked for, in one line.",
                },
                "patch": {
                    "type": "object",
                    "description": (
                        "The genome patch object conforming to GENOME_PATCH_SCHEMA: "
                        "{target:{organization_id,domain}, operation, "
                        "element:{archimate_type,layer,name,description?}, "
                        "provenance:{proposed_by,rationale,archimate_anchor}}."
                    ),
                },
            },
            "required": ["patch"],
        },
        "tier": "auto",
    },
    # ------------------------------------------------------------------ #
    # Governance / executive tools (Capability-Gap Register G1 + G2)      #
    # Three reads that bind existing services the copilot could not reach, #
    # and one governed write (create_adr) through the approval gate.       #
    # ------------------------------------------------------------------ #
    {
        "name": "get_investment_priorities",
        "mutates": False,
        "description": (
            "CTO view: the ranked capability investment priorities for this "
            "organization, with the split across CRITICAL/HIGH/MEDIUM/LOW "
            "priority tiers and recommended next steps. "
            "USE when the user asks 'where should we invest?', 'what is our "
            "investment posture?', or any portfolio-investment question. "
            "Read-only â safe to execute without confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max ranked capabilities to return (default 25)",
                    "default": 25,
                    "maximum": 100,
                },
            },
        },
        "tier": "auto",
    },
    {
        "name": "get_executive_dashboard",
        "mutates": False,
        "description": (
            "CTO/CIO one-call executive summary: portfolio health and stats, "
            "programme (ADM-phase) progress, the ARB decision pipeline, and the "
            "top risks â all from real data. Fields that could not be computed "
            "return null and MUST be shown as an em dash, never as zero. "
            "USE when the user asks for an executive overview, portfolio health, "
            "or a board-level status. Read-only â safe to execute without confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "tier": "auto",
    },
    {
        "name": "get_arb_status",
        "mutates": False,
        "description": (
            "Read a solution's Architecture Review Board status: each review "
            "item's status, decision (approved / approved_with_conditions / "
            "rejected / deferred), decision rationale and any conditions. "
            "USE when the user asks 'what did the ARB decide about X?', 'what are "
            "the conditions on X?', or wants a solution's governance outcome read "
            "back. Read-only â safe to execute without confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {
                    "type": "integer",
                    "description": "ID of the solution whose ARB reviews to read",
                },
            },
            "required": ["solution_id"],
        },
        "tier": "auto",
    },
    {
        "name": "create_adr",
        "mutates": True,
        "description": (
            "Author an Architecture Decision Record (ADR) for a solution â the "
            "artifact the solution-architect charter centres on. Captures the "
            "context, the decision taken, its rationale and consequences. The "
            "ADR is created in status 'proposed' and moves through approve/reject "
            "governance afterwards. REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "solution_id": {
                    "type": "integer",
                    "description": "ID of the solution this ADR relates to",
                },
                "title": {
                    "type": "string",
                    "description": "ADR title, e.g. 'Adopt event-driven integration'",
                },
                "context": {
                    "type": "string",
                    "description": "Why this decision was needed (the forces at play)",
                },
                "decision": {
                    "type": "string",
                    "description": "What was decided",
                },
                "rationale": {
                    "type": "string",
                    "description": "Why this option was chosen over the alternatives",
                },
                "consequences": {
                    "type": "string",
                    "description": "Consequences of the decision (trade-offs accepted)",
                },
                "decision_type": {
                    "type": "string",
                    "enum": [
                        "technology_choice", "vendor_selection",
                        "pattern_selection", "integration_approach",
                    ],
                    "description": "Classification of the decision (default technology_choice)",
                },
            },
            "required": ["solution_id", "title", "context", "decision", "rationale"],
        },
        "tier": "approve",
    },
    # ------------------------------------------------------------------ #
    # Governed ACTION/UPDATE tools (Capability-Gap Register G2)           #
    # Turn the copilot from a reader/proposer into a governed actor:      #
    # record a maturity assessment, and persist a TIME rationalization    #
    # score. Both mutates=True / tier 'approve' — they flow through the   #
    # existing confirmation gate.                                         #
    # ------------------------------------------------------------------ #
    {
        "name": "record_capability_maturity",
        "mutates": True,
        "description": (
            "Record a maturity assessment against a business capability: set its "
            "current maturity (1-5) and, optionally, its target maturity (1-5). "
            "This is the EA/business-architect headline write — the copilot can "
            "read capability gaps but, until now, could not record the assessment "
            "that closes them. Writes current_maturity_level / target_maturity_level "
            "(and the derived maturity_gap) on the business capability, stamping the "
            "assessment date so the estate can tell an assessed capability from an "
            "unassessed one. REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "capability_id": {
                    "type": "integer",
                    "description": "ID of the business capability to assess",
                },
                "current_maturity": {
                    "type": "integer",
                    "description": "Current maturity level, 1 (Initial) to 5 (Optimising)",
                    "minimum": 1,
                    "maximum": 5,
                },
                "target_maturity": {
                    "type": "integer",
                    "description": "Optional target maturity level, 1 to 5",
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": ["capability_id", "current_maturity"],
        },
        "tier": "approve",
    },
    {
        "name": "score_rationalization",
        "mutates": True,
        "description": (
            "Compute and PERSIST an application's TIME (Tolerate/Invest/Migrate/"
            "Eliminate) rationalization score and disposition. This is the EA/"
            "portfolio-manager headline write — propose_rationalization only reads; "
            "this tool runs the scoring service, writes the "
            "ApplicationRationalizationScore record (dimension scores, overall "
            "health, TIME action, 7R disposition and readiness gate) and creates "
            "the benefits-tracker row. "
            "USE when the user asks to score, re-score, or record a disposition for "
            "a specific application. REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {
                    "type": "integer",
                    "description": "ID of the application component to score",
                },
            },
            "required": ["app_id"],
        },
        "tier": "approve",
    },
    {
        "name": "merge_capabilities",
        "mutates": True,
        "description": (
            "Resolve a duplicate business capability by MERGING one into another "
            "(Capability-Gap Register G3 — the systemic duplication debt). The "
            "copilot can already DETECT duplicate capabilities; this is how it "
            "PROPOSES resolving one. Repoints the removed capability's children, "
            "APQC process mappings and application-capability mappings onto the "
            "kept capability, then RETIRES the duplicate (soft-delete — reversible; "
            "the row is marked deprecated, not physically removed). Returns a full "
            "before-state snapshot for audit. Both capabilities must belong to your "
            "organization; a capability cannot be merged into itself. "
            "USE when the user confirms two capabilities are the same record and "
            "asks to merge, consolidate, or de-duplicate them. This is a "
            "DESTRUCTIVE governance action — REQUIRES USER CONFIRMATION before "
            "executing, and you must never pick which one to keep on the user's "
            "behalf without their agreement."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "keep_capability_id": {
                    "type": "integer",
                    "description": "ID of the capability to KEEP (references are repointed onto this one)",
                },
                "remove_capability_id": {
                    "type": "integer",
                    "description": "ID of the duplicate capability to RETIRE (soft-deleted after its references move)",
                },
                "rationale": {
                    "type": "string",
                    "description": "Optional reason for the merge, recorded on the retired capability's deprecation note",
                },
            },
            "required": ["keep_capability_id", "remove_capability_id"],
        },
        "tier": "approve",
    },
    {
        "name": "create_vendor",
        "mutates": True,
        "description": (
            "Register a new vendor organization in the shared vendor catalogue — the "
            "Procurement / vendor-management headline write. Use when the user wants to "
            "add a supplier that is not yet on file (e.g. 'add ACME Corp as a vendor'). "
            "Creates the master vendor record (name, type, website, description, strategic "
            "tier); commercial terms and contracts are attached separately afterwards. "
            "NOTE: VendorOrganization is SHARED reference data by design (ADR-0003) — its "
            "name is globally unique, so a vendor is visible to every organisation, not "
            "tenant-private. REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Vendor name — globally unique, e.g. 'Snowflake Inc'",
                },
                "display_name": {
                    "type": "string",
                    "description": "Display/legal name, e.g. 'Snowflake Computing, Inc.' (defaults to name)",
                },
                "vendor_type": {
                    "type": "string",
                    "enum": ["software_vendor", "cloud_provider", "systems_integrator"],
                    "description": "Category of vendor (default software_vendor)",
                },
                "website": {"type": "string", "description": "Vendor website URL"},
                "headquarters_location": {"type": "string", "description": "HQ location"},
                "description": {"type": "string", "description": "What the vendor does"},
                "strategic_tier": {
                    "type": "string",
                    "enum": [
                        "tier_1_strategic", "tier_2_preferred",
                        "tier_3_approved", "tier_4_restricted",
                    ],
                    "description": "Strategic positioning (default tier_3_approved)",
                },
            },
            "required": ["name"],
        },
        "tier": "approve",
    },
    {
        "name": "extract_contract_from_document",
        "mutates": False,
        "description": (
            "Extract structured contract terms from pasted contract / MSA text — the "
            "Procurement 'paste this contract' capability. Reads the text and returns a "
            "structured first pass (contract name/number, vendor name, start/end/renewal "
            "dates, notice period, auto-renewal, value, currency, payment terms, "
            "termination summary, liability cap, risk flags). Any field the text does not "
            "state comes back as null — never a guess. This does NOT save anything: it "
            "extracts, and a human (or create_vendor / a contract form) applies the "
            "result. If the LLM backend is unavailable or returns something unparseable, "
            "it fails honestly with an error rather than fabricating fields. Read-only — "
            "safe to run without confirmation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "contract_text": {
                    "type": "string",
                    "description": "The full contract / MSA text to extract terms from",
                },
            },
            "required": ["contract_text"],
        },
        "tier": "auto",
    },
    # ------------------------------------------------------------------ #
    # Governed WRITE tools (Capability-Gap Register G4, G8)               #
    # A bulk portfolio lifecycle write and the two procurement commercial #
    # writes. All mutates=True / tier 'approve' — through the gate.       #
    # ------------------------------------------------------------------ #
    {
        "name": "bulk_update_application_status",
        "mutates": True,
        "description": (
            "Set the lifecycle stage of a SET of applications in one governed "
            "action — the portfolio / application-manager bulk write. "
            "update_application_status changes one app at a time; this applies one "
            "lifecycle stage to many under a single confirmation. Select the set "
            "EITHER by app_ids (explicit list) OR by a filter "
            "(current_status / component_type). The stage is validated against the "
            "TOGAF-decommission lifecycle vocabulary the portfolio actually uses "
            "(e.g. '2.1 strategic', '3. sunset', '4.2 decom planned', "
            "'5. decommissioned'); an invalid stage is rejected. Returns a per-app "
            "result (updated, or skipped with a reason) — it never reports a "
            "fabricated success — and, if a large selection is capped, says so "
            "explicitly rather than dropping applications silently. Scoped to your "
            "organisation. REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "app_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Explicit list of application component IDs to update",
                },
                "filter": {
                    "type": "object",
                    "description": "Alternative to app_ids: select by current_status and/or component_type",
                    "properties": {
                        "current_status": {
                            "type": "string",
                            "description": "Only apps currently at this lifecycle stage",
                        },
                        "component_type": {
                            "type": "string",
                            "description": "Only apps of this component_type",
                        },
                    },
                },
                "new_status": {
                    "type": "string",
                    "description": (
                        "Target lifecycle stage. One of: '1. undetermined', "
                        "'2.1 strategic', '2.2 tactical', '3. sunset', "
                        "'4.1 decom decided', '4.2 decom planned', '4.3 read-only', "
                        "'4.4 stopped', '5. decommissioned'"
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": "Why the batch change is being made (recorded in the log)",
                },
            },
            "required": ["new_status"],
        },
        "tier": "approve",
    },
    {
        "name": "create_contract",
        "mutates": True,
        "description": (
            "Create a PROCUREMENT (commercial) vendor contract — the "
            "vendor-management / procurement headline write. Captures the "
            "commercial agreement with a vendor: name, optional number, type "
            "(license/subscription/maintenance/support/custom_development), "
            "category (software/hardware/service/consulting), status, value, "
            "currency and start/end/renewal dates. This is NOT an API-interface "
            "contract — it is the commercial VendorContract. An end or renewal "
            "date before the start date is rejected; an invalid type/category/"
            "status is rejected; a missing start date defaults to today. "
            "Tenant-scoped to your organisation. REQUIRES USER CONFIRMATION "
            "before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "vendor_id": {
                    "type": "integer",
                    "description": "ID of the VendorOrganization this contract is with",
                },
                "name": {
                    "type": "string",
                    "description": "Contract name, e.g. 'Snowflake Enterprise Subscription 2026'",
                },
                "contract_number": {"type": "string", "description": "Optional reference number (globally unique)"},
                "description": {"type": "string", "description": "What the contract covers"},
                "contract_type": {
                    "type": "string",
                    "enum": ["license", "subscription", "maintenance", "support", "custom_development"],
                    "description": "Type of contract",
                },
                "contract_category": {
                    "type": "string",
                    "enum": ["software", "hardware", "service", "consulting"],
                    "description": "Category of contract",
                },
                "status": {
                    "type": "string",
                    "enum": ["active", "expired", "terminated", "pending", "under_negotiation"],
                    "description": "Contract status",
                },
                "value": {"type": "number", "description": "Total contract value"},
                "annual_cost": {"type": "number", "description": "Annual cost"},
                "currency": {"type": "string", "description": "Currency code (default USD)"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD (defaults to today if omitted)"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD (must be on/after start)"},
                "renewal_date": {"type": "string", "description": "Renewal date YYYY-MM-DD (must be on/after start)"},
                "auto_renewal": {"type": "boolean", "description": "Whether the contract auto-renews"},
                "contract_owner": {"type": "string", "description": "Internal owner of the contract"},
            },
            "required": ["name"],
        },
        "tier": "approve",
    },
    {
        "name": "create_programme",
        "mutates": True,
        "description": (
            "Create a canonical business-first Transformation Programme — the "
            "same aggregate the /solutions/new-programme wizard creates, "
            "reached through the exact same authorised, validated command. "
            "Only Enterprise Architects, CTOs and administrators can create "
            "programmes; anyone else's call is refused with a clear message. "
            "Requires a name, an objective, an outcome statement with a "
            "measurable metric (name/unit/direction/baseline/target), and "
            "either a target_date or a stated reason none is available yet. "
            "Do not invent any of these values — ask the user for anything "
            "not given. REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Programme name"},
                "objective": {"type": "string", "description": "What the programme sets out to achieve"},
                "owner_id": {
                    "type": "integer",
                    "description": "User id of the programme owner (defaults to the requesting user if omitted)",
                },
                "workstream_type": {
                    "type": "string",
                    "enum": [
                        "application_rationalisation", "process", "organisation_skills",
                        "policy_control", "data", "supplier", "technology", "other",
                    ],
                    "description": "First workstream's type",
                },
                "business_units": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Business units in scope",
                },
                "target_date": {"type": "string", "description": "Target completion date YYYY-MM-DD"},
                "target_date_unavailable_reason": {
                    "type": "string",
                    "description": "Required instead of target_date when no date is known yet",
                },
                "outcome_statement": {"type": "string", "description": "The outcome the programme commits to"},
                "outcome_direction": {
                    "type": "string",
                    "enum": ["increase", "decrease", "maintain"],
                    "description": "Direction the outcome metric should move",
                },
                "metric_name": {"type": "string", "description": "Name of the metric that proves the outcome"},
                "metric_unit": {"type": "string", "description": "Unit the metric is measured in"},
                "metric_aggregation": {
                    "type": "string",
                    "enum": ["sum", "average", "minimum", "maximum", "latest", "count"],
                    "description": "How the metric is aggregated (defaults to 'sum')",
                },
                "baseline_value": {"type": "number", "description": "Metric value today"},
                "baseline_unavailable_reason": {
                    "type": "string",
                    "description": "Required instead of baseline_value when today's value isn't known yet",
                },
                "target_value": {"type": "number", "description": "Metric value the programme is targeting"},
            },
            "required": ["name", "objective", "outcome_statement", "outcome_direction", "metric_name", "metric_unit"],
        },
        "tier": "approve",
    },
    {
        "name": "upsert_license",
        "mutates": True,
        "description": (
            "Create or update a licence entitlement under a procurement contract "
            "— the software-asset-management write. Records the product, licence "
            "type (named_user/concurrent/device/core/site) and metric, and the "
            "entitled / deployed / used quantities; compliance status "
            "(compliant / over_deployed / under_utilized) is DERIVED from the "
            "quantities, never taken on trust. A licence MUST belong to a "
            "contract (contract_id), re-read through your organisation's predicate "
            "so it cannot be hung off another org's contract. Supply license_id to "
            "update an existing entitlement; omit it to create one. Tenant-scoped. "
            "REQUIRES USER CONFIRMATION before executing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "license_id": {
                    "type": "integer",
                    "description": "ID of an existing entitlement to UPDATE; omit to create a new one",
                },
                "contract_id": {
                    "type": "integer",
                    "description": "ID of the VendorContract this licence belongs to (required to create)",
                },
                "product": {"type": "string", "description": "Product name the licence covers"},
                "license_type": {
                    "type": "string",
                    "enum": ["named_user", "concurrent", "device", "core", "site"],
                    "description": "Licensing model (default named_user)",
                },
                "license_metric": {"type": "string", "description": "Optional metric label, e.g. 'per seat'"},
                "entitled": {"type": "integer", "description": "Quantity entitled (purchased)"},
                "deployed": {"type": "integer", "description": "Quantity deployed (installed)"},
                "used": {"type": "integer", "description": "Quantity actually used"},
                "unit_cost": {"type": "number", "description": "Cost per unit"},
            },
            "required": ["contract_id"],
        },
        "tier": "approve",
    },
]

# Index by name for O(1) lookup
TOOL_SCHEMA_BY_NAME = {s["name"]: s for s in TOOL_SCHEMAS}


# Derived by reading each implementation for db.session.add/commit/delete — and
# through the five tools that delegate to a service — not from the tool's name.
# A startswith("create_") heuristic would miss mark_option_recommended,
# submit_for_arb_review and every link_*/update_*, while wrongly flagging
# propose_rationalization and run_inference_engine, which only read.
#
# One source of truth for three consumers: write receipts in the transcript, the
# next-artifact suggestion, and the approval tiering that toggle_auto_execute
# (chat_core.py) has been unable to enforce because `tier` conflates reads and
# writes.
# ---------------------------------------------------------------------------
# One tool per ArchiMate element type, generated from its semantics.
#
# check_ai_layer_coverage.py measured 54 of the product's 58 declared element
# types with no dedicated AI creation path: the assistant could reason about
# motivation and design solutions, and could not model the business, technology,
# strategy or migration layers.
#
# These are GENERATED rather than hand-written, for a reason that matters more
# than the saving: every element then carries the same three pieces of guidance
# — definition, when to use, and what it is confused with — so a new element
# type cannot ship a tool whose description omits the distinction that stops it
# being misused. A hand-written 58th entry would.
#
# They are not one generic create_archimate_element with a type parameter. That
# tool exists and is deliberately not counted as coverage: it accepts whatever
# type the model guesses, handing the modelling judgement back to a user who
# does not have it. The point of the product is to remove that.
def _archimate_element_schemas() -> list:
    from .archimate_specs import ELEMENT_SPECS, tool_description

    # A duplicate tool name is rejected outright by every provider's tool-calling
    # API (confirmed in production: DeepSeek returns HTTP 400 "Tool names must be
    # unique", failing every agentic chat call) - guard against this generator
    # colliding with an already hand-written tool of the same name, e.g.
    # create_contract (the commercial VendorContract writer, above) vs the
    # generic ArchiMate "Contract" business-layer element this loop would
    # otherwise also name create_contract.
    _existing_names = {s["name"] for s in TOOL_SCHEMAS}

    schemas = []
    for element_type, spec in sorted(ELEMENT_SPECS.items()):
        properties = {
            "name": {
                "type": "string",
                "description": "Short, specific name for this %s"
                               % element_type.replace("_", " "),
            },
            "description": {
                "type": "string",
                "description": "What it is, in the organisation's own words",
            },
        }
        for extra in spec.get("properties", []):
            if extra in properties:
                continue
            properties[extra] = {
                "type": "string",
                "description": "%s of this %s"
                               % (extra.replace("_", " "), element_type.replace("_", " ")),
            }
        candidate_name = "create_%s" % element_type
        if candidate_name in _existing_names:
            candidate_name = "create_archimate_%s" % element_type
        schemas.append({
            "name": candidate_name,
            "mutates": True,
            # 'approve', not 'auto'. These write typed elements into the model
            # of record, and REQUIRE_AI_APPROVAL exists so an operator decides
            # whether AI-proposed writes reach it unreviewed.
            "tier": "approve",
            "archimate_layer": spec["layer"],
            "description": tool_description(element_type, spec),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["name"],
            },
        })
    return schemas


TOOL_SCHEMAS.extend(_archimate_element_schemas())


def mutating_tool_names() -> set:
    """Names of every tool that writes to the repository."""
    return {t["name"] for t in TOOL_SCHEMAS if t.get("mutates")}
