/**
 * solutions/blueprint.js
 * Alpine.js component for the Solution Architecture Blueprint page.
 *
 * Reads config from window.__BLUEPRINT_CONFIG__:
 *   { solutionId, csrfToken, sectionDefinitions, scores, ... }
 *
 * Template uses: x-data="blueprintPage()"
 */

// F500-062: the four Governance & Compliance controls that have a real editor
// (solutions/partials/_blueprint_governance_editor.html). Required fields
// mirror _register_crud() in solution_sad_routes.py.
const GOVERNANCE_EDITOR_ID = 'bp-governance-editor';
const COMPOSITION_EDITOR_ID = 'bp-composition-editor';
const DELETE_CONFIRMATION_ID = 'bp-delete-confirmation';
const GOVERNANCE_EDITOR_TYPES = {
    governance_exception: { label: 'Governance Exception', required: [['exception_description', 'Exception description']] },
    compliance_mapping:   { label: 'Compliance Mapping',   required: [['framework', 'Framework'], ['control_id', 'Control ID']] },
    change_request:       { label: 'Change Request',       required: [['change_type', 'Change type'], ['title', 'Title']] },
    feasibility_review:   { label: 'Feasibility Review',   required: [['review_type', 'Review type']] }
};

function blueprintPage() {
    const cfg = window.__BLUEPRINT_CONFIG__ || {};

    const base = {
        solutionId: cfg.solutionId,
        csrfToken: cfg.csrfToken || '',
        narratives: {},
        scores: cfg.scores || {},
        // Declared so the shared solution partials (_technology_elements,
        // _phase_a_elements, …) that reference archimateElements don't throw a
        // ReferenceError on the blueprint page. Empty is handled by their guards.
        archimateElements: cfg.archimateElements || [],
        riskImporting: false,
        riskImportResult: null,
        sectionElements: {},
        sectionRelationships: {},
        diagramsLoaded: {},
        diagramErrors: {},
        renderers: {},
        saving: {},
        narrativeSaving: {},   // 'idle' | 'saving' | 'saved' | 'error' per section
        generatingNarrative: {},  // bool per section — AI generation in flight
        activeSection: '',

        // SAD data arrays for reused partials
        sadIntegrationFlows: [],
        sadComposition: [],
        sadRiskSnapshots: [],
        sadQualityAttributes: [],
        sadSLAs: [],
        sadInvestmentPhases: [],
        sadGovernanceExceptions: [],
        sadComplianceMappings: [],
        sadChangeRequests: [],
        sadFeasibilityReviews: [],
        sadBenefitRealizations: [],
        sadOrgImpacts: [],
        sadLessonsLearned: [],
        sadAppElements: [],
        sadTechElements: [],

        // Motivation layer entities (vision_motivation section)
        drivers: [],
        goals: [],
        constraints: [],

        // Entity modal state
        entityType: '',
        editingEntity: null,
        formData: {},
        activeModal: null,
        deleteTarget: null,
        deleteType: '',
        deleteError: '',
        deleteSaving: false,
        deleteRevision: 0,
        pendingDeletes: [],
        completedDeletes: [],
        _deleteHooked: false,
        modalSaving: false,
        entityError: '',
        entityNotice: '',
        entityRevision: 0,
        govPicker: { query: '', results: [], error: '' },
        _govEditorHooked: false,
        compositionPicker: { query: '', results: [], error: '', loading: false, searched: false },
        _compositionEditorHooked: false,
        apiBase: '/solutions/' + (cfg.solutionId || ''),
        
        // Compliance gap analysis state
        complianceGapLoading: false,
        complianceGapLoaded: false,
        complianceGap: null,

        /* ── lifecycle ───────────────────────────────────────────────── */

        init: function () {
            let self = this;
            let defs = cfg.sectionDefinitions || {};

            // Hydrate from server-rendered config
            Object.keys(defs).forEach(function (key) {
                self.narratives[key] = defs[key].narrative || '';
                self.sectionElements[key] = defs[key].elements || [];
                self.diagramsLoaded[key] = false;
                
                // Initialize specLoading to false for all elements
                const elems = defs[key].elements || [];
                elems.forEach(function(elem) {
                    const elemId = elem.element_id || elem.id;
                    if (elemId) {
                        self.specLoading[elemId] = false;
                    }
                });
            });

            // Hydrate SAD arrays from server context
            const sad = cfg.sadData || {};
            self.sadIntegrationFlows = sad.integration_flows || [];
            self.sadComposition = sad.composition || [];
            self.sadRiskSnapshots = sad.risk_snapshots || [];
            self.sadQualityAttributes = sad.quality_attributes || [];
            self.sadSLAs = sad.slas || [];
            self.sadInvestmentPhases = sad.investment_phases || [];
            self.sadGovernanceExceptions = sad.governance_exceptions || [];
            self.sadComplianceMappings = sad.compliance_mappings || [];
            self.sadChangeRequests = sad.change_requests || [];
            self.sadFeasibilityReviews = sad.feasibility_reviews || [];
            self.sadBenefitRealizations = sad.benefit_realizations || [];
            self.sadOrgImpacts = sad.org_impacts || [];
            self.sadLessonsLearned = sad.lessons_learned || [];
            self.sadAppElements = sad.app_elements || [];
            self.sadTechElements = sad.tech_elements || [];

            // Hydrate motivation layer entities from lifecycle data
            const lc = cfg.lifecycleData || {};
            self.drivers = lc.drivers || [];
            self.goals = lc.goals || [];
            self.constraints = lc.constraints || [];

            self._setupScrollObserver();
            self._loadAllSectionElements();

            // Reload a single section's elements after the link picker links/unlinks something
            window.addEventListener('bp-section-reload', function (e) {
                let sectionId = (e.detail || {}).sectionId;
                if (sectionId) {
                    self._reloadSectionElements(sectionId);
                    self._refreshScores();
                }
            });

            // Reload affected section when the AI Copilot writes to the database
            window.addEventListener('bp-agent-wrote', function (e) {
                let entityType = (e.detail || {}).entity_type;
                const sectionMap = {
                    'driver': 'sec-2',
                    'goal': 'sec-2',
                    'constraint': 'sec-2',
                    'requirement': 'sec-3',
                    'risk': 'sec-8',
                    'option': 'sec-5',
                    'application_link': 'sec-3',
                    'vendor_product_link': 'sec-4',
                };
                let sectionId = sectionMap[entityType];
                if (sectionId) {
                    self._reloadSectionElements(sectionId);
                }
                self._refreshScores();
            });
        },

        /* ── narrative auto-save (debounce handled by Alpine @input.debounce) ── */

        autoSave: async function (sectionId) {
            let self = this;
            if (self.saving[sectionId]) return;
            self.saving[sectionId] = true;
            self.narrativeSaving[sectionId] = 'saving';

            try {
                // Platform.fetch will throw on non-ok responses; we rely on that to go to catch.
                // It also automatically injects CSRF and serializes plain objects.
                await Platform.fetch('/solutions/' + self.solutionId + '/api/section-narratives/' + sectionId, {
                    method: 'PUT',
                    body: { narrative: self.narratives[sectionId] },
                    silent: true   // We paint our own inline error state (narrativeSaving[sectionId] = 'error')
                });
                self.narrativeSaving[sectionId] = 'saved';
                self._refreshScores();
                // Clear the "Saved" indicator after 3 seconds
                setTimeout(function () {
                    if (self.narrativeSaving[sectionId] === 'saved') {
                        self.narrativeSaving[sectionId] = 'idle';
                    }
                }, 3000);
            } catch (e) {
                // Platform.fetch already shows a toast unless silent:true; we passed silent:true,
                // so we must surface the failure to the user via our inline state.
                self.narrativeSaving[sectionId] = 'error';
            } finally {
                self.saving[sectionId] = false;
            }
        },

        generateNarrative: async function (sectionId) {
            let self = this;
            if (self.generatingNarrative[sectionId]) return;
            self.generatingNarrative[sectionId] = true;

            try {
                // Platform.fetch returns the parsed response body directly.
                const data = await Platform.fetch('/solutions/' + self.solutionId + '/api/blueprint/' + sectionId + '/generate', {
                    method: 'POST',
                    body: {},
                    // No need to pass CSRF token or Content-Type header; Platform.fetch handles them.
                    silent: false   // Let Platform.fetch show a toast on error.
                });
                if (!data.success && data.error) throw new Error(data.error);
                if (data.narrative) {
                    self.narratives[sectionId] = data.narrative;
                    self.narrativeSaving[sectionId] = 'saved';
                    setTimeout(function () {
                        if (self.narrativeSaving[sectionId] === 'saved') {
                            self.narrativeSaving[sectionId] = 'idle';
                        }
                    }, 3000);
                }
                self._refreshScores();
                if (window.Platform && Platform.toast) {
                    Platform.toast.success('Narrative generated (' + (data.word_count || 0) + ' words)');
                }
            } catch (e) {
                // Platform.fetch already shows a toast (silent:false), but we must still
                // rethrow to ensure the error is not swallowed.
                throw e;
            } finally {
                self.generatingNarrative[sectionId] = false;
            }
        },

        /* ── diagram lazy-loading (called via x-intersect.once) ────── */

        loadDiagram: function (sectionId) {
            let self = this;
            self.diagramsLoaded[sectionId] = true; // mark container ready

            if (typeof joint === 'undefined' || !joint.dia || typeof ComposerRenderer === 'undefined') {
                // JointJS not yet loaded or not fully initialised — retry once after a short delay
                setTimeout(function () { self._renderDiagram(sectionId); }, 800);
                return;
            }
            self._renderDiagram(sectionId);
        },

        _renderDiagram: async function (sectionId) {
            let self = this;

            if (typeof joint === 'undefined' || !joint.dia || typeof ComposerRenderer === 'undefined') return;

            const container = document.getElementById('diagram-' + sectionId);
            if (!container) return;

            // Destroy existing renderer if present
            if (self.renderers[sectionId]) {
                // Best-effort teardown of the previous JointJS instance before
                // replacing it — a throw here (e.g. paper already detached)
                // must not block rendering the new one.
                try { self.renderers[sectionId].destroy(); } catch (e) { /* swallow-ok: teardown of the previous JointJS paper before it is replaced; a throw on an already-detached paper must not stop the new diagram rendering */ }
                delete self.renderers[sectionId];
            }

            self.diagramErrors[sectionId] = false;

            try {
                // Platform.fetch returns the parsed response body directly.
                const data = await Platform.fetch('/solutions/' + self.solutionId + '/api/viewpoint/' + sectionId + '/elements', {
                    silent: true   // We paint our own error state (diagramErrors[sectionId] = true)
                });
                const elements = (data.data && data.data.elements) || [];
                const relationships = (data.data && data.data.relationships) || [];
                self.sectionElements[sectionId] = elements;
                self.sectionRelationships[sectionId] = relationships;
                self._doRenderDiagram(sectionId, container, elements, relationships);
            } catch (e) {
                // Platform.fetch throws on non-ok responses. We must surface the failure
                // via our inline error state.
                self.diagramErrors[sectionId] = true;
                // Rethrow to ensure the error is not swallowed.
                throw e;
            }
        },

        _doRenderDiagram: function (sectionId, container, elements, relationships) {
            let self = this;
            if (!elements.length) return; // empty — template shows the empty state div

            try {
                container.innerHTML = '';
                let renderer = ComposerRenderer.create(container, { mode: 'view', width: '100%', height: 380 });
                renderer.loadElements(elements, relationships || []);
                renderer.fitToContent();
                self.renderers[sectionId] = renderer;

                // AI Copilot: right-click on diagram element opens context menu
                renderer.paper.on('element:contextmenu', function (elementView, evt) {
                    evt.preventDefault();
                    const el = elementView.model;
                    // Find name: prefer JointJS model attr, then SVG nameLabel text, then any non-dash text
                    let elName = el.attr('label/text') || el.attr('.label/text') || el.attr('body/text') || null;
                    if (!elName) {
                        // Look for joint-selector="nameLabel" text element first
                        const rootEl = elementView.el || (evt.target && evt.target.closest('[model-id]'));
                        if (rootEl) {
                            const nameLabelEl = rootEl.querySelector('[joint-selector="nameLabel"]');
                            if (nameLabelEl && nameLabelEl.textContent.trim() && nameLabelEl.textContent.trim() !== '-') {
                                elName = nameLabelEl.textContent.trim();
                            } else {
                                const texts = Array.from(rootEl.querySelectorAll('text'));
                                const candidate = texts.find(function(t) {
                                    const v = t.textContent.trim();
                                    return v && v !== '-';
                                });
                                elName = candidate ? candidate.textContent.trim() : null;
                            }
                        }
                    }
                    if (!elName) { elName = 'Element'; }
                    const elType = el.get('archimateType') || el.attr('body/stereotype') || el.get('type') || 'Unknown';
                    window.dispatchEvent(new CustomEvent('bp-element-context-menu', {
                        detail: {
                            elementId: el.get('archimateId') || el.id,
                            elementName: elName,
                            elementType: elType,
                            sectionId: sectionId,
                            x: evt.clientX,
                            y: evt.clientY,
                        },
                    }));
                });
            } catch (e) {
                self.diagramErrors[sectionId] = true;
            }
        },

        /* ── toolbar actions ─────────────────────────────────────────── */

        openComposer: function (sectionId) {
            let self = this;
            /* The composer reads `solution_id`, not `solution` — and has no
             * mechanism to scroll to a specific blueprint section, so
             * `section` is dropped rather than passed as a param that does
             * nothing. */
            window.open('/archimate/composer?solution_id=' + self.solutionId, '_blank');
        },

        exportPng: function (sectionId) {
            let self = this;
            let renderer = self.renderers[sectionId];
            if (renderer && typeof renderer.exportPng === 'function') {
                renderer.exportPng();
            } else if (window.Platform && Platform.toast) {
                Platform.toast.error('No diagram is available to export for this section.');
            }
        },

        linkElements: function (sectionId) {
            window.dispatchEvent(new CustomEvent('bp-link-open', {
                detail: { sectionId: sectionId }
            }));
        },

        generateFromJourney: async function (sectionId) {
            let self = this;

            try {
                // Platform.fetch returns the parsed response body directly.
                const data = await Platform.fetch('/solutions/' + self.solutionId + '/api/blueprint/' + sectionId + '/generate', {
                    method: 'POST',
                    body: {},
                    silent: false   // Let Platform.fetch show a toast on error.
                });
                if (data.elements && data.elements.length) {
                    self.sectionElements[sectionId] = data.elements;
                }
                if (data.narrative) self.narratives[sectionId] = data.narrative;
                self._refreshScores();
                if (window.Platform && Platform.toast) {
                    Platform.toast.success(data.narrative ? 'Narrative generated (' + (data.word_count || 0) + ' words)' : 'Generation complete');
                }
            } catch (e) {
                // Platform.fetch surfaced this failure before it threw; the
                // catch only stops it becoming an unhandled rejection.
                // Rethrow to ensure the error is not swallowed.
                throw e;
            }
        },

        generateAll: async function () {
            let self = this;
            const desc = ((window.__BLUEPRINT_CONFIG__ || {}).sadData || {}).description ||
                       (window.__BLUEPRINT_CONFIG__ || {}).solutionName || '';

            if (!desc) {
                if (window.Platform && Platform.toast) Platform.toast.error('No problem statement — edit the solution description first');
                return;
            }

            if (window.Platform && Platform.toast) Platform.toast.success('Generating — this takes 20-40 seconds…');

            try {
                const data = await Platform.fetch('/solutions/' + self.solutionId + '/generate-draft', {
                    method: 'POST',
                    body: { problem_statement: desc },
                    silent: false   // Let Platform.fetch show a toast on error.
                });
                if (data.success) {
                    self._refreshScores();
                    if (window.Platform && Platform.toast) Platform.toast.success('Created ' + (data.total || 0) + ' entities — reloading…');
                    setTimeout(function () { window.location.reload(); }, 2000);
                } else {
                    if (window.Platform && Platform.toast) Platform.toast.error(data.error || 'Generation failed');
                }
            } catch (e) {
                // Platform.fetch surfaced this failure before it threw; the
                // catch only stops it becoming an unhandled rejection.
                // Rethrow to ensure the error is not swallowed.
                throw e;
            }
        },

        /* ── entity CRUD (ported from detail-phase-crud.js) ─────────── */

        openEntityModal: function (type, entity) {
            this.entityRevision += 1;
            this.entityType = type;
            this.editingEntity = entity || null;
            this.formData = entity ? Object.assign({}, entity) : this._defaults(type);
            this.entityError = '';
            this.govPicker = { query: '', results: [], error: '' };
            this.compositionPicker = { query: '', results: [], error: '', loading: false, searched: false };
            this.activeModal = type;
            // F500-062: the four Governance & Compliance controls render in the
            // Platform.modal editor (_blueprint_governance_editor.html). Other
            // Composition has its own editor below; other types remain unchanged.
            if (GOVERNANCE_EDITOR_TYPES[type]) {
                if (type === 'feasibility_review') {
                    // <select> models strings; the API returns true/false/null.
                    this.formData.feasible = this.formData.feasible === true ? 'true'
                        : this.formData.feasible === false ? 'false' : '';
                }
                if (window.Platform && Platform.modal) {
                    if (!this._govEditorHooked) {
                        this._govEditorHooked = true;
                        let self = this;
                        // Escape / backdrop / × close through Platform.modal, not closeModal():
                        // reset the shared state so the next click opens clean.
                        Platform.modal.on(GOVERNANCE_EDITOR_ID, 'close', function () {
                            if (self.activeModal) self._resetEntityState();
                        });
                    }
                    Platform.modal.open(GOVERNANCE_EDITOR_ID);
                }
            } else if (type === 'composition' && window.Platform && Platform.modal) {
                if (!this._compositionEditorHooked) {
                    this._compositionEditorHooked = true;
                    const self = this;
                    Platform.modal.on(COMPOSITION_EDITOR_ID, 'close', function () {
                        if (self.activeModal === 'composition') self._resetEntityState();
                    });
                }
                Platform.modal.open(COMPOSITION_EDITOR_ID);
            }
        },

        governanceEditorTitle: function () {
            const def = GOVERNANCE_EDITOR_TYPES[this.entityType];
            if (!def) return '';
            return (this.editingEntity && this.editingEntity.id ? 'Edit ' : 'Add ') + def.label;
        },

        _resetEntityState: function () {
            this.entityRevision += 1;
            this.activeModal = null;
            this.editingEntity = null;
            this.formData = {};
            this.entityError = '';
            this.govPicker = { query: '', results: [], error: '' };
            this.compositionPicker = { query: '', results: [], error: '', loading: false, searched: false };
            this.modalSaving = false;
        },

        closeModal: function () {
            if (this.entityType === 'composition' && window.Platform && Platform.modal &&
                Platform.modal.isOpen(COMPOSITION_EDITOR_ID)) {
                Platform.modal.close(COMPOSITION_EDITOR_ID);
            }
            if (GOVERNANCE_EDITOR_TYPES[this.entityType] && window.Platform && Platform.modal &&
                Platform.modal.isOpen(GOVERNANCE_EDITOR_ID)) {
                Platform.modal.close(GOVERNANCE_EDITOR_ID); // close hook resets state
            }
            this._resetEntityState();
        },

        clearCompositionComponent: function () {
            this.formData.component_id = null;
            this.formData.component_name = '';
            this.entityError = '';
            this.compositionPicker = { query: '', results: [], error: '', loading: false, searched: false };
        },

        searchCompositionComponents: async function () {
            const self = this;
            const picker = self.compositionPicker;
            const q = picker.query.trim();
            const revision = self.entityRevision;
            const type = self.formData.component_type;
            picker.results = [];
            picker.error = '';
            picker.searched = false;
            if (q.length < 2 || !['application', 'archimate_element'].includes(type)) return;
            picker.loading = true;
            const current = function () {
                return revision === self.entityRevision && picker === self.compositionPicker &&
                    picker.query.trim() === q && self.formData.component_type === type;
            };
            try {
                // The DESIGN.md decision-search URL was retired; this is the
                // current ArchiMate search route used by the architecture forms.
                const url = type === 'application'
                    ? '/applications/api/list?search=' + encodeURIComponent(q) + '&limit=10&per_page=10'
                    : '/archimate/api/elements/search?q=' + encodeURIComponent(q) + '&limit=10';
                const data = await Platform.fetch(url, { silent: true });
                const items = type === 'application' ? data.applications
                    : (Array.isArray(data) ? data : data.data);
                if (!Array.isArray(items) || items.some(function (item) {
                    return !item || !Number.isInteger(Number(item.id)) || Number(item.id) <= 0 ||
                        typeof item.name !== 'string' || !item.name.trim();
                })) throw new Error('Component search returned an invalid response.');
                if (!current()) return;
                picker.results = items.map(function (item) { return { id: Number(item.id), name: item.name }; });
                picker.searched = true;
            } catch (err) {
                if (!current()) return;
                picker.error = (err && err.message) || 'Component search failed. Try again.';
            } finally {
                if (current()) picker.loading = false;
            }
        },

        pickCompositionComponent: function (item) {
            this.formData.component_id = item.id;
            this.formData.component_name = item.name;
            this.compositionPicker = { query: '', results: [], error: '', loading: false, searched: false };
            this.entityError = '';
        },

        /* ── governance element picker (principle / mapped element) ── */

        searchGovElements: async function () {
            let self = this;
            const q = (self.govPicker.query || '').trim();
            if (q.length < 2) { self.govPicker.results = []; return; }
            try {
                const typeFilter = self.entityType === 'governance_exception' ? '&type=Principle' : '';
                const data = await Platform.fetch('/archimate/api/elements/search?q=' + encodeURIComponent(q) + '&limit=10' + typeFilter, { silent: true });
                const payload = Array.isArray(data) ? data : (data && (data.data || data.items));
                if (!Array.isArray(payload)) throw new Error('Element search returned an invalid response.');
                self.govPicker.results = payload.map(function (el) {
                    return { id: el.id, name: el.name, element_type: el.element_type || el.type || '' };
                });
                self.govPicker.error = '';
            } catch (e) {
                self.govPicker.results = [];
                self.govPicker.error = (e && e.message) || 'Element search failed';
            }
        },

        pickGovElement: function (el) {
            if (this.entityType === 'governance_exception') {
                this.formData.principle_id = el.id;
                this.formData.principle_name = el.name;
            } else if (this.entityType === 'compliance_mapping') {
                this.formData.archimate_element_id = el.id;
                this.formData.element_name = el.name;
            }
            this.govPicker = { query: '', results: [], error: '' };
        },

        clearGovElement: function () {
            if (this.entityType === 'governance_exception') {
                this.formData.principle_id = null;
                this.formData.principle_name = '';
            } else if (this.entityType === 'compliance_mapping') {
                this.formData.archimate_element_id = null;
                this.formData.element_name = '';
            }
        },

        _governancePayload: function () {
            const body = Object.assign({}, this.formData);
            if (this.entityType === 'feasibility_review') {
                body.feasible = body.feasible === 'true' ? true : body.feasible === 'false' ? false : null;
            }
            ['expiry_date', 'approval_date', 'affected_phase', 'review_phase'].forEach(function (k) {
                if (body[k] === '') body[k] = null;
            });
            return body;
        },

        _defaults: function (type) {
            if (type === 'risk') return { risk_description: '', impact: 'medium', probability: 'medium', mitigation: '', status: 'open', owner: '' };
            if (type === 'metric') return { name: '', unit: '', baseline_value: '', target_value: '', actual_value: '', status: 'not_measured' };
            if (type === 'tco') return { option_label: 'Proposed', cost_category: '', is_recurring: true, year: 1, amount: 0, notes: '' };
            if (type === 'plateau') return { name: '', description: '', order: 0, target_date: '' };
            if (type === 'driver') return { name: '', description: '', driver_type: 'internal', impact_level: 3, urgency: 3, source: '' };
            if (type === 'goal') return { name: '', description: '', priority: 3, measurement_criteria: '' };
            if (type === 'constraint') return { name: '', description: '', constraint_type: 'technical', value: '', severity: 3 };
            if (type === 'requirement') return { name: '', description: '', requirement_type: 'functional', priority: 3, is_mandatory: false, source: '', rationale: '', acceptance_criteria: '' };
            if (type === 'option') return { name: '', option_type: 'build', justification: '', estimated_cost_min: null, estimated_cost_max: null, timeline_months: null, score: null, confidence: null, is_recommended: false, pros: [], cons: [], risks: [] };
            if (type === 'integration_flow') return { flow_name: '', source_app_id: null, target_app_id: null, flow_type: 'sync', protocol: '', criticality: 'medium', notes: '' };
            if (type === 'composition') return { component_type: 'application', component_id: null, component_name: '', role: 'supporting', criticality: 'medium', coupling: 'loosely_coupled', notes: '' };
            if (type === 'risk_snapshot') return { risk_name: '', risk_category: 'technical', adm_phase: '', impact: 3, probability: 3, trend: 'stable', mitigation_status: 'identified', notes: '' };
            if (type === 'quality_attribute') return { attribute_name: '', attribute_type: 'performance', target_value: '', current_value: '', verification_method: '', test_status: 'not_tested', notes: '' };
            if (type === 'sla') return { sla_name: '', availability_target: null, response_time_ms: null, rto_hours: null, rpo_hours: null, support_hours: '', status: 'draft', notes: '' };
            if (type === 'investment_phase') return { phase_name: '', phase_number: 1, authorized_amount: null, currency: 'GBP', funding_source: '', status: 'pending', notes: '' };
            if (type === 'governance_exception') return { exception_description: '', justification: '', principle_name: '', risk_accepted: '', status: 'requested', notes: '' };
            if (type === 'compliance_mapping') return { framework: '', control_id: '', control_description: '', element_name: '', verification_status: 'not_assessed', notes: '' };
            if (type === 'change_request') return { change_type: 'scope', title: '', description: '', justification: '', priority: 'medium', affected_phase: '', notes: '' };
            if (type === 'feasibility_review') return { review_type: 'technical', review_phase: '', feasible: null, confidence_level: 'medium', recommendation: 'proceed', technical_risks: '', notes: '' };
            if (type === 'benefit_realization') return { benefit_name: '', benefit_type: 'operational', metric_name: '', baseline_value: null, target_value: null, status: 'not_started', notes: '' };
            if (type === 'org_impact') return { impact_area: '', description: '', headcount_delta: 0, reskilling_required: false, change_readiness: 'unknown', notes: '' };
            if (type === 'lesson_learned') return { title: '', category: 'technical', adm_phase: '', description: '', root_cause: '', recommendation: '', impact: 'medium', notes: '' };
            if (type === 'principle') return { name: '', description: '', rationale: '', implications: '', priority: 'medium', source: '', notes: '' };
            if (type === 'assessment') return { assessment_type: 'gap', name: '', current_state: '', target_state: '', gap_description: '', severity: 3, notes: '' };
            if (type === 'stakeholder_sad') return { name: '', role: '', organization: '', influence_level: 'medium', interest_level: 'medium', engagement_strategy: '', notes: '' };
            if (type === 'business_element') return { element_type: 'service', name: '', description: '', owner: '', status: 'current', apqc_process_id: null, notes: '' };
            if (type === 'app_element') return { element_type: 'service', name: '', description: '', technology: '', status: 'current', notes: '' };
            if (type === 'tech_element') return { element_type: 'node', name: '', description: '', specification: '', status: 'current', notes: '' };
            return {};
        },

        _apiPath: function (type) {
            if (type === 'risk') return '/risks';
            if (type === 'metric') return '/metrics';
            if (type === 'tco') return '/tco';
            if (type === 'plateau') return '/plateaus';
            if (type === 'driver') return '/drivers';
            if (type === 'goal') return '/goals';
            if (type === 'constraint') return '/constraints';
            if (type === 'requirement') return '/requirements';
            if (type === 'option') return '/options';
            if (type === 'integration_flow') return '/integration-flows';
            if (type === 'composition') return '/composition';
            if (type === 'risk_snapshot') return '/risk-snapshots';
            if (type === 'quality_attribute') return '/quality-attributes';
            if (type === 'sla') return '/slas';
            if (type === 'investment_phase') return '/investment-phases';
            if (type === 'governance_exception') return '/governance-exceptions';
            if (type === 'compliance_mapping') return '/compliance-mappings';
            if (type === 'change_request') return '/change-requests';
            if (type === 'feasibility_review') return '/feasibility-reviews';
            if (type === 'benefit_realization') return '/benefit-realizations';
            if (type === 'org_impact') return '/org-impacts';
            if (type === 'lesson_learned') return '/lessons-learned';
            if (type === 'principle') return '/principles';
            if (type === 'assessment') return '/assessments';
            if (type === 'stakeholder_sad') return '/stakeholders-sad';
            if (type === 'business_element') return '/business-elements';
            if (type === 'app_element') return '/app-elements';
            if (type === 'tech_element') return '/tech-elements';
            return '';
        },

        _listKey: function (type) {
            if (type === 'risk') return 'risks';
            if (type === 'metric') return 'metrics';
            if (type === 'tco') return 'tcoItems';
            if (type === 'plateau') return 'plateaus';
            if (type === 'driver') return 'drivers';
            if (type === 'goal') return 'goals';
            if (type === 'constraint') return 'constraints';
            if (type === 'requirement') return 'requirements';
            if (type === 'option') return 'recommendations';
            if (type === 'integration_flow') return 'sadIntegrationFlows';
            if (type === 'composition') return 'sadComposition';
            if (type === 'risk_snapshot') return 'sadRiskSnapshots';
            if (type === 'quality_attribute') return 'sadQualityAttributes';
            if (type === 'sla') return 'sadSLAs';
            if (type === 'investment_phase') return 'sadInvestmentPhases';
            if (type === 'governance_exception') return 'sadGovernanceExceptions';
            if (type === 'compliance_mapping') return 'sadComplianceMappings';
            if (type === 'change_request') return 'sadChangeRequests';
            if (type === 'feasibility_review') return 'sadFeasibilityReviews';
            if (type === 'benefit_realization') return 'sadBenefitRealizations';
            if (type === 'org_impact') return 'sadOrgImpacts';
            if (type === 'lesson_learned') return 'sadLessonsLearned';
            if (type === 'principle') return 'sadPrinciples';
            if (type === 'assessment') return 'sadAssessments';
            if (type === 'stakeholder_sad') return 'sadStakeholdersSad';
            if (type === 'business_element') return 'sadBusinessElements';
            if (type === 'app_element') return 'sadAppElements';
            if (type === 'tech_element') return 'sadTechElements';
            return '';
        },

        submitEntity: async function () {
            let self = this;
            let type = self.entityType;
            const govDef = GOVERNANCE_EDITOR_TYPES[type];
            const hasEditor = !!govDef || type === 'composition';
            const revision = self.entityRevision;
            if (self.modalSaving) return;
            if (type === 'composition') {
                if (!Number.isInteger(Number(self.formData.component_id)) || Number(self.formData.component_id) <= 0 ||
                    !self.formData.component_name || !self.formData.component_name.trim()) {
                    self.entityError = 'Select a component from the search results.';
                    return;
                }
                self.entityError = '';
            }
            if (govDef) {
                // Mirror the server's required-field check so a blank submit
                // gets a field-specific message without a round trip.
                for (let i = 0; i < govDef.required.length; i++) {
                    const v = self.formData[govDef.required[i][0]];
                    if (v === null || v === undefined || (typeof v === 'string' && !v.trim())) {
                        self.entityError = govDef.required[i][1] + ' is required.';
                        return;
                    }
                }
                self.entityError = '';
            }
            self.modalSaving = true;
            const isEdit = self.editingEntity && self.editingEntity.id;
            let url = self.apiBase + self._apiPath(type) + (isEdit ? '/' + self.editingEntity.id : '');
            const method = isEdit ? 'PUT' : 'POST';
            let writeSucceeded = false;

            try {
                // Platform.fetch will throw on non-ok responses.
                const res = await Platform.fetch(url, {
                    method: method,
                    body: govDef ? self._governancePayload() : self.formData,
                    silent: true   // We paint our own error state below.
                });
                // A 200 with {success:false} is still a failed save.
                if (res && typeof res === 'object' && res.success === false) {
                    throw new Error(res.error || res.message || 'Save failed');
                }
                writeSucceeded = true;
                await self.refreshEntityData(type);
                if (hasEditor && revision !== self.entityRevision) return;
                if (hasEditor) self.entityNotice = '';
                self.closeModal();
            } catch (err) {
                if (hasEditor && revision !== self.entityRevision) {
                    self.entityNotice = writeSucceeded
                        ? 'Saved, but the list could not be refreshed — reload the page to see the latest data'
                        : 'The dismissed editor could not save: ' + ((err && err.message) || 'Save failed');
                    return;
                }
                self.modalSaving = false;
                if (hasEditor) {
                    if (writeSucceeded) {
                        // The POST succeeded. A retry would create a duplicate.
                        self.closeModal();
                        self.entityNotice = 'Saved, but the list could not be refreshed — reload the page to see the latest data';
                        return;
                    }
                    // Keep the editor open with the user's input; show the server's reason.
                    self.entityError = (err && err.message) || 'Save failed';
                    return;
                }
                if (window.Platform && Platform.toast) Platform.toast.error('Save failed');
                // Rethrow to ensure the error is not swallowed.
                throw err;
            }
        },

        confirmDeleteEntity: function (type, entity) {
            const path = this._apiPath(type);
            if (!path || !entity || entity.id === null || entity.id === undefined) return;
            const key = type + ':' + entity.id;
            if (this.pendingDeletes.includes(key)) {
                this.entityNotice = 'Deletion is already in progress for this item.';
                return;
            }
            if (this.completedDeletes.includes(key)) {
                this.entityNotice = 'This item was already deleted. Reload the page to see the latest data.';
                return;
            }
            this.deleteRevision += 1;
            this.deleteType = type;
            this.deleteTarget = Object.assign({}, entity);
            this.deleteError = '';
            this.deleteSaving = false;
            if (!this._deleteHooked) {
                this._deleteHooked = true;
                const self = this;
                Platform.modal.on(DELETE_CONFIRMATION_ID, 'close', function () {
                    self.deleteRevision += 1;
                    self.deleteTarget = null;
                    self.deleteType = '';
                    self.deleteError = '';
                    self.deleteSaving = false;
                });
            }
            Platform.modal.open(DELETE_CONFIRMATION_ID);
        },

        deleteTargetName: function () {
            const target = this.deleteTarget || {};
            return target.component_name || target.name || target.title || target.exception_description ||
                target.control_id || target.review_type || ('Item ' + (target.id || ''));
        },

        closeDeleteConfirmation: function () {
            Platform.modal.close(DELETE_CONFIRMATION_ID);
        },

        executeDeleteEntity: async function () {
            const self = this;
            if (!self.deleteTarget || self.deleteSaving) return;
            const type = self.deleteType;
            const id = self.deleteTarget.id;
            const key = type + ':' + id;
            if (self.pendingDeletes.includes(key) || self.completedDeletes.includes(key)) return;
            const revision = self.deleteRevision;
            const url = self.apiBase + self._apiPath(type) + '/' + encodeURIComponent(id);
            self.deleteSaving = true;
            self.deleteError = '';
            self.pendingDeletes.push(key);
            let deleted = false;

            try {
                const response = await Platform.fetch(url, {
                    method: 'DELETE',
                    silent: true
                });
                if (response && (response.success === false || response.data?.success === false)) {
                    throw new Error(response.error || response.message || response.data?.error || 'Delete failed');
                }
                deleted = true;
                self.completedDeletes.push(key);
                if (self.entityNotice === 'Deletion is already in progress for this item.') self.entityNotice = '';
                // The DELETE is confirmed. Remove this known-deleted row even
                // if the following refresh fails, so stale UI cannot repeat it.
                const listKey = self._listKey(type);
                if (Array.isArray(self[listKey])) {
                    self[listKey] = self[listKey].filter(item => String(item.id) !== String(id));
                }
                await self.refreshEntityData(type, 'Deleted');
                if (revision === self.deleteRevision) self.closeDeleteConfirmation();
            } catch (err) {
                if (deleted) {
                    if (revision === self.deleteRevision) self.closeDeleteConfirmation();
                    self.entityNotice = 'Deleted, but the list could not be refreshed — reload the page to see the latest data';
                } else if (revision === self.deleteRevision) {
                    self.deleteError = (err && err.message) || 'Delete failed';
                } else {
                    self.entityNotice = 'The dismissed confirmation could not delete: ' + ((err && err.message) || 'Delete failed');
                }
            } finally {
                self.pendingDeletes = self.pendingDeletes.filter(item => item !== key);
                if (revision === self.deleteRevision) self.deleteSaving = false;
            }
        },

        refreshEntityData: async function (type, action) {
            let self = this;
            let url = self.apiBase + self._apiPath(type);

            try {
                const json = await Platform.fetch(url, { silent: true });
                const listKey = self._listKey(type);
                if ((type === 'composition' || action === 'Deleted') && (!json || json.success === false ||
                    !Array.isArray(json.data || json.items))) {
                    throw new Error('Component list returned an invalid response.');
                }
                const items = json.data || json.items || [];
                self[listKey] = items;
            } catch (err) {
                // The write that triggered this refresh already succeeded —
                // don't tell the user the save/delete failed. Tell them the
                // list on screen may be stale instead.
                if (window.Platform && Platform.toast) {
                    Platform.toast.error((action || 'Saved') + ', but the list could not be refreshed — reload the page to see the latest data');
                }
                // Rethrow to ensure the error is not swallowed.
                throw err;
            }
        },

        /* ── export / spec actions ───────────────────────────────────── */

        exportPdf: function () {
            let self = this;
            window.open('/solutions/' + self.solutionId + '/api/export/blueprint-pdf', '_blank');
        },

        generateSpecs: async function () {
            let self = this;

            try {
                const data = await Platform.fetch('/solutions/' + self.solutionId + '/api/generate-specs', {
                    method: 'POST',
                    silent: false   // Let Platform.fetch show a toast on error.
                });
                if (data.download_url) {
                    window.open(data.download_url, '_blank');
                }
                if (window.Platform && Platform.toast) Platform.toast.success('Specs generated');
            } catch (e) {
                // Platform.fetch surfaced this failure before it threw; the
                // catch only stops it becoming an unhandled rejection.
                // Rethrow to ensure the error is not swallowed.
                throw e;
            }
        },

        inferCodeSpecs: async function () {
            let self = this;

            try {
                await Platform.fetch('/solutions/' + self.solutionId + '/api/infer-code-specs', {
                    method: 'POST',
                    silent: false   // Let Platform.fetch show a toast on error.
                });
            } catch (e) {
                // Platform.fetch surfaced this failure before it threw; the
                // catch only stops it becoming an unhandled rejection.
                // Rethrow to ensure the error is not swallowed.
                throw e;
            }
        },

        /* ── internal helpers ────────────────────────────────────────── */

        _loadAllSectionElements: function () {
            let self = this;
            let defs = cfg.sectionDefinitions || {};

            Object.keys(defs).forEach(function (sectionId) {
                const def = defs[sectionId];
                // Only fetch for sections that have a viewpoint defined
                if (!def || !def.viewpoint) return;
                self._reloadSectionElements(sectionId);
            });
        },

        _reloadSectionElements: async function (sectionId) {
            let self = this;

            try {
                const data = await Platform.fetch('/solutions/' + self.solutionId + '/api/viewpoint/' + sectionId + '/elements', {
                    silent: true   // We paint our own error toast below.
                });
                self.sectionElements[sectionId] = (data.data && data.data.elements) || data.elements || [];
                self._sectionElementsErrorShown = false;
                // Re-render diagram if the container is already visible
                if (self.diagramsLoaded[sectionId]) {
                    self._renderDiagram(sectionId);
                }
            } catch (e) {
                // Called once per section on initial load plus after every
                // link/unlink and AI-copilot write — toast once per outage
                // rather than once per section/event.
                if (!self._sectionElementsErrorShown) {
                    self._sectionElementsErrorShown = true;
                    if (window.Platform && Platform.toast) {
                        Platform.toast.error('Some section data could not be refreshed — reload the page to see the latest data');
                    }
                }
                // Rethrow to ensure the error is not swallowed.
                throw e;
            }
        },

        _refreshScores: async function () {
            let self = this;

            try {
                const data = await Platform.fetch('/solutions/' + self.solutionId + '/api/blueprint-scores', {
                    silent: true   // No need for a toast; failure is not user‑visible.
                });
                self.scores = data.scores || data;
            } catch (e) {
                // Platform.fetch surfaced this failure before it threw; the
                // catch only stops it becoming an unhandled rejection.
                // Rethrow to ensure the error is not swallowed.
                throw e;
            }
        },

        _setupScrollObserver: function () {
            let self = this;

            if (typeof IntersectionObserver === 'undefined') return;

            const sections = document.querySelectorAll('[data-bp-section]');
            if (!sections.length) return;

            const observer = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (entry.isIntersecting) {
                        self.activeSection = entry.target.id || '';
                    }
                });
            }, {
                rootMargin: '-120px 0px -60% 0px'
            });

            sections.forEach(function (section) {
                observer.observe(section);
            });
        }
    };

    // ── Universal entity CRUD modal (drivers / goals / constraints / requirements / risks / options / plateaus) ──
    base.modal = { open: false, title: '', entity: '', fields: [], saving: false, error: '' };
    base.form = {};

    base.entityTypes = {
        driver:      { label: 'Driver',      path: '/drivers',      fields: [{key:'name',label:'Name',required:true},{key:'description',label:'Description',type:'textarea'},{key:'driver_type',label:'Type',type:'select',options:['internal','external','regulatory','technology']}] },
        goal:        { label: 'Goal',        path: '/goals',        fields: [{key:'name',label:'Name',required:true},{key:'description',label:'Description',type:'textarea'},{key:'priority',label:'Priority',type:'select',options:[{v:1,l:'Critical'},{v:2,l:'High'},{v:3,l:'Medium'},{v:4,l:'Low'}]}] },
        constraint:  { label: 'Constraint',  path: '/constraints',  fields: [{key:'name',label:'Name',required:true},{key:'description',label:'Description',type:'textarea'},{key:'constraint_type',label:'Type',type:'select',options:['budget','time','technical','regulatory','resource']},{key:'severity',label:'Severity',type:'select',options:[{v:1,l:'Critical'},{v:2,l:'High'},{v:3,l:'Medium'},{v:4,l:'Low'}]}] },
        requirement: { label: 'Requirement', path: '/requirements', fields: [{key:'name',label:'Name',required:true},{key:'description',label:'Description',type:'textarea'},{key:'requirement_type',label:'Type',type:'select',options:['functional','non_functional','compliance','security']},{key:'priority',label:'Priority',type:'select',options:[{v:1,l:'Critical'},{v:2,l:'High'},{v:3,l:'Medium'},{v:4,l:'Low'}]}] },
        risk:        { label: 'Risk',        path: '/risks',        fields: [{key:'name',label:'Name',required:true},{key:'description',label:'Description',type:'textarea'},{key:'severity',label:'Severity',type:'select',options:['critical','high','medium','low']},{key:'likelihood',label:'Likelihood',type:'select',options:['almost_certain','likely','possible','unlikely','rare']}] },
        option:      { label: 'Option',      path: '/options',      fields: [{key:'name',label:'Name',required:true},{key:'description',label:'Description',type:'textarea'},{key:'option_type',label:'Type',type:'select',options:['build','buy','partner','hybrid','reuse']}] },
        plateau:     { label: 'Plateau',     path: '/plateaus',     fields: [{key:'name',label:'Name',required:true},{key:'description',label:'Description',type:'textarea'},{key:'target_date',label:'Target Date',type:'date'}] }
    };

    // importRisksCsv — bulk-import solution risks from a CSV file (GAP-5)
    base.importRisksCsv = async function (event) {
        const file = event.target.files[0];
        if (!file) return;
        this.riskImporting = true;
        this.riskImportResult = null;
        const fd = new FormData();
        fd.append('file', file);
        try {
            // Platform.fetch cannot handle FormData automatically (it would JSON‑stringify).
            // We must use raw fetch and inspect raw headers.
            const r = await fetch('/solutions/' + this.solutionId + '/risks/import', { // raw-fetch-ok: FormData cannot be auto-serialized by Platform.fetch
                method: 'POST',
                headers: { 'X-CSRFToken': this.csrfToken },
                body: fd
            });
            /* Unchecked, a 500 parsed to `{}`: `d.created` was undefined, so the else
               branch fired and told the user "No risks imported" — an import that
               never ran, reported as an import that found nothing. */
            if (!r.ok) throw new Error('HTTP ' + r.status);
            const d = await r.json();
            this.riskImportResult = d;
            if (d.created > 0) {
                // Reload risks partial if function exists, else show toast
                if (typeof this.loadRisks === 'function') { this.loadRisks(); }
                let msg = d.created + ' risk' + (d.created !== 1 ? 's' : '') + ' imported';
                if (d.skipped) msg += ', ' + d.skipped + ' skipped';
                window.dispatchEvent(new CustomEvent('bp-toast', { detail: { message: msg, type: 'success' } }));
                if (typeof refreshMaturityScore === 'function') refreshMaturityScore();
            } else {
                const errMsg = d.error || ('No risks imported' + (d.errors && d.errors.length ? ': ' + d.errors[0].reason : ''));
                window.dispatchEvent(new CustomEvent('bp-toast', { detail: { message: errMsg, type: 'error' } }));
            }
        } catch(e) {
            this.riskImportResult = null;
            window.dispatchEvent(new CustomEvent('bp-toast', { detail: { message: 'Risk import failed — nothing was imported (' + ((e && e.message) || 'request failed') + ')', type: 'error' } }));
        } finally {
            this.riskImporting = false;
            event.target.value = '';
        }
    };

    // openAdd dispatches an event — the modal lives in {% block modals %} (separate Alpine scope)
    base.openAdd = function (entityType) {
        window.dispatchEvent(new CustomEvent('bp-add-entity', {
            detail: {
                entityType: entityType,
                solutionId: this.solutionId,
                csrfToken: this.csrfToken,
                entityTypes: this.entityTypes
            }
        }));
    };
    
    // Compliance gap analysis loader
    base.runComplianceAnalysis = async function() {
        const self = this;
        self.complianceGapLoading = true;
        try {
            const data = await Platform.fetch('/solutions/' + self.solutionId + '/api/compliance-gap', {
                silent: true   // We paint our own error toast below.
            });
            if (!data.success) throw new Error(data.error || 'the analysis did not complete');
            self.complianceGap = data.gap;
            self.complianceGapLoaded = true;
            self.complianceGapLoading = false;
        } catch (e) {
            /* This used to set complianceGapLoaded = true with complianceGap still
               null. The panel's only two branches are "loading" and
               "loaded && complianceGap", and the Run Analysis button hides once
               loaded — so a failure erased the whole section and left no way to
               retry. Staying "not loaded" puts the button back. */
            self.complianceGap = null;
            self.complianceGapLoaded = false;
            self.complianceGapLoading = false;
            if (window.Platform && Platform.toast) {
                Platform.toast.error('Compliance gap analysis failed — nothing was assessed. Use Run Analysis to try again.');
            }
            // Rethrow to ensure the error is not swallowed.
            throw e;
        }
    };

    // Merge spec panel mixins (component specs, integration contracts, deployment specs)
    const mixins = [
        typeof componentSpecsMixin === 'function' ? componentSpecsMixin() : null,
        typeof integrationContractsMixin === 'function' ? integrationContractsMixin() : null,
        typeof deploymentSpecsMixin === 'function' ? deploymentSpecsMixin() : null
    ];
    for (let i = 0; i < mixins.length; i++) {
        if (mixins[i]) {
            for (let key in mixins[i]) {
                if (mixins[i].hasOwnProperty(key)) {
                    base[key] = mixins[i][key];
                }
            }
        }
    }

    return base;
}
