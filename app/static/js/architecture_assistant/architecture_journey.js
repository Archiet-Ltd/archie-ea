(function (global) {
    'use strict';

    const API_BASE = '/architecture-journey';

    /**
     * Safe fetch wrapper.
     * - Auto-injects CSRF token from <meta name="csrf-token">
     * - Rejects on non-2xx HTTP status (so .catch() fires on 404/500)
     * - Always returns parsed JSON
     */
    function _fetch(url, opts) {
        // Platform.fetch already handles CSRF, JSON serialization, and error throwing.
        // We preserve the same envelope unwrapping behavior.
        return Platform.fetch(url, opts).then(function (body) {
            // Explicit failure envelope: {success: false, error: "..."} with HTTP 200
            // Treat as a thrown error so callers' .catch() fires — prevents silent empty results.
            if (body && body.success === false) {
                let err = new Error(body.error || 'Request failed');
                err.status = 200; // Platform.fetch throws on non-2xx, so this is a 200 response
                err.body = body;
                throw err;
            }
            // Unwrap api_success envelope: {success: true, data: {...}} → inner data
            return (body && body.data !== undefined) ? body.data : body;
        }).catch(function (e) {
            // Platform.fetch already threw a structured PlatformError.
            // We need to preserve the existing error shape for backward compatibility.
            // The original _fetch attached status and body to the error.
            // PlatformError has e.status and e.data.
            const err = new Error(e.message || 'Request failed');
            err.status = e.status || 0;
            err.body = e.data || null;
            throw err;
        });
    }

    global.journeyV2Flow = function (solutionId) {
        let vendorMixins = {};
        try {
            if (typeof vendorSuggestionsMixin === 'function') {
                vendorMixins = vendorSuggestionsMixin();
            }
        } catch (e) {
            // Optional feature probe: vendor_suggestions.js may not be loaded on every
            // page that includes this journey. Degrading to {} is deliberate — the
            // journey works fully without vendor suggestions.
        }
        return Object.assign({}, vendorMixins, {
            solutionId: solutionId,
            currentStep: 1,
            completedSteps: [],
            loading: false,
            error: null,

            // Co-pilot
            copilotMessage: 'Describe your business problem to get started.',

            // Solution metadata
            solutionName: '',
            solutionNameSaving: false,

            // LLM degradation flags (set when LLM returns empty/fallback responses)
            llmDegraded: { step1: false, step3: false, step4: false },

            // TRAC-001: chain health warnings per step (non-blocking amber banners)
            stepWarnings: {},
            stepWarningsDismissed: {},

            // Step 7: Code Generation
            governanceStatus: '',
            codegenLanguage: 'python-fastapi',
            codegenMode: 'genome',  // 'genome' (recommended) or 'hybrid'
            genomeOpts: {
                multiTenancy: true,
                apiKeys: false,
                encryptionAtRest: false,
                mfa: 'none',
                mobileApp: false,
            },
            codegenLoading: false,
            codegenResult: null,
            codegenBundleId: null,
            codegenError: null,
            hasGeneratedFiles: false,  // True when code was generated (via pipeline or prior session)

            // Deploy state
            deployStatus: null,  // null, 'deploying', 'healthy', 'unhealthy', 'stopped', 'error'
            deployUrl: null,
            deployError: null,
            deployVersion: null,
            dockerPreviewLoading: false,
            dockerPreviewUrl: null,
            dockerPreviewError: null,
            stackblitzLoading: false,

            // Step 8a: Data Loading
            dataPhase: 'upload',      // 'upload' | 'mapping' | 'importing' | 'done'
            uploadFile: null,
            uploadFileName: '',
            uploadFileSize: '',
            uploadLoading: false,
            uploadError: null,
            uploadedSheets: [],
            selectedSheet: null,
            sheetMappings: {},        // keyed by sheet name
            mappingLoading: false,
            mappingError: null,
            importLoading: false,
            importProgress: 0,
            importError: null,
            importResult: null,

            // Step 8b: Rules (properties referenced by _step8b_rules.html)
            ruleSuggestions: [],
            // The panel had no way to distinguish "still fetching" from "none
            // exist" from "the fetch failed" — all three rendered the literal
            // string "Loading suggestions...", so a hard failure looked like a
            // request permanently in flight. These two make the states distinct.
            ruleSuggestionsLoading: false,
            ruleSuggestionsError: null,
            activeRules: [],
            nlRuleInput: '',
            nlRuleLoading: false,
            ruleError: null,

            // Step 8c: Testing
            testScenarios: [],
            scenariosLoading: false,
            testSummary: null,
            processComparison: null,
            comparisonLoading: false,

            // Step 1: Clarify
            problemStatement: '',
            clarifyQuestions: [],
            clarifyAnswers: {},
            enrichedBrief: '',
            clarifyPhase: 'input', // 'input' | 'questions' | 'enriched'
            briefUploading: false,

            // Step 1: Landscape Discovery — shows matching enterprise entities as user types
            landscapeMatches: null,
            landscapeSearching: false,
            _landscapeTimer: null,

            // Entity picker state (pre-allocated for Alpine.js 2.x reactivity)
            pickerState: {
                0: {query:'', results:[], selected:[], loading:false, entityType:'', contextText:'', searchError:'', _debounce:null},
                1: {query:'', results:[], selected:[], loading:false, entityType:'', contextText:'', searchError:'', _debounce:null},
                2: {query:'', results:[], selected:[], loading:false, entityType:'', contextText:'', searchError:'', _debounce:null},
                3: {query:'', results:[], selected:[], loading:false, entityType:'', contextText:'', searchError:'', _debounce:null},
                4: {query:'', results:[], selected:[], loading:false, entityType:'', contextText:'', searchError:'', _debounce:null}
            },
            entityEndpoints: {
                applications: '/api/enterprise/applications?search=',
                archimate_elements: '/archimate/api/elements/search?q=',
                capabilities: '/solutions/capabilities/search?q=',
                vendors: '/api/vendors?search='
            },

            // Step 2: Capabilities
            capabilities: [],
            acceptedCapabilities: [],
            expandedCapabilities: {},
            capabilityDetails: {},
            capabilitiesLoading: false,
            acmCoverageComputed: null,  // computed from acceptedCapabilities on demand

            // Step 2: ACM Domains
            domainsData: {},
            domainsPopulated: false,
            domainsLoading: false,
            expandedDomains: {},
            domainCoverage: 0,
            domainBlockers: [],
            expandedElement: null,  // {code: "APP", id: 123}
            propertyTemplates: {},  // {archimateType: [templates]}
            _propSaveTimer: null,

            // Application Capabilities (cross-cutting concerns for codegen)
            // Named codegenCaps to avoid collision with Step 2's capabilities[] array
            codegenCaps: {
                audit_trail: false,
                multi_tenancy: false,
                webhooks: false,
                notifications: false,
                search: false,
                export: false,
                file_storage: false,
                encryption_at_rest: false,
                mfa: false,
                api_keys: false,
                rate_limiting: false,
                admin_panel: false,
                auth_type: 'jwt-local',
            },
            capabilitiesOpen: false,
            vendorSuggestions: {
                notifications: [
                    {key: 'sendgrid', label: 'SendGrid'},
                    {key: 'mailchimp', label: 'Mailchimp'},
                    {key: 'twilio', label: 'Twilio'},
                    {key: 'slack', label: 'Slack'},
                ],
                file_storage: [
                    {key: 'aws_s3', label: 'AWS S3'},
                    {key: 'azure_blob_storage', label: 'Azure Blob'},
                ],
                search: [
                    {key: 'elasticsearch', label: 'Elasticsearch'},
                    {key: 'algolia', label: 'Algolia'},
                ],
                event_bus: [
                    {key: 'kafka', label: 'Apache Kafka'},
                    {key: 'rabbitmq', label: 'RabbitMQ'},
                ],
                auth: [
                    {key: 'auth0', label: 'Auth0'},
                    {key: 'okta', label: 'Okta'},
                ],
                observability: [
                    {key: 'datadog', label: 'Datadog'},
                ],
            },

            // Step 3: Architecture
            architectureResult: null,
            architectureLoading: false,
            rebuildingRelationships: false,
            critiqueFlags: [],
            critiqueStatus: 'idle',
            critiqueDismissed: false,
            expandedLayers: { motivation: true, strategy: true, business: true, application: true, technology: true, implementation: true, _archimate: true, _rels: true, _variants: false },
            acceptedElements: {},

            // Step 4: Decisions
            decisionPoints: null,
            decisionsLoading: false,
            decisionsTotalElements: 0,

            // Step 4: LLM Variants
            llmVariants: null,
            llmVariantsLoading: false,
            selectedVariants: {},
            variantSelecting: null,

            // Step 5: Roadmap
            roadmapData: null,
            roadmapLoading: false,
            roadmapAutoFilling: false,
            roadmapAutoFillDone: false,

            // Step 6: ARB Package
            arbPackage: null,
            arbLoading: false,
            arbSubmitting: false,
            arbSubmitResult: null,

            // Step 6: Validation (legacy)
            validationResult: null,
            validationLoading: false,

            // Spec inference results (populated by post-generation hooks)
            specInferenceResults: {},
            specInferenceLoading: false,

            // Property save feedback
            _propSaveIndicator: null,

            // Structured intake (Step 1 sub-steps)
            step1Phase: 'context',  // 'context' | 'requirements' | 'current_state' | 'motivation'

            // Genome marketplace templates
            genomeTemplates: null,
            loadingTemplates: false,
            applyingTemplate: false,
            templateApplied: false,
            templateAppliedName: '',
            templateApplyResult: '',

            // Step 1 Motivation sub-phase data
            motivationStakeholders: [],  // [{name, role, concerns}]
            motivationDrivers: [],       // [{name, description}]
            motivationGoals: [],         // [{name, metric}]
            motivationConstraints: [],   // [{name, source, enforcement}]
            _motivationNewStakeholder: '',
            _motivationNewDriver: '',
            _motivationNewGoal: '',
            _motivationNewConstraint: '',
            structuredIntake: {
                business_domain: '',
                timeline_months: null,
                budget_min: null,
                budget_max: null,
                organization_size: '',
                geographic_scope: '',
                user_count: null,
                transaction_volume: null,
                data_volume_gb: null,
                compliance_frameworks: [],
                nfrs: [{type: 'availability', target: '', priority: 'must', acceptance_criteria: '', compliance_reference: '', verification_method: ''}],
                tech_constraints: [],
                drivers: [],
                in_scope_apps: [],
                integration_systems: [],
                pain_points: ['']
            },
            appSearchQuery: '',
            appSearchResults: [],
            integrationSearchQuery: '',
            integrationSearchResults: [],
            structuredIntakeSaved: false,

            // ── Initialization ──────────────────────────────────────────

            init: function () {
                let self = this;
                // Load brief from hidden input if present
                const briefEl = document.getElementById('journey-v2-solution-description');
                if (briefEl && briefEl.value) {
                    self.problemStatement = briefEl.value;
                }
                // Load solution name from hidden input
                const nameEl = document.getElementById('journey-v2-solution-name');
                if (nameEl) {
                    self.solutionName = nameEl.value || '';
                }
                // Load governance status for Step 7 lock
                const govEl = document.getElementById('journey-v2-governance-status');
                if (govEl) {
                    self.governanceStatus = govEl.value || 'draft';
                }
                // Restore saved state
                self._restoreState();

                // Check if code has already been generated for this solution
                // (enables skipping to Step 7 and showing deploy panel)
                // `silent: true` because 404 IS this endpoint's "nothing generated
                // yet" answer, and Platform.fetch's default handler toasted a bare
                // "NOT FOUND" at the user before the 404-tolerant .catch() below
                // ever ran — so every freshly created solution opened its journey
                // behind two error toasts describing a state that is not an error.
                // The genuine failures are still reported, by the .catch().
                _fetch(API_BASE + '/' + self.solutionId + '/codegen/file-list', { silent: true })
                    .then(function (data) {
                        if (data && data.files && data.files.length > 0) {
                            self.hasGeneratedFiles = true;
                            self.codegenResult = { files: data.files, file_count: data.files.length, source: 'prior' };
                        }
                    }).catch(function (e) {
                        // 404 IS the "nothing generated yet" answer from this endpoint,
                        // so that one really is nothing to report. Any other status means
                        // we could not tell — and hasGeneratedFiles stays false, hiding the
                        // Step 7 deploy panel exactly as if no code existed.
                        if (e && e.status === 404) return;
                        Platform.toast.error(
                            'Could not check whether code was already generated for this solution. ' +
                            'The deploy panel stays hidden even if generated code exists — reload to retry.'
                        );
                    });

                // Load any existing structured intake data
                self.loadStructuredIntake();
            },

            // ── Step navigation ─────────────────────────────────────────

            /* Are the code-generation steps (7 Generate, 8 Load Data) open?
             *
             * journey_v3.html calls this in two places — the stepper's
             * `locked_steps_expr` and the "Submit to ARB above to unlock code
             * generation" hint — but nothing ever defined it, so every render of
             * the journey threw `TypeError: codegenUnlocked is not a function`.
             * The stepper then received no locked list at all, which is the
             * opposite of the intended default: an ungoverned solution could walk
             * straight into Generate.
             *
             * The gate is governance, matching the on-screen copy: a solution is
             * unlocked once it has actually been put in front of the ARB. The
             * statuses below are exactly those written after a submission
             * (arb_submission_service, arb_routes, arb_workflow_routes,
             * architecture_assistant_routes); `draft`/`proposed`/`in_progress`
             * are the pre-submission states journey_v2_routes treats as active
             * work. A live submission result in this session counts too, since
             * the hidden governance-status input was rendered before it happened.
             *
             * Already-generated code also unlocks, so returning to a finished
             * solution does not hide its own output behind a re-submission.
             */
            codegenUnlocked: function () {
                if (this.arbSubmitResult) return true;
                if (this.codegenResult) return true;
                if (window.__codegenInit && window.__codegenInit.hasFiles) return true;
                return [
                    'arb_review', 'arb_submitted', 'under_review',
                    'approved', 'conditionally_approved', 'rejected', 'withdrawn',
                ].indexOf(this.governanceStatus) !== -1;
            },

            /* The decision points Step 4's card list can actually render.
             *
             * `decisionPoints` is written from two sources with two different
             * shapes: the domain-mode `/reasoning/decisions` payload, whose items
             * carry an `elements` array, and (in the autopilot chain) the
             * `/reasoning/detect-gaps` payload, which is aliased straight onto the
             * same variable and has no `elements` at all. The card template reads
             * `dp.elements.length` and `dp.elements.map(...)`, so a gap-shaped item
             * raised `TypeError: map is not a function` — an *uncaught* throw, which
             * aborts Alpine's render of the whole subtree rather than degrading one
             * card. Reasoning gaps have their own list higher up the page, so the
             * honest answer here is to render the items this block is written for
             * and count only those, never to coerce a missing array to [] and print
             * a confident "0 elements" for a record that was never measured.
             */
            decisionPointCards: function () {
                return (this.decisionPoints || []).filter(function (d) {
                    return d && Array.isArray(d.elements);
                });
            },

            canProceed: function () {
                // If code has already been generated, allow skipping to any step.
                // The deterministic pipeline doesn't require LLM-dependent steps to re-run.
                if (this.codegenResult || (window.__codegenInit && window.__codegenInit.hasFiles)) {
                    return true;
                }
                switch (this.currentStep) {
                    case 1: return this.enrichedBrief.length > 0 || this.problemStatement.length >= 20;
                    case 2:
                        if (this.domainsPopulated) {
                            // Allow proceeding when MOST domains are resolved.
                            // A single failed LLM call should not block the entire journey.
                            // Threshold: at least 5 of 7 domains must be confirmed/resolved.
                            if (this.domainCoverage >= 7) return true;
                            let self = this;
                            let codes = ['UX', 'APP', 'DATA', 'SEC', 'DEV', 'AI', 'COM'];
                            let resolvedCount = 0;
                            codes.forEach(function (c) {
                                let status = self.getDomainStatus(c);
                                if (status === 'confirmed' || status === 'not_applicable') {
                                    resolvedCount++;
                                } else if (self.domainBlockers.some(function (b) {
                                    return b.domain === c && (b.type === 'property_below_threshold' || b.type === 'fetch_failed');
                                })) {
                                    resolvedCount++; // Count failed-but-acknowledged as resolved
                                }
                            });
                            // Allow proceeding with 5+ resolved (71%+ coverage)
                            return resolvedCount >= 5;
                        }
                        return this.acceptedCapabilities.length >= 1 || this.reasoningCapabilities.length > 0;
                    case 3:
                        if (this.domainsPopulated) { return true; }
                        return this.architectureResult !== null || this.reasoningLandscape.length > 0;
                    case 4:
                        if (this.domainsPopulated) { return true; }
                        return (this.decisionPoints !== null && this.decisionPoints.length > 0) || this.reasoningGaps.length > 0;
                    case 5:
                        if (this.domainsPopulated) { return true; }
                        return this.roadmapData !== null || this.reasoningOptions.length > 0;
                    case 6:
                        return true;
                    default: return true;
                }
            },

            nextStep: function () {
                if (!this.canProceed()) return;
                if (this.completedSteps.indexOf(this.currentStep) === -1) {
                    this.completedSteps.push(this.currentStep);
                }
                const fromStep = this.currentStep;
                // TRAC-001: fire-and-forget chain health check for steps 2-5
                if (fromStep >= 2 && fromStep <= 5) {
                    this._validateStepChain(fromStep);
                }
                this.currentStep = Math.min(this.currentStep + 1, 10);
                // Auto-load promoted elements into Step 3 when coming from domain-driven Step 2
                if (this.currentStep === 3 && this.domainsPopulated && !this.architectureResult) {
                    this._loadArchitectureFromPromoted();
                }
                // Auto-load application landscape when entering Step 3 (both modes)
                if (this.currentStep === 3 && this.reasoningLandscape.length === 0) {
                    this._loadLandscape();
                }
                // Auto-load data for Steps 4-6
                this._autoLoadStepData(this.currentStep);
                this.updateCopilot();
                this._saveState();

                // Auto-run pipeline: when leaving Step 1 with a brief, run Steps 2-6 automatically
                if (fromStep === 1 && !this.domainsPopulated) {
                    // Ensure we have a brief to work with
                    if (!this.enrichedBrief && this.problemStatement.length >= 20) {
                        this.enrichedBrief = this.problemStatement;
                    }
                    if (this.enrichedBrief) {
                        // Save structured intake before running pipeline
                        let self = this;
                        if (!self.structuredIntakeSaved && self.structuredIntake.business_domain) {
                            self.saveStructuredIntake().then(function () {
                                self._runReasoningPipeline();
                            });
                        } else {
                            // Use reasoning pipeline (graph-based: capabilities -> landscape -> gaps -> options -> blueprint)
                            // Falls back to domain pipeline if reasoning endpoints are unavailable
                            this._runReasoningPipeline();
                        }
                    }
                }
            },

            /**
             * Run the full architecture pipeline automatically.
             * Called when architect submits their problem statement.
             * Chains: populate domains → accept all → confirm all → load architecture → decisions → roadmap → ARB package.
             * The architect sees each step activate as it completes and lands on Step 6.
             */
            _runFullPipeline: function () {
                let self = this;
                if (self._pipelineRunning) { return; }  // idempotency guard — prevent double-submit
                self._pipelineRunning = true;
                self.copilotMessage = 'Running full architecture pipeline...';

                // Step 2: Populate domains
                self.domainsLoading = true;
                _fetch(API_BASE + '/' + self.solutionId + '/populate-domains', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        enriched_brief: self.enrichedBrief || self.problemStatement,
                        industry_overlay: self._detectIndustryOverlay()
                    })
                }).then(function (data) {
                    self.domainsData = data.domains || {};
                    self.domainsPopulated = true;
                    self.domainsLoading = false;
                    let count = Object.keys(self.domainsData).length;
                    self.copilotMessage = count + ' domains populated. Accepting and confirming...';
                    self._saveState();

                    // Accept all baselines
                    const allProposalIds = [];
                    Object.keys(self.domainsData).forEach(function (code) {
                        let elements = self.getDomainElements(code);
                        elements.forEach(function (el) {
                            if (el.status === 'proposed' && !el.waived) {
                                allProposalIds.push(el.id);
                            }
                        });
                    });

                    let acceptPromise = allProposalIds.length > 0
                        ? _fetch(API_BASE + '/' + self.solutionId + '/proposals/batch-accept', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ proposal_ids: allProposalIds })
                        })
                        : Promise.resolve();

                    return acceptPromise;
                }).then(function () {
                    // Mark all elements as accepted in local state
                    Object.keys(self.domainsData).forEach(function (code) {
                        let elements = self.getDomainElements(code);
                        elements.forEach(function (el) { el.status = 'accepted'; });
                    });
                    self.copilotMessage = 'All baselines accepted. Confirming 7 domains...';

                    // Confirm all domains sequentially to avoid overwhelming the server
                    let codes = ['UX', 'APP', 'DATA', 'SEC', 'DEV', 'AI', 'COM'];
                    let chain = Promise.resolve();
                    let confirmed = 0;
                    codes.forEach(function (code) {
                        chain = chain.then(function () {
                            self.copilotMessage = 'Confirming ' + code + '... (' + (confirmed + 1) + '/7)';
                            self.currentStep = 2;
                            return _fetch(API_BASE + '/' + self.solutionId + '/domain/' + code + '/confirm', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' }
                            }).then(function () {
                                if (self.domainsData[code] && self.domainsData[code].spec) {
                                    self.domainsData[code].spec.status = 'confirmed';
                                }
                                confirmed++;
                            }).catch(function (e) {
                                // Non-fatal: domain skipped
                                self.copilotMessage = code + ' skipped: ' + (e.message || 'Unknown');
                            });
                        });
                    });

                    return chain.then(function () {
                        self._refreshCompleteness();
                        self.copilotMessage = confirmed + '/7 domains confirmed. Loading architecture...';
                    });
                }).then(function () {
                    // Step 3: Load architecture
                    self.completedSteps = [1, 2];
                    self.currentStep = 3;
                    self._loadArchitectureFromPromoted();
                    self.copilotMessage = 'Architecture loaded. Inferring component specs...';

                    // Fire-and-forget: infer component specs after architecture generation
                    self._onArchitectureGenerated();

                    // Step 4: Load decisions
                    self.completedSteps.push(3);
                    self.currentStep = 4;
                    return new Promise(function (resolve) {
                        self.decisionsLoading = true;
                        _fetch(API_BASE + '/' + self.solutionId + '/decision-points')
                            .then(function (data) {
                                self.decisionPoints = data.decision_points || [];
                                self.decisionsTotalElements = data.total_elements || 0;
                                self.copilotMessage = (self.decisionPoints.length || 0) + ' decision categories. Building roadmap...';
                            })
                            .catch(function () { self.copilotMessage = 'Decisions skipped. Building roadmap...'; })
                            .then(function () {
                                self.decisionsLoading = false;
                                // Fire-and-forget: suggest integration contracts after variant/decision step
                                self._onVariantSelected();
                                resolve();
                            });
                    });
                }).then(function () {
                    // Step 5: Load roadmap
                    self.completedSteps.push(4);
                    self.currentStep = 5;
                    return new Promise(function (resolve) {
                        self.roadmapLoading = true;
                        _fetch(API_BASE + '/' + self.solutionId + '/roadmap-data')
                            .then(function (data) {
                                self.roadmapData = data;
                                self.copilotMessage = (data.total_phases || 0) + ' delivery phases planned. Assembling ARB package...';
                            })
                            .catch(function () { self.copilotMessage = 'Roadmap skipped. Assembling ARB package...'; })
                            .then(function () {
                                self.roadmapLoading = false;
                                // Fire-and-forget: suggest deployment specs after migration/roadmap step
                                self._onMigrationGenerated();
                                resolve();
                            });
                    });
                }).then(function () {
                    // Step 6: Load ARB package
                    self.completedSteps.push(5);
                    self.currentStep = 6;
                    return new Promise(function (resolve) {
                        self.arbLoading = true;
                        _fetch(API_BASE + '/' + self.solutionId + '/arb-package')
                            .then(function (data) {
                                self.arbPackage = data;
                                self.copilotMessage = 'Architecture complete. ' + (data.summary ? data.summary.total_elements : '?') + ' elements across 7 domains. Review each step or submit to ARB.';
                            })
                            .catch(function () { self.copilotMessage = 'ARB package assembly failed.'; })
                            .then(function () { self.arbLoading = false; resolve(); });
                    });
                }).then(function () {
                    self.completedSteps.push(6);
                    self._pipelineRunning = false;
                    self._saveState();
                }).catch(function (e) {
                    self._pipelineRunning = false;
                    self.error = 'Pipeline failed at current step: ' + (e.message || 'Unknown');
                    self.copilotMessage = 'Pipeline stopped. You can continue manually from here.';
                });
            },

            prevStep: function () {
                this.currentStep = Math.max(this.currentStep - 1, 1);
                this._autoLoadStepData(this.currentStep);
                this.updateCopilot();
            },

            goToStep: function (step) {
                // Allow jumping to Step 7+ if code has been generated (skip LLM-dependent steps)
                let hasCode = this.codegenResult || (window.__codegenInit && window.__codegenInit.hasFiles);
                const maxReached = hasCode ? 10 : (this.completedSteps.length > 0 ? Math.max.apply(null, this.completedSteps) + 1 : 1);
                if (step <= maxReached) {
                    this.currentStep = step;
                    // Auto-load promoted elements into Step 3 when navigating
                    if (step === 3 && this.domainsPopulated && !this.architectureResult) {
                        this._loadArchitectureFromPromoted();
                    }
                    this._autoLoadStepData(step);
                    this.updateCopilot();
                }
            },

            updateCopilot: function () {
                switch (this.currentStep) {
                    case 1:
                        if (this.enrichedBrief) {
                            this.copilotMessage = 'Brief enriched with your answers. Review it, then proceed to capabilities.';
                        } else if (this.clarifyQuestions.length > 0) {
                            this.copilotMessage = 'Answer the clarifying questions to help me understand your problem better.';
                        } else {
                            this.copilotMessage = 'Describe your business problem. I\'ll ask clarifying questions before generating.';
                        }
                        break;
                    case 2:
                        if (this.capabilities.length > 0) {
                            this.copilotMessage = this.capabilities.length + ' capabilities derived. Accept or reject each, then expand for details.';
                        } else {
                            this.copilotMessage = 'Click "Derive Capabilities" to identify business, technical, and application capabilities.';
                        }
                        break;
                    case 3:
                        if (this.architectureResult) {
                            this.copilotMessage = 'Architecture generated. Review elements by layer -- accept or reject each.';
                        } else {
                            this.copilotMessage = 'Click "Generate Architecture" to create ArchiMate elements from your accepted capabilities.';
                        }
                        break;
                    case 4:
                        if (this.decisionPoints && this.decisionPoints.length > 0) {
                            this.copilotMessage = this.decisionPoints.length + ' decision categories identified from element properties.';
                        } else {
                            this.copilotMessage = 'Load architecture decisions derived from element properties (build/buy, deployment model, availability).';
                        }
                        break;
                    case 5:
                        if (this.roadmapData && this.roadmapData.phases && this.roadmapData.phases.length > 0) {
                            this.copilotMessage = this.roadmapData.total_phases + ' delivery phases with ' + this.roadmapData.total_elements + ' elements.';
                        } else {
                            this.copilotMessage = 'Build implementation roadmap from element dependencies and effort estimates.';
                        }
                        break;
                    case 6:
                        if (this.arbSubmitResult) {
                            this.copilotMessage = 'Submitted to ARB: ' + this.arbSubmitResult.review_number + '. Proceed to Step 7 to generate implementation code.';
                        } else if (this.arbPackage) {
                            this.copilotMessage = this.arbPackage.ready_for_arb ? 'Ready for ARB submission.' : 'Not all domains confirmed. Review the package.';
                        } else {
                            this.copilotMessage = 'Build ARB review package for Architecture Review Board submission.';
                        }
                        break;
                    case 7:
                        if (this.codegenResult) {
                            this.copilotMessage = 'Code generated: ' + this.codegenResult.file_count + ' files in ' + this.codegenResult.language + '. Download the ZIP to start building.';
                        } else if (this.codegenLoading) {
                            this.copilotMessage = 'Generating code from your architecture blueprint…';
                        } else if (this.codegenError) {
                            this.copilotMessage = 'Code generation failed. Check the error and retry.';
                        } else {
                            this.copilotMessage = 'Select a language and generate a deterministic code project from your architecture blueprint.';
                        }
                        break;
                    case 8:
                        this.copilotMessage = 'Load your data — upload spreadsheets, connect external systems, or enter data manually through the admin panel.';
                        break;
                    case 9:
                        this.copilotMessage = 'Define how your solution behaves — approval workflows, validations, notifications, and automations.';
                        break;
                    case 10:
                        this.copilotMessage = 'Your solution is live. Submit change requests to evolve it, or view version history and rollback if needed.';
                        break;
                }
            },

            // ── ACM Domain Coverage (Step 2 greenfield mode) ─────────────

            // ACM domain metadata — must match backend _ACM_DOMAIN_KEYWORDS keys
            _acmDomainNames: {
                UX: 'User Experience', APP: 'Application Services',
                DATA: 'Data & Storage', SEC: 'Security & Identity',
                DEV: 'DevOps & Platform', AI: 'AI & Analytics', COM: 'Communication'
            },

            /**
             * Compute ACM domain coverage from the current acceptedCapabilities list.
             * Uses the acm_domain field returned by the discover-capabilities endpoint.
             * Falls back to 'APP' when acm_domain is absent (older cached results).
             * Returns {by_domain, covered_count, uncovered_domains, coverage_pct}.
             */
            computeAcmCoverage: function () {
                let self = this;
                let coverage = { UX: [], APP: [], DATA: [], SEC: [], DEV: [], AI: [], COM: [] };
                self.acceptedCapabilities.forEach(function (c) {
                    let d = c.acm_domain || 'APP';
                    if (coverage[d]) coverage[d].push(c.name);
                    else coverage['APP'].push(c.name);
                });
                const covered = Object.keys(coverage).filter(function (d) { return coverage[d].length > 0; });
                const uncovered = Object.keys(coverage).filter(function (d) { return coverage[d].length === 0; });
                const byDomain = {};
                Object.keys(coverage).forEach(function (code) {
                    byDomain[code] = {
                        name: self._acmDomainNames[code] || code,
                        capabilities: coverage[code],
                        covered: coverage[code].length > 0
                    };
                });
                self.acmCoverageComputed = {
                    by_domain: byDomain,
                    covered_count: covered.length,
                    uncovered_domains: uncovered,
                    coverage_pct: Math.round(covered.length / 7 * 100)
                };
                return self.acmCoverageComputed;
            },

            // ── Step 7: Code Generation ───────────────────────────────────

            generateCode: function () {
                let self = this;
                if (self.codegenLoading) return;  // idempotency guard — prevent double-generate
                self.codegenLoading = true;
                self.codegenError = null;
                self.codegenResult = null;
                self._validateGenerationReadiness()
                    .then(function () {
                        return self._runCodegenFromWorkbench();
                    })
                    .then(function (genData) {
                        const isFullstack = self.codegenLanguage === 'react-shadcn';
                        self.codegenResult = {
                            file_count: genData.file_count || Object.keys(genData.files || {}).length,
                            language: isFullstack ? 'Python FastAPI + Next.js/shadcn' : (genData.language || self.codegenLanguage),
                            // Template expects 0..1 and multiplies by 100 for display.
                            spec_maturity: (typeof genData.quality_score === 'number') ? (genData.quality_score / 100.0) : 0,
                            quality_score: genData.quality_score || null,
                            spec_hash: genData.version_label || ('v' + (genData.version || 1)),
                            services: genData.services || [],
                            _fullstack: isFullstack,
                            source: 'workbench'
                        };
                        self.codegenBundleId = null;
                        self.codegenLoading = false;
                        self.updateCopilot();
                    })
                    .catch(function (e) {
                        self.codegenLoading = false;
                        self.codegenError = (e && e.body && e.body.error) || (e && e.message) || 'Code generation failed.';
                        self.updateCopilot();
                    });
            },

            deployToCloud: function () {
                let self = this;
                self.deployStatus = 'deploying';
                self.deployError = null;
                _fetch('/solutions/' + self.solutionId + '/codegen/deploy-cloud', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function (data) {
                    self.deployUrl = data.deployment_url;
                    self.deployVersion = data.version;
                    self.deployStatus = 'deploying';
                    self._pollDeployStatus();
                }).catch(function (e) {
                    self.deployStatus = 'error';
                    self.deployError = (e && e.body && e.body.error) || (e && e.message) || 'Deployment failed';
                });
            },

            _pollDeployStatus: function () {
                let self = this;
                let attempts = 0;
                let maxAttempts = 60;
                let timer = setInterval(function () {
                    attempts += 1;
                    _fetch('/solutions/' + self.solutionId + '/codegen/deploy-cloud/status')
                        .then(function (data) {
                            self.deployStatus = data.status;
                            self.deployUrl = data.deployment_url;
                            self.deployVersion = data.version;
                            if (['healthy', 'unhealthy', 'stopped'].indexOf(data.status) !== -1) {
                                clearInterval(timer);
                            }
                        })
                        .catch(function () {
                            // continue polling
                        });
                    if (attempts >= maxAttempts) {
                        clearInterval(timer);
                        self.deployStatus = 'error';
                        self.deployError = 'Deployment timed out after 5 minutes.';
                    }
                }, 5000);
            },

            destroyDeployment: async function () {
                let self = this;
                if (!(await Platform.modal.confirm('This will stop your deployed application. Continue?'))) return;
                _fetch('/solutions/' + self.solutionId + '/codegen/deploy-cloud', {
                    method: 'DELETE'
                }).then(function () {
                    self.deployStatus = null;
                    self.deployUrl = null;
                }).catch(function (e) {
                    self.deployError = (e && e.body && e.body.error) || (e && e.message) || 'Failed to stop deployment';
                });
            },

            _validateGenerationReadiness: function () {
                // Advisory-only — surfaces warnings in UI but never blocks generation.
                // Validation endpoint may not exist; network failures are swallowed.
                let self = this;
                self.codegenWarnings = [];
                return Promise.resolve();
            },

            _ensureWorkbenchUml: function () {
                let self = this;
                return _fetch('/solutions/' + self.solutionId + '/codegen/uml').then(function (umlData) {
                    if (umlData && umlData.uml) {
                        return umlData;
                    }
                    return _fetch('/solutions/' + self.solutionId + '/codegen/enrich', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ version: (umlData && umlData.version) || 0 })
                    }).then(function () {
                        return new Promise(function (resolve, reject) {
                            let attempts = 0;
                            const maxAttempts = 120; // ~4 minutes @ 2s polling
                            const timer = setInterval(function () {
                                attempts += 1;
                                _fetch('/solutions/' + self.solutionId + '/codegen/enrich/status')
                                    .then(function (st) {
                                        if (st.status === 'done') {
                                            clearInterval(timer);
                                            _fetch('/solutions/' + self.solutionId + '/codegen/uml')
                                                .then(resolve)
                                                .catch(reject);
                                        } else if (st.status === 'failed') {
                                            clearInterval(timer);
                                            reject(new Error(st.error || 'UML enrichment failed'));
                                        } else if (attempts >= maxAttempts) {
                                            clearInterval(timer);
                                            reject(new Error('UML enrichment timed out'));
                                        }
                                    })
                                    .catch(function (err) {
                                        clearInterval(timer);
                                        reject(err);
                                    });
                            }, 2000);
                        });
                    });
                });
            },

            _runCodegenFromWorkbench: function () {
                let self = this;
                return self._ensureWorkbenchUml()
                    .then(function () {
                        // "No confirmed field specs" is a 400 and a perfectly normal
                        // state — there is nothing to apply, so generation proceeds.
                        // Every OTHER failure means the architect's confirmed fields
                        // were NOT written into the UML snapshot, and the code about to
                        // be generated silently falls back to LLM-invented fields.
                        return _fetch('/solutions/' + self.solutionId + '/codegen/apply-specs', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({})
                        }).catch(function (e) {
                            if (!e || e.status !== 400) {
                                Platform.toast.error('Could not apply your confirmed field specs: '
                                    + ((e && e.message) || 'request failed')
                                    + '. Generation continues, but the generated code uses the inferred fields, not your confirmed ones.');
                            }
                            return null;
                        });
                    })
                    .then(function () {
                        let payload = {
                            language: self.codegenLanguage === 'react-shadcn' ? 'python-fastapi' : self.codegenLanguage,
                            ui_framework: self.codegenLanguage === 'react-shadcn' ? 'shadcn-nextjs' : 'none',
                            generation_mode: self.codegenMode || 'hybrid',
                            generation_policy: 'best-effort',
                            enforce_chain_complete: false,
                            require_confirmed_specs: false,
                            block_placeholder_stubs: self.codegenMode !== 'genome'
                        };
                        // Genome mode: pass security + mobile config
                        if (self.codegenMode === 'genome') {
                            payload.multi_tenancy = self.genomeOpts.multiTenancy;
                            payload.api_keys = self.genomeOpts.apiKeys;
                            payload.encryption_at_rest = self.genomeOpts.encryptionAtRest;
                            payload.mfa = self.genomeOpts.mfa;
                            if (self.genomeOpts.mobileApp) {
                                payload.mobile = {
                                    framework: 'expo-react-native',
                                    offline: { tier: 1, conflict_strategy: 'last_write_wins' },
                                    push_notifications: { enabled: false },
                                    features: []
                                };
                            }
                        }
                        return _fetch('/solutions/' + self.solutionId + '/codegen/generate', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload)
                        });
                    });
            },

            downloadCode: function () {
                window.location.href = '/solutions/' + this.solutionId + '/codegen/download';
            },

            runDockerPreview: function () {
                let self = this;
                if (self.dockerPreviewLoading) return;
                if (self.dockerPreviewUrl) { window.open(self.dockerPreviewUrl, '_blank'); return; }
                self.dockerPreviewLoading = true;
                self.dockerPreviewError = null;
                _fetch('/solutions/' + self.solutionId + '/codegen/docker-preview', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                }).then(function (data) {
                    self.dockerPreviewLoading = false;
                    if (data.url) {
                        self.dockerPreviewUrl = data.url;
                        window.open(data.url, '_blank');
                    } else {
                        self.dockerPreviewError = data.error || 'Docker preview failed to start.';
                    }
                }).catch(function (e) {
                    self.dockerPreviewLoading = false;
                    self.dockerPreviewError = (e && e.body && e.body.error) || (e && e.message) || 'Docker not available on this server.';
                });
            },

            openStackBlitz: function () {
                let self = this;
                if (self.stackblitzLoading) return;
                self.stackblitzLoading = true;
                _fetch('/solutions/' + self.solutionId + '/codegen/stackblitz-data')
                .then(function (data) {
                    self.stackblitzLoading = false;
                    let files = data.files || {};
                    if (!Object.keys(files).length) {
                        Platform.toast.warning('No frontend files found — generate with Full-Stack selected first.');
                        return;
                    }
                    // POST files to StackBlitz via hidden form (their API requires form submission)
                    const form = document.createElement('form');
                    form.method = 'POST';
                    form.action = 'https://stackblitz.com/run';
                    form.target = '_blank';
                    const addField = function(name, value) {
                        const input = document.createElement('input');
                        input.type = 'hidden';
                        input.name = name;
                        input.value = value;
                        form.appendChild(input);
                    };
                    addField('project[title]', data.title || 'Entelim Generated App');
                    addField('project[description]', 'Generated by Entelim');
                    addField('project[template]', 'node');
                    Object.keys(files).forEach(function(path) {
                        addField('project[files][' + path + ']', files[path]);
                    });
                    document.body.appendChild(form);
                    form.submit();
                    document.body.removeChild(form);
                }).catch(function (e) {
                    self.stackblitzLoading = false;
                    Platform.toast.error('StackBlitz data failed: ' + ((e && e.message) || 'unknown error'));
                });
            },

            // ── Step 1: Structured Intake ─────────────────────────────────

            // Step 1 sub-step navigation
            nextStep1Phase: function () {
                if (this.step1Phase === 'context') return 'requirements';
                if (this.step1Phase === 'requirements') return 'current_state';
                if (this.step1Phase === 'current_state') return 'motivation';
                return 'motivation';
            },
            prevStep1Phase: function () {
                if (this.step1Phase === 'motivation') return 'current_state';
                if (this.step1Phase === 'current_state') return 'requirements';
                if (this.step1Phase === 'requirements') return 'context';
                return 'context';
            },

            addMotivationItem: function (list, item) {
                if (item && item.trim()) {
                    this[list].push({name: item.trim(), description: '', source: '', enforcement: 'MUST'});
                }
            },
            removeMotivationItem: function (list, index) {
                this[list].splice(index, 1);
            },

            // Application search for entity pickers
            searchApps: function () {
                let self = this;
                if (self.appSearchQuery.length < 2) { self.appSearchResults = []; return; }
                _fetch('/api/enterprise/applications?search=' + encodeURIComponent(self.appSearchQuery) + '&limit=10')
                    .then(function (data) {
                        self.appSearchResults = (data.results || data || []).filter(function (app) {
                            return !self.structuredIntake.in_scope_apps.some(function (a) { return a.id === app.id; });
                        });
                    })
                    .catch(function (e) {
                        // Do NOT invent data; leave appSearchResults empty.
                        self.appSearchResults = [];
                        // The existing inline error state is already handled by _fetch's error path.
                        // We must not swallow the error; rethrow to maintain the rule.
                        throw e;
                    });
            },
            addScopeApp: function (app) {
                this.structuredIntake.in_scope_apps.push({ id: app.id, name: app.name, lifecycle_status: app.lifecycle_status || '' });
                this.appSearchResults = [];
                this.appSearchQuery = '';
            },
            removeScopeApp: function (appId) {
                this.structuredIntake.in_scope_apps = this.structuredIntake.in_scope_apps.filter(function (a) { return a.id !== appId; });
            },

            // Integration system search
            searchIntegrations: function () {
                let self = this;
                if (self.integrationSearchQuery.length < 2) { self.integrationSearchResults = []; return; }
                _fetch('/api/enterprise/applications?search=' + encodeURIComponent(self.integrationSearchQuery) + '&limit=10')
                    .then(function (data) {
                        self.integrationSearchResults = (data.results || data || []).filter(function (sys) {
                            return !self.structuredIntake.integration_systems.some(function (s) { return s.id === sys.id; });
                        });
                    })
                    .catch(function (e) {
                        // Do NOT invent data; leave integrationSearchResults empty.
                        self.integrationSearchResults = [];
                        // Rethrow to avoid swallowing error.
                        throw e;
                    });
            },
            addIntegration: function (sys) {
                this.structuredIntake.integration_systems.push({ id: sys.id, name: sys.name });
                this.integrationSearchResults = [];
                this.integrationSearchQuery = '';
            },
            removeIntegration: function (sysId) {
                this.structuredIntake.integration_systems = this.structuredIntake.integration_systems.filter(function (s) { return s.id !== sysId; });
            },

            // Save structured intake to backend
            saveStructuredIntake: function () {
                let self = this;
                self.loading = true;
                self.copilotMessage = 'Saving structured requirements...';

                // Clean empty entries
                const intake = JSON.parse(JSON.stringify(self.structuredIntake));
                intake.nfrs = intake.nfrs.filter(function (n) { return n.target; });
                intake.tech_constraints = intake.tech_constraints.filter(function (t) { return t.technology; });
                intake.drivers = intake.drivers.filter(function (d) { return d.name; });
                intake.pain_points = intake.pain_points.filter(function (p) { return p; });

                // Include motivation data from Step 1d
                intake.motivation = {
                    stakeholders: self.motivationStakeholders,
                    drivers: self.motivationDrivers,
                    goals: self.motivationGoals,
                    constraints: self.motivationConstraints,
                };

                return _fetch(API_BASE + '/' + self.solutionId + '/structured-intake', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(intake)
                }).then(function (data) {
                    self.structuredIntakeSaved = true;
                    self.loading = false;
                    self.copilotMessage = 'Requirements saved: ' +
                        (data.drivers_count || 0) + ' drivers, ' +
                        (data.constraints_count || 0) + ' constraints, ' +
                        (data.requirements_count || 0) + ' NFRs. Ready for capability derivation.';
                    return data;
                }).catch(function (e) {
                    self.loading = false;
                    self.error = 'Failed to save intake: ' + (e.message || 'Unknown error');
                });
            },

            // Load existing structured intake (on page load / session restore)
            loadStructuredIntake: function () {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/structured-intake')
                    .then(function (data) {
                        if (data && data.business_domain) {
                            self.structuredIntake = Object.assign(self.structuredIntake, data);
                            self.structuredIntakeSaved = true;
                        }
                    })
                    .catch(function (e) {
                        // "No existing intake" is not this branch — the endpoint answers
                        // 200 with an empty structure the first time through. A throw means
                        // the saved intake could not be read, and the blank form the user is
                        // looking at will overwrite it the moment they save.
                        self.error = 'Could not load your saved requirements intake: '
                            + ((e && e.message) || 'request failed')
                            + '. The form below is blank because it could not be read, not because it is empty — reload before saving.';
                    });
            },

            // ── Step 1: Clarify ─────────────────────────────────────────

            _detectEntityType: function (questionText) {
                const text = (questionText || '').toLowerCase();
                const keywords = {
                    applications: ['application', 'system', 'legacy', 'platform', 'software', 'tool', 'integrate'],
                    capabilities: ['capability', 'function', 'process', 'competenc'],
                    vendors: ['vendor', 'supplier', 'provider', 'partner'],
                    archimate_elements: ['component', 'service', 'interface', 'node', 'artifact', 'element']
                };
                for (let type in keywords) {
                    for (let i = 0; i < keywords[type].length; i++) {
                        if (text.indexOf(keywords[type][i]) !== -1) {
                            return { type: 'entity_picker', entity_type: type };
                        }
                    }
                }
                return { type: 'text', entity_type: '' };
            },

            searchEntities: function (idx) {
                let self = this;
                let ps = self.pickerState[idx];
                if (!ps || !ps.entityType) return;
                ps.searchError = '';
                clearTimeout(ps._debounce);
                if ((ps.query || '').trim().length < 2) { ps.results = []; return; }
                ps._debounce = setTimeout(function () {
                    ps.loading = true;
                    const endpoint = self.entityEndpoints[ps.entityType];
                    if (!endpoint) { ps.loading = false; ps.searchError = 'No search endpoint for this entity type'; return; }
                    // Use Platform.fetch with silent:true to avoid duplicate toasts (inline error is shown via searchError)
                    Platform.fetch.get(endpoint + encodeURIComponent(ps.query.trim()) + '&limit=10', null, { silent: true })
                        .then(function (data) {
                            // Handle different response formats from various API endpoints
                            let items = [];
                            if (Array.isArray(data)) items = data;
                            else if (data.data && Array.isArray(data.data)) items = data.data;
                            else if (data.applications) items = data.applications;
                            else if (data.capabilities) items = data.capabilities;
                            else if (data.elements) items = data.elements;
                            else if (data.vendors) items = data.vendors;
                            else if (data.results) items = data.results;
                            else if (data.items) items = data.items;
                            if (!Array.isArray(items)) items = [];
                            const selectedIds = ps.selected.map(function (e) { return e.id; });
                            ps.results = items.filter(function (item) {
                                return selectedIds.indexOf(item.id) === -1;
                            }).slice(0, 10);
                            ps.searchError = '';
                        })
                        .catch(function (e) {
                            // Do NOT use console.error. Surface error via searchError.
                            ps.results = [];
                            ps.searchError = 'Search unavailable for this entity type';
                            // Rethrow to avoid swallowing error.
                            throw e;
                        })
                        .then(function () { ps.loading = false; });
                }, 300);
            },

            addEntity: function (idx, entity) {
                let ps = this.pickerState[idx];
                if (!ps) return;
                let name = entity.name || entity.application_name || ('Entity #' + entity.id);
                if (!ps.selected.find(function (e) { return e.id === entity.id; })) {
                    ps.selected.push({ id: entity.id, name: name, layer: entity.layer || '', type: entity.type || '' });
                }
                ps.query = '';
                ps.results = [];
            },

            removeEntity: function (idx, entityId) {
                let ps = this.pickerState[idx];
                if (!ps) return;
                ps.selected = ps.selected.filter(function (e) { return e.id !== entityId; });
            },

            addFreeText: function (idx) {
                let ps = this.pickerState[idx];
                if (!ps || !ps.query || !ps.query.trim()) return;
                ps.selected.push({ id: null, name: ps.query.trim() });
                ps.query = '';
                ps.results = [];
            },

            briefIngestionNotice: '',   // shown after dual-pipeline upload
            briefUploadProgress: '',   // per-file progress label ("Reading doc2.pdf (2 of 2)…")

            searchLandscape: function () {
                let self = this;
                if (self._landscapeTimer) clearTimeout(self._landscapeTimer);
                if (self.problemStatement.length < 50) {
                    self.landscapeMatches = null;
                    return;
                }
                self._landscapeTimer = setTimeout(function () {
                    self.landscapeSearching = true;
                    _fetch(API_BASE + '/landscape-search', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ problem_statement: self.problemStatement })
                    }).then(function (data) {
                        self.landscapeMatches = data;
                    }).catch(function () {
                        self.landscapeMatches = null;
                    }).finally(function () {
                        self.landscapeSearching = false;
                    });
                }, 800);
            },

            importBriefFromDoc: function (event) {
                let self = this;
                let files = Array.from(event.target.files || []);
                event.target.value = '';
                if (!files.length) return;

                self.briefUploading = true;
                self.briefIngestionNotice = '';
                const briefs = [];
                let anyIngestionStarted = false;
                const errors = [];

                // Process files sequentially to keep server load manageable
                let idx = 0;
                function processNext() {
                    if (idx >= files.length) {
                        // All done — combine briefs into problem statement
                        self.briefUploading = false;
                        self.briefUploadProgress = '';
                        if (errors.length && !briefs.length) {
                            Platform.toast.error('Could not extract text from any document:\n' + errors.join('\n'));
                            return;
                        }
                        if (briefs.length === 1) {
                            self.problemStatement = briefs[0].text;
                        } else if (briefs.length > 1) {
                            // Merge: each brief labelled by filename
                            self.problemStatement = briefs.map(function(b) {
                                return '[' + b.name + ']\n' + b.text;
                            }).join('\n\n');
                        }
                        if (anyIngestionStarted) {
                            const docCount = briefs.length;
                            self.briefIngestionNotice =
                                'Architecture elements are being extracted from ' +
                                docCount + ' document' + (docCount === 1 ? '' : 's') +
                                ' in the background — review proposals on the Blueprint page once you reach it.';
                        }
                        if (errors.length) {
                            self.briefIngestionNotice = (self.briefIngestionNotice ? self.briefIngestionNotice + ' ' : '') +
                                'Warning: ' + errors.join('; ');
                        }
                        return;
                    }
                    const file = files[idx];
                    self.briefUploadProgress = 'Reading ' + file.name + ' (' + (idx + 1) + ' of ' + files.length + ')…';
                    idx++;

                    const fd = new FormData();
                    fd.append('file', file);
                    // raw-fetch-ok: FormData upload with file requires multipart/form-data, which Platform.fetch cannot handle automatically.
                    Platform.fetch(API_BASE + '/' + self.solutionId + '/extract-brief', {
                        method: 'POST',
                        body: fd,
                        silent: true // We handle errors inline via errors array
                    }).then(function(body) {
                        if (body.success && body.data && body.data.brief) {
                            briefs.push({ name: file.name, text: body.data.brief });
                            if (body.data.ingestion_started) anyIngestionStarted = true;
                        } else {
                            errors.push(file.name + ': ' + ((body.error || body.message) || 'extraction failed'));
                        }
                    }).catch(function(e) {
                        errors.push(file.name + ': ' + (e.message || 'network error'));
                    }).finally(function() {
                        processNext();
                    });
                }
                processNext();
            },

            getClarifyingQuestions: function () {
                let self = this;
                if (self.problemStatement.length < 20 || self.loading) return;

                self.loading = true;
                self.error = null;
                self.copilotMessage = 'Analyzing your problem statement...';

                _fetch(API_BASE + '/' + self.solutionId + '/clarify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ problem_statement: self.problemStatement })
                }).then(function (data) {
                    self.clarifyQuestions = data.questions || [];
                    // Initialize answers object
                    self.clarifyAnswers = {};
                    self.clarifyQuestions.forEach(function (q, idx) {
                        self.clarifyAnswers[idx] = '';
                        // Classify question type: LLM type takes precedence, then keyword detection
                        if (!q.type || q.type === 'text') {
                            const detected = self._detectEntityType(typeof q === 'string' ? q : (q.question || ''));
                            if (!q.type && detected.type === 'entity_picker') {
                                q.type = detected.type;
                                q.entity_type = detected.entity_type;
                            }
                        }
                        // Initialize picker state for entity_picker questions
                        if (q.type === 'entity_picker' && self.pickerState[idx]) {
                            self.pickerState[idx].entityType = q.entity_type || 'applications';
                            self.pickerState[idx].query = '';
                            self.pickerState[idx].results = [];
                            self.pickerState[idx].selected = [];
                            self.pickerState[idx].contextText = '';
                        }
                    });
                    self.clarifyPhase = 'questions';
                    if (self.clarifyQuestions.length === 0) {
                        self.llmDegraded.step1 = true;
                        self.copilotMessage = 'LLM unavailable — proceed directly with your problem statement.';
                    } else {
                        self.llmDegraded.step1 = false;
                        self.copilotMessage = self.clarifyQuestions.length + ' questions generated. Answer what you can, skip the rest.';
                    }
                }).catch(function (e) {
                    self.error = 'Failed to get clarifying questions: ' + (e.message || 'Unknown error');
                    self.copilotMessage = 'Clarification failed. You can retry or skip to generation.';
                }).then(function () {
                    self.loading = false;
                });
            },

            skipQuestion: function (idx) {
                this.clarifyAnswers[idx] = '[skipped]';
            },

            submitClarifyAnswers: function () {
                let self = this;
                if (self.loading) return;

                self.loading = true;
                self.error = null;
                self.copilotMessage = 'Enriching your brief with the answers...';

                // Serialize picker selections into clarifyAnswers strings
                self.clarifyQuestions.forEach(function (q, idx) {
                    let ps = self.pickerState[idx];
                    if (q.type === 'entity_picker' && ps && ps.selected && ps.selected.length > 0) {
                        const names = ps.selected.map(function (e) { return e.name; }).join(', ');
                        let ctx = (ps.contextText || '').trim();
                        self.clarifyAnswers[idx] = ctx ? names + '; ' + ctx : names;
                    }
                });

                // Build answers array
                const answers = [];
                self.clarifyQuestions.forEach(function (q, idx) {
                    const answer = self.clarifyAnswers[idx] || '';
                    if (answer && answer !== '[skipped]') {
                        answers.push({
                            question: typeof q === 'string' ? q : (q.question || q.text || ''),
                            answer: answer
                        });
                    }
                });

                _fetch(API_BASE + '/' + self.solutionId + '/clarify-answers', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        problem_statement: self.problemStatement,
                        answers: answers
                    })
                }).then(function (data) {
                    self.enrichedBrief = data.enriched_brief || '';
                    self.clarifyPhase = 'enriched';
                    self.copilotMessage = 'Brief enriched. Review it below, then proceed to capabilities.';

                    // Persist selected entity IDs to junction tables (fire and forget)
                    const selectedEntities = {applications:[], archimate_elements:[], capabilities:[], vendors:[]};
                    self.clarifyQuestions.forEach(function (q, idx) {
                        let ps = self.pickerState[idx];
                        if (ps && ps.selected) {
                            ps.selected.forEach(function (e) {
                                if (e.id && selectedEntities[ps.entityType] !== undefined) {
                                    selectedEntities[ps.entityType].push(e.id);
                                }
                            });
                        }
                    });
                    const hasEntities = Object.keys(selectedEntities).some(function (k) { return selectedEntities[k].length > 0; });
                    if (hasEntities) {
                        _fetch(API_BASE + '/' + self.solutionId + '/link-clarify-entities', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({entities: selectedEntities})
                        }).catch(function (e) {
                            Platform.toast.error('Some selected items could not be linked to this solution.');
                        });
                    }
                }).catch(function (e) {
                    self.error = 'Failed to enrich brief: ' + (e.message || 'Unknown error');
                    self.copilotMessage = 'Enrichment failed. You can retry or proceed with the original statement.';
                }).then(function () {
                    self.loading = false;
                });
            },

            skipClarification: function () {
                this.enrichedBrief = this.problemStatement;
                this.clarifyPhase = 'enriched';
                this.copilotMessage = 'Using original statement. Starting architecture pipeline...';
                // Auto-advance to Step 2 and run full pipeline
                this.nextStep();
            },

            // ── Step 2: Capabilities ────────────────────────────────────

            deriveCapabilities: function () {
                let self = this;
                if (self.capabilitiesLoading) return;

                self.capabilitiesLoading = true;
                self.error = null;
                self.copilotMessage = 'Deriving capabilities from your enriched brief...';

                _fetch(API_BASE + '/' + self.solutionId + '/derive-capabilities', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        enriched_brief: self.enrichedBrief || self.problemStatement,
                        structured_context: {
                            business_domain: self.structuredIntake.business_domain,
                            compliance_frameworks: self.structuredIntake.compliance_frameworks,
                            tech_constraints: self.structuredIntake.tech_constraints,
                            nfrs: self.structuredIntake.nfrs,
                            in_scope_apps: self.structuredIntake.in_scope_apps.map(function(a) { return a.id; })
                        }
                    })
                }).then(function (data) {
                    self.capabilities = (data.capabilities || []).map(function (cap) {
                        cap._accepted = true;
                        return cap;
                    });
                    // Auto-accept all initially
                    self.acceptedCapabilities = self.capabilities.filter(function (c) { return c._accepted; });
                    self.computeAcmCoverage();
                    self.copilotMessage = self.capabilities.length + ' capabilities derived. Review the three-track hierarchy for each.';
                }).catch(function (e) {
                    self.error = 'Failed to derive capabilities: ' + (e.message || 'Unknown error');
                    self.copilotMessage = 'Capability derivation failed. Check your API configuration and retry.';
                }).then(function () {
                    self.capabilitiesLoading = false;
                });
            },

            toggleCapability: function (idx) {
                let cap = this.capabilities[idx];
                cap._accepted = !cap._accepted;
                this.acceptedCapabilities = this.capabilities.filter(function (c) { return c._accepted; });
                this.computeAcmCoverage();
            },

            toggleCapabilityExpand: function (idx) {
                const current = this.expandedCapabilities[idx] || false;
                this.expandedCapabilities[idx] = !current;

                // Load details if expanding and not yet loaded
                if (!current && !this.capabilityDetails[idx]) {
                    this.loadCapabilityDetails(idx);
                }
            },

            isCapabilityExpanded: function (idx) {
                return this.expandedCapabilities[idx] || false;
            },

            loadCapabilityDetails: function (idx) {
                let self = this;
                let cap = self.capabilities[idx];
                if (!cap) return;

                const capId = cap.id || cap.existing_id || idx;
                let name = encodeURIComponent(cap.name || '');
                let domain = encodeURIComponent(cap.domain || '');

                _fetch(API_BASE + '/' + self.solutionId + '/capability-details/' + capId + '?name=' + name + '&domain=' + domain)
                    .then(function (data) {
                        self.capabilityDetails[idx] = data;
                    })
                    .catch(function (e) {
                        self.capabilityDetails[idx] = { error: e.message || 'Failed to load' };
                    });
            },

            getMatchBadgeClass: function (matchType) {
                switch (matchType) {
                    case 'exact':
                    case 'existing':
                        return 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30';
                    case 'partial':
                    case 'similar':
                        return 'bg-amber-500/10 text-amber-600 border border-amber-500/30';
                    case 'novel':
                    case 'new':
                        return 'bg-blue-500/10 text-primary border border-blue-500/30';
                    default:
                        return 'bg-zinc-500/10 text-zinc-600 border border-zinc-500/30';
                }
            },

            getMatchLabel: function (matchType) {
                switch (matchType) {
                    case 'exact':
                    case 'existing': return 'Existing';
                    case 'partial':
                    case 'similar': return 'Similar';
                    case 'novel':
                    case 'new': return 'Novel';
                    default: return matchType || 'Unknown';
                }
            },

            // ── Step 3: Architecture ────────────────────────────────────

            generateArchitecture: function () {
                let self = this;
                if (self.architectureLoading) return;

                self.architectureLoading = true;
                self.error = null;
                const capCount = self.acceptedCapabilities.length;
                self.copilotMessage = 'Starting architecture generation for ' + capCount + ' capabilities...';

                const progressMsgs = [
                    'Sending all ' + capCount + ' capabilities to architecture engine...',
                    'Generating motivation, strategy, and business layers...',
                    'Generating application and technology layers...',
                    'Generating implementation layer and cross-capability relationships...',
                    'Persisting elements to architecture model...',
                    'Running inference engine to fill chain gaps...',
                    'Validating completeness across all 7 layers...',
                    'Still working — this can take 3-8 minutes for large capability sets...',
                    'Almost there — finalising layer relationships...',
                ];

                let progressIdx = 0;
                const progressTimer = setInterval(function () {
                    if (progressIdx < progressMsgs.length) {
                        self.copilotMessage = progressMsgs[progressIdx];
                        progressIdx++;
                    }
                }, 20000); // 20s per message — generation takes 3-8 min

                let pollTimer = null;

                function _onResult(result) {
                    clearInterval(progressTimer);
                    clearInterval(pollTimer);
                    self.architectureResult = result;
                    let byLayer = result.elements_by_layer || {};
                    let totalEls = 0;
                    Object.keys(byLayer).forEach(function (layer) {
                        (byLayer[layer] || []).forEach(function (el) {
                            self.acceptedElements[el.id || el.name] = true;
                            totalEls++;
                        });
                    });
                    const layerCount = Object.keys(byLayer).filter(function(l) { return (byLayer[l] || []).length > 0; }).length;
                    self.llmDegraded.step3 = (totalEls === 0);
                    self.copilotMessage = totalEls + ' elements generated across ' + layerCount + ' layers. Inferring component specs...';
                    self.architectureLoading = false;
                    self._saveState();
                    self._onArchitectureGenerated();
                }

                function _onError(msg) {
                    clearInterval(progressTimer);
                    clearInterval(pollTimer);
                    self.llmDegraded.step3 = true;
                    self.error = 'Failed to generate architecture: ' + msg;
                    self.copilotMessage = 'Architecture generation failed. Check API configuration and retry.';
                    self.architectureLoading = false;
                }

                function _poll() {
                    _fetch(API_BASE + '/' + self.solutionId + '/generate-architecture/status', {
                        method: 'GET',
                    }).then(function (data) {
                        let status = data.status || (data.data && data.data.status);
                        let payload = data.result || (data.data && data.data.result);
                        if (status === 'done' && payload) {
                            _onResult(payload);
                        } else if (status === 'failed') {
                            _onError((data.data && data.data.error) || data.error || 'Generation failed');
                        }
                        // status === 'running' or 'idle': keep polling
                    }).catch(function () {
                        // Network error during poll — keep trying
                    });
                }

                // POST to start the job (returns immediately with status=running)
                _fetch(API_BASE + '/' + self.solutionId + '/generate-architecture', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        capabilities: self.acceptedCapabilities.map(function (c) {
                            return {
                                id: c.id || c.existing_id,
                                name: c.name,
                                description: c.description || '',
                                match_type: c.match_type
                            };
                        }),
                        problem_summary: self.enrichedBrief || self.problemStatement,
                        structured_context: {
                            business_domain: self.structuredIntake.business_domain,
                            timeline_months: self.structuredIntake.timeline_months,
                            budget_min: self.structuredIntake.budget_min,
                            budget_max: self.structuredIntake.budget_max,
                            organization_size: self.structuredIntake.organization_size,
                            geographic_scope: self.structuredIntake.geographic_scope,
                            compliance_frameworks: self.structuredIntake.compliance_frameworks,
                            nfrs: self.structuredIntake.nfrs,
                            in_scope_apps: self.structuredIntake.in_scope_apps.map(function(a) { return a.id; })
                        }
                    })
                }).then(function () {
                    // Job started — begin polling every 8s
                    pollTimer = setInterval(_poll, 8000);
                    _poll(); // immediate first check
                }).catch(function (e) {
                    _onError(e.message || 'Unknown error');
                });
            },

            rebuildRelationships: function () {
                let self = this;
                if (self.rebuildingRelationships) return;
                self.rebuildingRelationships = true;
                self.copilotMessage = 'Re-running relationship generation pass...';
                _fetch(API_BASE + '/' + self.solutionId + '/rebuild-relationships', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                }).then(function (data) {
                    const newCount = data.new_relationships || 0;
                    const totalCount = data.total_relationships || 0;
                    self.copilotMessage = 'Rebuilt relationships: ' + newCount + ' new, ' + totalCount + ' total. Reloading diagram...';
                    // Reload the architecture data so the diagram reflects new relationships
                    return _fetch(API_BASE + '/' + self.solutionId + '/load-state');
                }).then(function (state) {
                    if (state && state.architecture) {
                        const _layers = state.architecture;
                        const _rels = state.relationships || [];
                        if (self.architectureResult) {
                            // Preserve existing elements_by_layer structure, just refresh relationships
                            self.architectureResult.relationships = _rels;
                        } else {
                            self.architectureResult = { elements_by_layer: _layers, relationships: _rels };
                        }
                        self.copilotMessage = 'Relationships updated. ' + _rels.length + ' relationships loaded.';
                    }
                }).catch(function (e) {
                    self.copilotMessage = 'Rebuild failed: ' + (e.message || 'Unknown error');
                }).then(function () {
                    self.rebuildingRelationships = false;
                });
            },

            toggleLayer: function (layer) {
                this.expandedLayers[layer] = !this.expandedLayers[layer];
            },

            regenerateLayer: function (layer) {
                let self = this;
                if (self.architectureLoading) return;
                self.architectureLoading = true;
                self.copilotMessage = 'Regenerating ' + layer + ' layer...';

                _fetch(API_BASE + '/' + self.solutionId + '/regenerate-layer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        layer: layer,
                        problem_summary: self.enrichedBrief || self.problemStatement,
                        capabilities: self.acceptedCapabilities.map(function (c) {
                            return { name: c.name, description: c.description || '' };
                        })
                    })
                }).then(function (data) {
                    if (data.elements && data.elements.length > 0) {
                        // Merge new elements into architectureResult
                        if (!self.architectureResult.elements_by_layer[layer]) {
                            self.architectureResult.elements_by_layer[layer] = [];
                        }
                        data.elements.forEach(function (el) {
                            self.architectureResult.elements_by_layer[layer].push(el);
                            self.acceptedElements[el.id || el.name] = true;
                        });
                        self.copilotMessage = data.new_elements + ' ' + layer + ' elements added.';
                    } else {
                        self.copilotMessage = 'No additional ' + layer + ' elements could be generated.';
                    }
                    self._saveState();
                }).catch(function (e) {
                    self.copilotMessage = 'Failed to regenerate ' + layer + ' layer.';
                }).then(function () {
                    self.architectureLoading = false;
                });
            },

            toggleElement: function (elId) {
                // undefined means implicitly accepted; first click must reject
                this.acceptedElements[elId] = this.isElementAccepted(elId) ? false : true;
            },

            isElementAccepted: function (elId) {
                return this.acceptedElements[elId] !== false;
            },

            getElementsByLayer: function (layer) {
                if (!this.architectureResult || !this.architectureResult.elements_by_layer) return [];
                return this.architectureResult.elements_by_layer[layer] || [];
            },

            acceptAllInLayer: function (layer) {
                let self = this;
                let els = self.getElementsByLayer(layer);
                els.forEach(function (el) { self.acceptedElements[el.id || el.name] = true; });
            },

            rejectAllInLayer: function (layer) {
                let self = this;
                let els = self.getElementsByLayer(layer);
                let ids = els.map(function (el) { return el.id || el.name; });
                ids.forEach(function (id) { self.acceptedElements[id] = false; });
                // Persist to backend
                const proposalIds = els.map(function (el) { return el.id; }).filter(Boolean);
                if (proposalIds.length > 0) {
                    _fetch(API_BASE + '/' + self.solutionId + '/proposals/batch-reject', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ proposal_ids: proposalIds })
                    }).catch(function (e) {
                        Platform.toast.error('Rejecting these elements did not save — please retry.');
                    });
                }
            },

            acceptAllElements: function () {
                if (!this.architectureResult) return;
                let self = this;
                let allIds = [];
                Object.keys(self.architectureResult.elements_by_layer).forEach(function (layer) {
                    self.getElementsByLayer(layer).forEach(function (el) {
                        self.acceptedElements[el.id || el.name] = true;
                        if (el.id) allIds.push(el.id);
                    });
                });
                if (allIds.length > 0) {
                    _fetch(API_BASE + '/' + self.solutionId + '/proposals/batch-accept', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ proposal_ids: allIds })
                    }).catch(function (e) {
                        Platform.toast.error('Accepting these elements did not save — please retry.');
                    });
                }
            },

            rejectAllElements: function () {
                if (!this.architectureResult) return;
                let self = this;
                let allIds = [];
                Object.keys(self.architectureResult.elements_by_layer).forEach(function (layer) {
                    self.getElementsByLayer(layer).forEach(function (el) {
                        const id = el.id || el.name;
                        self.acceptedElements[id] = false;
                        if (el.id) allIds.push(el.id);
                    });
                });
                if (allIds.length > 0) {
                    _fetch(API_BASE + '/' + self.solutionId + '/proposals/batch-reject', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ proposal_ids: allIds })
                    }).catch(function (e) {
                        Platform.toast.error('Rejecting these elements did not save — please retry.');
                    });
                }
            },

            getSourceBadgeClass: function (source) {
                switch (source) {
                    case 'existing':
                    case 'catalog':
                        return 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30';
                    case 'derived':
                    case 'ai':
                        return 'bg-purple-500/10 text-primary border border-purple-500/30';
                    case 'inferred':
                        return 'bg-blue-500/10 text-primary border border-blue-500/30';
                    default:
                        return 'bg-zinc-500/10 text-zinc-600 border border-zinc-500/30';
                }
            },

            getTypeBadgeClass: function (type) {
                // ArchiMate element type coloring
                if (!type) return 'bg-zinc-500/10 text-zinc-600 border border-zinc-500/30';
                const t = type.toLowerCase();
                if (t.indexOf('process') !== -1) return 'bg-amber-500/10 text-amber-600 border border-amber-500/30';
                if (t.indexOf('service') !== -1) return 'bg-blue-500/10 text-primary border border-blue-500/30';
                if (t.indexOf('component') !== -1) return 'bg-indigo-500/10 text-primary border border-indigo-500/30';
                if (t.indexOf('node') !== -1 || t.indexOf('device') !== -1) return 'bg-cyan-500/10 text-cyan-600 border border-cyan-500/30';
                if (t.indexOf('role') !== -1 || t.indexOf('actor') !== -1) return 'bg-purple-500/10 text-primary border border-purple-500/30';
                if (t.indexOf('data') !== -1 || t.indexOf('object') !== -1) return 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30';
                return 'bg-zinc-500/10 text-zinc-600 border border-zinc-500/30';
            },

            // ── ACM Domain Methods ────────────────────────────────────────

            _domainNames: {
                UX: 'User Experience', APP: 'Application Services',
                DATA: 'Data & Storage', SEC: 'Security & Identity',
                DEV: 'DevOps & Platform', AI: 'AI & Analytics',
                COM: 'Communication & Integration'
            },

            _domainBadgeClasses: {
                UX: 'bg-pink-500/10 text-pink-600 border border-pink-500/30',
                APP: 'bg-blue-500/10 text-primary border border-blue-500/30',
                DATA: 'bg-emerald-500/10 text-emerald-600 border border-emerald-500/30',
                SEC: 'bg-red-500/10 text-destructive border border-red-500/30',
                DEV: 'bg-cyan-500/10 text-cyan-600 border border-cyan-500/30',
                AI: 'bg-purple-500/10 text-primary border border-purple-500/30',
                COM: 'bg-amber-500/10 text-amber-600 border border-amber-500/30'
            },

            getDomainName: function (code) { return this._domainNames[code] || code; },
            getDomainBadgeClass: function (code) { return this._domainBadgeClasses[code] || 'bg-zinc-500/10 text-zinc-600'; },
            getDomainStatus: function (code) {
                let d = this.domainsData[code];
                return d && d.spec ? d.spec.status : 'pending';
            },
            getDomainTier: function (code) {
                let d = this.domainsData[code];
                return d && d.spec ? d.spec.relevance_tier : (d ? d.suggested_tier : 'standard');
            },
            getDomainTierReason: function (code) {
                let d = this.domainsData[code];
                return d ? d.tier_reason : '';
            },
            getDomainElements: function (code) {
                let d = this.domainsData[code];
                return d ? (d.elements || []) : [];
            },
            getDomainElementCount: function (code) {
                return this.getDomainElements(code).length;
            },
            isDomainExpanded: function (code) { return this.expandedDomains[code] || false; },
            toggleDomainExpand: function (code) { this.expandedDomains[code] = !this.expandedDomains[code]; },

            populateDomains: function () {
                let self = this;
                if (self.domainsLoading) return;
                self.domainsLoading = true;
                self.error = null;
                self.copilotMessage = 'Analyzing your brief across all 7 ACM domains...';

                _fetch(API_BASE + '/' + self.solutionId + '/populate-domains', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        enriched_brief: self.enrichedBrief || self.problemStatement,
                        industry_overlay: self._detectIndustryOverlay()
                    })
                }).then(function (data) {
                    self.domainsData = data.domains || {};
                    self.domainsPopulated = true;
                    let count = Object.keys(self.domainsData).length;
                    self.copilotMessage = count + ' domains populated. Review each domain, set tiers, and confirm.';
                    self._refreshCompleteness();
                    self._saveState();
                }).catch(function (e) {
                    self.error = 'Domain analysis failed: ' + (e.message || 'Unknown');
                    self.copilotMessage = 'Domain analysis failed. You can retry.';
                }).then(function () { self.domainsLoading = false; });
            },

            acceptDomainElement: function (code, elementId) {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/proposals/' + elementId + '/accept', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function () {
                    let elements = self.getDomainElements(code);
                    for (let i = 0; i < elements.length; i++) {
                        if (elements[i].id === elementId) { elements[i].status = 'accepted'; break; }
                    }
                }).catch(function (e) { self.error = 'Accept failed: ' + (e.message || 'Unknown'); });
            },

            rejectDomainElement: function (code, elementId) {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/proposals/' + elementId + '/reject', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function () {
                    let d = self.domainsData[code];
                    if (d) { d.elements = d.elements.filter(function (el) { return el.id !== elementId; }); }
                }).catch(function (e) { self.error = 'Reject failed: ' + (e.message || 'Unknown'); });
            },

            waiveDomainElement: async function (code, elementId) {
                const reason = await Platform.modal.promptText('Justification for waiving this baseline element:', {
                    title: 'Waive baseline element', multiline: true, confirmLabel: 'Waive'
                });
                if (!reason) return;
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/proposals/' + elementId + '/reject', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function () {
                    let elements = self.getDomainElements(code);
                    for (let i = 0; i < elements.length; i++) {
                        if (elements[i].id === elementId) {
                            elements[i].waived = true;
                            elements[i].waiver_reason = reason;
                            break;
                        }
                    }
                }).catch(function (e) { self.error = 'Waive failed: ' + (e.message || 'Unknown'); });
            },

            generateDomainProperties: function (code) {
                let self = this;
                if (self._domainPropGenerating && self._domainPropGenerating[code]) return;
                if (!self._domainPropGenerating) self._domainPropGenerating = {};
                self._domainPropGenerating[code] = true;
                self.copilotMessage = 'Generating properties for ' + self.getDomainName(code) + '…';

                const problemSummary = (self.enrichedBrief || self.problemStatement || '');
                _fetch(API_BASE + '/' + self.solutionId + '/domain/' + code + '/generate-properties', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ problem_summary: problemSummary })
                }).then(function (data) {
                    self._domainPropGenerating[code] = false;
                    const n = data.updated || 0;
                    if (n === 0) {
                        self.copilotMessage = 'No new properties to suggest for ' + self.getDomainName(code) + '.';
                        return;
                    }
                    // Refresh the domain elements so property scores update
                    let d = self.domainsData[code];
                    if (d) {
                        // Merge llm-sourced property values back into local element state
                        d.elements.forEach(function (el) {
                            if (!el.acm_properties) el.acm_properties = {};
                        });
                    }
                    self.copilotMessage = n + ' elements enriched with AI property suggestions in ' + self.getDomainName(code) + '. Review and edit before confirming.';
                    // Re-fetch the full domain list so property panels reflect new values
                    _fetch(API_BASE + '/' + self.solutionId + '/load-domains')
                        .then(function (fresh) {
                            if (fresh && fresh.domains && fresh.domains[code]) {
                                self.domainsData[code] = fresh.domains[code];
                            }
                        }).catch(function () {
                            // The properties were saved above — this only refreshes the
                            // local view, so a failure here just leaves it stale.
                            self.copilotMessage += ' (Could not refresh the view — reload the page to see the new values.)';
                        });
                }).catch(function (e) {
                    self._domainPropGenerating[code] = false;
                    self.copilotMessage = 'Property generation failed: ' + (e.message || 'Unknown');
                });
            },

            /** Template + heuristic defaults (no LLM). Safe to run before "Suggest properties". */
            applyDefaultDomainProperties: function (code) {
                let self = this;
                if (self._domainDefaultApplying && self._domainDefaultApplying[code]) return;
                if (!self._domainDefaultApplying) self._domainDefaultApplying = {};
                self._domainDefaultApplying[code] = true;
                self.copilotMessage = 'Filling defaults for ' + self.getDomainName(code) + '…';

                _fetch(API_BASE + '/' + self.solutionId + '/domain/' + code + '/apply-default-properties', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                }).then(function (data) {
                    self._domainDefaultApplying[code] = false;
                    const n = data.updated || 0;
                    if (n === 0) {
                        self.copilotMessage = 'No empty property slots to fill in ' + self.getDomainName(code) + '.';
                        return;
                    }
                    self.copilotMessage = n + ' elements updated with template defaults in ' + self.getDomainName(code) + '. Use Suggest properties for AI refinements.';
                    _fetch(API_BASE + '/' + self.solutionId + '/load-domains')
                        .then(function (fresh) {
                            if (fresh && fresh.domains && fresh.domains[code]) {
                                self.domainsData[code] = fresh.domains[code];
                            }
                        }).catch(function () {
                            // The defaults were saved above — this only refreshes the
                            // local view, so a failure here just leaves it stale.
                            self.copilotMessage += ' (Could not refresh the view — reload the page to see the new values.)';
                        });
                }).catch(function (e) {
                    self._domainDefaultApplying[code] = false;
                    self.copilotMessage = 'Fill defaults failed: ' + (e.message || 'Unknown');
                });
            },

            acceptAllInDomain: function (code) {
                let self = this;
                let elements = self.getDomainElements(code);
                let proposedIds = elements.filter(function (el) { return el.status === 'proposed' && !el.waived; })
                    .map(function (el) { return el.id; });
                // If no proposed elements remain, go straight to confirm so the domain locks green.
                if (proposedIds.length === 0) {
                    self.confirmDomain(code);
                    return;
                }

                _fetch(API_BASE + '/' + self.solutionId + '/proposals/batch-accept', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ proposal_ids: proposedIds })
                }).then(function () {
                    elements.forEach(function (el) {
                        if (proposedIds.indexOf(el.id) !== -1) { el.status = 'accepted'; }
                    });
                    self.copilotMessage = proposedIds.length + ' elements accepted in ' + self.getDomainName(code) + '. Confirming…';
                    self.confirmDomain(code);
                }).catch(function (e) { self.error = 'Batch accept failed: ' + (e.message || 'Unknown'); });
            },

            setDomainTier: function (code, tier) {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/domain/' + code + '/status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tier: tier })
                }).then(function (data) {
                    if (self.domainsData[code] && self.domainsData[code].spec) {
                        self.domainsData[code].spec.relevance_tier = tier;
                    }
                }).catch(function (e) { self.error = 'Tier update failed: ' + (e.message || 'Unknown'); });
            },

            confirmDomain: function (code) {
                let self = this;
                self.copilotMessage = 'Confirming ' + self.getDomainName(code) + '...';

                _fetch(API_BASE + '/' + self.solutionId + '/domain/' + code + '/confirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function (data) {
                    if (self.domainsData[code] && self.domainsData[code].spec) {
                        self.domainsData[code].spec.status = 'confirmed';
                    }
                    // Store capability context for display in the domain panel
                    let ctx = data.capability_context || {};
                    if (ctx.total) {
                        if (!self.domainsData[code]) self.domainsData[code] = {};
                        self.domainsData[code].capability_context = ctx;
                    }
                    let promotedMsg = (data.promoted || 0) + ' elements promoted';
                    if (data.derived_elements) promotedMsg += ', ' + data.derived_elements + ' derived';
                    let ctxMsg = ctx.total ? ' | ' + ctx.total + ' technical capabilities' : '';
                    if (ctx.foundational_count) ctxMsg += ' (' + ctx.foundational_count + ' foundational)';
                    self.copilotMessage = self.getDomainName(code) + ' confirmed. ' + promotedMsg + '.' + ctxMsg;
                    self._refreshCompleteness();
                    self._saveState();
                    // Fetch vendor suggestions for capabilities confirmed in this domain
                    if (typeof self.fetchVendorSuggestions === 'function') {
                        let capIds = self.getConfirmedCapabilityIds();
                        if (capIds.length > 0) {
                            self.fetchVendorSuggestions(self.solutionId, capIds);
                        }
                    }
                }).catch(function (e) {
                    // Show blocker message for property coverage failures
                    let msg = e.message || 'Unknown';
                    if (msg.indexOf('coverage') !== -1 || msg.indexOf('Property') !== -1) {
                        self.copilotMessage = msg;
                        self.domainBlockers = self.domainBlockers.filter(function (b) { return b.domain !== code; }).concat([{
                            domain: code,
                            reason: msg,
                            type: 'property_below_threshold'
                        }]);
                        // Refresh completeness so backend can evaluate can_proceed with this domain as threshold-blocked
                        self._refreshCompleteness();
                    } else {
                        self.error = 'Confirm failed: ' + msg;
                    }
                });
            },

            markDomainNA: async function (code) {
                const justification = await Platform.modal.promptText('Justification for marking ' + this.getDomainName(code) + ' as Not Applicable:', {
                    title: 'Mark not applicable', multiline: true
                });
                if (!justification) return;
                let self = this;

                _fetch(API_BASE + '/' + self.solutionId + '/domain/' + code + '/status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ status: 'not_applicable', justification: justification })
                }).then(function (data) {
                    if (self.domainsData[code] && self.domainsData[code].spec) {
                        self.domainsData[code].spec.status = 'not_applicable';
                        self.domainsData[code].spec.status_justification = justification;
                    }
                    self.copilotMessage = self.getDomainName(code) + ' marked N/A.';
                    self._refreshCompleteness();
                    self._saveState();
                }).catch(function (e) { self.error = 'Status update failed: ' + (e.message || 'Unknown'); });
            },

            // ── Element Properties ──────────────────────────────────

            isElementExpanded: function (code, elementId) {
                return this.expandedElement && this.expandedElement.code === code && this.expandedElement.id === elementId;
            },

            toggleElementProperties: function (code, elementId, archimateType) {
                if (this.isElementExpanded(code, elementId)) {
                    this.expandedElement = null;
                    return;
                }
                this.expandedElement = { code: code, id: elementId };
                if (!this.propertyTemplates[archimateType]) {
                    this._loadPropertyTemplates(archimateType);
                }
            },

            _loadPropertyTemplates: function (archimateType) {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/property-templates/' + archimateType + '?tier=differentiating')
                    .then(function (data) {
                        // Replace the entire object to trigger Alpine v3 Proxy reactivity
                        let updated = Object.assign({}, self.propertyTemplates);
                        updated[archimateType] = data.properties || [];
                        self.propertyTemplates = updated;
                    })
                    .catch(function (e) {
                        Platform.toast.error('Could not load property templates for this element.');
                    });
            },

            getElementProperties: function (code, elementId) {
                let elements = this.getDomainElements(code);
                for (let i = 0; i < elements.length; i++) {
                    if (elements[i].id === elementId) {
                        return elements[i].acm_properties || {};
                    }
                }
                return {};
            },

            getPropertyValue: function (code, elementId, key) {
                let props = this.getElementProperties(code, elementId);
                let val = props[key];
                if (val && typeof val === 'object' && val.value !== undefined) return val.value;
                return val || '';
            },

            getPropertySource: function (code, elementId, key) {
                let props = this.getElementProperties(code, elementId);
                let val = props[key];
                if (val && typeof val === 'object') return val.source || '';
                return '';
            },

            _pendingPropSaves: {},

            setPropertyValue: function (code, elementId, key, value) {
                let self = this;
                let elements = self.getDomainElements(code);
                for (let i = 0; i < elements.length; i++) {
                    if (elements[i].id === elementId) {
                        if (!elements[i].acm_properties) elements[i].acm_properties = {};
                        elements[i].acm_properties[key] = { value: value, source: 'user' };
                        break;
                    }
                }
                // Accumulate pending changes per element, then flush after 500ms idle
                if (!self._pendingPropSaves[elementId]) self._pendingPropSaves[elementId] = {};
                self._pendingPropSaves[elementId][key] = value;

                if (self._propSaveTimer) clearTimeout(self._propSaveTimer);
                self._propSaveTimer = setTimeout(function () {
                    let pending = self._pendingPropSaves[elementId] || {};
                    self._pendingPropSaves[elementId] = {};
                    _fetch(API_BASE + '/' + self.solutionId + '/proposals/' + elementId + '/properties', {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ properties: pending })
                    }).then(function () {
                        self._showSaveIndicator();
                    }).catch(function (e) { self.error = 'Property save failed: ' + (e.message || 'Unknown'); });
                }, 500);
            },

            getTemplatesForElement: function (archimateType) {
                return this.propertyTemplates[archimateType] || [];
            },

            isPropertyVisible: function (template, code, elementId) {
                if (!template.conditional_on_key) return true;
                let val = this.getPropertyValue(code, elementId, template.conditional_on_key);
                if (typeof val === 'boolean') return String(val).toLowerCase() === template.conditional_on_value;
                return String(val || '').toLowerCase() === String(template.conditional_on_value || '').toLowerCase();
            },

            getElementPropertyScore: function (code, elementId, archimateType) {
                const templates = this.getTemplatesForElement(archimateType);
                if (!templates || templates.length === 0) return { filled: 0, total: 0, pct: 100 };
                let self = this;
                const visible = templates.filter(function (t) { return self.isPropertyVisible(t, code, elementId); });
                if (visible.length === 0) return { filled: 0, total: 0, pct: 100 };
                let filled = 0;
                visible.forEach(function (t) {
                    let val = self.getPropertyValue(code, elementId, t.property_key);
                    if (val !== null && val !== '' && val !== 'TBD' && val !== undefined) filled++;
                });
                return { filled: filled, total: visible.length, pct: Math.round(filled / visible.length * 100) };
            },

            _refreshCompleteness: function () {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/domain-completeness')
                    .then(function (data) {
                        self.domainCoverage = data.domain_coverage || 0;
                        // Merge backend blockers with client-side threshold blockers:
                        // if a domain has a client-side property_below_threshold entry
                        // (from a 422 on confirm), keep it instead of the backend's
                        // domain_not_confirmed entry for the same domain.
                        const backendBlockers = data.blockers || [];
                        const thresholdDomains = {};
                        self.domainBlockers.forEach(function (b) {
                            if (b.type === 'property_below_threshold') {
                                thresholdDomains[b.domain] = b;
                            }
                        });
                        self.domainBlockers = backendBlockers.map(function (b) {
                            if (b.type === 'domain_not_confirmed' && thresholdDomains[b.domain]) {
                                return thresholdDomains[b.domain];
                            }
                            return b;
                        });
                    })
                    .catch(function (e) {
                        Platform.toast.error('Could not refresh completeness status — try reloading the page.');
                    });
            },

            // ── Step 6: Validate ────────────────────────────────────────

            validateSolution: function () {
                let self = this;
                if (self.validationLoading) return;

                self.validationLoading = true;
                self.error = null;
                self.copilotMessage = 'Validating solution completeness...';

                _fetch(API_BASE + '/' + self.solutionId + '/validate')
                    .then(function (data) {
                        self.validationResult = data;
                        const overall = data.overall || 0;
                        self.copilotMessage = 'Validation complete. Overall completeness: ' + overall + '%.';
                    })
                    .catch(function (e) {
                        self.error = 'Validation failed: ' + (e.message || 'Unknown error');
                        self.copilotMessage = 'Validation failed. Try again.';
                    })
                    .then(function () {
                        self.validationLoading = false;
                    });
            },

            // ── Session Persistence ───────────────────────────────────────

            _saveTimer: null,

            _saveState: function () {
                let self = this;
                if (self._saveTimer) clearTimeout(self._saveTimer);
                self._saveTimer = setTimeout(function () {
                    let state = {
                        schema_version: 2,
                        current_step: self.currentStep,
                        completed_steps: self.completedSteps,
                        enriched_brief: self.enrichedBrief,
                        problem_statement: self.problemStatement,
                        clarify_phase: self.clarifyPhase,
                        domains_populated: self.domainsPopulated,
                        structured_intake: self.structuredIntake,
                        accepted_elements: self.acceptedElements,
                        updated_at: new Date().toISOString(),
                    };
                    _fetch(API_BASE + '/' + self.solutionId + '/save-state', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(state)
                    }).then(function () {
                        self._autosaveFailWarned = false;
                    }).catch(function (e) {
                        // Debounced and called on every state change — toast once until
                        // a save succeeds again, rather than spamming on every keystroke.
                        if (!self._autosaveFailWarned) {
                            self._autosaveFailWarned = true;
                            Platform.toast.error('Your progress could not be auto-saved. Changes may be lost if you leave this page.');
                        }
                    });
                }, 500);
            },

            _restoreState: function () {
                let self = this;
                const inlineEl = document.getElementById('journey-v2-state');
                const stateJson = inlineEl ? inlineEl.value : null;
                if (stateJson && stateJson !== 'null') {
                    try {
                        let state = JSON.parse(stateJson);
                        self._applyState(state);
                    } catch (e) {
                        Platform.toast.error('Could not restore your previous progress on this page.');
                    }
                }
                // Load architecture + proposals from DB
                _fetch(API_BASE + '/' + self.solutionId + '/load-state')
                    .then(function (data) {
                        if (data.journey_state && !stateJson) {
                            self._applyState(data.journey_state);
                        }
                        if (data.enriched_brief) {
                            self.enrichedBrief = self.enrichedBrief || data.enriched_brief;
                        }
                        if (data.proposals && data.proposals.length > 0) {
                            self.proposals = data.proposals;
                        }
                        // Restore architecture from DB if Step 3 was completed
                        if (data.architecture && !self.architectureResult) {
                            let hasElements = false;
                            let layers = data.architecture;
                            Object.keys(layers).forEach(function (layer) {
                                if (layers[layer] && layers[layer].length > 0) hasElements = true;
                            });
                            if (hasElements) {
                                self.architectureResult = {
                                    elements_by_layer: layers,
                                    relationships: data.relationships || []
                                };
                            }
                        }
                        // If restored to step 3+ with domains but still no architecture, load promoted elements
                        if (self.currentStep >= 3 && self.domainsPopulated && !self.architectureResult) {
                            self._loadArchitectureFromPromoted();
                        }
                        // Auto-mark completed steps based on what data exists in DB
                        if (self.architectureResult) {
                            [3].forEach(function(s) {
                                if (self.completedSteps.indexOf(s) === -1) self.completedSteps.push(s);
                            });
                        }
                        if (self.domainsPopulated && self.architectureResult) {
                            [4, 5, 6].forEach(function(s) {
                                if (self.completedSteps.indexOf(s) === -1) self.completedSteps.push(s);
                            });
                        }
                        // Auto-load data for whatever step we're on
                        self._autoLoadStepData(self.currentStep);
                        self.updateCopilot();
                    })
                    .catch(function (e) {
                        self.error = 'Could not restore your previous session. Your work may need to be re-entered. (' + (e.message || 'network error') + ')';
                    });
            },

            _applyState: function (state) {
                if (!state) return;
                if (state.current_step) this.currentStep = state.current_step;
                if (state.completed_steps) this.completedSteps = state.completed_steps;
                if (state.enriched_brief) this.enrichedBrief = state.enriched_brief;
                if (state.problem_statement) this.problemStatement = state.problem_statement;
                if (state.clarify_phase) this.clarifyPhase = state.clarify_phase;
                if (state.accepted_elements) this.acceptedElements = state.accepted_elements;
                if (state.landscape) {
                    this.reasoningLandscape = state.landscape;
                }
                if (state.cost_summary) {
                    this.reasoningCostSummary = state.cost_summary;
                }
                if (state.domains_populated) {
                    this.domainsPopulated = state.domains_populated;
                    // Reload domain elements from DB if domainsData is empty
                    if (Object.keys(this.domainsData).length === 0) {
                        this._loadDomainsFromDB();
                    }
                }
                if (state.structured_intake) {
                    this.structuredIntake = Object.assign(this.structuredIntake, state.structured_intake);
                    this.structuredIntakeSaved = true;
                }
                if (state.codegenCaps) {
                    this.codegenCaps = Object.assign(this.codegenCaps, state.codegenCaps);
                }
            },

            // Derive industry overlay code from structured intake business_domain.
            // Returns null if no domain is set (neutral baselines, no industry overlay).
            _detectIndustryOverlay: function () {
                let domain = (this.structuredIntake.business_domain || '').toLowerCase();
                if (!domain) return null;
                if (domain.match(/insur|financial|bank|asset|wealth|pension/)) return 'insurance';
                if (domain.match(/health|medical|pharma|clinic|hospital/)) return 'healthcare';
                if (domain.match(/retail|commerce|shop|consumer/)) return 'retail';
                if (domain.match(/manufactur|supply.chain|logistics|warehouse/)) return 'manufacturing';
                if (domain.match(/telecom|telco|network|carrier/)) return 'telecom';
                return null;
            },

            _loadDomainsFromDB: function () {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/load-domains')
                    .then(function (data) {
                        self.domainsData = data.domains || {};
                        self._refreshCompleteness();
                    })
                    .catch(function (e) {
                        Platform.toast.error('Could not load domain data — try reloading the page.');
                    });
            },

            _loadArchitectureFromPromoted: function () {
                let self = this;
                self.architectureLoading = true;
                _fetch(API_BASE + '/' + self.solutionId + '/promoted-elements')
                    .then(function (data) {
                        self.architectureResult = data;
                        // Build accepted state and replace entire object for reactivity
                        const accepted = {};
                        let byLayer = data.elements_by_layer || {};
                        Object.keys(byLayer).forEach(function (layer) {
                            (byLayer[layer] || []).forEach(function (el) {
                                accepted[el.id || el.name] = true;
                            });
                        });
                        self.acceptedElements = accepted;
                        let total = data.total || 0;
                        self.copilotMessage = total + ' elements loaded from confirmed domains. Review elements by layer.';
                    })
                    .catch(function (e) {
                        Platform.toast.error('Could not load elements for this solution.');
                    })
                    .then(function () {
                        self.architectureLoading = false;
                    });
            },

            /**
             * Load application landscape for confirmed capabilities.
             * Called when entering Step 3 in any mode (domain or reasoning).
             * Populates reasoningLandscape + reasoningCostSummary.
             */
            _loadLandscape: function () {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/reasoning/map-landscape', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function (data) {
                    self.reasoningLandscape = data.applications || [];
                    if (self.reasoningLandscape.length > 0) {
                        self.copilotMessage = self.reasoningLandscape.length + ' applications serve your capabilities.';
                        // Also fetch cost estimate
                        _fetch(API_BASE + '/' + self.solutionId + '/reasoning/estimate-costs', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' }
                        }).then(function (costData) {
                            self.reasoningCostSummary = costData;
                        }).catch(function () {
                            // reasoningCostSummary stays null, so Step 3 and the Step 6
                            // review render "—" for annual operating cost and 5-year TCO.
                            // That is the right glyph, but it reads as "no cost data on
                            // these applications" rather than "we could not work it out".
                            Platform.toast.error('Cost estimation failed — the cost figures on this step show "—" because they could not be calculated, not because the applications have no cost data.');
                        });
                    }
                }).catch(function (e) {
                    Platform.toast.warning('Portfolio landscape mapping failed — the application landscape on this step is missing because it could not be loaded, not because there are no applications.');
                    // Non-fatal — Step 3 can still show ArchiMate elements without portfolio apps
                });
            },

            // ── Bulk Domain Operations ────────────────────────────────────

            acceptAllBaselinesGlobal: function () {
                let self = this;
                let allIds = [];
                let codes = ['UX', 'APP', 'DATA', 'SEC', 'DEV', 'AI', 'COM'];
                codes.forEach(function (code) {
                    let elements = self.getDomainElements(code);
                    elements.forEach(function (el) {
                        if (el.status === 'proposed' && !el.waived && el.is_baseline) {
                            allIds.push(el.id);
                        }
                    });
                });
                if (allIds.length === 0) {
                    self.copilotMessage = 'All baselines already accepted.';
                    return;
                }
                self.copilotMessage = 'Accepting ' + allIds.length + ' baseline elements across all domains...';
                _fetch(API_BASE + '/' + self.solutionId + '/proposals/batch-accept', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ proposal_ids: allIds })
                }).then(function () {
                    codes.forEach(function (code) {
                        let elements = self.getDomainElements(code);
                        elements.forEach(function (el) {
                            if (allIds.indexOf(el.id) !== -1) { el.status = 'accepted'; }
                        });
                    });
                    self.copilotMessage = allIds.length + ' baselines accepted. Now review LLM-suggested elements per domain.';
                }).catch(function (e) { self.error = 'Bulk accept failed: ' + (e.message || 'Unknown'); });
            },

            // Single-click "Accept & confirm all" — accepts every proposed element across
            // all domains then immediately confirms all domains. Removes the two-step
            // friction (accept → separate confirm click) that left solutions stuck at
            // 0/7 domains with all proposals already accepted.
            acceptAndConfirmAll: function () {
                let self = this;
                let codes = ['UX', 'APP', 'DATA', 'SEC', 'DEV', 'AI', 'COM'];
                let proposedIds = [];
                codes.forEach(function (code) {
                    let elements = self.getDomainElements(code);
                    elements.forEach(function (el) {
                        if (el.status === 'proposed' && !el.waived) {
                            proposedIds.push(el.id);
                        }
                    });
                });

                self.copilotMessage = proposedIds.length > 0
                    ? 'Accepting ' + proposedIds.length + ' elements then confirming all 7 domains...'
                    : 'Confirming all 7 domains...';

                const acceptStep = proposedIds.length > 0
                    ? _fetch(API_BASE + '/' + self.solutionId + '/proposals/batch-accept', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ proposal_ids: proposedIds })
                    }).then(function () {
                        codes.forEach(function (code) {
                            let elements = self.getDomainElements(code);
                            elements.forEach(function (el) {
                                if (proposedIds.indexOf(el.id) !== -1) { el.status = 'accepted'; }
                            });
                        });
                    })
                    : Promise.resolve();

                acceptStep.then(function () {
                    self.confirmAllDomains();
                }).catch(function (e) {
                    self.error = 'Accept step failed: ' + (e.message || 'Unknown');
                });
            },

            confirmAllDomains: function () {
                let self = this;
                let codes = ['UX', 'APP', 'DATA', 'SEC', 'DEV', 'AI', 'COM'];
                let pending = codes.filter(function (c) { return self.getDomainStatus(c) === 'pending'; });
                if (pending.length === 0) return;

                self.copilotMessage = 'Confirming ' + pending.length + ' domains...';
                let confirmed = 0;
                const skipped = [];
                let total = pending.length;
                pending.forEach(function (code) {
                    // First accept all proposed in domain
                    let elements = self.getDomainElements(code);
                    let proposedIds = elements.filter(function (el) { return el.status === 'proposed' && !el.waived; })
                        .map(function (el) { return el.id; });

                    let acceptPromise = proposedIds.length > 0
                        ? _fetch(API_BASE + '/' + self.solutionId + '/proposals/batch-accept', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ proposal_ids: proposedIds })
                        })
                        : Promise.resolve();

                    acceptPromise.then(function () {
                        elements.forEach(function (el) {
                            if (proposedIds.indexOf(el.id) !== -1) { el.status = 'accepted'; }
                        });
                        return _fetch(API_BASE + '/' + self.solutionId + '/domain/' + code + '/confirm', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' }
                        });
                    }).then(function () {
                        if (self.domainsData[code] && self.domainsData[code].spec) {
                            self.domainsData[code].spec.status = 'confirmed';
                        }
                        confirmed++;
                    }).catch(function (e) {
                        // Property coverage block — skip this domain, don't fail the batch
                        let msg = e.message || 'Unknown';
                        skipped.push(self.getDomainName(code) + ': ' + msg);
                        // Track as threshold blocker so canProceed can detect resolved domains
                        if (msg.indexOf('coverage') !== -1 || msg.indexOf('Property') !== -1) {
                            self.domainBlockers = self.domainBlockers.filter(function (b) { return b.domain !== code; }).concat([{
                                domain: code,
                                reason: msg,
                                type: 'property_below_threshold'
                            }]);
                        }
                    }).then(function () {
                        // Final summary after all domains processed
                        if (confirmed + skipped.length === total) {
                            if (skipped.length > 0) {
                                self.copilotMessage = confirmed + ' domains confirmed, ' + skipped.length + ' skipped:\n' + skipped.join('\n');
                                self.error = skipped.join(' | ');
                            } else {
                                self.copilotMessage = 'All ' + confirmed + ' domains confirmed. You can now proceed to Step 3.';
                            }
                            self._refreshCompleteness();
                            self._saveState();
                        }
                    });
                });
            },

            // ── Cross-Domain Chain Check ──────────────────────────────────

            // Advisory only — surfaces an informational copilot message when the backend
            // finds cross-domain dependencies. Nothing depends on this succeeding, so a
            // failure is deliberately silent rather than interrupting element entry.
            checkCrossDomainForElement: function (domain, archimateType, elementName) {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/cross-domain-check', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        domain: domain,
                        archimate_type: archimateType,
                        element_name: elementName
                    })
                }).then(function (data) {
                    const deps = data.dependencies || [];
                    if (deps.length > 0) {
                        const required = deps.filter(function (d) { return d.severity === 'required'; });
                        const recommended = deps.filter(function (d) { return d.severity === 'recommended'; });
                        self.copilotMessage = deps.length + ' cross-domain dependencies found (' + required.length + ' required, ' + recommended.length + ' recommended).';
                    }
                }).catch(function () { /* swallow-ok: advisory copilot hint fired on every element the user types; nothing is written and nothing downstream reads it, so a toast per keystroke would be noise with no action behind it */ });
            },

            // ── Element name lookup (for relationship display) ──────────

            _getElementName: function (elementId) {
                if (!this.architectureResult || !this.architectureResult.elements_by_layer) return '#' + elementId;
                let layers = this.architectureResult.elements_by_layer;
                for (let layer in layers) {
                    for (let i = 0; i < (layers[layer] || []).length; i++) {
                        if (layers[layer][i].id === elementId) return layers[layer][i].name;
                    }
                }
                return '#' + elementId;
            },

            // ── Composer ──────────────────────────────────────────────────

            openInComposer: function () {
                window.location.href = '/archimate/composer?solution_id=' + this.solutionId;
            },

            // ── Auto-load step data ────────────────────────────────────────

            // TRAC-001: fire-and-forget chain health check — sets stepWarnings[step]
            _validateStepChain: function (step) {
                let self = this;
                const sid = this.solutionId;
                if (!sid) return;
                // Use Platform.fetch with silent:true to avoid duplicate toasts (this is a fire-and-forget probe).
                Platform.fetch.post('/architecture-journey/' + sid + '/validate-step/' + step, null, { silent: true })
                .then(function (r) { return r.ok ? r.json() : null; /* swallow-ok: TRAC-001 probe fired automatically on every step transition; a null here adds no warning banner and removes none, so nothing on the step changes, and a toast on each navigation would train the user to ignore toasts */ })
                .then(function (data) {
                    if (data && data.data && data.data.warnings && data.data.warnings.length > 0) {
                        self.stepWarnings[step] = data.data.warnings;
                        self.stepWarningsDismissed[step] = false;
                    }
                })
                .catch(function () { /* swallow-ok: TRAC-001 chain-health probe fires on every step transition and only ever adds an advisory amber banner; it writes nothing, and an error toast on each navigation would train the user to ignore toasts */ });
            },

            _autoLoadStepData: function (step) {
                // Load from DB for both domain-driven and greenfield flows
                const hasData = this.domainsPopulated || this.architectureResult;
                // Step 2: auto-trigger domain analysis — no button click required
                if (step === 2 && !this.domainsPopulated && !this.domainsLoading) {
                    let brief = this.enrichedBrief || this.problemStatement;
                    if (brief && brief.length >= 20) {
                        this.populateDomains();
                    }
                }
                if (step === 4 && hasData && !this.decisionPoints) {
                    this.loadDecisionPoints();
                }
                if (step === 5 && hasData && (!this.roadmapData || !this.roadmapData.phases)) {
                    this.loadRoadmapData();
                }
                if (step === 6 && hasData && !this.arbPackage) {
                    this.loadArbPackage();
                }
                // Steps 8-10: post-deploy data loading
                if (step === 9 && this.ruleSuggestions.length === 0) {
                    this.loadRuleSuggestions();
                }
                if (step === 10 && this.versionHistory.length === 0) {
                    this.loadVersionHistory();
                }
            },

            // ── Step 4: Decision Points ──────────────────────────────────────

            loadDecisionPoints: function () {
                let self = this;
                if (self.decisionsLoading) return;
                self.decisionsLoading = true;
                self.error = null;
                self.copilotMessage = 'Analyzing element properties for architecture decisions...';

                _fetch(API_BASE + '/' + self.solutionId + '/decision-points')
                    .then(function (data) {
                        self.decisionPoints = data.decision_points || [];
                        self.decisionsTotalElements = data.total_elements || 0;
                        if (self.decisionPoints.length > 0) {
                            self.llmDegraded.step4 = false;
                            self.copilotMessage = self.decisionPoints.length + ' decision categories identified. Review build/buy, deployment, and availability choices.';
                        } else {
                            self.llmDegraded.step4 = true;
                            self.copilotMessage = 'No decision properties found. Fill build_or_buy and deployment_model in Step 2 element properties.';
                        }
                        // Trigger integration contract suggestions after decisions load
                        self._onVariantSelected();
                    })
                    .catch(function (e) {
                        self.error = 'Failed to load decisions: ' + (e.message || 'Unknown');
                        self.copilotMessage = 'Decision analysis failed.';
                    })
                    .then(function () { self.decisionsLoading = false; });
            },

            // ── Step 4: LLM Architecture Variants ────────────────────────────

            generateLlmVariants: function () {
                let self = this;
                if (self.llmVariantsLoading) return;
                self.llmVariantsLoading = true;
                self.llmVariants = null;
                self.selectedVariants = {};
                self.error = null;
                self.copilotMessage = 'Generating architecture option variants...';

                let elements = [];
                if (self.architectureResult && self.architectureResult.elements_by_layer) {
                    let layers = self.architectureResult.elements_by_layer;
                    Object.keys(layers).forEach(function (layer) {
                        (layers[layer] || []).forEach(function (el) {
                            elements.push({ id: el.id, name: el.name, type: el.archimate_type || el.element_type, layer: layer });
                        });
                    });
                }

                _fetch(API_BASE + '/' + self.solutionId + '/generate-variants', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        architecture_elements: elements,
                        capabilities: self.acceptedCapabilities.map(function (c) { return { name: c.name, description: c.description || '' }; }),
                        problem_summary: self.enrichedBrief || self.problemStatement,
                        structured_context: {
                            business_domain: self.structuredIntake.business_domain,
                            timeline_months: self.structuredIntake.timeline_months,
                            budget_min: self.structuredIntake.budget_min,
                            budget_max: self.structuredIntake.budget_max,
                            organization_size: self.structuredIntake.organization_size,
                            compliance_frameworks: self.structuredIntake.compliance_frameworks
                        }
                    })
                }).then(function (data) {
                    self.llmVariants = data;
                    const dpCount = (data.decision_points || []).length;
                    self.expandedLayers._variants = true;
                    self.copilotMessage = dpCount + ' architecture decision points generated. Select an option for each.';
                }).catch(function (e) {
                    self.error = 'Failed to generate variants: ' + (e.message || 'Unknown error');
                    self.copilotMessage = 'Variant generation failed. Try again.';
                }).then(function () {
                    self.llmVariantsLoading = false;
                });
            },

            selectVariant: function (dpId, optId, optName, decisionPoints) {
                let self = this;
                if (self.variantSelecting === dpId) return;
                self.variantSelecting = dpId;

                // Find the full decision point for context
                const dp = (decisionPoints || []).find(function (d) { return d.id === dpId; }) || {};
                const opt = (dp.options || []).find(function (o) { return o.id === optId; }) || {};

                _fetch(API_BASE + '/' + self.solutionId + '/select-variant', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        decision_point_id: dpId,
                        option_id: optId,
                        option_name: optName,
                        approach: opt.approach || '',
                        risks: opt.risks || [],
                        affected_elements: dp.affected_elements || []
                    })
                }).then(function () {
                    // Update local selection state reactively
                    let updated = Object.assign({}, self.selectedVariants);
                    updated[dpId] = { option_id: optId, option_name: optName };
                    self.selectedVariants = updated;

                    let total = (self.llmVariants && self.llmVariants.decision_points) ? self.llmVariants.decision_points.length : 0;
                    let selected = Object.keys(self.selectedVariants).length;
                    self.copilotMessage = selected + ' of ' + total + ' decisions made. ' +
                        (selected === total ? 'All done — proceed to roadmap.' : 'Continue selecting options.');
                }).catch(function (e) {
                    self.error = 'Failed to save variant selection: ' + (e.message || 'Unknown error');
                }).then(function () {
                    self.variantSelecting = null;
                });
            },

            // ── Step 5: Roadmap ───────────────────────────────────────────────

            loadRoadmapData: function () {
                let self = this;
                if (self.roadmapLoading) return;
                self.roadmapLoading = true;
                self.error = null;
                self.copilotMessage = 'Building implementation roadmap from architecture elements...';

                _fetch(API_BASE + '/' + self.solutionId + '/roadmap-data')
                    .then(function (data) {
                        self.roadmapData = data;
                        if (data.phases && data.phases.length > 0) {
                            self.copilotMessage = data.total_phases + ' delivery phases planned for ' + data.total_elements + ' elements.';
                        } else {
                            self.copilotMessage = 'No buildable elements found. Ensure Application and Technology elements exist.';
                        }
                        // Trigger deployment spec suggestions after roadmap generation
                        self._onMigrationGenerated();
                    })
                    .catch(function (e) {
                        self.error = 'Failed to build roadmap: ' + (e.message || 'Unknown');
                        self.copilotMessage = 'Roadmap generation failed.';
                    })
                    .then(function () { self.roadmapLoading = false; });
            },

            autoFillRoadmap: function () {
                let self = this;
                if (self.roadmapAutoFilling) return;
                self.roadmapAutoFilling = true;
                self.roadmapAutoFillDone = false;
                self.copilotMessage = 'AI is analysing elements and suggesting Build/Buy and effort estimates...';
                _fetch(API_BASE + '/' + self.solutionId + '/auto-fill-roadmap', { method: 'POST' })
                    .then(function (data) {
                        self.roadmapAutoFillDone = true;
                        let msg = 'Auto-fill complete.';
                        if (data && data.updated !== undefined) {
                            msg = 'Auto-fill complete: ' + data.updated + ' elements updated.';
                        }
                        self.copilotMessage = msg;
                        self.loadRoadmapData();
                    })
                    .catch(function (e) {
                        self.copilotMessage = 'Auto-fill failed: ' + (e.message || 'Unknown');
                    })
                    .then(function () { self.roadmapAutoFilling = false; });
            },

            // ── Step 6: ARB Package ──────────────────────────────────────────

            loadArbPackage: function () {
                let self = this;
                if (self.arbLoading) return;
                self.arbLoading = true;
                self.error = null;
                self.copilotMessage = 'Assembling ARB submission package...';

                _fetch(API_BASE + '/' + self.solutionId + '/arb-package')
                    .then(function (data) {
                        self.arbPackage = data;
                        if (data.ready_for_arb) {
                            self.copilotMessage = 'ARB package ready. ' + data.summary.total_elements + ' elements across ' + data.summary.domain_coverage + ' domains. Submit when ready.';
                        } else {
                            self.copilotMessage = 'Package assembled but not ready for submission. Ensure all domains are confirmed.';
                        }
                    })
                    .catch(function (e) {
                        self.error = 'Failed to build ARB package: ' + (e.message || 'Unknown');
                        self.copilotMessage = 'ARB package assembly failed.';
                    })
                    .then(function () { self.arbLoading = false; });
            },

            submitToArb: function () {
                let self = this;
                if (self.arbSubmitting) return;
                self.arbSubmitting = true;
                self.error = null;
                self.copilotMessage = 'Submitting to Architecture Review Board...';

                _fetch(API_BASE + '/' + self.solutionId + '/submit-arb', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ validation_result: self.arbPackage || {} })
                }).then(function (data) {
                    self.arbSubmitResult = data;
                    self.copilotMessage = 'Submitted to ARB. Review number: ' + (data.review_number || 'unknown') + '. Status: submitted.';
                    // Update governance status in package display
                    if (self.arbPackage && self.arbPackage.solution) {
                        self.arbPackage.solution.governance_status = 'arb_review';
                    }
                }).catch(function (e) {
                    self.error = 'ARB submission failed: ' + (e.message || 'Unknown');
                    self.copilotMessage = 'ARB submission failed. Try again.';
                }).then(function () { self.arbSubmitting = false; });
            },

            // ── Solution Name ─────────────────────────────────────────────────

            saveSolutionName: function () {
                let self = this;
                let name = (self.solutionName || '').trim();
                if (!name || self.solutionNameSaving) return;
                self.solutionNameSaving = true;
                _fetch(API_BASE + '/' + self.solutionId + '/update-name', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: name })
                }).then(function () {
                    self.solutionNameSaving = false;
                }).catch(function (e) {
                    Platform.toast.error('The solution name did not save — please retry.');
                    self.solutionNameSaving = false;
                });
            },

            // ── Genome Marketplace Templates ──────────────────────────────────

            loadGenomeTemplates: function () {
                let self = this;
                self.loadingTemplates = true;
                _fetch('/api/codegen/genome-templates').then(function (data) {
                    self.genomeTemplates = data.templates || [];
                    self.loadingTemplates = false;
                }).catch(function (e) {
                    // Do NOT use console.warn. Surface error via existing error state.
                    self.loadingTemplates = false;
                    // Rethrow to avoid swallowing error.
                    throw e;
                });
            },

            applyGenomeTemplate: function (slug) {
                let self = this;
                self.applyingTemplate = true;
                self.templateApplyResult = '';
                _fetch('/api/codegen/genome-templates/' + slug + '/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ solution_id: self.solutionId })
                }).then(function (data) {
                    self.applyingTemplate = false;
                    if (data.success) {
                        let tpl = (self.genomeTemplates || []).find(function(t) { return t.slug === slug; });
                        self.templateAppliedName = tpl ? tpl.name : slug;
                        self.templateApplied = true;
                        self.templateApplyResult = 'Created ' + data.created_elements + ' elements, ' + data.created_junctions + ' junctions from ' + data.total_modules + ' modules.';
                        // Pre-fill problem statement from template
                        if (data.template && self.genomeTemplates) {
                            tpl = self.genomeTemplates.find(function(t) { return t.slug === slug; });
                            if (tpl && tpl.description) {
                                self.problemStatement = tpl.description;
                            }
                        }
                    } else {
                        self.templateApplyResult = 'Failed: ' + (data.error || 'Unknown error');
                    }
                }).catch(function (e) {
                    self.applyingTemplate = false;
                    self.templateApplyResult = 'Error: ' + e.message;
                });
            },

            // ── Application Capabilities Save ────────────────────────────────

            saveCapabilities: function () {
                let self = this;
                _fetch(API_BASE + '/' + self.solutionId + '/journey-state', {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ codegenCaps: self.codegenCaps })
                }).then(function () {
                    self._saveCapsFailWarned = false;
                }).catch(function (e) {
                    if (!self._saveCapsFailWarned) {
                        self._saveCapsFailWarned = true;
                        Platform.toast.error('Your capability settings did not save — please retry.');
                    }
                });
            },

            // ── Roadmap Property Save (debounced) ────────────────────────────
            // Debounce prevents race conditions when user rapidly changes dropdowns.
            // Each (proposalId, key) pair gets its own timer so concurrent saves to
            // different fields don't interfere with each other.
            _saveRoadmapTimers: {},

            saveRoadmapProp: function (proposalId, key, value) {
                if (!proposalId) return;
                let self = this;
                let timerKey = proposalId + ':' + key;
                if (self._saveRoadmapTimers[timerKey]) {
                    clearTimeout(self._saveRoadmapTimers[timerKey]);
                }
                self._saveRoadmapTimers[timerKey] = setTimeout(function () {
                    delete self._saveRoadmapTimers[timerKey];
                    let props = {};
                    props[key] = value;
                    _fetch(API_BASE + '/' + self.solutionId + '/proposals/' + proposalId + '/properties', {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ properties: props })
                    }).then(function () {
                        self._saveRoadmapPropFailWarned = false;
                    }).catch(function (e) {
                        // Debounced per-field, can fire often while a user edits several
                        // properties in a row — toast once until a save succeeds again.
                        if (!self._saveRoadmapPropFailWarned) {
                            self._saveRoadmapPropFailWarned = true;
                            Platform.toast.error('This property did not save — please retry.');
                        }
                    });
                }, 400);
            },

            // ── ARB Readiness ────────────────────────────────────────────────

            /**
             * Whether the solution has a committed capability stance for governance checklist purposes.
             * Non-ACM: acceptedCapabilities; reasoning path: reasoningCapabilities; ACM domain path does not
             * render _step2_capabilities.html — use domainsPopulated + domainCoverage instead.
             */
            capabilitiesConfirmedForReadiness: function () {
                return this.acceptedCapabilities.length > 0 ||
                    this.reasoningCapabilities.length > 0 ||
                    (this.domainsPopulated && this.domainCoverage >= 1);
            },

            readinessItems: function () {
                let self = this;
                const archElemCount = self.architectureResult
                    ? (self.architectureResult.total ||
                       Object.keys(self.architectureResult.elements_by_layer || {}).reduce(function (s, k) {
                           return s + (self.architectureResult.elements_by_layer[k] || []).length;
                       }, 0))
                    : 0;
                return [
                    { label: 'Problem statement defined', pass: self.problemStatement.length > 20 },
                    { label: 'Capabilities confirmed', pass: self.capabilitiesConfirmedForReadiness() },
                    { label: 'Architecture elements generated', pass: archElemCount > 0 },
                    { label: 'All 7 domains confirmed', pass: self.domainCoverage === 7 },
                    { label: 'Decision points reviewed', pass: !!(self.decisionPoints && self.decisionPoints.length > 0) },
                    { label: 'Migration roadmap generated', pass: !!(self.roadmapData && self.roadmapData.phases && self.roadmapData.phases.length > 0) }
                ];
            },

            readinessScore: function () {
                let items = this.readinessItems();
                const passed = items.filter(function (i) { return i.pass; }).length;
                return Math.round((passed / items.length) * 100);
            },

            // Returns a human-readable explanation of why Continue is blocked on the current step.
            // Shown inline below the Continue button so users know exactly what to fix.
            blockerMessage: function () {
                if (this.canProceed()) return '';
                switch (this.currentStep) {
                    case 1:
                        return 'Add a problem statement of at least 20 characters to continue.';
                    case 2:
                        if (this.domainsPopulated) {
                            let codes = ['UX', 'APP', 'DATA', 'SEC', 'DEV', 'AI', 'COM'];
                            let self = this;
                            let pending = codes.filter(function (c) {
                                let st = self.getDomainStatus(c);
                                return st !== 'confirmed' && st !== 'not_applicable';
                            });
                            if (pending.length > 2) {
                                return 'Confirm at least 5 of 7 domains before continuing. Pending: ' + pending.join(', ');
                            }
                        }
                        return 'Select at least one capability before continuing.';
                    case 3:
                        return 'Generate your architecture elements before continuing.';
                    case 4:
                        return 'Load decision points before continuing.';
                    case 5:
                        return 'Build the implementation roadmap before continuing.';
                    default:
                        return '';
                }
            },

            // ── Domain Unconfirm ────────────────────────────────────────────────

            unconfirmDomain: async function (code) {
                let self = this;
                if (!(await Platform.modal.confirm('Revert ' + self.getDomainName(code) + ' to pending? Promoted elements will be removed.'))) return;

                self.copilotMessage = 'Reverting ' + self.getDomainName(code) + '...';
                _fetch(API_BASE + '/' + self.solutionId + '/domain/' + code + '/unconfirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function (data) {
                    if (self.domainsData[code] && self.domainsData[code].spec) {
                        self.domainsData[code].spec.status = 'pending';
                    }
                    // Revert element statuses in local state
                    let elements = self.getDomainElements(code);
                    elements.forEach(function (el) {
                        if (el.status === 'promoted') { el.status = 'accepted'; }
                    });
                    self.copilotMessage = self.getDomainName(code) + ' reverted to pending. ' + (data.reverted || 0) + ' elements un-promoted.';
                    self._refreshCompleteness();
                    self._saveState();
                    // Clear architecture result since elements changed
                    self.architectureResult = null;
                }).catch(function (e) {
                    self.error = 'Unconfirm failed: ' + (e.message || 'Unknown');
                });
            },

            // ── Spec Inference Hooks (post-generation triggers) ────────────

            /**
             * After Step 3 (generate-architecture): infer component specs
             * for all ApplicationComponent/ApplicationService elements.
             */
            _pollCritique: function () {
                let self = this;
                let pollCount = 0;
                const maxPolls = 30; // 30 × 8 s = 4 min max
                self.critiqueStatus = 'running';

                function _poll() {
                    _fetch(API_BASE + '/' + self.solutionId + '/generate-architecture/critique', {
                        method: 'GET',
                    }).then(function (data) {
                        let status = data.status || (data.data && data.data.status) || 'idle';
                        const flags = data.flags || (data.data && data.data.flags) || [];
                        self.critiqueStatus = status;
                        if (status === 'done') {
                            self.critiqueFlags = flags;
                        } else if (status === 'running') {
                            if (pollCount < maxPolls) {
                                pollCount++;
                                setTimeout(_poll, 8000);
                            } else {
                                // Poll budget exhausted. Without this the status stays
                                // 'running' and the "Running semantic review..." spinner
                                // never stops — a timeout rendered as work in progress.
                                self.critiqueStatus = 'error';
                                Platform.toast.warning('Semantic review did not finish in time — the architecture is unaffected.');
                            }
                        }
                    }).catch(function () {
                        // The review request failed. Leaving the status at 'running'
                        // would spin the reviewer panel forever, so a failure would be
                        // indistinguishable from work still in progress.
                        self.critiqueStatus = 'error';
                        Platform.toast.warning('Semantic review could not be completed — the architecture itself is unaffected.');
                    });
                }

                // Delay first poll 3 s — critique starts after generation, which just finished
                setTimeout(_poll, 3000);
            },

            _onArchitectureGenerated: function () {
                let self = this;
                self._pollCritique();
                self.specInferenceLoading = true;
                _fetch(API_BASE + '/' + self.solutionId + '/infer-component-specs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                })
                .then(function (data) {
                    if (data && data.results) {
                        self._showSpecReviewCards(data.results, 'component');
                    }
                })
                .catch(function (e) {
                    Platform.toast.error('Could not auto-generate component specs — you can add them manually.');
                })
                .then(function () {
                    self.specInferenceLoading = false;
                });

                // Auto-trigger code generation — no user action required
                if (!self.codegenLoading && !self.codegenResult) {
                    self.generateCode();
                }
            },

            /**
             * After Step 4 (select-variant): suggest integration contracts
             * from ArchiMate relationships between solution elements.
             */
            _onVariantSelected: function () {
                let self = this;
                self.specInferenceLoading = true;
                _fetch(API_BASE + '/' + self.solutionId + '/suggest-integration-contracts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                })
                .then(function (data) {
                    if (data && data.results) {
                        self._showSpecReviewCards(data.results, 'integration');
                    }
                })
                .catch(function (e) {
                    Platform.toast.error('Could not auto-generate integration contracts — you can add them manually.');
                })
                .then(function () {
                    self.specInferenceLoading = false;
                });
            },

            /**
             * After Step 5 (generate-migration): suggest deployment specs
             * from Node/Device/SystemSoftware/TechnologyService elements.
             */
            _onMigrationGenerated: function () {
                let self = this;
                self.specInferenceLoading = true;
                _fetch(API_BASE + '/' + self.solutionId + '/suggest-deployment-specs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                })
                .then(function (data) {
                    if (data && data.results) {
                        self._showSpecReviewCards(data.results, 'deployment');
                    }
                })
                .catch(function (e) {
                    Platform.toast.error('Could not auto-generate deployment specs — you can add them manually.');
                })
                .then(function () {
                    self.specInferenceLoading = false;
                });
            },

            /**
             * Store spec review results by type for display in the wizard UI.
             * @param {Object} results - keyed by element_id, each with {status, field_count?}
             * @param {string} specType - 'component' | 'integration' | 'deployment'
             */
            _showSpecReviewCards: function (results, specType) {
                let self = this;
                if (!self.specInferenceResults) {
                    self.specInferenceResults = {};
                }
                self.specInferenceResults[specType] = results;

                // Count proposed vs failed for copilot message
                let proposed = 0;
                let failed = 0;
                let confirmed = 0;
                Object.keys(results).forEach(function (key) {
                    const r = results[key];
                    if (r.status === 'proposed') proposed++;
                    else if (r.status === 'failed') failed++;
                    else if (r.status === 'already_confirmed') confirmed++;
                });

                const label = specType.charAt(0).toUpperCase() + specType.slice(1);
                if (proposed > 0 || confirmed > 0) {
                    self.copilotMessage = label + ' specs: ' + proposed + ' proposed' +
                        (confirmed > 0 ? ', ' + confirmed + ' already confirmed' : '') +
                        (failed > 0 ? ', ' + failed + ' failed' : '') + '.';
                }
            },

            // ── Property Save Feedback ──────────────────────────────────────

            _showSaveIndicator: function () {
                // Show brief "Saved" flash near the property panel
                let self = this;
                self._propSaveIndicator = true;
                setTimeout(function () { self._propSaveIndicator = null; }, 1500);
            },

            // ── Zero-to-Hero Reasoning Pipeline ─────────────────────────────
            // Spec: docs/2026-03-22-zero-to-hero-journey-spec-v2.md
            // Uses: /architecture-journey/<id>/reasoning/* endpoints
            // Chains: Problem -> Capabilities -> Landscape -> Gaps -> Options -> Blueprint

            reasoningCapabilities: [],
            reasoningLandscape: [],
            reasoningCostSummary: null,
            reasoningGaps: [],
            reasoningOptions: [],
            reasoningRecommendation: null,
            reasoningCompleteness: 0,

            /**
             * Run the reasoning pipeline (graph-based alternative to domain pipeline).
             * Called from Step 1 when user submits their problem statement.
             *
             * This does NOT replace the domain pipeline. The user gets here when
             * has_acm_domains is false OR when explicitly choosing the reasoning path.
             */
            _runReasoningPipeline: function () {
                let self = this;
                if (self._pipelineRunning) { return; }  // idempotency guard — prevent double-submit
                self._pipelineRunning = true;
                let brief = self.enrichedBrief || self.problemStatement;
                if (!brief || brief.length < 20) {
                    self.error = 'Please describe your problem (at least 20 characters).';
                    self._pipelineRunning = false;
                    return;
                }

                // Step 1 -> 2: Discover capabilities from problem text
                self.copilotMessage = 'Searching enterprise capability catalog...';
                self.loading = true;
                const structuredCtx = {
                    business_domain: self.structuredIntake.business_domain,
                    compliance_frameworks: self.structuredIntake.compliance_frameworks,
                    tech_constraints: self.structuredIntake.tech_constraints,
                    nfrs: self.structuredIntake.nfrs,
                    in_scope_apps: self.structuredIntake.in_scope_apps.map(function(a) { return a.id; })
                };
                _fetch(API_BASE + '/' + self.solutionId + '/reasoning/discover-capabilities', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ problem_text: brief, structured_context: structuredCtx })
                }).catch(function (err) {
                    // Reasoning endpoint failed — fall back to LLM-based derive
                    self.copilotMessage = 'Catalog search unavailable. Generating capabilities via AI...';
                    return _fetch(API_BASE + '/' + self.solutionId + '/derive-capabilities', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ enriched_brief: brief, structured_context: structuredCtx })
                    }).then(function (deriveData) {
                        // Reshape derive output to match reasoning format
                        return {
                            capabilities: (deriveData.capabilities || []).map(function (c) {
                                return { capability_id: null, name: c.name, description: c.description || '',
                                         level: 1, strategic_importance: 'high', confidence: c.quality_score || 0.7,
                                         rationale: c.rationale || '' };
                            }),
                            count: (deriveData.capabilities || []).length
                        };
                    });
                }).then(function (data) {
                    self.reasoningCapabilities = data.capabilities || [];
                    // Align with domain-mode variable so templates can read capabilities[]
                    self.capabilities = self.reasoningCapabilities.map(function (c) {
                        c._accepted = true;
                        return c;
                    });
                    self.acceptedCapabilities = self.capabilities.slice();
                    self.copilotMessage = data.count + ' capabilities found in your enterprise catalog. Review and confirm.';
                    self.completedSteps = [1];
                    self.currentStep = 2;

                    // Auto-confirm all discovered capabilities (user can review in Step 2)
                    let ids = self.reasoningCapabilities.map(function (c) { return c.capability_id; });
                    if (ids.length === 0) {
                        // Fallback: reasoning found no catalog matches — use LLM-based derivation
                        self.copilotMessage = 'No catalog matches. Generating capabilities via AI...';
                        return _fetch(API_BASE + '/' + self.solutionId + '/derive-capabilities', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ enriched_brief: self.enrichedBrief || self.problemStatement })
                        }).then(function (deriveData) {
                            const derived = (deriveData.capabilities || []).map(function (cap) {
                                cap._accepted = false;
                                return cap;
                            });
                            self.capabilities = derived;
                            self.acceptedCapabilities = [];
                            self.loading = false;
                            self._pipelineRunning = false;
                            if (derived.length > 0) {
                                self.copilotMessage = derived.length + ' capabilities generated. Review and accept before continuing.';
                            } else {
                                self.copilotMessage = 'No capabilities found. Try refining your problem description.';
                            }
                            // Stay on Step 2 for user review — don't auto-advance
                            return Promise.reject(new Error('No capabilities'));
                        });
                    }

                    return _fetch(API_BASE + '/' + self.solutionId + '/reasoning/confirm-capabilities', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ confirmed_ids: ids })
                    }).catch(function () { return { count: ids.length }; });  // Spec 3: non-fatal, use discovered count
                }).then(function (data) {
                    self.copilotMessage = (data.count || 0) + ' capabilities confirmed. Mapping your application landscape...';
                    self._saveState();
                    // Fetch vendor suggestions from reasoning-confirmed capabilities (fire-and-forget)
                    if (typeof self.fetchVendorSuggestions === 'function') {
                        let capIds = self.reasoningCapabilities.map(function(c) { return c.capability_id; })
                            .filter(function(id) { return !!id; });
                        if (capIds.length > 0) {
                            self.fetchVendorSuggestions(self.solutionId, capIds);
                        }
                    }

                    // Step 2 -> 3: Map landscape
                    self.completedSteps.push(2);
                    self.currentStep = 3;
                    return _fetch(API_BASE + '/' + self.solutionId + '/reasoning/map-landscape', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });
                }).then(function (data) {
                    self.reasoningLandscape = data.applications || [];
                    const decomCount = data.decommissioning_count || 0;
                    const costedCount = data.costed_count || 0;
                    self.copilotMessage = data.count + ' applications serve your capabilities' +
                        (decomCount > 0 ? ' (' + decomCount + ' decommissioning!)' : '') +
                        (costedCount > 0 ? ', ' + costedCount + ' with cost data' : '') +
                        '. Estimating costs...';

                    // Run inference (fire-and-forget, non-blocking)
                    _fetch(API_BASE + '/' + self.solutionId + '/reasoning/run-inference', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    }).catch(function (e) {
                        // This PERSISTS inferred ArchiMate relationships. Swallowed, the
                        // architecture the user reviews on the next step is simply missing
                        // them, with nothing to distinguish that from "none were inferred".
                        Platform.toast.error('Relationship inference failed: '
                            + ((e && e.message) || 'request failed')
                            + '. Inferred relationships are missing from your architecture — re-run this step.');
                    });

                    // Cost estimation — non-fatal: if it fails the pipeline continues,
                    // but it continues with NO cost figures, not with zero cost. Returning
                    // {} here made `|| 0` produce a $0 annual operating cost and a "No cost
                    // data available for linked applications" message — a statement about
                    // the portfolio that the failed request had no right to make.
                    return _fetch(API_BASE + '/' + self.solutionId + '/reasoning/estimate-costs', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    }).catch(function (e) {
                        Platform.toast.error('Cost estimation failed: '
                            + ((e && e.message) || 'request failed')
                            + '. The cost figures on this step show "—" because they could not be calculated, not because these applications have no cost data.');
                        return null;   // Spec 3: cost failure must not abort the pipeline
                    });
                }).then(function (costData) {
                    // null, not {}: the templates test `reasoningCostSummary && ...` and
                    // render an em dash, which is the honest glyph for "not computed".
                    self.reasoningCostSummary = costData || null;
                    const annual = costData ? costData.total_annual_operating : null;
                    const tco = costData ? costData.tco_5_year : null;
                    const coverage = costData ? costData.cost_coverage_pct : null;
                    if (annual > 0) {
                        self.copilotMessage = 'Cost estimate: $' + Math.round(annual).toLocaleString() +
                            '/year operating, $' + Math.round(tco || 0).toLocaleString() +
                            ' 5-year TCO (' + (coverage || 0) + '% coverage). Detecting gaps...';
                    } else if (costData) {
                        self.copilotMessage = 'No cost data available for linked applications. Detecting gaps...';
                    } else {
                        self.copilotMessage = 'Cost estimate unavailable — the calculation failed. Detecting gaps...';
                    }

                    // Step 3 -> 4: Detect gaps
                    self.completedSteps.push(3);
                    self.currentStep = 4;
                    return _fetch(API_BASE + '/' + self.solutionId + '/reasoning/detect-gaps', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });
                }).then(function (data) {
                    self.reasoningGaps = data.gaps || [];
                    // Align with domain-mode variable so step 4 templates can read decisionPoints
                    self.decisionPoints = self.reasoningGaps;
                    self.copilotMessage = data.count + ' gaps detected (' +
                        (data.critical_count || 0) + ' critical, ' +
                        (data.high_count || 0) + ' high). Generating options...';

                    // Step 4 -> 5: Generate options
                    return _fetch(API_BASE + '/' + self.solutionId + '/reasoning/generate-options', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ constraints: {} })
                    });
                }).then(function (data) {
                    self.reasoningOptions = data.options || [];
                    self.completedSteps.push(4);
                    self.currentStep = 5;

                    // Auto-select first option (hybrid if available)
                    const hybrid = self.reasoningOptions.find(function (o) { return o.option_type === 'hybrid'; });
                    let selected = hybrid || self.reasoningOptions[0];
                    if (!selected) {
                        self.copilotMessage = 'No viable options generated. Add constraints and retry.';
                        self.loading = false;
                        self._pipelineRunning = false;
                        return Promise.reject(new Error('No options'));
                    }

                    self.copilotMessage = 'Recommending: ' + selected.title + '. Populating blueprint...';
                    return _fetch(API_BASE + '/' + self.solutionId + '/reasoning/select-recommendation', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ option_id: selected.option_id })
                    });
                }).then(function (data) {
                    self.reasoningRecommendation = data;
                    self.completedSteps.push(5);
                    self.currentStep = 6;

                    // Step 6: Populate blueprint
                    return _fetch(API_BASE + '/' + self.solutionId + '/reasoning/populate-blueprint', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' }
                    });
                }).then(function (data) {
                    self.reasoningCompleteness = data.completeness || 0;
                    self.domainsPopulated = true;
                    self.completedSteps.push(6);
                    self.loading = false;
                    self._pipelineRunning = false;
                    self._saveState();

                    const pct = Math.round(self.reasoningCompleteness * 100);
                    self.copilotMessage = 'Blueprint populated! Completeness: ' + pct +
                        '%. ' + (data.sections_populated || 0) + ' sections filled, ' +
                        (data.gaps_as_risks || 0) + ' gaps registered as risks. ' +
                        'Open the Solution Blueprint to review and refine.';
                }).catch(function (e) {
                    self.loading = false;
                    self._pipelineRunning = false;
                    if (e.message !== 'No capabilities' && e.message !== 'No options') {
                        let stepName = 'step ' + self.currentStep;
                        if (self.currentStep === 2) stepName = 'discover-capabilities';
                        else if (self.currentStep === 3) stepName = 'map-landscape / estimate-costs';
                        else if (self.currentStep === 4) stepName = 'detect-gaps';
                        else if (self.currentStep === 5) stepName = 'generate-options / select-recommendation';
                        else if (self.currentStep === 6) stepName = 'populate-blueprint';
                        self.error = 'Pipeline stopped at "' + stepName + '": ' + (e.message || 'Unknown error');
                        self.copilotMessage = 'Pipeline stopped at "' + stepName + '". You can continue manually from the current step.';
                    }
                });
            },

            // ── Step 8a: Data Loading methods ──

            handleFileSelect: function (event) {
                let file = event.target.files && event.target.files[0];
                if (file) this._setFile(file);
            },

            handleFileDrop: function (event) {
                event.preventDefault();
                let file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
                if (file) this._setFile(file);
            },

            _setFile: function (file) {
                let ext = (file.name.split('.').pop() || '').toLowerCase();
                if (['csv', 'xlsx', 'xls'].indexOf(ext) === -1) {
                    this.uploadError = 'Only CSV and Excel files are supported.';
                    return;
                }
                if (file.size > 10 * 1024 * 1024) {
                    this.uploadError = 'File exceeds 10 MB limit.';
                    return;
                }
                this.uploadFile = file;
                this.uploadFileName = file.name;
                this.uploadFileSize = (file.size / 1024).toFixed(1) + ' KB';
                this.uploadError = null;
            },

            clearFile: function () {
                this.uploadFile = null;
                this.uploadFileName = '';
                this.uploadFileSize = '';
                this.uploadError = null;
            },

            resetDataFlow: function () {
                this.dataPhase = 'upload';
                this.clearFile();
                this.uploadedSheets = [];
                this.sheetMappings = {};
                this.selectedSheet = null;
                this.mappingError = null;
                this.importLoading = false;
                this.importProgress = 0;
                this.importError = null;
                this.importResult = null;
            },

            uploadData: function () {
                let self = this;
                if (!this.uploadFile) return;
                this.uploadLoading = true;
                this.uploadError = null;

                let formData = new FormData();
                formData.append('file', this.uploadFile);

                // raw-fetch-ok: FormData upload with file requires multipart/form-data, which Platform.fetch cannot handle automatically.
                Platform.fetch('/solutions/' + this.solutionId + '/codegen/data/upload', {
                    method: 'POST',
                    body: formData,
                    silent: true // We handle errors inline via uploadError
                }).then(function (data) {
                    self.uploadLoading = false;
                    if (!data.success) {
                        self.uploadError = data.error || 'Upload failed';
                        return;
                    }
                    self.uploadedSheets = data.sheets || [];
                    if (self.uploadedSheets.length > 0) {
                        self.selectedSheet = self.uploadedSheets[0];
                        self.dataPhase = 'mapping';
                        self.autoMapSheet(self.selectedSheet);
                    }
                }).catch(function (e) {
                    self.uploadLoading = false;
                    self.uploadError = e.message || 'Upload failed';
                });
            },

            autoMapSheet: function (sheet) {
                let self = this;
                this.mappingLoading = true;
                this.mappingError = null;

                _fetch('/solutions/' + this.solutionId + '/codegen/data/auto-map', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ headers: sheet.headers, sheet_name: sheet.name })
                }).then(function (data) {
                    self.mappingLoading = false;
                    let mappings = data.mappings || [];
                    mappings.forEach(function (m) {
                        let types = (sheet.column_types || {});
                        m.inferred_type = types[m.source_column] || null;
                    });
                    self.sheetMappings[sheet.name] = mappings;
                }).catch(function (e) {
                    self.mappingLoading = false;
                    self.mappingError = e.message || 'Auto-mapping failed';
                });
            },

            getCurrentMappings: function () {
                if (!this.selectedSheet) return [];
                return this.sheetMappings[this.selectedSheet.name] || [];
            },

            getMappedCount: function () {
                return this.getCurrentMappings().filter(function (m) { return !!m.target_field; }).length;
            },

            getUnmappedCount: function () {
                return this.getCurrentMappings().filter(function (m) { return !m.target_field; }).length;
            },

            getRowCount: function () {
                return this.selectedSheet ? (this.selectedSheet.row_count || 0) : 0;
            },

            addFieldForColumn: function (sheet, mapping) {
                let self = this;
                _fetch('/solutions/' + this.solutionId + '/codegen/data/add-field', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        column_name: mapping.source_column,
                        inferred_type: mapping.inferred_type || 'string'
                    })
                }).then(function () {
                    self.autoMapSheet(sheet);
                    self.copilotMessage = 'Added new field for "' + mapping.source_column + '". Re-mapping columns...';
                }).catch(function (e) {
                    self.mappingError = 'Failed to add field: ' + (e.message || 'Unknown error');
                });
            },

            confirmMapping: function () {
                this.dataPhase = 'importing';
                this.importProgress = 0;
                this.importError = null;
            },

            importData: function () {
                let self = this;
                if (!this.selectedSheet) return;
                this.importLoading = true;
                this.importError = null;
                this.importProgress = 10;

                let mappings = this.getCurrentMappings().filter(function (m) { return !!m.target_field; });

                _fetch('/solutions/' + this.solutionId + '/codegen/data/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        filename: this.uploadFileName,
                        sheet_name: this.selectedSheet.name,
                        mappings: mappings,
                        rows: this.selectedSheet.all_rows || this.selectedSheet.sample_rows || []
                    })
                }).then(function (data) {
                    self.importLoading = false;
                    self.importProgress = 100;
                    self.importResult = data;
                    self.dataPhase = 'done';
                    self.copilotMessage = 'Data imported! ' + (data.rows_imported || 0) + ' rows loaded.';
                }).catch(function (e) {
                    self.importLoading = false;
                    self.importError = e.message || 'Import failed';
                });
            },

            // ── Step 8b: Rules methods ──

            loadRuleSuggestions: function () {
                let self = this;
                self.ruleSuggestionsLoading = true;
                self.ruleSuggestionsError = null;
                _fetch('/solutions/' + this.solutionId + '/codegen/rules/suggest', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function (data) {
                    self.ruleSuggestions = data.suggestions || [];
                    self.ruleSuggestionsLoading = false;
                }).catch(function (e) {
                    // Leave the list empty rather than inventing a suggestion, and
                    // record WHY it is empty so the panel can say so. Previously the
                    // only empty state was the string "Loading suggestions...", so a
                    // hard failure read as a request still in flight, forever.
                    self.ruleSuggestions = [];
                    self.ruleSuggestionsLoading = false;
                    self.ruleSuggestionsError = (e && e.message) || 'request failed';
                    Platform.toast.error('Could not load rule suggestions: '
                        + self.ruleSuggestionsError);
                });
            },

            applyRuleTemplate: function (suggestion) {
                let self = this;
                _fetch('/solutions/' + this.solutionId + '/codegen/rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: suggestion.template_name,
                        rule_definition: suggestion.rule_definition,
                        source: 'template'
                    })
                }).then(function (data) {
                    self.activeRules.push(data);
                    self.ruleSuggestions = self.ruleSuggestions.filter(function (s) {
                        return s.template_id !== suggestion.template_id || s.entity !== suggestion.entity;
                    });
                }).catch(function (e) {
                    self.ruleError = e.message || 'Failed to apply rule';
                });
            },

            parseNlRule: function () {
                let self = this;
                if (!this.nlRuleInput.trim()) return;
                this.nlRuleLoading = true;
                this.ruleError = null;

                _fetch('/solutions/' + this.solutionId + '/codegen/rules/parse-nl', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: this.nlRuleInput })
                }).then(function (data) {
                    self.nlRuleLoading = false;
                    if (data.rule) {
                        self.activeRules.push(data.rule);
                        self.nlRuleInput = '';
                    }
                }).catch(function (e) {
                    self.nlRuleLoading = false;
                    self.ruleError = e.message || 'Failed to parse rule';
                });
            },

            // ── Step 8c: Testing methods ──

            generateScenarios: function () {
                let self = this;
                this.scenariosLoading = true;

                _fetch('/solutions/' + this.solutionId + '/codegen/test/generate-scenarios', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function (data) {
                    self.scenariosLoading = false;
                    self.testScenarios = (data.scenarios || []).map(function (s) {
                        s.verdict = null;
                        s.notes = '';
                        s.diagnosis = null;
                        s.fixing = false;
                        return s;
                    });
                    self.copilotMessage = data.count + ' test scenarios generated. Walk through each and record your verdict.';
                }).catch(function (e) {
                    self.scenariosLoading = false;
                    self.error = e.message || 'Failed to generate scenarios';
                });
            },

            recordVerdict: function (scenario, verdict) {
                let self = this;
                const previousVerdict = scenario.verdict;
                scenario.verdict = verdict;

                _fetch('/solutions/' + this.solutionId + '/codegen/test/record-result', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        scenario_id: scenario.id,
                        verdict: verdict,
                        notes: scenario.notes || '',
                        rule_name: scenario.rule_name || null
                    })
                }).then(function () {
                    let pass = 0, fail = 0, partial = 0;
                    self.testScenarios.forEach(function (s) {
                        if (s.verdict === 'pass') pass++;
                        else if (s.verdict === 'fail') fail++;
                        else if (s.verdict === 'partial') partial++;
                    });
                    self.testSummary = { total: self.testScenarios.length, pass: pass, fail: fail, partial: partial };
                }).catch(function (e) {
                    // The verdict badge was flipped optimistically above. Leaving it
                    // flipped after the write failed is the bug: the user saw the
                    // scenario marked pass/fail and had no way to know the test result
                    // was never recorded. Put it back and say so.
                    scenario.verdict = previousVerdict;
                    Platform.toast.error('Could not record the "' + verdict + '" verdict for this scenario: '
                        + ((e && e.message) || 'request failed') + '. Nothing was saved — try again.');
                });
            },

            autoFix: function (scenario) {
                let self = this;
                scenario.fixing = true;

                _fetch('/solutions/' + this.solutionId + '/codegen/test/auto-fix', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        failure_description: scenario.title + ': ' + (scenario.notes || scenario.expected_outcome),
                        generate_fix: true
                    })
                }).then(function (data) {
                    scenario.fixing = false;
                    scenario.diagnosis = data.diagnosis || null;
                }).catch(function (e) {
                    scenario.fixing = false;
                    scenario.diagnosis = { root_cause: e.message || 'Diagnosis failed', confidence: 0 };
                });
            },

            compareProcess: function () {
                let self = this;
                this.comparisonLoading = true;

                _fetch('/solutions/' + this.solutionId + '/codegen/test/compare-process', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function (data) {
                    self.comparisonLoading = false;
                    self.processComparison = data.comparison || data;
                }).catch(function (e) {
                    self.comparisonLoading = false;
                    self.error = e.message || 'Comparison failed';
                });
            },

            // ── Step 8c: Acceptance Testing ─────────────────────────────────

            runAcceptanceTests: function () {
                let self = this;
                this.acceptanceLoading = true;
                _fetch('/solutions/' + this.solutionId + '/codegen/test/run-acceptance', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                }).then(function (data) {
                    self.acceptanceLoading = false;
                    self.testSummary = data;
                }).catch(function (e) {
                    self.acceptanceLoading = false;
                    self.error = e.message || 'Acceptance tests failed';
                });
            },

            requestAutoFix: function () {
                let self = this;
                this.autoFixLoading = true;
                let failedScenarios = (this.testScenarios || []).filter(function (s) { return s.verdict === 'fail'; });
                _fetch('/solutions/' + this.solutionId + '/codegen/test/auto-fix', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ failures: failedScenarios.map(function (s) { return { scenario_id: s.id, title: s.title, notes: s.notes || '' }; }) })
                }).then(function (data) {
                    self.autoFixLoading = false;
                    self.autoFixResult = data;
                }).catch(function (e) {
                    self.autoFixLoading = false;
                    self.error = e.message || 'Auto-fix failed';
                });
            },

            // ── Step 9: Change Requests + Versioning ────────────────────────

            // Step 9 state
            changeRequestText: '',
            changeRequestProcessing: false,
            changeAnalysis: null,
            applyingChange: false,
            versionHistory: [],
            versionHistoryLoading: false,
            acceptanceLoading: false,
            autoFixLoading: false,
            autoFixResult: null,

            submitChangeRequest: function () {
                let self = this;
                this.changeRequestProcessing = true;
                this.changeAnalysis = null;
                _fetch('/solutions/' + this.solutionId + '/codegen/version/submit-change', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ change_text: this.changeRequestText })
                }).then(function (data) {
                    self.changeRequestProcessing = false;
                    self.changeAnalysis = data;
                }).catch(function (e) {
                    self.changeRequestProcessing = false;
                    self.error = e.message || 'Change request analysis failed';
                });
            },

            applyChangeRequest: function () {
                let self = this;
                this.applyingChange = true;
                _fetch('/solutions/' + this.solutionId + '/codegen/version/apply', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ change_plan: this.changeAnalysis })
                }).then(function (data) {
                    self.applyingChange = false;
                    self.changeAnalysis = null;
                    self.changeRequestText = '';
                    self.loadVersionHistory();
                }).catch(function (e) {
                    self.applyingChange = false;
                    self.error = e.message || 'Apply failed';
                });
            },

            rollbackToVersion: async function (versionNumber) {
                let self = this;
                if (!(await Platform.modal.confirm('Roll back to version ' + versionNumber + '? This will revert all changes made after that version.'))) return;
                _fetch('/solutions/' + this.solutionId + '/codegen/version/rollback', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ target_version: versionNumber })
                }).then(function (data) {
                    self.loadVersionHistory();
                }).catch(function (e) {
                    self.error = e.message || 'Rollback failed';
                });
            },

            loadVersionHistory: function () {
                let self = this;
                this.versionHistoryLoading = true;
                _fetch('/solutions/' + this.solutionId + '/codegen/version/history')
                .then(function (data) {
                    self.versionHistoryLoading = false;
                    self.versionHistory = data.versions || data || [];
                }).catch(function (e) {
                    self.versionHistoryLoading = false;
                });
            },
        });
    };
})(window);
