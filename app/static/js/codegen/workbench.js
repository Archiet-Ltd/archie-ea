/**
 * Code Workbench — Alpine.js component
 * Drives all 4 phases: Enrich → Review → Configure → Generate
 */
(function() {
    // Non-reactive cache for groupedFiles getter.
    // MUST NOT be in Alpine component state — reading+writing reactive properties
    // inside a getter called from a template creates an infinite reactivity loop.
    let _gfCacheKey = '';
    let _gfCache = null;
    // Register immediately if Alpine is ready, otherwise wait for alpine:init
    function register() {
    try {
        Alpine.data('codegenWorkbench', function (initialData) {
        return {
            /* ── state ── */
            solutionId: initialData.solutionId,
            solutionName: initialData.solutionName || '',
            phase: initialData.phase || 1,       // 1-4: furthest unlocked phase
            version: initialData.version || 1,

            enriching: false,
            enrichingFields: false,
            resetting: false,
            savingConfig: false,
            generating: false,
            deploying: false,
            applyingSpecs: false,

            /* UML data */
            uml: initialData.uml || null,

            /* Config form */
            config: {
                language: 'python-fastapi',
                generation_mode: 'deterministic',
                python_version: '3.12',
                auth: 'none',
                github_org: '',
                repo_name: '',
                visibility: 'private',
                include_readme: true,
                include_frontend: false,
                template_set_id: null,
                ui_framework: 'none',
                mobile_framework: 'none',
                generation_policy: 'scaffold',
                package_mode: 'unmanaged',
                namespace_prefix: '',
            },

            /* IaC generation */
            iacGenerating: false,
            iacResult: null,
            iacRegion: 'eu-west-1',
            iacEnvironment: 'staging',

            /* Template Marketplace (Feature 6) */
            templateSets: [],
            templateFilter: 'all',       // 'all' | language slug
            templatePreviewId: null,     // id of expanded template set
            templatePreviewFiles: [],    // [{template_name, version}]
            templatePreviewLoading: false,
            saveTemplateModal: false,
            newTemplateName: '',
            newTemplateDesc: '',
            savingAsTemplate: false,

            /* Generated output */
            generatedFiles: {},      // { path: content }
            fileList: [],            // sorted paths
            selectedFile: '',
            selectedContent: '',

            /* Lifecycle guard */
            _initialized: false,

            /* CodeMirror 6 inline editor */
            cmEditor: null,
            _cmLangCompartment: null,  // Compartment for dynamic language switching
            /* AI selection toolbar */
            aiSelectionMenu: false,
            aiSelectionFrom: 0,
            aiSelectionTo: 0,
            aiMenuTop: 0,
            aiMenuLeft: 0,
            editorDirty: false,
            dirtyFiles: {},       // { path: true } — per-file unsaved changes (survives tab switch)
            fileSaving: false,
            editedFiles: [],
            hasLlm: initialData.hasLlm || false,

            /* AI patch mode */
            patchOpen: false,
            patchInstruction: '',
            patchRunning: false,
            patchDiff: null,         // { path, old, new } — awaiting confirm

            /* Genome NL patch */
            // genomeJson is NOT reactive — stored in window.__codegenGenome to avoid
            // Alpine deep-proxy overhead on large objects (causes Page Unresponsive)
            genomePatchInstruction: '',
            genomePatchRunning: false,
            genomePatchResult: null,
            genomePatchApplying: false,

            /* Prompt group progress */
            promptGroups: ['models', 'schemas', 'routes', 'services', 'integrations', 'tests', 'infrastructure'],
            promptGroupStatus: {},   // { key: 'pending'|'running'|'done'|'error' }
            generateLabel: '',       // current phase label from SSE
            generateElapsed: 0,      // seconds since generate() started
            _generateTimer: null,    // interval handle

            /* Chain completeness (GAP-02) */
            chainCompleteness: initialData.chainCompleteness,

            /* Active LLM provider (shown in AI chat header) */
            activeProvider: null,
            activeModel: null,

            /* Blueprint freshness gate */
            blueprintStale: initialData.blueprintStale || false,

            /* Quality scoring */
            qualityScore: null,
            qualityDetails: null,
            qualityOpen: false,
            dimFix: {},          // keyed by dimension name → {loading, message, error}
            recFix: {},          // keyed by recommendation index → {loading, message, error}

            /* Full-screen Preview modal */
            previewModal: false,
            previewModalTab: 'web',  // 'web' | 'mobile' | 'admin' | 'api'

            /* Test execution (Docker verify) */
            verifying: false,
            verifyResult: null,
            verifyLog: [],

            /* Drift detection */
            driftReport: null,
            searchResults: null,
            driftHasGithub: false,
            driftScanning: false,
            driftImporting: false,

            /* NL-to-Architecture (Feature 4) */
            nlDescription: '',
            nlGenerating: false,
            nlResult: null,
            linkedElementsCount: initialData.linkedElementsCount || 0,

            /* Document Upload (Phase 1) */
            docUploading: false,
            docFiles: [],               // FileList display: [{name, size, status}]
            docProposals: [],            // [{id, archimate_type, name, description, confidence, status}]
            docProposalsLoaded: false,
            docSummary: '',
            docErrors: [],
            docAccepting: false,

            /* Docker Live Preview */
            dockerBuilding: false,
            dockerRunning: false,
            dockerUrl: null,
            dockerError: null,
            dockerContainer: null,

            /* StackBlitz Frontend Preview */
            stackBlitzLoading: false,

            /* Architecture Diagram */
            archDiagramLoading: false,
            archDiagramTotal: 0,
            archDiagramError: null,
            _archDiagramRenderer: null,

            /* Live Preview (Feature 3) */
            previewActive: false,
            previewStartedAt: null,
            previewMinutesLeft: 10,
            previewOpen: false,
            previewStarting: false,
            previewEndpointCount: 0,
            previewSchemaCount: 0,
            _previewTimer: null,

            /* IDE layout state */
            rightTab: 'ai',
            leftW: 208,              // file-tree width px (draggable)
            rightW: 400,             // right-panel width px (draggable)
            dragging: null,          // 'left' | 'right' | null
            _dragStartX: 0,
            _dragStartW: 0,
            aiSectionsCollapsed: true,   // collapse intelligences + intent planner by default
            expandedDirs: {},

            /* Debug mode (AIC-319) */
            debugMode: false,
            debugWorkspaceId: null,
            debugErrorLog: '',
            debugAnalyzing: false,
            debugAnalysis: null,
            debugSuggestedChanges: [],
            debugHistory: [],        // [{role: 'user'|'assistant', text: str}]
            debugApplying: false,
            debugInstruction: '',    // Current debug message input
            debugDiffModal: null,    // {file, suggestion, reason, allChanges, currentIndex}
            openTabs: [],          // [{path, name}] — open file tabs
            fileFilter: '',        // file tree search input
            fileTreeExpanded: false, // show all files beyond the 60-file cap
            ideReady: false,       // set true after Alpine's first render tick (defers IDE template mount)
            ideShowSetup: false,   // true = show wizard/setup overlay over the IDE

            /* Generation history (GAP-04) */
            history: [],
            historyOpen: false,
            lastImpact: null,
            versionLabel: '',

            /* Language metadata (GAP-03) */
            LANGUAGE_META: {
                'python-fastapi': { label: 'Python / FastAPI', tier: 'Full', templates: 47 },
                'python-flask': { label: 'Python / Flask', tier: 'Full', templates: 12 },
                'go-chi': { label: 'Go / Chi', tier: 'Full', templates: 43 },
                'java-spring-boot': { label: 'Java / Spring Boot', tier: 'Basic', templates: 12 },
                'salesforce-apex': { label: 'Salesforce / Apex', tier: 'Basic', templates: 16 },
                'sap-cap': { label: 'SAP CAP (BTP / OData V4)', tier: 'Full', templates: 14 },
                'sap-btp-integration': { label: 'SAP BTP Integration Suite', tier: 'Full', templates: 8 },
            },

            /* SAP System Import (reverse-engineering — reads live SAP to populate ArchiMate) */
            sapImport: {
                open: false,            // panel visible
                mock: true,             // use mock data (no real SAP needed)
                ashost: '',
                sysnr: '00',
                client: '100',
                user: '',
                passwd: '',
                packageFilter: '',
                tableLimit: 500,
                tcodeLimit: 200,
                roleLimit: 50,
                running: false,
                result: null,           // { ok, stats, errors }
                error: null,
            },

            /* GitHub result */
            githubUrl: initialData.githubUrl || '',
            githubSha: '',

            /* Notifications */
            errors: [],
            successMsg: '',

            /* Phase 2 diagram view */
            diagramView: false,
            _diagramRendered: false,

            /* Phase 2 field editor (Gap 1) */
            fieldEditorOpen: {},    // { className: true/false }
            editingFields: {},     // { className: [...fields] } — working copy
            confirmingClass: '',   // currently saving class name

            /* Phase 2 confirmed tracking */
            confirmedClasses: {},  // { className: true } — persists confirmation state

            /* Incremental regeneration */
            changePreview: null,   // { added: [], removed: [], changed: [], total_affected: N }
            loadingPreview: false,

            /* Phase 4 chat (Gap 3) */
            intentInstruction: '',
            intentPlanning: false,
            intentPlan: null,
            intentVerifyRunning: false,
            intentVerify: null,
            allowChatWithoutVerify: false,
            chatOverrideReason: '',
            intentGateStats: { total_events: 0, blocked: 0, verified: 0, override: 0, override_rate: 0.0 },
            intentLastGateEvent: null,
            chatInstruction: '',
            chatSending: false,
            chatHistory: [],       // [{ role: 'user'|'assistant', text: str }]
            chatSuggestions: [],   // last 5 instructions from localStorage

            /* AI-powered intelligences (advisory — POST /codegen/intelligence) */
            intelligenceLoading: false,
            intelligenceAction: '',
            intelligenceDisplay: '',
            intelligenceRaw: null,
            intelligenceTraceElementId: '',
            intelligenceFailureText: '',

            /* North star: unified command surface + grounding context */
            journeyBriefText: initialData.journeyBrief || '',
            commandPaletteOpen: false,
            commandQuery: '',
            commandPaletteIndex: 0,
            _cmdPaletteKeyHandler: null,

            /* ── lifecycle ── */
            async init() {
                // Guard against Alpine calling init() twice (can happen on DOM re-mount)
                if (this._initialized) return;
                this._initialized = true;

                // Seed shared stores with identity so other components can reach them
                const _codegenStore = Alpine.store('codegen');
                if (_codegenStore) {
                    _codegenStore.solutionId = this.solutionId;
                    _codegenStore.solutionName = this.solutionName;
                    _codegenStore.phase = this.phase;
                    _codegenStore.version = this.version;
                }

                // Fetch config from API (not inlined in HTML — keeps page lightweight)
                let saved = {};
                try {
                    const cfgResp = await this._fetch(`/solutions/${this.solutionId}/codegen/config`);
                    saved = (cfgResp && cfgResp.config) || {};
                } catch (e) {
                    // "First visit" is NOT this branch: the endpoint answers 200
                    // {"config": {}} when nothing is stored. A throw is a real
                    // failure (403/500/offline), and the defaults applied below then
                    // render as if they were the saved configuration — and overwrite
                    // it the moment the user saves. Say so instead.
                    this._addError(
                        'Could not load this solution\'s saved code-generation settings. ' +
                        'The panel is showing defaults, not your configuration — reload before saving. (' +
                        (e.message || 'request failed') + ')'
                    );
                }
                this.editedFiles = Object.keys(saved.manual_edits || {});
                this.config.language = saved.language || 'python-fastapi';
                this.config.generation_mode = saved.generation_mode || 'genome';
                this.config.python_version = saved.python_version || '3.12';
                // Use saved auth if set; otherwise pre-populate from blueprint security_viewpoint
                this.config.auth = saved.auth || initialData.suggestedAuth || 'none';
                this.config.github_org = saved.github_org || '';
                this.config.repo_name = saved.repo_name || this.kebabCase(this.solutionName);
                this.config.visibility = saved.visibility || 'private';
                this.config.include_readme = saved.include_readme !== false;
                this.config.include_frontend = saved.include_frontend === true;
                this.config.template_set_id = saved.template_set_id || null;
                this.config.ui_framework = saved.ui_framework || 'none';
                this.config.mobile_framework = saved.mobile_framework || 'none';
                this.config.namespace_prefix = saved.namespace_prefix || saved.namespace || '';
                this.config.package_mode = saved.package_mode || (this.config.namespace_prefix ? 'managed' : 'unmanaged');
                this.loadTemplateSets();

                this.journeyBriefText = initialData.journeyBrief || '';

                // Pre-populate NL description from architecture journey brief
                if (initialData.journeyBrief && !this.nlDescription) {
                    this.nlDescription = initialData.journeyBrief;
                }

                // If we already have UML from a previous session, restore it.
                // If we have linked elements but no UML yet, generate it automatically —
                // the user clicked "Generate Code" and already did the architecture work.
                if (initialData.hasUml) {
                    this._loadUml();
                } else if (initialData.linkedElementsCount > 0 && !initialData.hasFiles) {
                    // Elements exist but no UML yet — show quick generate option
                    // Don't auto-trigger LLM enrichment (fails on quota limits)
                    this.phase = 1;
                }
                // If no linked elements and no UML, user stays at Phase 1 with manual options

                // Load existing document proposals (if any were uploaded in a previous session)
                if (this.phase === 1) {
                    this.loadProposals();
                }

                // If files already generated
                if (initialData.hasFiles) {
                    // Load file list first (fast, drives the file tree)
                    await this._loadFiles();
                    // Stagger secondary fetches to avoid flooding the main thread
                    // with 5 concurrent state updates that each trigger Alpine re-renders
                    setTimeout(() => this.loadQuality(), 100);
                    setTimeout(() => this.loadHistory(), 200);
                    setTimeout(() => this.loadDrift(), 300);
                    setTimeout(() => this.checkPreviewStatus(), 400);
                    setTimeout(() => this.loadIntentState(), 500);
                    setTimeout(() => this._fetchGenome(), 700);
                }

                // Fetch expensive metadata lazily (chain completeness + spec counts).
                // Deferred from server-side render to keep page load fast.
                setTimeout(() => this._loadMeta(), 800);

                // Reset prompt group statuses
                this.promptGroups.forEach(k => { this.promptGroupStatus[k] = 'pending'; });

                // Load chat instruction history from localStorage. Best-effort cache —
                // unavailable in private browsing or corrupt JSON just means the seed
                // suggestions below are used instead.
                try {
                    const stored = localStorage.getItem('codegen_chat_history_' + this.solutionId);
                    if (stored) this.chatSuggestions = JSON.parse(stored);
                } catch (_) { /* swallow-ok: localStorage throws in private mode and the stored JSON is our own; either way the seeded suggestions below are used, so there is nothing to tell the user */ }
                // Seed contextual suggestions if none saved yet
                if (this.chatSuggestions.length === 0) {
                    this.chatSuggestions = [
                        'Add rate limiting to all POST endpoints',
                        'Add pagination parameters to all list endpoints',
                        'Add a /health endpoint returning service status',
                        'Add input validation error responses to all routes',
                        'Add structured logging middleware',
                    ];
                }

                // Load confirmed classes from localStorage. Best-effort cache — a
                // missing/corrupt value just means classes start unconfirmed.
                try {
                    const conf = localStorage.getItem('codegen_confirmed_' + this.solutionId);
                    if (conf) this.confirmedClasses = JSON.parse(conf);
                } catch (_) { /* swallow-ok: localStorage throws in private mode; this is a display-only cache of a confirmation the server already owns, so classes simply start unconfirmed */ }

                // Check if Docker preview container is already running
                if (initialData.hasFiles) this.checkDockerStatus();

                // Restore saved panel widths from localStorage. Best-effort — falls
                // back to the default widths already set above.
                try {
                    const sl = localStorage.getItem('wb-leftW');
                    const sr = localStorage.getItem('wb-rightW');
                    if (sl) this.leftW = Math.max(0, Math.min(480, parseInt(sl, 10)));
                    if (sr) this.rightW = Math.max(280, Math.min(800, parseInt(sr, 10)));
                } catch (_) { /* swallow-ok: localStorage throws in private mode; panel widths are a cosmetic preference and the defaults already applied are correct */ }

                // Global mouse-drag handlers for panel resize
                this._onDragMove = (e) => {
                    if (!this.dragging) return;
                    const delta = e.clientX - this._dragStartX;
                    if (this.dragging === 'left') {
                        this.leftW = Math.max(0, Math.min(480, this._dragStartW + delta));
                    } else {
                        this.rightW = Math.max(280, Math.min(800, this._dragStartW - delta));
                    }
                };
                this._onDragEnd = () => this.stopDrag();
                window.addEventListener('mousemove', this._onDragMove);
                window.addEventListener('mouseup', this._onDragEnd);

                this.$watch('commandQuery', () => {
                    this.commandPaletteIndex = 0;
                });
                this.$watch('commandPaletteOpen', (open) => {
                    if (open) {
                        this.commandQuery = '';
                        this.commandPaletteIndex = 0;
                        this.$nextTick(() => {
                            const el = this.$refs.cmdPaletteInput;
                            if (el && typeof el.focus === 'function') el.focus();
                        });
                    }
                });

                const self = this;
                this._cmdPaletteKeyHandler = function (e) {
                    if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
                        e.preventDefault();
                        self.commandPaletteOpen = !self.commandPaletteOpen;
                        if (self.commandPaletteOpen) {
                            self.commandQuery = '';
                            self.commandPaletteIndex = 0;
                            self.$nextTick(() => {
                                const el = self.$refs.cmdPaletteInput;
                                if (el && typeof el.focus === 'function') el.focus();
                            });
                        }
                    }
                    if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === 'f' || e.key === 'F')) {
                        e.preventDefault();
                        self.searchFiles();
                    }
                    // Ctrl/Cmd+I — toggle AI chat tab
                    if ((e.metaKey || e.ctrlKey) && (e.key === 'i' || e.key === 'I') && !e.shiftKey) {
                        e.preventDefault();
                        self.rightTab = self.rightTab === 'ai' ? 'preview' : 'ai';
                        if (self.rightTab === 'ai') {
                            self.$nextTick(() => {
                                const chatInput = self.$refs.chatTextarea || document.querySelector('[x-ref="chatTextarea"]');
                                if (chatInput) chatInput.focus();
                            });
                        }
                    }
                };
                document.addEventListener('keydown', this._cmdPaletteKeyHandler);

                // Cleanup on Alpine destroy (prevents listener accumulation on SPA navigations)
                this.$cleanup && this.$cleanup(() => {
                    document.removeEventListener('keydown', this._cmdPaletteKeyHandler);
                    window.removeEventListener('beforeunload', this._beforeUnloadHandler);
                    if (this._onDragMove) window.removeEventListener('mousemove', this._onDragMove);
                    if (this._onDragEnd) window.removeEventListener('mouseup', this._onDragEnd);
                });

                // Warn before navigating away with unsaved changes
                this._beforeUnloadHandler = function(e) {
                    const dirty = Object.keys(self.dirtyFiles || {}).length > 0 || self.editorDirty;
                    if (dirty) {
                        e.preventDefault();
                        e.returnValue = 'You have unsaved changes. Leave anyway?';
                    }
                };
                window.addEventListener('beforeunload', this._beforeUnloadHandler);

                // Signal that init() is done — triggers IDE/wizard template mount.
                // Using $nextTick ensures Alpine has flushed the skeleton render before
                // mounting the 471-directive IDE template, preventing the browser freeze
                // that occurred when all directives were evaluated synchronously during init.
                this.$nextTick(() => { this.ideReady = true; });
            },

            commandPaletteShortcutLabel() {
                if (typeof navigator !== 'undefined' && navigator.platform && navigator.platform.indexOf('Win') >= 0) {
                    return 'Ctrl+K';
                }
                return '⌘K';
            },

            /* ── Panel resize ── */
            startDrag(side, e) {
                e.preventDefault();
                this.dragging = side;
                this._dragStartX = e.clientX;
                this._dragStartW = side === 'left' ? this.leftW : this.rightW;
            },
            stopDrag() {
                if (!this.dragging) return;
                this.dragging = null;
                // Best-effort: remember panel widths for next visit. Unavailable in
                // private browsing just means the layout resets to defaults next time.
                try {
                    localStorage.setItem('wb-leftW', this.leftW);
                    localStorage.setItem('wb-rightW', this.rightW);
                } catch (_) { /* swallow-ok: localStorage throws in private mode or when the quota is full; the panel is already the width the user dragged it to, only the recall next visit is lost */ }
            },
            toggleLeftPanel() {
                this.leftW = this.leftW > 40 ? 0 : 208;
                try { localStorage.setItem('wb-leftW', this.leftW); } catch (_) { /* swallow-ok: localStorage throws in private mode; the panel already toggled, only the recall next visit is lost */ }
            },
            toggleRightWide() {
                this.rightW = this.rightW <= 440 ? 660 : 400;
                try { localStorage.setItem('wb-rightW', this.rightW); } catch (_) { /* swallow-ok: localStorage throws in private mode; the panel already toggled, only the recall next visit is lost */ }
            },

            traceMarkerSummary() {
                const sig = this.qualityDetails && this.qualityDetails.artifact_file_signals;
                if (!sig || !sig.length) return '';
                const withT = sig.filter(function (r) { return r && r.has_trace_marker; }).length;
                return withT + '/' + sig.length + ' files with trace markers';
            },

            journeyBriefAttached() {
                const t = (this.journeyBriefText || '').trim();
                return t.length > 0;
            },

            commandPaletteActionsAll() {
                const base = [
                    { id: 'quality', label: 'Open Quality panel', kw: 'score structural' },
                    { id: 'ai', label: 'Open AI tab', kw: 'intelligence copilot' },
                    { id: 'preview', label: 'Open API / Preview tab', kw: 'api docs swagger' },
                    { id: 'deploy', label: 'Open Deploy tab', kw: 'terraform iac' },
                    { id: 'verify', label: 'Run Docker verify', kw: 'test check' },
                ];
                if (this.hasLlm) {
                    base.push({ id: 'patch', label: 'Open AI patch', kw: 'edit diff nl' });
                }
                base.push(
                    { id: 'journey_tips', label: 'Journey copilot tips', kw: 'advisory' },
                    { id: 'setup', label: 'Open Setup wizard', kw: 'configure phase' },
                    { id: 'history', label: 'Open History', kw: 'version' },
                    { id: 'templates', label: 'Open Templates', kw: 'marketplace' },
                    { id: 'search', label: 'Search across files', kw: 'find grep' },
                    { id: 'new_file', label: 'Create new file', kw: 'add create' },
                    { id: 'rename', label: 'Rename / move file', kw: 'move path' },
                    { id: 'duplicate', label: 'Duplicate file', kw: 'copy clone' },
                    { id: 'solution', label: 'Back to solution', kw: 'navigate' },
                );
                return base;
            },

            filteredCommandPaletteList() {
                const all = this.commandPaletteActionsAll();
                const q = (this.commandQuery || '').trim().toLowerCase();
                if (!q) return all;
                return all.filter(function (a) {
                    if (a.label.toLowerCase().indexOf(q) >= 0) return true;
                    const kw = (a.kw || '').toLowerCase();
                    const parts = kw.split(/\s+/);
                    for (let i = 0; i < parts.length; i++) {
                        if (parts[i].indexOf(q) >= 0 || q.indexOf(parts[i]) >= 0) return true;
                    }
                    return false;
                });
            },

            executeCommandAction(id) {
                this.commandPaletteOpen = false;
                switch (id) {
                    case 'quality':
                        this.rightTab = 'quality';
                        break;
                    case 'ai':
                        this.rightTab = 'ai';
                        break;
                    case 'debug':
                        this.rightTab = 'debug';
                        break;
                    case 'preview':
                        this.rightTab = 'preview';
                        break;
                    case 'deploy':
                        this.rightTab = 'deploy';
                        break;
                    case 'verify':
                        this.verify();
                        break;
                    case 'patch':
                        if (this.hasLlm) {
                            this.patchOpen = true;
                        } else {
                            this._addError('Configure an LLM in Admin → API Settings to use patch mode.');
                        }
                        break;
                    case 'journey_tips':
                        this.rightTab = 'ai';
                        this.runCodegenIntelligence('journey_copilot_tips');
                        break;
                    case 'setup':
                        this.ideShowSetup = true;
                        this.phase = Math.max(this.phase, 3);
                        break;
                    case 'history':
                        this.rightTab = 'history';
                        break;
                    case 'templates':
                        this.rightTab = 'templates';
                        break;
                    case 'search':
                        this.searchFiles();
                        break;
                    case 'new_file':
                        this.createFile();
                        break;
                    case 'rename':
                        this.renameFile();
                        break;
                    case 'duplicate':
                        this.duplicateFile();
                        break;
                    case 'solution':
                        window.location.href = '/solutions/' + this.solutionId;
                        break;
                    default:
                        break;
                }
            },

            executeCommandPaletteAtIndex() {
                const list = this.filteredCommandPaletteList();
                if (!list.length) return;
                const idx = Math.min(Math.max(this.commandPaletteIndex, 0), list.length - 1);
                const cmd = list[idx];
                if (cmd) this.executeCommandAction(cmd.id);
            },

            /* ── IaC generation ── */
            async generateIac() {
                this.iacGenerating = true;
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/generate-iac`,
                        {
                            method: 'POST',
                            body: {
                                region: this.iacRegion,
                                environment: this.iacEnvironment,
                            },
                        }
                    );
                    this.iacResult = data;
                    // Reload file list to include new terraform/ files
                    await this._loadFiles();
                    this.successMsg = `Terraform IaC generated — ${data.file_count} files in terraform/`;
                    setTimeout(() => { this.successMsg = ''; }, 5000);
                } catch (e) {
                    this.errors.push({ id: Date.now(), text: 'IaC generation failed: ' + e.message });
                }
                this.iacGenerating = false;
            },

            /* ── Template Marketplace (Feature 6) ── */
            async loadTemplateSets() {
                try {
                    let data = await this._fetch('/api/codegen/template-sets');
                    this.templateSets = Array.isArray(data) ? data : [];
                } catch (e) {
                    this._addError('Could not load the template marketplace.', true);
                }
            },

            filteredTemplateSets() {
                if (this.templateFilter === 'all') return this.templateSets;
                return this.templateSets.filter(t => (t.language || '').startsWith(this.templateFilter));
            },

            async toggleTemplatePreview(id) {
                if (this.templatePreviewId === id) {
                    this.templatePreviewId = null;
                    this.templatePreviewFiles = [];
                    return;
                }
                this.templatePreviewId = id;
                this.templatePreviewFiles = [];
                this.templatePreviewLoading = true;
                try {
                    const data = await this._fetch('/api/codegen/template-sets/' + id);
                    this.templatePreviewFiles = data.files || [];
                } catch (e) {
                    this._addError('Could not load the template preview.', true);
                }
                this.templatePreviewLoading = false;
            },

            async deleteTemplateSet(id) {
                if (!(await Platform.modal.confirm('Delete this template set? This cannot be undone.'))) return;
                try {
                    await this._fetch('/api/codegen/template-sets/' + id, { method: 'DELETE' });
                    if (this.config.template_set_id === id) this.config.template_set_id = null;
                    this.templateSets = this.templateSets.filter(t => t.id !== id);
                    if (this.templatePreviewId === id) { this.templatePreviewId = null; this.templatePreviewFiles = []; }
                } catch (e) {
                    this.errors.push({ id: Date.now(), text: 'Delete failed: ' + e.message });
                }
            },

            async saveAsTemplate() {
                const name = (this.newTemplateName || '').trim();
                if (!name) { this.errors.push({ id: Date.now(), text: 'Template name is required.' }); return; }
                if (this.fileList.length === 0) { this.errors.push({ id: Date.now(), text: 'No generated files to save.' }); return; }
                this.savingAsTemplate = true;
                try {
                    const data = await this._fetch(
                        '/solutions/' + this.solutionId + '/codegen/save-as-template',
                        {
                            method: 'POST',
                            body: {
                                name: name,
                                description: (this.newTemplateDesc || '').trim() || null,
                                language: this.config.language,
                            },
                        }
                    );
                    this.templateSets.unshift(data);
                    this.config.template_set_id = data.id;
                    this.saveTemplateModal = false;
                    this.newTemplateName = '';
                    this.newTemplateDesc = '';
                    this.successMsg = 'Template set saved — ' + data.file_count + ' files captured.';
                    setTimeout(() => { this.successMsg = ''; }, 4000);
                } catch (e) {
                    this.errors.push({ id: Date.now(), text: 'Save failed: ' + e.message });
                }
                this.savingAsTemplate = false;
            },

            /* ── helpers ── */
            _csrfToken() {
                const s = Alpine.store('codegen');
                if (s) return s.csrfToken();
                const el = document.querySelector('meta[name="csrf-token"]');
                return el ? el.content : '';
            },

            async _fetch(url, opts) {
                // Delegate to shared store fetch when available
                const s = Alpine.store('codegen');
                if (s) return s.apiFetch(url, opts);
                // Fallback (store not loaded) — use Platform.fetch
                // Platform.fetch automatically injects CSRF token, serializes JSON, and throws on non-ok.
                // We need to preserve the existing error handling that shows inline errors.
                // The caller already paints its own error state, so we pass { silent: true } to avoid duplicate toasts.
                try {
                    const options = opts || {};
                    // Platform.fetch expects body as plain object for auto-JSON; ensure we don't double-stringify.
                    // If the caller already stringified, we need to adjust, but existing callers pass plain objects.
                    // We'll rely on Platform.fetch's detection.
                    return await Platform.fetch(url, { ...options, silent: true });
                } catch (error) {
                    // Rethrow: this is a shared helper whose callers own the
                    // user-facing error state (which is why silent:true is set).
                    // Swallowing would return undefined, and every caller reads
                    // that as an empty result -- a failed request rendered as
                    // "nothing here", which the user cannot tell apart.
                    throw error;
                }
            },

            async runSapImport() {
                const si = this.sapImport;
                si.running = true;
                si.result = null;
                si.error = null;
                try {
                    const body = {
                        architecture_id: this.solutionId,
                        mock: si.mock,
                        package_filter: si.packageFilter || undefined,
                        table_limit: parseInt(si.tableLimit) || 500,
                        tcode_limit: parseInt(si.tcodeLimit) || 200,
                        role_limit: parseInt(si.roleLimit) || 50,
                    };
                    if (!si.mock) {
                        body.config = {
                            ashost: si.ashost,
                            sysnr: si.sysnr,
                            client: si.client,
                            user: si.user,
                            passwd: si.passwd,
                            lang: 'EN',
                        };
                    }
                    const data = await this._fetch('/api/sap/import', { method: 'POST', body: body });
                    si.result = data;
                    if (data.ok) {
                        const s = data.stats || {};
                        const total = (s.tables || 0) + (s.transactions || 0) + (s.roles || 0) + (s.components || 0);
                        this.successMsg = `SAP import complete — ${total} elements, ${s.relationships_created || 0} relationships added to ArchiMate model.`;
                        setTimeout(() => { this.successMsg = ''; }, 7000);
                    } else {
                        si.error = data.error || 'Import failed';
                    }
                } catch (e) {
                    si.error = e.message;
                } finally {
                    si.running = false;
                }
            },

            async _loadMeta() {
                // Fetch chain completeness + spec counts — deferred from page load
                // because _compute_chain_completeness calls ArchiMateInferenceEngine
                // per element which is too slow for synchronous server render.
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/meta`);
                    if (data.chain_completeness !== null && data.chain_completeness !== undefined) {
                        this.chainCompleteness = data.chain_completeness;
                    }
                    if (typeof data.pending_specs_count === 'number') {
                        this.pendingSpecsCount = data.pending_specs_count;
                    }
                    if (typeof data.confirmed_specs_count === 'number') {
                        this.confirmedSpecsCount = data.confirmed_specs_count;
                    }
                    if (data.active_provider) this.activeProvider = data.active_provider;
                    if (data.active_model) this.activeModel = data.active_model;
                } catch (e) {
                    // chainCompleteness stays null and renders as an em dash, which is
                    // honest but identical to "never computed". Say which one it is.
                    this._addError(
                        'Could not load chain completeness and spec counts ('
                        + (e.message || 'request failed')
                        + ') — those figures show as "—" because they could not be read, not because they are zero.',
                        true
                    );
                }
            },

            async _fetchGenome() {
                // Genome JSON is no longer inlined in HTML (was causing parse stalls
                // on large solutions). Fetched on-demand when Genome Patch is triggered.
                if (window.__codegenGenome && Object.keys(window.__codegenGenome).length) {
                    return window.__codegenGenome;
                }
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/genome`);
                    window.__codegenGenome = data.genome || {};
                } catch (_) {
                    window.__codegenGenome = {};
                }
                return window.__codegenGenome;
            },

            _addError(msg, autoDismiss = false) {
                // Mirror to shared store so other components can read the error bus
                const s = Alpine.store('codegen');
                if (s) s.addError(msg, autoDismiss);
                this.errors.unshift({ id: Date.now(), text: msg });
                // Only auto-dismiss transient warnings; persistent errors stay until user dismisses
                if (autoDismiss) {
                    const id = this.errors[0].id;
                    setTimeout(() => {
                        this.errors = this.errors.filter(e => e.id !== id);
                    }, 8000);
                }
            },

            _setSuccess(msg) {
                const s = Alpine.store('codegen');
                if (s) s.setSuccess(msg);
                this.successMsg = msg;
                setTimeout(() => { this.successMsg = ''; }, 5000);
            },

            _scrollChatToBottom() {
                this.$nextTick(() => {
                    const chatEl = document.querySelector('[x-show*="rightTab === \'ai\'"] .flex-1.overflow-y-auto');
                    if (chatEl) chatEl.scrollTop = chatEl.scrollHeight;
                });
            },

            kebabCase(str) {
                const s = Alpine.store('codegen');
                if (s) return s.kebabCase(str);
                return (str || '')
                    .toLowerCase()
                    .replace(/[^a-z0-9\s-]/g, '')
                    .replace(/[\s_]+/g, '-')
                    .replace(/-+/g, '-')
                    .replace(/^-|-$/g, '');
            },

            /* ── UML summary helpers ── */
            get classCount() {
                return this.uml ? (this.uml.class_diagram?.classes?.length || 0) : 0;
            },
            get flowCount() {
                return this.uml ? (this.uml.sequence_diagram?.flows?.length || 0) : 0;
            },
            get componentCount() {
                return this.uml ? (this.uml.component_diagram?.components?.length || 0) : 0;
            },
            get nodeCount() {
                return this.uml ? (this.uml.deployment_diagram?.nodes?.length || 0) : 0;
            },
            get umlClasses() {
                return this.uml ? (this.uml.class_diagram?.classes || []) : [];
            },
            get confirmedCount() {
                return this.umlClasses.filter(function(c) { return this.confirmedClasses[c.name]; }, this).length;
            },
            get unconfirmedCount() {
                return this.umlClasses.length - this.confirmedCount;
            },

            /* ── file tree helpers ── */
            _fileCategory(path) {
                if (path.startsWith('app/models')) return 'models';
                if (path.startsWith('app/schemas')) return 'schemas';
                if (path.startsWith('app/api') || path.startsWith('app/routes')) return 'routes';
                if (path.startsWith('app/services')) return 'services';
                if (path.startsWith('app/integrations')) return 'integrations';
                if (path.startsWith('app/static')) return 'frontend';
                if (path.startsWith('app/templates')) return 'templates';
                if (path.startsWith('app/handlers')) return 'handlers';
                if (path.startsWith('ui/')) return 'frontend';
                if (path.startsWith('frontend/')) return 'frontend';
                if (path.startsWith('tests')) return 'tests';
                if (path.startsWith('alembic') || path.startsWith('migrations')) return 'migrations';
                if (path.startsWith('k8s') || path.startsWith('helm') || path.startsWith('docker') || path.startsWith('monitoring') || path.startsWith('terraform')) return 'infrastructure';
                if (path.startsWith('mobile/')) return 'mobile';
                if (path.startsWith('docs/')) return 'docs';
                if (path === 'app/main.py' || path === 'app/__init__.py' || path === 'app/config.py' || path === 'app/auth_config.py' || path.startsWith('app/auth')) return 'config';
                // Root-level files (no directory separator) — group by type instead of dumping into 'other'
                if (!path.includes('/')) {
                    if (path === 'Dockerfile' || path.startsWith('docker-compose') || path === 'Makefile' || path === '.dockerignore') return 'infrastructure';
                    if (path === 'README.md' || path === 'ARCHITECTURE.md' || path.endsWith('.md')) return 'docs';
                    if (path === 'openapi.yaml' || path === 'openapi.json' || path === 'asyncapi.yaml' || path === 'alembic.ini' || path === 'pyproject.toml' || path === 'requirements.txt' || path === 'setup.py' || path === 'poetry.lock' || path === '.env.template' || path === '.gitignore') return 'config';
                    if (path.endsWith('.json') || path.endsWith('.yaml') || path.endsWith('.yml') || path.endsWith('.toml')) return 'config';
                }
                return 'other';
            },

            // groupedFiles uses closure-level cache vars (_gfCacheKey, _gfCache defined at
            // top of IIFE) — NOT reactive component state. Reading+writing reactive props
            // inside a template-bound getter creates an Alpine infinite reactivity loop
            // (read _groupedFilesCacheKey → tracked → write → re-trigger → loop → freeze).
            get groupedFiles() {
                const key = this.fileList.join('|');
                if (_gfCache && _gfCacheKey === key) {
                    return _gfCache;
                }
                const groups = {};
                this.fileList.forEach(path => {
                    const cat = this._fileCategory(path);
                    if (!groups[cat]) groups[cat] = [];
                    groups[cat].push(path);
                });
                _gfCache = groups;
                _gfCacheKey = key;
                return groups;
            },

            /* ── Skip enrichment — go straight to generate using ArchiMate elements ── */
            async skipEnrichment() {
                this.phase = Math.max(this.phase, 3);  // skip to Configure
                this.config.generation_mode = 'deterministic';
                this._setSuccess(
                    'Enrichment skipped — will generate code directly from ArchiMate elements. ' +
                    'Configure your stack below, then click Generate.'
                );
            },

            /* ── Quick Generate — one click from blueprint to code ── */
            async quickGenerate() {
                this.config.generation_mode = 'deterministic';
                this.phase = 4;
                this.blueprintStale = false;  // deterministic mode doesn't use enrichment
                await this.generate();
            },

            /* ── Phase 1: Enrich ── */
            async enrich() {
                this.enriching = true;
                this.errors = [];
                try {
                    await this._fetch(
                        `/solutions/${this.solutionId}/codegen/enrich`,
                        { method: 'POST', body: { version: this.version } }
                    );
                    // Job started in background — poll for completion
                    await this._pollEnrich();
                } catch (e) {
                    this._addError('Enrich failed: ' + e.message);
                    this.enriching = false;
                }
            },

            async _pollEnrich() {
                const self = this;
                return new Promise((resolve, reject) => {
                    let polls = 0;
                    const max = 60; // 60 × 8s = 8 min
                    const timer = setInterval(async () => {
                        try {
                            const data = await self._fetch(`/solutions/${self.solutionId}/codegen/enrich/status`);
                            if (data.status === 'done') {
                                clearInterval(timer);
                                self.version = data.version;
                                await self._loadUml();
                                self.phase = Math.max(self.phase, 2);
                                self._setSuccess(`UML generated — ${data.element_count || 0} ArchiMate elements processed.`);
                                self.enriching = false;
                                resolve();
                            } else if (data.status === 'failed') {
                                clearInterval(timer);
                                self._addError('Enrich failed: ' + (data.error || 'Unknown error'));
                                self.enriching = false;
                                reject(new Error(data.error));
                            } else if (++polls >= max) {
                                clearInterval(timer);
                                self._addError('Enrich timed out after 8 minutes');
                                self.enriching = false;
                                reject(new Error('timeout'));
                            }
                        } catch (e) {
                            // A single transient blip must not abort a long generation,
                            // so we keep polling — but the attempt still has to count.
                            // Previously `polls` only advanced on a SUCCESSFUL response,
                            // so an endpoint that failed every time polled forever and
                            // left the user watching a spinner that could never resolve.
                            if (++polls >= max) {
                                clearInterval(timer);
                                self._addError('Lost contact with the server while generating UML: '
                                    + (e.message || 'network error') + '. The job may still be running — reload to check.');
                                self.enriching = false;
                                reject(e);
                            }
                        }
                    }, 8000);
                });
            },

            async _loadUml() {
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/uml`);
                    this.uml = data.uml;
                    this.version = data.version;
                } catch (e) {
                    this._addError('Could not load UML: ' + e.message);
                }
            },

            /* ── Apply confirmed specs → lock into UML class diagram ── */
            async applySpecs() {
                if (!this.uml) {
                    this._addError('Run Phase 1 enrichment first to generate a UML snapshot.');
                    return;
                }
                this.applyingSpecs = true;
                this.errors = [];
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/apply-specs`,
                        { method: 'POST', body: {} }
                    );
                    this.version = data.version;
                    await this._loadUml();
                    this._setSuccess(
                        `Confirmed specs locked — ${data.applied_classes} class${data.applied_classes !== 1 ? 'es' : ''} now use architect-verified field definitions.`
                    );
                } catch (e) {
                    this._addError('Apply specs failed: ' + e.message);
                } finally {
                    this.applyingSpecs = false;
                }
            },

            /* ── Phase 2: Review ── */
            async resetUml() {
                if (!(await Platform.modal.confirm('Re-enrich will discard all UML edits and regenerated code. Continue?'))) return;
                this.resetting = true;
                try {
                    await this._fetch(
                        `/solutions/${this.solutionId}/codegen/uml/reset`,
                        { method: 'POST', body: {} }
                    );
                    this.uml = null;
                    this.generatedFiles = {};
                    this.fileList = [];
                    this.selectedFile = '';
                    this.selectedContent = '';
                    this.githubUrl = '';
                    this.phase = 1;
                    this._setSuccess('Reset complete. Run Enrich again.');
                } catch (e) {
                    this._addError('Reset failed: ' + e.message);
                } finally {
                    this.resetting = false;
                }
            },

            skipToConfig() {
                if (this.phase >= 2) {
                    if (this.umlClasses.length > 0 && this.unconfirmedCount > 0) {
                        this._addError(
                            this.unconfirmedCount + ' of ' + this.umlClasses.length +
                            ' entities have unconfirmed fields. The LLM will invent schemas for these at generation time. ' +
                            'Return to Phase 2 and click "Confirm Fields" to lock them in.'
                        );
                    }
                    this.phase = Math.max(this.phase, 3);
                }
            },

            showDiagram() {
                this.diagramView = true;
                this.$nextTick(function() {
                    let container = document.getElementById('uml-canvas');
                    if (container && this.uml && this.uml.class_diagram && !this._diagramRendered) {
                        if (window.UMLShapes) {
                            UMLShapes.renderClassDiagram(container, this.uml.class_diagram, {
                                onClassClick: function(cls) { /* could show detail panel */ }
                            });
                            this._diagramRendered = true;
                        }
                    }
                }.bind(this));
            },

            showTable() {
                this.diagramView = false;
            },

            /* ── Phase 3: Configure ── */
            async saveConfig() {
                this.savingConfig = true;
                try {
                    const payload = {
                        ...this.config,
                        ...this.normalizedSalesforceConfig(),
                    };
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/config`,
                        { method: 'PUT', body: payload }
                    );
                    this.version = data.version;
                    this.phase = Math.max(this.phase, 4);
                    this._setSuccess('Configuration saved.');
                } catch (e) {
                    this._addError('Save config failed: ' + e.message);
                } finally {
                    this.savingConfig = false;
                }
            },

            /* ── Phase 4: Generate (SSE streaming) ── */
            async generate() {
                // Hard gate: stale blueprint means enrichment hasn't seen latest architectural decisions.
                // Genome mode compiles directly from ArchiMate elements — it does NOT use the UML/enrichment
                // blueprint, so the staleness check is not applicable there.
                if (this.blueprintStale && this.config.generation_mode !== 'genome') {
                    this._addError('Blueprint updated since last enrichment. Re-run Phase 1 (Enrich) first — architectural decisions (security, NFRs, deployment) need to be reflected in the UML before generating.');
                    return;
                }

                // Clear previous errors, then add any soft warnings
                // Guard: warn before wiping manual edits
                if (this.editedFiles.length > 0 || Object.keys(this.dirtyFiles).length > 0) {
                    const editCount = new Set([...this.editedFiles, ...Object.keys(this.dirtyFiles)]).size;
                    if (!(await Platform.modal.confirm(
                        `Re-generating will overwrite ${editCount} manually edited file(s).\n\n` +
                        `Use the AI Chat or "Edit with AI" to make targeted changes instead, ` +
                        `or download a ZIP first to preserve your edits.\n\nContinue?`
                    ))) return;
                }

                this.errors = [];
                this.generatedFiles = {};
                this.fileList = [];
                this.selectedFile = '';
                this.selectedContent = '';
                this.openTabs = [];
                this.dirtyFiles = {};
                this.editedFiles = [];

                // Soft warn: unconfirmed field schemas will be LLM-invented (auto-dismisses)
                if (this.umlClasses.length > 0 && this.unconfirmedCount > 0) {
                    this._addError(
                        this.unconfirmedCount + ' of ' + this.umlClasses.length +
                        ' entities have unconfirmed field schemas — the LLM will invent fields for these. ' +
                        'Use Phase 2 → Confirm Fields to lock in accurate schemas. Dismiss to generate anyway.',
                        true
                    );
                }

                this.generating = true;
                this.generateLabel = 'Starting…';
                this.generateElapsed = 0;
                this.promptGroups.forEach(k => { this.promptGroupStatus[k] = 'pending'; });

                // Start elapsed-time counter so user can see real work is happening
                this._generateTimer = setInterval(() => { this.generateElapsed++; }, 1000);

                // SSE phase → display groups mapping (1:many — one backend phase covers multiple rows)
                const phaseToGroups = {
                    deterministic: ['models', 'schemas', 'routes'],
                    enrichment:    ['services'],
                    documentation: ['infrastructure', 'integrations'],
                    validation:    ['tests'],
                };

                try {
                    // SSE streaming response requires raw fetch to read stream.
                    const resp = await fetch(  // raw-fetch-ok: SSE stream; needs body.getReader() on the raw Response
                        `/solutions/${this.solutionId}/codegen/generate-stream`,
                        {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': this._csrfToken(),
                            },
                            body: JSON.stringify({
                                version: this.version,
                                generation_policy: this.config.generation_policy || 'scaffold',
                                generation_mode: this.config.generation_mode || 'genome',
                                language: this.config.language || 'python-fastapi',
                                ui_framework: this.config.ui_framework || 'none',
                                // PROG-014: set by "Generate anyway" after a conformance block
                                override_conformance: !!this._overrideConformance,
                                ...this.normalizedSalesforceConfig(),
                            }),
                        }
                    ); // raw-fetch-ok: SSE streaming response requires raw fetch to read stream.

                    if (!resp.ok) {
                        // Non-streaming error (e.g. 400 / 409)
                        const err = await resp.json().catch(() => ({ error: resp.statusText }));
                        throw new Error(err.error || resp.statusText);
                    }

                    const reader = resp.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = '';
                    const SSE_MAX = 512 * 1024; // 512KB safety cap

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buffer += decoder.decode(value, { stream: true });
                        if (buffer.length > SSE_MAX) buffer = buffer.slice(-SSE_MAX);
                        // SSE lines end with \n\n; process complete events
                        const parts = buffer.split('\n\n');
                        buffer = parts.pop(); // keep incomplete tail
                        for (const part of parts) {
                            const dataLine = part.split('\n').find(l => l.startsWith('data: '));
                            if (!dataLine) continue;
                            let event;
                            try { event = JSON.parse(dataLine.slice(6)); } catch { continue; }

                            if (event.phase === 'auto_verify') {
                                // Auto-verify test results from Docker
                                if (event.status === 'done' && event.result) {
                                    const av = event.result;
                                    const s = av.summary || {};
                                    if (av.pass_rate !== null && av.pass_rate !== undefined) {
                                        this._setSuccess(
                                            `Tests: ${s.passed || 0} passed, ${s.failed || 0} failed (${av.pass_rate}% pass rate)`
                                        );
                                        if (s.failed > 0 || s.errors > 0) {
                                            this._addError(
                                                `${s.failed + (s.errors || 0)} test(s) failed — use AI Edit or fix manually.`
                                            );
                                        }
                                    }
                                } else if (event.status === 'skipped') {
                                    // Docker not available or verify failed — not a blocking issue
                                }
                                this.generateLabel = 'Verifying tests…';
                                continue;
                            }

                            if (event.phase === 'conformance') {
                                // PROG-014: architectural-conformance gate result.
                                if (event.status === 'error') {
                                    this.generateLabel = 'Blocked — conformance';
                                    const link = event.review_packet_url
                                        ? ' Open the review packet: ' + location.origin + event.review_packet_url
                                        : '';
                                    this._addError('Architecture conformance: ' + (event.error || 'blocked') + link);
                                    this._pendingConformanceOverride = true;
                                    this._conformanceMessage = event.error || '';
                                } else if (event.status === 'warning') {
                                    this._addError('Conformance: ' + (event.message || 'findings need attention'), true);
                                }
                                continue;
                            }

                            if (event.phase === 'complete') {
                                const data = event.result || {};
                                // Server sends errors at event.error (no result wrapper) or event.result.error
                                const _success = data.success !== undefined ? data.success : event.success;
                                const _error = data.error || event.error || 'Unknown error';
                                if (!_success) {
                                    this.generateLabel = 'Failed';
                                    this._addError('Generation failed: ' + _error);
                                    break;
                                }
                                // Mark all groups done
                                this.generateLabel = 'Complete';
                                this.promptGroups.forEach(k => { this.promptGroupStatus[k] = 'done'; });

                                this.version = data.version;
                                this.fileList = data.files || [];
                                if (data.chain_completeness !== undefined) this.chainCompleteness = data.chain_completeness;
                                await this._loadFiles();
                                if (data.errors && data.errors.length)
                                    data.errors.forEach(e => this._addError(`${e.prompt}: ${e.error}`));
                                if (data.completeness_warning) this._addError(data.completeness_warning);
                                if (data.secret_warnings && data.secret_warnings.length)
                                    data.secret_warnings.forEach(w => this._addError('Secret scan: ' + w));
                                if (data.syntax_warnings && data.syntax_warnings.length)
                                    data.syntax_warnings.forEach(w => this._addError('Import: ' + w));
                                if (data.version_label) this.versionLabel = data.version_label;
                                if (data.impact) this.lastImpact = data.impact;
                                if (data.quality_score !== undefined && data.quality_score !== null) {
                                    this.qualityScore = data.quality_score;
                                    this.qualityDetails = data.quality_details || null;
                                    this.qualityOpen = true;
                                }
                                this.loadHistory();
                                this._setSuccess(`Generated ${data.file_count} files (${data.version_label || 'v1'}).`);
                            } else if (event.phase && event.status) {
                                // Progress event — update all display groups for this phase
                                if (event.label) this.generateLabel = event.label;
                                const grps = phaseToGroups[event.phase] || [];
                                for (const grp of grps) {
                                    if (event.status === 'running') {
                                        this.promptGroupStatus[grp] = 'running';
                                    } else if (event.status === 'done' || event.status === 'skipped') {
                                        this.promptGroupStatus[grp] = 'done';
                                    } else if (event.status === 'error') {
                                        this.promptGroupStatus[grp] = 'error';
                                    }
                                }
                            }
                        }
                    }
                } catch (e) {
                    this.generateLabel = 'Failed';
                    this.promptGroups.forEach(k => {
                        if (this.promptGroupStatus[k] === 'running') this.promptGroupStatus[k] = 'error';
                    });
                    this._addError('Generation failed: ' + e.message);
                } finally {
                    clearInterval(this._generateTimer);
                    this._generateTimer = null;
                    this.generating = false;
                    this.ideShowSetup = false;  // switch back to IDE view after generation
                    this._overrideConformance = false;  // PROG-014: one-shot override
                }

                // PROG-014: after a conformance block, offer to generate anyway.
                if (this._pendingConformanceOverride) {
                    this._pendingConformanceOverride = false;
                    if ((await Platform.modal.confirm(
                        'This blueprint has critical architecture-conformance issues:\n\n' +
                        (this._conformanceMessage || '') +
                        '\n\nGenerating ships the defect. Open the review packet to fix it, ' +
                        'or click OK to generate anyway.'
                    ))) {
                        this._overrideConformance = true;
                        this.generate();
                    }
                }
            },

            /* ── Test execution (Docker verify) ── */
            async verify() {
                this.verifying = true;
                this.verifyResult = null;
                this.verifyLog = [];

                const phaseLabel = {
                    setup: 'Writing files…',
                    build: 'Building Docker image…',
                    test: 'Running pytest…',
                    teardown: 'Cleaning up…',
                };

                try {
                    // SSE streaming response requires raw fetch to read stream.
                    const resp = await fetch(  // raw-fetch-ok: SSE stream; needs body.getReader() on the raw Response
                        `/solutions/${this.solutionId}/codegen/verify`,
                        {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': this._csrfToken(),
                            },
                            body: JSON.stringify({}),
                        }
                    ); // raw-fetch-ok: SSE streaming response requires raw fetch to read stream.
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({ error: resp.statusText }));
                        throw new Error(err.error || resp.statusText);
                    }

                    const reader = resp.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = '';
                    const VERIFY_SSE_MAX = 256 * 1024; // 256KB cap for verify stream

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buffer += decoder.decode(value, { stream: true });
                        if (buffer.length > VERIFY_SSE_MAX) buffer = buffer.slice(-VERIFY_SSE_MAX);
                        const parts = buffer.split('\n\n');
                        buffer = parts.pop();
                        for (const part of parts) {
                            const dataLine = part.split('\n').find(l => l.startsWith('data: '));
                            if (!dataLine) continue;
                            let event;
                            try { event = JSON.parse(dataLine.slice(6)); } catch { continue; }

                            if (event.line) {
                                this.verifyLog.push(event.line);
                                if (this.verifyLog.length > 100) this.verifyLog = this.verifyLog.slice(-50);
                            }
                            if (event.phase === 'complete') {
                                this.verifyResult = event;
                                if (event.success) {
                                    const s = event.summary || {};
                                    this._setSuccess(
                                        `Tests: ${s.passed || 0} passed, ${s.failed || 0} failed` +
                                        (s.errors ? `, ${s.errors} errors` : '') +
                                        ` — ${event.pass_rate}% pass rate`
                                    );
                                    // Reload quality score to show real test_coverage
                                    this.loadQuality();
                                } else {
                                    this._addError('Verify failed: ' + (event.message || event.error || 'Unknown error'));
                                }
                            }
                        }
                    }
                } catch (e) {
                    this._addError('Verify failed: ' + e.message);
                } finally {
                    this.verifying = false;
                }
            },

            /* ── Blueprint violation one-click remediation ── */
            async patchViolation(constraint) {
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/patch-violation`,
                        { method: 'POST', body: { constraint } }
                    );
                    if (data.success) {
                        this._setSuccess(data.message);
                        if (data.needs_regen) {
                            // Reload quality details so the patched violation reflects in UI
                            this.loadQuality();
                        }
                    } else {
                        this._addError(data.message || 'Patch failed');
                    }
                } catch (e) {
                    this._addError('Patch failed: ' + e.message);
                }
            },

            /* Shared SSE+patch consumer for Fix-with-AI flows.
             * stream_chat_edit yields:  event: patch\ndata: {file,diff}\n\n
             * The event type is in the "event:" line, NOT in the data JSON body.
             * We track currentEventType across lines so we know what each data payload means. */
            async _streamAndApplyPatches(url, body) {
                const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
                let patchesApplied = 0;
                let errorMsg = '';
                const patchFailures = [];

                // SSE streaming response requires raw fetch to read stream.
                const resp = await fetch(url, {  // raw-fetch-ok: SSE stream; needs body.getReader() on the raw Response
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
                    body: JSON.stringify(body),
                }); // raw-fetch-ok: SSE streaming response requires raw fetch to read stream.

                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.error || `HTTP ${resp.status}`);
                }

                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let currentEventType = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });

                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        if (line.startsWith('event:')) {
                            currentEventType = line.slice(6).trim();
                            continue;
                        }
                        if (line === '') { currentEventType = ''; continue; }
                        if (!line.startsWith('data:')) continue;

                        let payload;
                        try { payload = JSON.parse(line.slice(5).trim()); } catch { continue; }

                        if (currentEventType === 'patch' && payload.file && payload.diff) {
                            try {
                                // This is a regular JSON endpoint, not SSE, so we can use Platform.fetch.
                                // The caller already paints its own error state, so we pass { silent: true }.
                                const applyData = await Platform.fetch(
                                    `/solutions/${this.solutionId}/codegen/chat-edit/apply-patch`,
                                    {
                                        method: 'POST',
                                        body: { file: payload.file, diff: payload.diff },
                                        silent: true,
                                    }
                                );
                                // Platform.fetch throws on non-ok, so reaching here means success.
                                patchesApplied++;
                                const newContent = applyData.content || applyData.new_content;
                                if (newContent) {
                                    this.generatedFiles[payload.file] = newContent;
                                    if (this.selectedFile === payload.file && this._editorView) {
                                        const { EditorState } = await import('/@codemirror/state');
                                        this._editorView.setState(
                                            EditorState.create({ doc: newContent, extensions: this._editorView.state.facet(EditorState.extensions) })
                                        );
                                    }
                                }
                            } catch (e) {
                                // The AI produced a patch and writing it FAILED. Swallowed,
                                // this left patchesApplied at 0 and errorMsg empty, so both
                                // callers rendered "No patches needed" / "No changes needed"
                                // — telling the user their code was already fine when in
                                // fact nothing was written to it.
                                patchFailures.push(payload.file + ': ' + (e.message || 'apply failed'));
                            }
                        } else if (currentEventType === 'error') {
                            errorMsg = payload.message || 'AI error';
                        }
                        currentEventType = '';
                    }
                }

                if (!errorMsg && patchFailures.length) {
                    errorMsg = patchFailures.length + ' patch'
                        + (patchFailures.length > 1 ? 'es' : '')
                        + ' could not be applied — ' + patchFailures[0];
                }

                return { patchesApplied, errorMsg };
            },

            async fixDimension(key, label) {
                if (this.dimFix[key] && this.dimFix[key].loading) return;
                this.dimFix = { ...this.dimFix, [key]: { loading: true, message: `Asking AI to fix ${label}…`, error: false } };

                let patchesApplied = 0;
                let errorMsg = '';
                try {
                    ({ patchesApplied, errorMsg } = await this._streamAndApplyPatches(
                        `/solutions/${this.solutionId}/codegen/fix-dimension`,
                        { dimension: key }
                    ));
                } catch (e) {
                    errorMsg = e.message || 'Fix failed';
                }

                await this.loadQuality();

                const msg = errorMsg
                    ? `Fix failed: ${errorMsg}`
                    : patchesApplied > 0
                        ? `Applied ${patchesApplied} fix${patchesApplied > 1 ? 'es' : ''} — quality refreshed`
                        : 'No patches needed';
                this.dimFix = { ...this.dimFix, [key]: { loading: false, message: msg, error: !!errorMsg } };
            },

            async fixRecommendation(rec, idx) {
                if (this.recFix[idx] && this.recFix[idx].loading) return;
                this.recFix = { ...this.recFix, [idx]: { loading: true, message: 'Asking AI to implement…', error: false } };

                // Support both plain strings (legacy) and structured {text, dimension, icon} objects
                const recText = typeof rec === 'string' ? rec : (rec.text || String(rec));
                const recDimension = rec && rec.dimension;

                let patchesApplied = 0;
                let errorMsg = '';
                try {
                    // Use fix-dimension when the rec has a known dimension key (more targeted)
                    const endpoint = recDimension
                        ? `/solutions/${this.solutionId}/codegen/fix-dimension`
                        : `/solutions/${this.solutionId}/codegen/fix-recommendation`;
                    const payload = recDimension
                        ? { dimension: recDimension }
                        : { recommendation: recText };
                    ({ patchesApplied, errorMsg } = await this._streamAndApplyPatches(endpoint, payload));
                } catch (e) {
                    errorMsg = e.message || 'Fix failed';
                }

                await this.loadQuality();

                const msg = errorMsg
                    ? `Failed: ${errorMsg}`
                    : patchesApplied > 0
                        ? `Applied ${patchesApplied} change${patchesApplied > 1 ? 's' : ''}`
                        : 'No changes needed';
                this.recFix = { ...this.recFix, [idx]: { loading: false, message: msg, error: !!errorMsg } };
            },

            /* Max files to keep in memory at once — evict LRU when exceeded */
            _FILE_CACHE_MAX: 15,
            _fileCacheOrder: [],

            _evictLruFiles() {
                // Keep only the most recently accessed files in generatedFiles
                while (this._fileCacheOrder.length > this._FILE_CACHE_MAX) {
                    const oldest = this._fileCacheOrder.shift();
                    // Never evict dirty files or the currently selected file
                    if (this.dirtyFiles[oldest] || oldest === this.selectedFile) {
                        this._fileCacheOrder.push(oldest);
                        continue;
                    }
                    delete this.generatedFiles[oldest];
                }
            },

            _touchFileCache(path) {
                const idx = this._fileCacheOrder.indexOf(path);
                if (idx !== -1) this._fileCacheOrder.splice(idx, 1);
                this._fileCacheOrder.push(path);
                this._evictLruFiles();
            },

            async _loadFiles() {
                // Only fall back to initialData when fileList is empty (e.g. page reload).
                // Do NOT overwrite a freshly-generated fileList from generate().
                if (!this.fileList.length && initialData.fileList && initialData.fileList.length) {
                    this.fileList = initialData.fileList;
                }
                // Sync fileList from server — also covers the case where fileList
                // was not seeded from initialData (e.g. hasFiles=true but list missing).
                if (initialData.hasFiles && Object.keys(this.generatedFiles).length === 0) {
                    try {
                        let data = await this._fetch('/solutions/' + this.solutionId + '/codegen/file-list');
                        if (data.files) {
                            const serverPaths = data.files.sort();
                            if (serverPaths.length > this.fileList.length) {
                                this.fileList = serverPaths;
                            }
                        }
                    } catch (e) {
                        // Platform.fetch surfaced this failure before it threw; the
                        // catch only stops it becoming an unhandled rejection.
                    }
                }
                // Auto-open the most useful file on first load. Deferred so Alpine finishes
                // rendering the file tree before we trigger CM6 editor mount.
                if (this.fileList.length && !this.selectedFile) {
                    const preferred = ['README.md', 'app/main.py', 'app/__init__.py', 'main.go', 'Main.java', 'index.js', 'index.ts'];
                    let pick = preferred.find(p => this.fileList.includes(p));
                    if (!pick) {
                        pick = this.fileList.find(p => p.startsWith('app/api') || p.startsWith('app/routes')) ||
                               this.fileList.find(p => p.startsWith('app/models')) ||
                               this.fileList[0];
                    }
                    // Defer selectFile() so Alpine finishes rendering before CM6 mounts.
                    const self = this;
                    setTimeout(() => self.selectFile(pick), 100);
                }
                // Expand all top-level dirs in IDE file tree
                this.fileList.forEach(p => {
                    let parts = p.split('/');
                    if (parts.length > 1) {
                        this.expandedDirs[parts[0]] = true;
                    }
                });
            },

            async regenerate(groupKey) {
                const prev = this.promptGroupStatus[groupKey];
                this.promptGroupStatus[groupKey] = 'running';
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/regenerate`,
                        { method: 'POST', body: { file_key: groupKey, version: this.version } }
                    );
                    this.promptGroupStatus[groupKey] = 'done';
                    this.version = data.version;
                    // Merge regenerated files into list
                    if (data.files) {
                        data.files.forEach(f => {
                            if (!this.fileList.includes(f)) this.fileList.push(f);
                        });
                        this.fileList.sort();
                    }
                    this._setSuccess(`Regenerated: ${data.files ? data.files.join(', ') : groupKey}`);
                } catch (e) {
                    this.promptGroupStatus[groupKey] = 'error';
                    this._addError(`Regenerate ${groupKey} failed: ` + e.message);
                }
            },

            async selectFile(path) {
                // If the target file is beyond the 60-file render cap, expand tree so it's visible
                const catFiles = this.groupedFiles[this._fileCategory(path)] || [];
                if (!this.fileTreeExpanded && catFiles.indexOf(path) >= 60) {
                    this.fileTreeExpanded = true;
                }
                // Preserve unsaved edits in memory before switching files
                if (this.editorDirty && this.cmEditor && this.selectedFile && this.selectedFile !== path) {
                    this.generatedFiles[this.selectedFile] = this.cmEditor.state.doc.toString();
                }
                this.selectedFile = path;
                this.patchDiff = null;  // clear any pending diff on file switch
                this.editorDirty = !!this.dirtyFiles[path];
                // Track open tabs (max 12, deduplicated)
                let name = path.split('/').pop();
                if (!this.openTabs.find(t => t.path === path)) {
                    this.openTabs.push({ path, name });
                    if (this.openTabs.length > 12) this.openTabs.shift();
                }
                let content;
                if (this.generatedFiles[path]) {
                    content = this.generatedFiles[path];
                    this._touchFileCache(path);
                } else {
                    content = '// Loading…';
                    this._updateEditor(path, content);
                    try {
                        let data = await this._fetch(
                            '/solutions/' + this.solutionId + '/codegen/file-content?path=' + encodeURIComponent(path)
                        );
                        this.generatedFiles[path] = data.content;
                        this._touchFileCache(path);
                        content = data.content;
                    } catch (e) {
                        content = '// Could not load file: ' + e.message;
                    }
                }
                this.selectedContent = content;
                this._updateEditor(path, content);
            },

            /* ── CodeMirror 6 helpers ── */

            _cmLang(path) {
                const filename = (path || '').split('/').pop().toLowerCase();
                const ext = filename.split('.').pop();
                if (!window.__cm6langs) return [];
                const factory = window.__cm6langs[ext];
                try { return factory ? factory() : []; }
                catch(_) { /* swallow-ok: CodeMirror language-mode probe, not a network call; an empty extension list only costs syntax highlighting and the file's text is displayed unchanged either way */ return []; }
            },

            _cmAppTheme() {
                const cm = window.__cm6;
                if (!cm) return [];
                return cm.EditorView.theme({
                    '&': { height: '100%', fontSize: '12px' },
                    '.cm-scroller': { fontFamily: "'JetBrains Mono','Cascadia Code',ui-monospace,monospace", overflow: 'auto' },
                    '.cm-focused': { outline: 'none' },
                    '.cm-gutters': { backgroundColor: 'hsl(var(--muted))', color: 'hsl(var(--muted-foreground))', borderRight: '1px solid hsl(var(--border))' },
                    '.cm-activeLineGutter': { backgroundColor: 'hsl(var(--primary)/.05)' },
                    '.cm-activeLine': { backgroundColor: 'hsl(var(--primary)/.03)' },
                    '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'hsl(var(--primary)/.25) !important' },
                    '.cm-cursor': { borderLeftColor: 'hsl(var(--primary))' },
                });
            },

            _updateEditor(path, content) {
                const self = this;
                const doInit = function() {
                    const container = document.getElementById('cm-editor-container');
                    if (!container) return;
                    const cm = window.__cm6;
                    if (!cm) { self.selectedContent = content; return; }

                    if (!self._cmLangCompartment) {
                        self._cmLangCompartment = new cm.Compartment();
                    }

                    if (!self.cmEditor) {
                        const langExt = self._cmLang(path);
                        const state = cm.EditorState.create({
                            doc: content,
                            extensions: [
                                cm.basicSetup,
                                self._cmLangCompartment.of(langExt),
                                cm.keymap.of([
                                    ...cm.defaultKeymap,
                                    ...cm.historyKeymap,
                                    cm.indentWithTab,
                                    { key: 'Mod-s', run: function() { self.saveFile(); return true; } },
                                ]),
                                cm.EditorView.updateListener.of(function(update) {
                                    if (update.docChanged && !self._suppressDirty) {
                                        self.editorDirty = true;
                                        if (self.selectedFile) self.dirtyFiles[self.selectedFile] = true;
                                        self.selectedContent = update.state.doc.toString();
                                    }
                                    if (update.selectionSet && !update.docChanged) {
                                        self._onCMSelection(update.view);
                                    }
                                }),
                                self._cmAppTheme(),
                            ],
                        });
                        self.cmEditor = new cm.EditorView({ state, parent: container });
                    } else {
                        // File switch: update language and content atomically.
                        // _suppressDirty prevents the updateListener from marking this
                        // programmatic content load as a user edit.
                        const langExt = self._cmLang(path);
                        self._suppressDirty = true;
                        self.cmEditor.dispatch({
                            changes: { from: 0, to: self.cmEditor.state.doc.length, insert: content },
                            effects: self._cmLangCompartment.reconfigure(langExt),
                            selection: { anchor: 0 },
                        });
                        self._suppressDirty = false;
                        self.cmEditor.scrollDOM.scrollTop = 0;
                        self.aiSelectionMenu = false;
                    }
                    self.editorDirty = !!self.dirtyFiles[path];
                };

                if (window.__cmReady) {
                    doInit();
                } else {
                    document.addEventListener('codemirror:ready', doInit, { once: true });
                }
            },

            _onCMSelection(view) {
                const {from, to} = view.state.selection.main;
                if (from === to) { this.aiSelectionMenu = false; return; }
                this.aiSelectionFrom = from;
                this.aiSelectionTo = to;
                this.aiSelectionMenu = true;
                // Position toolbar just above the end of the selection
                const coords = view.coordsAtPos(to);
                if (coords) {
                    const rect = view.dom.getBoundingClientRect();
                    this.aiMenuTop = Math.max(0, coords.top - rect.top - 38);
                    this.aiMenuLeft = Math.min(coords.left - rect.left, rect.width - 260);
                }
            },

            async aiEditSelection(mode) {
                if (!this.cmEditor || this.aiSelectionFrom === this.aiSelectionTo) return;
                const from = this.aiSelectionFrom, to = this.aiSelectionTo;
                const selectedText = this.cmEditor.state.sliceDoc(from, to);
                let instruction;
                if (mode === 'edit') {
                    instruction = await Platform.modal.promptText('What do you want to do with this code?', {
                        title: 'Edit with AI', defaultValue: 'Add error handling'
                    });
                    if (!instruction) return;
                } else if (mode === 'fix') {
                    instruction = 'Fix any errors or issues in this code snippet';
                } else {
                    instruction = 'Refactor this code snippet: improve readability, add type hints, better variable names';
                }
                this.aiSelectionMenu = false;
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/patch`,
                        { method: 'POST', body: {
                            path: this.selectedFile,
                            instruction: instruction + '\n\nApply change ONLY to this exact snippet — return the replacement code with no explanation or markdown:\n\n' + selectedText,
                        } }
                    );
                    if (data.new_content) {
                        // Replace selection range with AI result
                        this.cmEditor.dispatch({ changes: { from, to, insert: data.new_content } });
                        this.editorDirty = true;
                        if (this.selectedFile) this.dirtyFiles[this.selectedFile] = true;
                        this._setSuccess('AI edit applied');
                    }
                } catch(e) {
                    this._addError('AI edit failed: ' + e.message);
                }
            },

            /* ── Save file ── */

            async saveFile() {
                if (!this.selectedFile || this.fileSaving) return;
                let content = this.cmEditor ? this.cmEditor.state.doc.toString() : this.selectedContent;
                this.fileSaving = true;
                try {
                    let data = await this._fetch(
                        '/solutions/' + this.solutionId + '/codegen/files',
                        { method: 'PATCH', body: { path: this.selectedFile, content: content } }
                    );
                    this.generatedFiles[this.selectedFile] = content;
                    this.selectedContent = content;
                    this.version = data.version;
                    this.editorDirty = false;
                    delete this.dirtyFiles[this.selectedFile];
                    if (!this.editedFiles.includes(this.selectedFile)) {
                        this.editedFiles = this.editedFiles.concat([this.selectedFile]);
                    }
                    this._setSuccess('Saved ' + this.selectedFile.split('/').pop());
                } catch (e) {
                    this._addError('Save failed: ' + e.message);
                } finally {
                    this.fileSaving = false;
                }
            },

            /* ── Patch mode (AI single-file edit) ── */

            async patchFile() {
                if (!this.selectedFile || !this.patchInstruction.trim() || this.patchRunning) return;
                this.patchRunning = true;
                this.patchDiff = null;
                try {
                    let data = await this._fetch(
                        '/solutions/' + this.solutionId + '/codegen/patch',
                        { method: 'POST', body: {
                            path: this.selectedFile,
                            instruction: this.patchInstruction.trim()
                        } }
                    );
                    this.patchDiff = { path: data.path, old: data.old_content, new: data.new_content };
                } catch (e) {
                    this._addError('AI edit failed: ' + e.message);
                } finally {
                    this.patchRunning = false;
                }
            },

            confirmPatch() {
                if (!this.patchDiff) return;
                const newContent = this.patchDiff.new;
                let path = this.patchDiff.path;
                this.generatedFiles[path] = newContent;
                this.selectedContent = newContent;
                if (this.cmEditor && path === this.selectedFile) {
                    this._suppressDirty = true;
                    this.cmEditor.dispatch({
                        changes: { from: 0, to: this.cmEditor.state.doc.length, insert: newContent },
                        selection: { anchor: 0 },
                    });
                    this._suppressDirty = false;
                }
                this.patchDiff = null;
                this.patchInstruction = '';
                this.patchOpen = false;
                this.editorDirty = true;
                // Auto-save the patched content
                this.saveFile();
            },

            async genomePatch() {
                if (!this.genomePatchInstruction.trim() || this.genomePatchRunning) return;
                // Genome is fetched lazily on first use — not inlined in HTML
                const genome = await this._fetchGenome();
                if (!Object.keys(genome).length) {
                    this._addError('No genome found for this generation. Use genome mode to generate first.');
                    return;
                }
                this.genomePatchRunning = true;
                this.genomePatchResult = null;
                try {
                    const data = await this._fetch('/api/codegen/genome/patch', {
                        method: 'POST',
                        body: {
                            genome: genome,
                            nl_instruction: this.genomePatchInstruction.trim()
                        }
                    });
                    this.genomePatchResult = data;
                } catch (e) {
                    this._addError('Genome patch failed: ' + e.message);
                } finally {
                    this.genomePatchRunning = false;
                }
            },

            async applyGenomePatch() {
                if (!this.genomePatchResult || !this.genomePatchResult.patch_ops) return;
                this.genomePatchApplying = true;
                try {
                    // Step 1: apply patch to get updated genome
                    const patchResult = await this._fetch('/api/codegen/genome/patch/apply', {
                        method: 'POST',
                        body: {
                            genome: window.__codegenGenome || {},
                            patch_ops: this.genomePatchResult.patch_ops
                        }
                    });
                    if (!patchResult.success) throw new Error(patchResult.error || 'Patch apply failed');
                    // Update the non-reactive genome reference
                    window.__codegenGenome = patchResult.genome;

                    // Step 2: persist patched genome to DB
                    await this._fetch('/solutions/' + this.solutionId + '/codegen/genome-patch/store', {
                        method: 'POST',
                        body: { genome: patchResult.genome }
                    });

                    // Step 3: trigger full regeneration (uses stored genome)
                    this.generate();

                    this.genomePatchResult = null;
                    this.genomePatchInstruction = '';
                } catch (e) {
                    this._addError('Apply failed: ' + e.message);
                } finally {
                    this.genomePatchApplying = false;
                }
            },

            /* ── Deploy ── */
            githubPrUrl: '',

            async deploy() {
                if (!(this.config && this.config.repo_name && this.config.repo_name.trim())) {
                    this._addError('Set a repository name in Setup before pushing to GitHub.');
                    this.ideShowSetup = true;
                    return;
                }
                this.deploying = true;
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/push-to-git`,
                        { method: 'POST', body: { version: this.version } }
                    );
                    this.githubUrl = data.github_url;
                    this.githubPrUrl = data.pr_url || '';
                    this.githubSha = data.commit_sha;
                    if (data.warning) this._addError(data.warning);
                    this._setSuccess(data.pr_url ? 'Pull request created on GitHub.' : 'Pushed to GitHub successfully.');
                } catch (e) {
                    this._addError('GitHub deploy failed: ' + e.message);
                } finally {
                    this.deploying = false;
                }
            },

            download() {
                window.location.href = `/solutions/${this.solutionId}/codegen/download`;
            },

            /* ── GAP-04: History ── */
            async loadHistory() {
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/history`);
                    this.history = data.history || [];
                    // Set versionLabel from latest entry if not already set by generate()
                    if (!this.versionLabel && this.history.length > 0 && this.history[0].version) {
                        this.versionLabel = this.history[0].version;
                    }
                } catch (_) {
                    this.history = [];
                }
            },

            /* ── IDE tab management ── */
            closeTab(path) {
                const idx = this.openTabs.findIndex(t => t.path === path);
                this.openTabs = this.openTabs.filter(t => t.path !== path);
                if (this.selectedFile === path) {
                    if (this.openTabs.length > 0) {
                        const next = this.openTabs[Math.min(idx, this.openTabs.length - 1)];
                        this.selectFile(next.path);
                    } else {
                        this.selectedFile = '';
                        this.selectedContent = '';
                        if (this.cmEditor) this.cmEditor.dispatch({ changes: { from: 0, to: this.cmEditor.state.doc.length, insert: '' } });
                    }
                }
            },

            async deleteFile(path) {
                if (!(await Platform.modal.confirm(`Delete "${path}" from generated code? This cannot be undone.`))) return;
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/files`, {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrfToken() },
                        body: { path },
                    });
                    this.closeTab(path);
                    this.fileList = this.fileList.filter(f => f !== path);
                    delete this.generatedFiles[path];
                    delete this.dirtyFiles[path];
                    this.version = data.version;
                    this._setSuccess(`Deleted ${path}`);
                } catch (e) {
                    this._addError(`Failed to delete ${path}: ${e.message || e}`);
                }
            },

            async createFile() {
                const path = await Platform.modal.promptText('New file path (e.g. app/utils/helpers.py):', {
                    title: 'New file'
                });
                if (!path || !path.trim()) return;
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/files/create`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrfToken() },
                        body: { path: path.trim(), content: '' },
                    });
                    this.fileList.push(path.trim());
                    this.fileList.sort();
                    this.version = data.version;
                    this.selectFile(path.trim());
                    this._setSuccess(`Created ${path.trim()}`);
                } catch (e) {
                    this._addError(`Failed to create file: ${e.message || e}`);
                }
            },

            async renameFile(oldPath) {
                const p = oldPath || this.selectedFile;
                if (!p) return;
                const newPath = await Platform.modal.promptText(`Rename / move "${p}" to:`, {
                    title: 'Rename file', defaultValue: p
                });
                if (!newPath || newPath.trim() === p) return;
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/files/rename`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrfToken() },
                        body: { old_path: p, new_path: newPath.trim() },
                    });
                    // Update local state
                    this.fileList = this.fileList.map(f => f === p ? newPath.trim() : f).sort();
                    if (this.generatedFiles[p] !== undefined) {
                        this.generatedFiles[newPath.trim()] = this.generatedFiles[p];
                        delete this.generatedFiles[p];
                    }
                    const tab = this.openTabs.find(t => t.path === p);
                    if (tab) {
                        tab.path = newPath.trim();
                        tab.name = newPath.trim().split('/').pop();
                    }
                    if (this.selectedFile === p) this.selectedFile = newPath.trim();
                    this.version = data.version;
                    this._setSuccess(`Moved ${p} → ${newPath.trim()}`);
                } catch (e) {
                    this._addError(`Failed to rename: ${e.message || e}`);
                }
            },

            async duplicateFile(sourcePath) {
                const p = sourcePath || this.selectedFile;
                if (!p) return;
                const ext = p.includes('.') ? '.' + p.split('.').pop() : '';
                const base = p.includes('.') ? p.slice(0, p.lastIndexOf('.')) : p;
                const suggested = `${base}_copy${ext}`;
                const dest = await Platform.modal.promptText(`Duplicate "${p}" to:`, {
                    title: 'Duplicate file', defaultValue: suggested
                });
                if (!dest || !dest.trim()) return;
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/files/duplicate`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrfToken() },
                        body: { source_path: p, dest_path: dest.trim() },
                    });
                    this.fileList.push(dest.trim());
                    this.fileList.sort();
                    this.version = data.version;
                    this.selectFile(dest.trim());
                    this._setSuccess(`Duplicated to ${dest.trim()}`);
                } catch (e) {
                    this._addError(`Failed to duplicate: ${e.message || e}`);
                }
            },

            async searchFiles() {
                const query = await Platform.modal.promptText('Search across all files:', {
                    title: 'Search files'
                });
                if (!query || !query.trim()) return;
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/files/search`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': this._csrfToken() },
                        body: { query: query.trim() },
                    });
                    this.searchResults = data;
                    this.rightTab = 'search';
                    const totalMatches = (data.results || []).reduce((s, r) => s + r.match_count, 0);
                    this._setSuccess(`Found ${totalMatches} match${totalMatches !== 1 ? 'es' : ''} in ${data.total_files} file${data.total_files !== 1 ? 's' : ''}`);
                } catch (e) {
                    this._addError(`Search failed: ${e.message || e}`);
                }
            },

            /* ── Quality Scoring ── */
            async loadQuality() {
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/quality`);
                    this.qualityScore = data.quality_score;
                    this.qualityDetails = data.quality_details;
                } catch (_) {
                    // Graceful — quality is optional
                }
            },

            async loadIntentState() {
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/intent-state`);
                    this.intentPlan = data.plan || null;
                    this.allowChatWithoutVerify = false;
                    this.chatOverrideReason = '';
                    this.intentGateStats = data.gate_stats || { total_events: 0, blocked: 0, verified: 0, override: 0, override_rate: 0.0 };
                    this.intentLastGateEvent = data.last_gate_event || null;

                    if (!data.verify_state) {
                        this.intentVerify = null;
                        return;
                    }

                    const vs = data.verify_state;
                    const passed = Number(vs.passed || 0);
                    const failed = Number(vs.failed || 0);
                    const errors = Number(vs.errors || 0);
                    const testStatus = data.regenerate_allowed ? 'pass' : (
                        (vs.status === 'pending') ? 'pending' : 'warn'
                    );
                    const sourceLabel = vs.source ? `Evidence source: ${vs.source}` : '';

                    this.intentVerify = {
                        overall: data.regenerate_allowed ? 'pass' : (vs.status || 'warn'),
                        checked_at: vs.verified_at || null,
                        steps: [
                            { key: 'quality', label: 'Quality scan', status: 'pending', detail: sourceLabel },
                            {
                                key: 'tests',
                                label: 'Run tests',
                                status: testStatus,
                                detail: `${passed} passed · ${failed} failed · ${errors} errors`,
                            },
                            { key: 'preview', label: 'Live preview health', status: 'pending', detail: '' },
                        ],
                    };
                } catch (_) {
                    // Intent state hydration is optional on old rows.
                }
            },

            /* ── Drift Detection ── */
            async loadDrift() {
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/drift-report`);
                    this.driftHasGithub = data.has_github || false;
                    this.driftReport = data.report || null;
                } catch (e) {
                    this._addError('Could not load the drift report.', true);
                }
            },

            async scanDrift() {
                this.driftScanning = true;
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/scan-drift`,
                        { method: 'POST' }
                    );
                    if (data.success) {
                        this.driftReport = {
                            status: data.status,
                            scanned_at: data.scanned_at,
                            drift_file_count: data.drift_file_count,
                            base_commit_sha: data.base_sha,
                            head_commit_sha: data.head_sha,
                            drift_items: data.drift_items || [],
                            error_message: null,
                        };
                        this.driftHasGithub = true;
                        if (data.status === 'clean') {
                            this._setSuccess('No drift detected — repo matches generated baseline.');
                        } else {
                            this._setSuccess(`Drift detected: ${data.drift_file_count} file(s) changed since last generation.`);
                        }
                    } else {
                        this._addError('Drift scan failed: ' + (data.error || 'unknown error'));
                    }
                } catch (e) {
                    this._addError('Drift scan failed: ' + e.message);
                } finally {
                    this.driftScanning = false;
                }
            },

            async importDrift() {
                this.driftImporting = true;
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/import-drift`,
                        { method: 'POST' }
                    );
                    if (data.success) {
                        this._setSuccess(data.message || 'Baseline advanced.');
                        // Refresh drift panel — should now show clean
                        await this.loadDrift();
                    } else {
                        this._addError('Import drift failed: ' + (data.error || 'unknown error'));
                    }
                } catch (e) {
                    this._addError('Import drift failed: ' + e.message);
                } finally {
                    this.driftImporting = false;
                }
            },

            /* ── Live Preview (Feature 3) ── */
            async checkPreviewStatus() {
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/preview/status`);
                    this.previewActive = data.active || false;
                    if (data.active) {
                        this.previewOpen = true;
                        this.previewStartedAt = data.started_at;
                        this.previewMinutesLeft = data.minutes_remaining !== undefined ? data.minutes_remaining : 10;
                        this.previewEndpointCount = data.endpoint_count || 0;
                        this.previewSchemaCount = data.schema_count || 0;
                        this._startPreviewCountdown();
                    }
                } catch (e) {
                    this._addError('Could not check live preview status.', true);
                }
            },

            async startPreview() {
                this.previewStarting = true;
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/preview/start`,
                        { method: 'POST' }
                    );
                    if (data.success) {
                        this.previewActive = true;
                        this.previewOpen = true;
                        this.previewStartedAt = data.started_at;
                        this.previewMinutesLeft = 10;
                        this.previewEndpointCount = data.endpoint_count || 0;
                        this.previewSchemaCount = data.schema_count || 0;
                        this._startPreviewCountdown();
                        this._setSuccess(`Live preview started — ${data.endpoint_count} endpoints across ${data.schema_count} schemas.`);
                    } else {
                        this._addError('Preview failed: ' + (data.error || 'unknown error'));
                    }
                } catch (e) {
                    this._addError('Preview failed: ' + e.message);
                } finally {
                    this.previewStarting = false;
                }
            },

            async stopPreview() {
                try {
                    await this._fetch(
                        `/solutions/${this.solutionId}/codegen/preview/stop`,
                        { method: 'DELETE' }
                    );
                } catch (e) {
                    // The panel below still closes (the user asked to stop it), but the
                    // container may still be running server-side — tell them so it
                    // isn't silently left consuming resources.
                    this._addError('Could not confirm the live preview stopped on the server — it may still be running.');
                }
                this.previewActive = false;
                this.previewOpen = false;
                if (this._previewTimer) { clearInterval(this._previewTimer); this._previewTimer = null; }
            },

            _startPreviewCountdown() {
                if (this._previewTimer) clearInterval(this._previewTimer);
                this._previewTimer = setInterval(async () => {
                    this.previewMinutesLeft = Math.max(0, this.previewMinutesLeft - 1);
                    if (this.previewMinutesLeft <= 0) {
                        clearInterval(this._previewTimer);
                        this._previewTimer = null;
                        await this.stopPreview();
                        this._addError('Live preview expired after 10 minutes.');
                    }
                }, 60000);
            },

            /* ── Document Upload (Phase 1) ── */
            async uploadDocuments(inputEl) {
                const files = inputEl.files;
                if (!files || files.length === 0) return;

                // Validate limits: 10 files, 50MB total
                if (files.length > 10) {
                    this._addError('Maximum 10 files allowed per upload.');
                    inputEl.value = '';
                    return;
                }
                let totalSize = 0;
                for (let i = 0; i < files.length; i++) totalSize += files[i].size;
                if (totalSize > 50 * 1024 * 1024) {
                    this._addError('Total file size exceeds 50 MB limit.');
                    inputEl.value = '';
                    return;
                }

                this.docUploading = true;
                this.docErrors = [];
                this.docFiles = Array.from(files).map(f => ({ name: f.name, size: f.size, status: 'uploading' }));

                const formData = new FormData();
                for (let i = 0; i < files.length; i++) {
                    formData.append('files', files[i]);
                }

                try {
                    // FormData upload requires raw fetch.
                    const resp = await fetch(  // raw-fetch-ok: FormData upload; the wrapper would re-serialise the body
                        `/architecture-journey/${this.solutionId}/upload-documents`,
                        { method: 'POST', headers: { 'X-CSRFToken': this._csrfToken() }, body: formData }
                    ); // raw-fetch-ok: FormData upload requires raw fetch.
                    const data = await resp.json();
                    if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);

                    const result = data.data || data;
                    this.docFiles = this.docFiles.map(f => ({ ...f, status: 'done' }));
                    this.docSummary = result.summary || '';

                    // Merge new proposals into existing list
                    const newProposals = (result.proposals || []).map(p => ({ ...p, _selecting: false }));
                    this.docProposals = [...this.docProposals, ...newProposals];
                    this.docProposalsLoaded = true;

                    this._setSuccess(`Extracted ${result.proposals_created || 0} architecture elements from ${files.length} document${files.length > 1 ? 's' : ''}.`);
                } catch (e) {
                    this.docFiles = this.docFiles.map(f => ({ ...f, status: 'error' }));
                    this._addError('Document extraction failed: ' + e.message);
                } finally {
                    this.docUploading = false;
                    inputEl.value = '';
                }
            },

            async loadProposals() {
                try {
                    const data = await this._fetch(`/architecture-journey/${this.solutionId}/proposals?status=proposed`);
                    const result = data.data || data;
                    this.docProposals = (result.proposals || []).map(p => ({ ...p, _selecting: false }));
                    this.docProposalsLoaded = true;
                } catch (e) {
                    this._addError('Failed to load proposals: ' + e.message);
                }
            },

            async acceptProposal(proposalId) {
                const p = this.docProposals.find(x => x.id === proposalId);
                if (p) p._selecting = true;
                try {
                    await this._fetch(
                        `/architecture-journey/${this.solutionId}/proposals/${proposalId}/accept`,
                        { method: 'POST' }
                    );
                    // Update local state
                    this.docProposals = this.docProposals.map(x =>
                        x.id === proposalId ? { ...x, status: 'accepted', _selecting: false } : x
                    );
                    this.linkedElementsCount++;
                } catch (e) {
                    if (p) p._selecting = false;
                    this._addError('Accept failed: ' + e.message);
                }
            },

            async rejectProposal(proposalId) {
                const p = this.docProposals.find(x => x.id === proposalId);
                if (p) p._selecting = true;
                try {
                    await this._fetch(
                        `/architecture-journey/${this.solutionId}/proposals/${proposalId}/reject`,
                        { method: 'POST' }
                    );
                    this.docProposals = this.docProposals.map(x =>
                        x.id === proposalId ? { ...x, status: 'rejected', _selecting: false } : x
                    );
                } catch (e) {
                    if (p) p._selecting = false;
                    this._addError('Reject failed: ' + e.message);
                }
            },

            async batchAcceptProposals() {
                const pending = this.docProposals.filter(p => p.status === 'proposed');
                if (pending.length === 0) return;
                this.docAccepting = true;
                try {
                    const data = await this._fetch(
                        `/architecture-journey/${this.solutionId}/proposals/batch-accept`,
                        { method: 'POST', body: { proposal_ids: pending.map(p => p.id) } }
                    );
                    const result = data.data || data;
                    // Mark all as accepted
                    const acceptedIds = new Set(pending.map(p => p.id));
                    this.docProposals = this.docProposals.map(p =>
                        acceptedIds.has(p.id) ? { ...p, status: 'accepted' } : p
                    );
                    this.linkedElementsCount += result.accepted || pending.length;
                    this._setSuccess(`Accepted ${result.accepted || pending.length} elements. ${result.elements_created || 0} chain elements inferred.`);
                } catch (e) {
                    this._addError('Batch accept failed: ' + e.message);
                } finally {
                    this.docAccepting = false;
                }
            },

            get pendingProposalCount() {
                return this.docProposals.filter(p => p.status === 'proposed').length;
            },

            get acceptedProposalCount() {
                return this.docProposals.filter(p => p.status === 'accepted').length;
            },

            _proposalTypeBadgeClass(type) {
                // Each badge keeps bg/text/border in the same colour family.
                // Semantic tokens (primary/success/warning/destructive) are used
                // where they map cleanly so the badges adapt to the active theme.
                const map = {
                    'Goal': 'bg-primary/10 text-primary border-primary/30',
                    'Driver': 'bg-purple-500/10 text-purple-600 border-purple-500/30',
                    'Capability': 'bg-success/10 text-success border-success/30',
                    'BusinessProcess': 'bg-warning/10 text-warning border-warning/30',
                    'ApplicationComponent': 'bg-violet-500/10 text-violet-600 border-violet-500/30',
                    'DataObject': 'bg-cyan-500/10 text-cyan-600 border-cyan-500/30',
                    'Constraint': 'bg-destructive/10 text-destructive border-destructive/30',
                    'Requirement': 'bg-orange-500/10 text-orange-600 border-orange-500/30',
                };
                return map[type] || 'bg-muted text-muted-foreground border-border';
            },

            /* ── NL-to-Architecture (Feature 4) ── */
            async generateArchitecture(append) {
                if (!this.nlDescription.trim()) return;
                this.nlGenerating = true;
                try {
                    const data = await this._fetch(
                        `/solutions/${this.solutionId}/codegen/generate-architecture`,
                        {
                            method: 'POST',
                            body: {
                                description: this.nlDescription,
                                append: append || false,
                            },
                        }
                    );
                    if (data.success) {
                        this.nlResult = data;
                        this._setSuccess(data.message);
                    } else {
                        this._addError('Architecture generation failed: ' + (data.error || 'unknown error'));
                    }
                } catch (e) {
                    this._addError('Architecture generation failed: ' + e.message);
                } finally {
                    this.nlGenerating = false;
                }
            },

            /* ── GAP-03: Language tier ── */
            get currentLanguageMeta() {
                return this.LANGUAGE_META[this.config.language] || { tier: 'Unknown', templates: 0 };
            },

            get isSalesforceApex() {
                return this.config.language === 'salesforce-apex';
            },

            normalizedSalesforceConfig() {
                const namespace = this.isSalesforceApex
                    ? String(this.config.namespace_prefix || '').trim()
                    : '';
                const requestedMode = this.isSalesforceApex
                    ? String(this.config.package_mode || '').trim().toLowerCase()
                    : 'unmanaged';
                const packageMode = requestedMode === 'managed' ? 'managed' : 'unmanaged';
                return {
                    package_mode: packageMode,
                    namespace_prefix: packageMode === 'managed' ? namespace : '',
                };
            },

            /* ── Gap 1: Field Editor ── */
            toggleFieldEditor(cls) {
                let name = cls.name;
                if (this.fieldEditorOpen[name]) {
                    this.fieldEditorOpen[name] = false;
                    return;
                }
                // Initialize working copy of fields
                this.editingFields[name] = JSON.parse(JSON.stringify(cls.fields || []));
                this.fieldEditorOpen[name] = true;
            },

            addField(className) {
                if (!this.editingFields[className]) this.editingFields[className] = [];
                this.editingFields[className].push({
                    name: '',
                    type: 'str',
                    nullable: true,
                    description: '',
                });
            },

            removeField(className, idx) {
                if (this.editingFields[className]) {
                    this.editingFields[className].splice(idx, 1);
                }
            },

            async confirmClass(cls) {
                let name = cls.name;
                let fields = this.editingFields[name];
                if (!fields || !fields.length) {
                    this._addError('No fields to confirm for ' + name);
                    return;
                }
                // Validate fields
                const validationErr = this.validateClassFields(name);
                if (validationErr) {
                    this._addError(name + ': ' + validationErr);
                    return;
                }
                this.confirmingClass = name;
                try {
                    let data = await this._fetch(
                        '/solutions/' + this.solutionId + '/codegen/confirm-fields',
                        {
                            method: 'POST',
                            body: {
                                classes: [{
                                    source_element_id: cls.source_element_id,
                                    fields: fields,
                                }],
                            },
                        }
                    );
                    this.version = data.version;
                    // Update the UML class in local state
                    const umlClass = this.umlClasses.find(function(c) { return c.name === name; });
                    if (umlClass) {
                        umlClass.fields = JSON.parse(JSON.stringify(fields));
                    }
                    this.fieldEditorOpen[name] = false;
                    this.confirmedClasses[name] = true;
                    // Best-effort local cache of the confirmation the server just saved
                    // above — a failure here doesn't affect the confirmation itself.
                    try { localStorage.setItem('codegen_confirmed_' + this.solutionId, JSON.stringify(this.confirmedClasses)); } catch (_) { /* swallow-ok: localStorage throws in private mode or over quota; the server already persisted the confirmation on the line above, so this cache is redundant and a failure would be a false alarm */ }
                    this._setSuccess('Fields confirmed for ' + name + '. Next generation will use these exact fields.');
                } catch (e) {
                    this._addError('Confirm failed for ' + name + ': ' + e.message);
                } finally {
                    this.confirmingClass = '';
                }
            },

            async confirmAllClasses() {
                const classes = [];
                let self = this;
                this.umlClasses.forEach(function(cls) {
                    let fields = self.editingFields[cls.name] || cls.fields || [];
                    if (fields.length) {
                        classes.push({
                            source_element_id: cls.source_element_id,
                            fields: fields,
                        });
                    }
                });
                if (!classes.length) {
                    this._addError('No classes with fields to confirm');
                    return;
                }
                this.confirmingClass = '__all__';
                try {
                    let data = await this._fetch(
                        '/solutions/' + this.solutionId + '/codegen/confirm-fields',
                        { method: 'POST', body: { classes: classes } }
                    );
                    this.version = data.version;
                    // Mark every class confirmed in local state
                    const self2 = this;
                    this.umlClasses.forEach(function(cls) {
                        self2.confirmedClasses[cls.name] = true;
                    });
                    // Best-effort local cache of the confirmation the server just saved
                    // above — a failure here doesn't affect the confirmation itself.
                    try { localStorage.setItem('codegen_confirmed_' + this.solutionId, JSON.stringify(this.confirmedClasses)); } catch (_) { /* swallow-ok: localStorage throws in private mode or over quota; the server already persisted the confirmation on the line above, so this cache is redundant and a failure would be a false alarm */ }
                    this._setSuccess('Confirmed fields for ' + data.confirmed_count + ' classes.');
                } catch (e) {
                    this._addError('Confirm all failed: ' + e.message);
                } finally {
                    this.confirmingClass = '';
                }
            },

            async enrichFields() {
                this.enrichingFields = true;
                this._setSuccess('');
                try {
                    let data = await this._fetch(
                        '/solutions/' + this.solutionId + '/api/component-specs/infer-all',
                        { method: 'POST' }
                    );
                    if (data.success && data.data) {
                        let results = data.data.results || {};
                        let total = data.data.total || 0;
                        let enriched = Object.values(results).filter(r => r.status === 'proposed').length;
                        let failed = Object.values(results).filter(r => r.status !== 'proposed').length;
                        let msg = 'Enriched ' + enriched + '/' + total + ' entities with domain-specific fields.';
                        if (failed > 0) msg += ' (' + failed + ' failed)';
                        this._setSuccess(msg);
                        // Reload the page to pick up the new spec_data
                        if (enriched > 0) {
                            setTimeout(() => window.location.reload(), 1500);
                        }
                    } else {
                        this._addError('Field enrichment failed: ' + (data.error || 'Unknown error'));
                    }
                } catch (e) {
                    this._addError('Field enrichment failed: ' + e.message);
                } finally {
                    this.enrichingFields = false;
                }
            },

            _formatIntelligenceResult(res) {
                if (!res) return '';
                if (typeof res.content === 'string') return res.content;
                if (Array.isArray(res.tips)) return res.tips.map(t => '• ' + t).join('\n');
                if (res.ranked_groups) {
                    let s = (res.narrative || '') + '\n\n**Suggested regeneration order:**\n';
                    s += res.ranked_groups.join(' → ');
                    if (res.group_reasons && typeof res.group_reasons === 'object') {
                        s += '\n\n**Signals:**\n';
                        const sig = res.quality_signals || {};
                        s += Object.keys(sig).map(k => k + ': ' + sig[k]).join('\n');
                    }
                    if (res.sparse_classes && res.sparse_classes.length) {
                        s += '\n\n**Sparse classes (≤3 fields):** ' + res.sparse_classes.join(', ');
                    }
                    return s.trim();
                }
                try {
                    return JSON.stringify(res, null, 2);
                } catch (_) {
                    return String(res);
                }
            },

            async runCodegenIntelligence(action) {
                const needsLlm = !['journey_copilot_tips', 'regeneration_copilot'].includes(action);
                if (needsLlm && !this.hasLlm) {
                    this._addError('Configure an LLM in Admin → API Settings to use this intelligence.');
                    return;
                }
                if (action === 'traceability_explainer') {
                    const id = parseInt((this.intelligenceTraceElementId || '').trim(), 10);
                    if (!id) {
                        this._addError('Enter a numeric ArchiMate source_element_id for traceability.');
                        return;
                    }
                }
                if (action === 'failure_triage') {
                    const err = (this.intelligenceFailureText || '').trim();
                    if (!err) {
                        this._addError('Paste an error log or failure message for triage.');
                        return;
                    }
                }

                this.intelligenceLoading = true;
                this.intelligenceAction = action;
                this.intelligenceDisplay = '';
                this.intelligenceRaw = null;

                let payload = {};
                if (action === 'journey_copilot_tips') {
                    payload = {
                        phase: this.phase,
                        has_uml: !!this.uml,
                        has_files: (this.fileList || []).length > 0,
                        blueprint_stale: !!this.blueprintStale,
                        quality_score: this.qualityScore,
                    };
                } else if (action === 'traceability_explainer') {
                    payload = { source_element_id: parseInt(this.intelligenceTraceElementId.trim(), 10) };
                } else if (action === 'failure_triage') {
                    payload = { error_text: this.intelligenceFailureText.trim() };
                }

                try {
                    const data = await this._fetch(
                        '/solutions/' + this.solutionId + '/codegen/intelligence',
                        {
                            method: 'POST',
                            body: { action: action, payload: payload },
                        }
                    );
                    if (!data.success) {
                        throw new Error(data.error || 'Intelligence request failed');
                    }
                    this.intelligenceRaw = data.result || null;
                    this.intelligenceDisplay = this._formatIntelligenceResult(data.result);
                    this._setSuccess('Intelligence ready — ' + action.replace(/_/g, ' '));
                } catch (e) {
                    this._addError('Intelligence failed: ' + e.message);
                } finally {
                    this.intelligenceLoading = false;
                }
            },

            /* ── AI-native intent planning ── */
            async buildIntentPlan() {
                let instruction = (this.intentInstruction || '').trim();
                if (!instruction || this.intentPlanning) return;

                this.intentPlanning = true;
                this.intentPlan = null;
                this.intentVerify = null;
                this.allowChatWithoutVerify = false;
                this.chatOverrideReason = '';
                try {
                    let data = await this._fetch(
                        '/solutions/' + this.solutionId + '/codegen/intent-plan',
                        {
                            method: 'POST',
                            body: {
                                instruction: instruction,
                                selected_path: this.selectedFile || null,
                            },
                        }
                    );
                    this.intentPlan = data.plan || null;
                    this._setSuccess('Intent plan ready (' + (data.source || 'heuristic') + ').');
                } catch (e) {
                    this._addError('Intent planning failed: ' + e.message);
                } finally {
                    this.intentPlanning = false;
                }
            },

            useIntentAsChatInstruction() {
                if (!this.intentPlan) return;
                this.chatInstruction = (this.intentPlan.goal || this.intentInstruction || '').trim();
                if (!this.chatInstruction) return;
                this._setSuccess('Intent copied to AI chat input.');
            },

            openPlannedFile(path) {
                if (!path) return;
                this.selectFile(path);
            },

            isChatBlockedByVerify() {
                if (!this.intentPlan) return false;
                if (this.allowChatWithoutVerify) return false;
                if (!this.intentVerify) return true;
                return this.intentVerify.overall !== 'pass';
            },

            chatVerifyGateMessage() {
                if (!this.intentPlan) return '';
                if (!this.intentVerify) {
                    return 'Run Verify Loop before applying AI changes for this plan.';
                }
                if (this.intentVerify.overall === 'pass') return '';
                if (this.intentVerify.overall === 'warn') {
                    return 'Verification has warnings. Resolve them or explicitly override before applying AI changes.';
                }
                return 'Verification is required before applying AI changes.';
            },

            enableChatOverride() {
                const reason = (this.chatOverrideReason || '').trim();
                if (reason.length < 12) {
                    this._addError('Override reason is required (minimum 12 characters).');
                    return;
                }
                this.allowChatWithoutVerify = true;
                this._setSuccess('Override enabled for this plan. AI changes are now allowed.');
            },

            clearChatOverride() {
                this.allowChatWithoutVerify = false;
                this.chatOverrideReason = '';
            },

            _initIntentVerifyState() {
                this.intentVerify = {
                    overall: 'pending',
                    checked_at: null,
                    steps: [
                        { key: 'quality', label: 'Quality scan', status: 'pending', detail: '' },
                        { key: 'tests', label: 'Run tests', status: 'pending', detail: '' },
                        { key: 'preview', label: 'Live preview health', status: 'pending', detail: '' },
                    ],
                };
            },

            _setIntentVerifyStep(key, status, detail) {
                if (!this.intentVerify) this._initIntentVerifyState();
                const step = this.intentVerify.steps.find(s => s.key === key);
                if (!step) return;
                step.status = status;
                step.detail = detail || '';
            },

            async runIntentVerifyLoop() {
                if (this.intentVerifyRunning) return;
                this.intentVerifyRunning = true;
                this._initIntentVerifyState();
                this.allowChatWithoutVerify = false;

                try {
                    // Step 1: quality scan
                    this._setIntentVerifyStep('quality', 'running', 'Refreshing quality metrics…');
                    await this.loadQuality();
                    const qualityErrors = ((this.qualityDetails && this.qualityDetails.blueprint_violations) || [])
                        .filter(v => v && v.severity === 'error').length;
                    const qualityReady = this.qualityScore !== null && this.qualityScore !== undefined;
                    if (qualityReady && qualityErrors === 0) {
                        this._setIntentVerifyStep('quality', 'pass', `Score ${this.qualityScore}% · no error-level blueprint violations`);
                    } else if (qualityReady) {
                        this._setIntentVerifyStep('quality', 'warn', `Score ${this.qualityScore}% · ${qualityErrors} blueprint error(s)`);
                    } else {
                        this._setIntentVerifyStep('quality', 'warn', 'No quality score available yet');
                    }

                    // Step 2: test execution
                    this._setIntentVerifyStep('tests', 'running', 'Executing generated tests in Docker…');
                    await this.verify();
                    const vr = this.verifyResult || {};
                    const sum = vr.summary || {};
                    const passed = Number(sum.passed || 0);
                    const failed = Number(sum.failed || 0);
                    const errors = Number(sum.errors || 0);
                    if (vr.success && failed === 0 && errors === 0) {
                        this._setIntentVerifyStep('tests', 'pass', `${passed} passed · ${failed} failed · ${errors} errors`);
                    } else if (vr.success || vr.error || vr.message) {
                        const detail = vr.error || vr.message || `${passed} passed · ${failed} failed · ${errors} errors`;
                        this._setIntentVerifyStep('tests', 'warn', detail);
                    } else {
                        this._setIntentVerifyStep('tests', 'warn', 'No test summary returned');
                    }

                    // Step 3: live preview health
                    this._setIntentVerifyStep('preview', 'running', 'Validating preview endpoint availability…');
                    await this.checkPreviewStatus();
                    if (!this.previewActive) {
                        await this.startPreview();
                    }
                    if (this.previewActive) {
                        this._setIntentVerifyStep(
                            'preview',
                            'pass',
                            `${this.previewEndpointCount || 0} endpoints · ${this.previewSchemaCount || 0} schemas`
                        );
                    } else {
                        this._setIntentVerifyStep('preview', 'warn', 'Preview is not active');
                    }

                    const statuses = this.intentVerify.steps.map(s => s.status);
                    if (statuses.every(s => s === 'pass')) {
                        this.intentVerify.overall = 'pass';
                        this.allowChatWithoutVerify = false;
                        this._setSuccess('Intent verification loop passed all checks.');
                    } else if (statuses.some(s => s === 'warn')) {
                        this.intentVerify.overall = 'warn';
                        this._addError('Intent verification finished with warnings. Review step details.');
                    } else {
                        this.intentVerify.overall = 'pending';
                    }
                } catch (e) {
                    this.intentVerify.overall = 'warn';
                    this._addError('Intent verification loop failed: ' + e.message);
                } finally {
                    this.intentVerify.checked_at = new Date().toISOString();
                    this.intentVerifyRunning = false;
                }
            },

            /* ── Gap 3: Chat Regenerate ── */
            async chatRegenerate() {
                let instruction = (this.chatInstruction || '').trim();
                if (!instruction) return;
                if (this.isChatBlockedByVerify()) {
                    this._addError(this.chatVerifyGateMessage() || 'Verification gate blocked AI changes.');
                    return;
                }
                this.chatSending = true;
                this.chatHistory.push({ role: 'user', text: instruction });
                this._scrollChatToBottom();
                this.chatInstruction = '';
                // Save to localStorage suggestions — best-effort recent-history cache;
                // unavailable in private browsing just means suggestions aren't recalled.
                this.chatSuggestions = [instruction].concat(this.chatSuggestions.filter(function(s) { return s !== instruction; })).slice(0, 5);
                try { localStorage.setItem('codegen_chat_history_' + this.solutionId, JSON.stringify(this.chatSuggestions)); } catch (_) { /* swallow-ok: localStorage throws in private mode or over quota; this only recalls recent instructions as suggestions next visit, and the instruction itself is sent to the server below */ }
                try {
                    let data = await this._fetch(
                        '/solutions/' + this.solutionId + '/codegen/chat-regenerate',
                        {
                            method: 'POST',
                            body: {
                                instruction: instruction,
                                version: this.version,
                                intent_plan_active: !!this.intentPlan,
                                verification_override: !!this.allowChatWithoutVerify,
                                override_reason: this.allowChatWithoutVerify
                                    ? (this.chatOverrideReason || '').trim()
                                    : '',
                            },
                        }
                    );
                    this.version = data.version;

                    // Update local file state
                    if (data.files) {
                        this.fileList = data.files.sort();
                    }
                    if (data.generated_content) {
                        let self = this;
                        Object.keys(data.generated_content).forEach(function(path) {
                            self.generatedFiles[path] = data.generated_content[path];
                        });
                        // Select first changed file
                        const firstChanged = (data.changed_files || [])[0] || (data.added_files || [])[0];
                        if (firstChanged) {
                            self.selectFile(firstChanged);
                        }
                    }

                    // Build diff summary for each changed file
                    const diffParts = [];
                    const oldContent = data.old_content || {};
                    (data.changed_files || []).forEach(function(path) {
                        const oldLines = (oldContent[path] || '').split('\n');
                        const newLines = (data.generated_content[path] || '').split('\n');
                        let added = 0, removed = 0;
                        const maxLen = Math.max(oldLines.length, newLines.length);
                        for (let i = 0; i < maxLen; i++) {
                            if (i >= oldLines.length) { added++; }
                            else if (i >= newLines.length) { removed++; }
                            else if (oldLines[i] !== newLines[i]) { added++; removed++; }
                        }
                        diffParts.push({ path, label: path.split('/').pop() + ': +' + added + ' -' + removed });
                    });
                    (data.added_files || []).forEach(function(path) {
                        const lines = (data.generated_content[path] || '').split('\n').length;
                        diffParts.push({ path, label: path.split('/').pop() + ': +' + lines + ' (new)' });
                    });

                    const responseText = diffParts.length
                        ? diffParts.map(function(d) { return d.label; }).join(' · ')
                        : 'No files changed.';
                    this.chatHistory.push({ role: 'assistant', text: responseText, diffs: diffParts });
                    this._scrollChatToBottom();
                    this.loadHistory();
                    this._setSuccess('Chat regeneration complete (' + data.version_label + ').');
                } catch (e) {
                    this.chatHistory.push({ role: 'assistant', text: 'Error: ' + e.message });
                    this._addError('Chat regenerate failed: ' + e.message);
                } finally {
                    this.chatSending = false;
                }
            },

            /* ── Field validation ── */
            _PYTHON_RESERVED: new Set(['False','None','True','and','as','assert','async','await','break',
                'class','continue','def','del','elif','else','except','finally','for','from','global',
                'if','import','in','is','lambda','nonlocal','not','or','pass','raise','return','try',
                'while','with','yield']),

            validateField(field) {
                if (!field.name || !field.name.trim()) return 'Name required';
                if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(field.name)) return 'Invalid Python identifier';
                if (this._PYTHON_RESERVED.has(field.name)) return 'Reserved word';
                return '';
            },

            validateClassFields(className) {
                let fields = this.editingFields[className] || [];
                const names = {};
                for (let i = 0; i < fields.length; i++) {
                    let err = this.validateField(fields[i]);
                    if (err) return err + ' (field ' + (i + 1) + ')';
                    if (names[fields[i].name]) return 'Duplicate field: ' + fields[i].name;
                    names[fields[i].name] = true;
                }
                return '';
            },

            useSuggestion(text) {
                this.chatInstruction = text;
            },

            /* ── Docker Live Preview ── */
            async dockerPreview() {
                if (this.dockerBuilding) return;
                this.dockerBuilding = true;
                this.dockerError = null;
                this.dockerUrl = null;
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/docker-preview`, { method: 'POST' });
                    this.dockerUrl = data.app_url || data.api_url;
                    this.dockerContainer = data.container;
                    this.dockerRunning = true;
                    this._setSuccess('Container running — opening /docs');
                    setTimeout(() => { window.open(this.dockerUrl, '_blank'); }, 800);
                } catch (e) {
                    this.dockerError = e.message || 'Docker preview failed';
                    this._addError('Docker preview: ' + this.dockerError);
                } finally {
                    this.dockerBuilding = false;
                }
            },

            async dockerStop() {
                try {
                    await this._fetch(`/solutions/${this.solutionId}/codegen/docker-preview/stop`, { method: 'POST' });
                    this.dockerRunning = false;
                    this.dockerUrl = null;
                    this.dockerContainer = null;
                } catch (e) {
                    this._addError('Could not stop the Docker preview container — it may still be running.');
                }
            },

            async checkDockerStatus() {
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/docker-preview/status`);
                    this.dockerRunning = data.running;
                    if (data.running) this.dockerUrl = data.app_url || data.api_url;
                } catch (e) {
                    this._addError('Could not check Docker preview status.', true);
                }
            },

            /* ── StackBlitz Frontend Preview ── */
            async openStackBlitz() {
                if (this.stackBlitzLoading) return;
                this.stackBlitzLoading = true;
                try {
                    const data = await this._fetch(`/solutions/${this.solutionId}/codegen/stackblitz-data`);
                    if (!data || !data.files || Object.keys(data.files).length === 0) {
                        throw new Error('No frontend files generated. Select "Full-Stack (Python + Next.js)" as the language in Phase 3 to enable StackBlitz.');
                    }

                    // Build a hidden form and POST it to StackBlitz (opens in new tab)
                    const form = document.createElement('form');
                    form.method = 'POST';
                    form.action = 'https://stackblitz.com/run';
                    form.target = '_blank';
                    form.style.display = 'none';

                    const addField = (name, value) => {
                        const input = document.createElement('input');
                        input.type = 'hidden';
                        input.name = name;
                        input.value = value;
                        form.appendChild(input);
                    };

                    addField('project[title]', data.title || this.solutionName + ' — Frontend');
                    addField('project[description]', 'Generated by Entelim — Architecture & Code Generator');
                    addField('project[template]', 'node');
                    addField('project[tags][]', 'nextjs');
                    addField('project[tags][]', 'typescript');
                    addField('project[tags][]', 'shadcn');

                    for (const [path, content] of Object.entries(data.files)) {
                        addField(`project[files][${path}]`, content);
                    }

                    document.body.appendChild(form);
                    form.submit();
                    document.body.removeChild(form);
                } catch (err) {
                    this.errors.push('StackBlitz error: ' + (err.message || err));
                } finally {
                    this.stackBlitzLoading = false;
                }
            },

            /* ── Architecture Diagram ── */
            async loadArchDiagram() {
                if (this.archDiagramLoading) return;
                this.archDiagramLoading = true;
                this.archDiagramError = null;
                try {
                    const data = await this._fetch('/architecture-journey/' + this.solutionId + '/promoted-elements');
                    const byLayer = (data && data.elements_by_layer) || {};
                    const elements = [];
                    Object.keys(byLayer).forEach(layer => {
                        (byLayer[layer] || []).forEach(el => {
                            elements.push({ id: String(el.id), name: el.name, type: el.type, layer });
                        });
                    });
                    const rels = ((data && data.relationships) || []).map((r, i) => ({
                        id: 'r-' + i,
                        source_id: String(r.source_id),
                        target_id: String(r.target_id),
                        type: r.rel_type || 'association',
                    }));
                    this.archDiagramTotal = elements.length;
                    if (elements.length === 0) {
                        this.archDiagramError = 'No architecture elements found. Complete the Architecture Journey for this solution first.';
                        return;
                    }
                    await this.$nextTick();
                    const container = document.getElementById('arch-diagram-container-' + this.solutionId);
                    if (!container || typeof ComposerRenderer === 'undefined') return;
                    if (this._archDiagramRenderer) this._archDiagramRenderer.destroy();
                    this._archDiagramRenderer = ComposerRenderer.create(container, { mode: 'view', width: '100%', height: 360 });
                    this._archDiagramRenderer.loadElements(elements, rels);
                    this._archDiagramRenderer.fitToContent();
                } catch (e) {
                    this.archDiagramError = 'Failed to load diagram: ' + (e.message || 'Unknown error');
                } finally {
                    this.archDiagramLoading = false;
                }
            },

        };
    });
    } catch (e) {
        document.addEventListener('DOMContentLoaded', () => {
            const wb = document.querySelector('[x-data*="codegenWorkbench"]');
            if (wb) {
                wb.innerHTML = '<div class="flex-1 flex items-center justify-center p-8">' +
                    '<div class="text-center max-w-md">' +
                    '<h2 class="text-xl font-bold text-slate-900 mb-2">Workbench failed to load</h2>' +
                    '<p class="text-slate-500 mb-4">' + e.message + '</p>' +
                    '<button type="button" data-workbench-reload class="bg-primary text-primary-foreground px-4 py-2 rounded-lg font-medium">Reload Page</button>' +
                    '</div></div>';
            }
            // Delegated, because the CSP refuses inline on*= attributes: this
            // Reload button carried onclick="location.reload()" and never fired,
            // so the workbench's only escape from a boot failure was dead.
            document.addEventListener('click', (ev) => {
                if (!ev.target.closest('[data-workbench-reload]')) return;
                ev.preventDefault();
                location.reload();
            });
        });
    }
    }

    // Guard against double-registration: Alpine may already be available (cached
    // page, early script evaluation) AND fire alpine:init — calling Alpine.data()
    // twice throws and produces a console.error that breaks J6-01.
    let _registered = false;
    function registerOnce() {
        if (_registered) return;
        _registered = true;
        register();
    }

    if (typeof Alpine !== 'undefined' && typeof Alpine.data === 'function') {
        registerOnce();
    }
    document.addEventListener('alpine:init', registerOnce);

    // ── Monaco NL Chat Bridge ────────────────────────────────────────────────
    // Expose Monaco editor content + file refresh to wbChatPanel (Alpine component
    // in _wb_chat_panel.html). Installs after DOM is ready so Alpine has initialised.
    (function installWbBridge() {
        function setup() {
            let el = document.querySelector('[x-data*="codegenWorkbench"]');
            if (!el) {
                setTimeout(setup, 400);
                return;
            }
            window.wbGetCurrentFileContent = function () {
                try {
                    let data = Alpine.$data(el);
                    if (data && data.cmEditor) return data.cmEditor.state.doc.toString() || '';
                    return (data && data.selectedContent) || '';
                } catch (_) { /* swallow-ok: reads the open editor's buffer for the chat panel and throws only when the workbench component has been torn down; there is then no open file, and the empty string is the buffer's true contents rather than a failed load */ return ''; }
            };
            window.wbRefreshFile = function (path) {
                // Best-effort DOM/Alpine bridge from the chat panel — the AI edit
                // itself already succeeded or failed on its own path with its own
                // feedback; this only refreshes the editor view to match. A failure
                // here just leaves the open file view stale until the user re-opens it.
                try {
                    let data = Alpine.$data(el);
                    if (!data) return;
                    if (data.selectedFile === path) {
                        data.loadFile(path);
                    }
                    // Notify file tree of dirty state
                    window.dispatchEvent(new CustomEvent('wb:file-changed', { detail: { path: path } }));
                } catch (_) { /* swallow-ok: Alpine.$data throws if the workbench component has been torn down; the AI edit itself already reported its own success or failure, and this only refreshes the open editor view */ }
            };
        }
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', setup);
        } else {
            setup();
        }
    })();
})();
