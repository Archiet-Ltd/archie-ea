function vendorAnalysisDetail(analysisId) {
  return {
    analysisId: analysisId,
    activeTab: 'setup',
    runningAnalysis: false,
    loadingComparison: false,
    loadingDecision: false,
    loadingVendors: false,
    completedSteps: new Set(),
    manualScoreOpen: true,
    manualScoreRequested: false,
    manualScoreSaving: {},
    reqMatrixOpen: true,
    requirements: [],
    newReqName: '',
    newReqImportance: 'medium',
    newReqMustHave: false,
    editingReqId: null,
    editingReqName: '',
    scenarioOpen: false,
    scenarios: [],
    loadingScenarios: false,
    newScenarioName: '',
    saveScenarioName: '',
    showNewScenarioForm: false,
    newScenarioWeights: { cost: 20, capability_coverage: 20, risk: 20, strategic_fit: 20, implementation: 20 },

    // Stakeholder multi-rater
    stakeholderOpen: false,
    stakeholders: [],
    loadingStakeholders: false,
    showInviteForm: false,
    inviteUserId: '',
    inviteRole: 'IT',
    stakeholderConsensus: null,

    tabs: [
      { id: 'setup', label: 'Setup', icon: 'settings' },
      { id: 'scoring', label: 'Scoring', icon: 'sliders-horizontal' },
      { id: 'comparison', label: 'Comparison', icon: 'bar-chart-3' },
      { id: 'decision', label: 'Decision', icon: 'stamp' },
      { id: 'export', label: 'Export', icon: 'download' }
    ],

    selectionMode: 'capabilities',
    capabilityType: '',

    capabilityTypes: [
      { value: '', label: 'All Types', color: 'bg-foreground/40' },
      { value: 'BUSINESS', label: 'Business', color: 'bg-warning' },
      { value: 'APPLICATION', label: 'Application', color: 'bg-primary' },
      { value: 'TECHNICAL', label: 'Technical', color: 'bg-primary' },
      { value: 'MANUFACTURING', label: 'Manufacturing', color: 'bg-info' }
    ],

    analysis: { name: '', status: 'new' },
    form: {
      name: '', priority: 'high', description: '',
      orgSize: 'enterprise', industry: '', timeHorizon: '24months', scope: 'enterprise'
    },

    _ensureFormStrings() {
      if (!this.form) this.form = {};
      this.form.name = (this.form.name != null) ? String(this.form.name) : '';
      this.form.industry = (this.form.industry != null) ? String(this.form.industry) : '';
      this.form.description = (this.form.description != null) ? String(this.form.description) : '';
      if (!this.analysis) this.analysis = {};
      this.analysis.name = (this.analysis.name != null) ? String(this.analysis.name) : '';
      if (!this.decision) this.decision = {};
      this.decision.rationale = (this.decision.rationale != null) ? String(this.decision.rationale) : '';
    },

    domains: [],

    /* Capability selection state */
    capOptionsL1: [], capOptionsL2: [], capOptionsL3: [],
    selectedL1: [], selectedL2: [], selectedL3: [],
    capSearchL1: '', capSearchL2: '', capSearchL3: '',
    loadingL1: false, loadingL2: false, loadingL3: false,

    get filteredL1() {
      const q = this.capSearchL1.toLowerCase();
      if (!q) return this.capOptionsL1;
      return this.capOptionsL1.filter(function(c) { return (c.name || '').toLowerCase().indexOf(q) !== -1 || (c.description || '').toLowerCase().indexOf(q) !== -1; });
    },
    get filteredL2() {
      const q = this.capSearchL2.toLowerCase();
      if (!q) return this.capOptionsL2;
      return this.capOptionsL2.filter(function(c) { return (c.name || '').toLowerCase().indexOf(q) !== -1 || (c.description || '').toLowerCase().indexOf(q) !== -1; });
    },
    get filteredL3() {
      const q = this.capSearchL3.toLowerCase();
      if (!q) return this.capOptionsL3;
      return this.capOptionsL3.filter(function(c) { return (c.name || '').toLowerCase().indexOf(q) !== -1 || (c.description || '').toLowerCase().indexOf(q) !== -1; });
    },

    get allCapabilities() {
      const l1 = this.selectedL1.map(function(c) { return Object.assign({}, c, { _level: 'L1' }); });
      const l2 = this.selectedL2.map(function(c) { return Object.assign({}, c, { _level: 'L2' }); });
      const l3 = this.selectedL3.map(function(c) { return Object.assign({}, c, { _level: 'L3' }); });
      return l1.concat(l2, l3);
    },

    isCapSelected(cap, level) {
      const arr = level === 'L1' ? this.selectedL1 : level === 'L2' ? this.selectedL2 : this.selectedL3;
      return arr.some(function(c) { return String(c.id) === String(cap.id); });
    },

    toggleCapability(cap, level, checked) {
      const self = this;
      const arrKey = level === 'L1' ? 'selectedL1' : level === 'L2' ? 'selectedL2' : 'selectedL3';
      if (checked) {
        if (!this.isCapSelected(cap, level)) {
          this[arrKey] = this[arrKey].concat([cap]);
        }
      } else {
        this[arrKey] = this[arrKey].filter(function(c) { return String(c.id) !== String(cap.id); });
      }
      if (level === 'L1') {
        this.selectedL2 = [];
        this.selectedL3 = [];
        this.capOptionsL2 = [];
        this.capOptionsL3 = [];
        if (this.selectedL1.length > 0) this.loadChildCaps(this.selectedL1, 2, 'capOptionsL2', 'loadingL2');
      } else if (level === 'L2') {
        this.selectedL3 = [];
        this.capOptionsL3 = [];
        if (this.selectedL2.length > 0) this.loadChildCaps(this.selectedL2, 3, 'capOptionsL3', 'loadingL3');
      }
    },

    removeCapability(cap) {
      this.selectedL1 = this.selectedL1.filter(function(c) { return String(c.id) !== String(cap.id); });
      this.selectedL2 = this.selectedL2.filter(function(c) { return String(c.id) !== String(cap.id); });
      this.selectedL3 = this.selectedL3.filter(function(c) { return String(c.id) !== String(cap.id); });
    },

    clearCapabilities() {
      this.selectedL1 = []; this.selectedL2 = []; this.selectedL3 = [];
      this.capOptionsL2 = []; this.capOptionsL3 = [];
      this.capSearchL1 = ''; this.capSearchL2 = ''; this.capSearchL3 = '';
    },

    archimateLayerClass(type) {
      if (!type) return 'border-border text-muted-foreground';
      switch (type.toUpperCase()) {
        case 'BUSINESS': return 'border-warning/50 text-warning-emphasis bg-warning/10';
        case 'APPLICATION': return 'border-primary/50 text-primary bg-primary/10';
        case 'TECHNICAL': return 'border-primary/50 text-primary bg-primary/10';
        case 'MANUFACTURING': return 'border-info/50 text-info-emphasis bg-info/10';
        default: return 'border-border text-muted-foreground';
      }
    },

    /* APQC Process selection state */
    procOptionsL1: [], procOptionsL2: [], procOptionsL3: [],
    procSelectedL1: [], procSelectedL2: [], procSelectedL3: [],
    procSearchL1: '', procSearchL2: '', procSearchL3: '',
    loadingProcL1: false, loadingProcL2: false, loadingProcL3: false,

    get filteredProcL1() {
      const q = this.procSearchL1.toLowerCase();
      if (!q) return this.procOptionsL1;
      return this.procOptionsL1.filter(function(p) { return ((p.process_code || '') + ' ' + (p.process_name || p.name || '')).toLowerCase().indexOf(q) !== -1; });
    },
    get filteredProcL2() {
      const q = this.procSearchL2.toLowerCase();
      if (!q) return this.procOptionsL2;
      return this.procOptionsL2.filter(function(p) { return ((p.process_code || '') + ' ' + (p.process_name || p.name || '')).toLowerCase().indexOf(q) !== -1; });
    },
    get filteredProcL3() {
      const q = this.procSearchL3.toLowerCase();
      if (!q) return this.procOptionsL3;
      return this.procOptionsL3.filter(function(p) { return ((p.process_code || '') + ' ' + (p.process_name || p.name || '')).toLowerCase().indexOf(q) !== -1; });
    },

    get allProcesses() {
      const l1 = this.procSelectedL1.map(function(p) { return Object.assign({}, p, { _level: 'L1' }); });
      const l2 = this.procSelectedL2.map(function(p) { return Object.assign({}, p, { _level: 'L2' }); });
      const l3 = this.procSelectedL3.map(function(p) { return Object.assign({}, p, { _level: 'L3' }); });
      return l1.concat(l2, l3);
    },

    isProcSelected(proc, level) {
      const arr = level === 'L1' ? this.procSelectedL1 : level === 'L2' ? this.procSelectedL2 : this.procSelectedL3;
      return arr.some(function(p) { return String(p.id) === String(proc.id); });
    },

    toggleProcess(proc, level, checked) {
      const arrKey = level === 'L1' ? 'procSelectedL1' : level === 'L2' ? 'procSelectedL2' : 'procSelectedL3';
      if (checked) {
        if (!this.isProcSelected(proc, level)) {
          this[arrKey] = this[arrKey].concat([proc]);
        }
      } else {
        this[arrKey] = this[arrKey].filter(function(p) { return String(p.id) !== String(proc.id); });
      }
      if (level === 'L1') {
        this.procSelectedL2 = [];
        this.procSelectedL3 = [];
        this.procOptionsL2 = [];
        this.procOptionsL3 = [];
        if (this.procSelectedL1.length > 0) this.loadProcChildren(this.procSelectedL1, 2, 'procOptionsL2', 'loadingProcL2');
      } else if (level === 'L2') {
        this.procSelectedL3 = [];
        this.procOptionsL3 = [];
        if (this.procSelectedL2.length > 0) this.loadProcChildren(this.procSelectedL2, 3, 'procOptionsL3', 'loadingProcL3');
      }
    },

    removeProcess(proc) {
      this.procSelectedL1 = this.procSelectedL1.filter(function(p) { return String(p.id) !== String(proc.id); });
      this.procSelectedL2 = this.procSelectedL2.filter(function(p) { return String(p.id) !== String(proc.id); });
      this.procSelectedL3 = this.procSelectedL3.filter(function(p) { return String(p.id) !== String(proc.id); });
    },

    clearProcesses() {
      this.procSelectedL1 = []; this.procSelectedL2 = []; this.procSelectedL3 = [];
      this.procOptionsL2 = []; this.procOptionsL3 = [];
      this.procSearchL1 = ''; this.procSearchL2 = ''; this.procSearchL3 = '';
      this.discoveredVendors = [];
      this.vendorSearchDone = false;
      this.selectedVendorIds = new Set();
    },

    /* Value Stream selection state */
    vsFilterDomain: '',
    vsOptions: [],
    vsSelectedStream: [],

    discoveredVendors: [],
    vendorSearchDone: false,
    selectedVendorIds: new Set(),

    weights: { cost: 25, capability_coverage: 25, risk: 20, strategic_fit: 15, implementation: 15 },
    weightKeys: ['cost', 'capability_coverage', 'risk', 'strategic_fit', 'implementation'],
    weightLabels: {
      cost: 'Cost', capability_coverage: 'Capability Coverage',
      risk: 'Risk', strategic_fit: 'Strategic Fit', implementation: 'Implementation'
    },
    weightDescriptions: {
      cost: 'License, support, and total cost of ownership',
      capability_coverage: 'How well the vendor covers required capabilities',
      risk: 'Vendor viability, security, and compliance risk',
      strategic_fit: 'Alignment with strategic direction and roadmap',
      implementation: 'Ease of deployment, migration, and integration'
    },
    get weightTotal() {
      return Object.values(this.weights).reduce(function(a, b) { return a + b; }, 0);
    },

    comparisonVendors: [],
    comparisonRows: [
      { label: 'Total Score', key: 'total_score', format: 'score' },
      { label: 'Cost Score', key: 'cost_score', format: 'score' },
      { label: 'Capability Coverage', key: 'capability_score', format: 'score' },
      { label: 'Risk Score', key: 'risk_score', format: 'score' },
      { label: 'Strategic Fit', key: 'strategic_fit_score', format: 'score' },
      { label: 'Implementation', key: 'implementation_score', format: 'score' },
      { label: 'TCO (5yr)', key: 'tco_total', format: 'currency' },
      { label: 'License Cost (Annual)', key: 'license_cost_annual', format: 'currency' },
      { label: 'Support Cost (Annual)', key: 'support_cost_annual', format: 'currency' }
    ],
    rankedVendors: [],

    decision: { vendorId: '', status: '', rationale: '' },

    scoreChart: null,
    tcoChart: null,
    radarChart: null,

    /* ============================================================ */
    /* Lifecycle                                                     */
    /* ============================================================ */

    async init() {
      await this.loadDomains();
      this.loadL1Capabilities();
      this.loadAPQCL1();
      if (this.analysisId) {
        await this.loadExistingAnalysis();
        this.seedCompletedSteps();
      }
      this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    /* ============================================================ */
    /* Wizard navigation                                             */
    /* ============================================================ */

    switchTab(id) {
      this.activeTab = id;
      if (id === 'comparison' && this.analysisId) { this.loadComparison(); this.loadScenarios(); this.loadRequirements(); }
      if (id === 'decision' && this.analysisId) this.loadResults();
      this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    statusTone() {
      const a = this.analysis || {};
      if (a.approval_status === 'approved') return 'success';
      if (a.approval_status === 'rejected') return 'destructive';
      if (a.status === 'draft' || a.status === 'running') return 'warning';
      if (a.status === 'completed' && !a.approval_status) return 'info';
      return 'neutral';
    },

    vendorMatchTone(v) {
      const pct = this.vendorMatchPct(v);
      return pct >= 80 ? 'high' : pct >= 50 ? 'mid' : 'low';
    },

    stepStatus(tabId) {
      if (this.activeTab === tabId) return 'current';
      if (this.completedSteps.has(tabId)) return 'completed';
      if (this.canAccessTab(tabId)) return 'available';
      return 'locked';
    },

    hasSelections() {
      if (this.selectionMode === 'capabilities') return this.allCapabilities.length > 0;
      if (this.selectionMode === 'processes') return this.allProcesses.length > 0;
      if (this.selectionMode === 'valuestreams') return this.vsSelectedStream.length > 0;
      return false;
    },

    canAccessTab(tabId) {
      switch (tabId) {
        case 'setup': return true;
        case 'scoring': return !!(this.form.name.trim() && this.hasSelections());
        case 'comparison': return !!this.analysisId;
        case 'decision': return this.rankedVendors.length > 0;
        case 'export': return !!this.analysisId;
        default: return false;
      }
    },

    gateMessage(tabId) {
      switch (tabId) {
        case 'scoring': return 'Enter an analysis name and select capabilities, processes, or value streams first';
        case 'comparison': return 'Run the analysis before viewing comparisons';
        case 'decision': return 'Run the analysis to generate vendor rankings first';
        case 'export': return 'Create an analysis before exporting';
        default: return '';
      }
    },

    unlockHint(tabId) {
      switch (tabId) {
        case 'scoring': return 'Unlocks once the analysis has a name and at least one capability, process or value stream is chosen.';
        case 'comparison': return 'Unlocks once the analysis has been run, because the comparison is built from its vendors.';
        case 'decision': return 'Unlocks once the analysis has been run and the vendors are ranked.';
        case 'export': return 'Unlocks once the analysis has been created.';
        default: return '';
      }
    },

    lockedTabs() {
      const self = this;
      return this.tabs.filter(function(t) { return self.stepStatus(t.id) === 'locked'; });
    },

    scoreText(value) {
      return value ? Number(value).toFixed(1) : 'Not recorded';
    },

    pluralise(count, one, many) {
      return count + ' ' + (count === 1 ? one : many);
    },

    get showScoringRubric() {
      if (this.comparisonVendors.length === 0) return false;
      const scored = this.comparisonVendors.some(function(v) { return v.total_score != null; });
      const rated = this.comparisonVendors.some(function(v) {
        return Object.values(v.manual_scores || {}).some(function(n) { return n; });
      });
      return !scored || rated || this.manualScoreRequested;
    },

    attemptSwitchTab(tabId) {
      if (!this.canAccessTab(tabId)) {
        this.showToast(this.gateMessage(tabId), 'error');
        return;
      }
      this.markStepCompleteIfReady(this.activeTab);
      this.switchTab(tabId);
    },

    markStepCompleteIfReady(tabId) {
      let dominated = false;
      switch (tabId) {
        case 'setup':
          dominated = !!(this.form.name.trim() && this.hasSelections());
          break;
        case 'scoring':
          dominated = this.weightTotal === 100 && !!this.analysisId;
          break;
        case 'comparison':
          dominated = this.comparisonVendors.length > 0;
          break;
        case 'decision':
          dominated = !!this.analysis.approval_status;
          break;
        case 'export':
          dominated = !!this.analysisId;
          break;
      }
      if (dominated) {
        this.completedSteps.add(tabId);
        this.completedSteps = new Set(this.completedSteps);
      }
    },

    seedCompletedSteps() {
      if (this.form.name.trim() && (this.hasSelections() || this.analysisId)) {
        this.completedSteps.add('setup');
      }
      if (this.analysisId) {
        this.completedSteps.add('scoring');
      }
      if (this.comparisonVendors.length > 0) {
        this.completedSteps.add('comparison');
      }
      if (this.analysis.approval_status) {
        this.completedSteps.add('decision');
      }
      this.completedSteps = new Set(this.completedSteps);
    },

    /* ============================================================ */
    /* Mode switching with state cleanup                             */
    /* ============================================================ */

    async switchSelectionMode(mode) {
      if (mode === this.selectionMode) return;
      const hadSelections = this.hasSelections();
      if (hadSelections) {
        if (!(await Platform.modal.confirm('Switching analysis mode will clear your current selections. Continue?'))) return;
      }
      this.clearCapabilities();
      this.clearProcesses();
      this.vsSelectedStream = [];
      this.discoveredVendors = [];
      this.vendorSearchDone = false;
      this.selectedVendorIds = new Set();
      this.selectionMode = mode;
      this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
    },

    /* ============================================================ */
    /* Data loading                                                  */
    /* ============================================================ */

    async loadDomains() {
      try {
        this.domains = await Platform.fetch.get('/dashboard/api/business-domains', null, { silent: true });
      } catch (e) {
        this.domains = [];
        this.showToast('Could not load business domains', 'error');
      }
    },

    async loadL1Capabilities() {
      this.loadingL1 = true;
      try {
        let url = '/dashboard/api/unified-capabilities?level=1';
        if (this.capabilityType) url += '&specialization_type=' + encodeURIComponent(this.capabilityType);
        this.capOptionsL1 = await Platform.fetch.get(url, null, { silent: true });
      } catch (e) {
        this.capOptionsL1 = [];
        this.showToast('Could not load capabilities', 'error');
      }
      finally { this.loadingL1 = false; }
    },

    reloadL1Capabilities() {
      this.clearCapabilities();
      this.loadL1Capabilities();
    },

    async loadChildCaps(parents, targetLevel, optionsKey, loadingKey) {
      this[loadingKey] = true;
      try {
        this[optionsKey] = await Platform.fetch.post('/dashboard/api/related-capabilities', {
          capability_ids: parents.map(function(c) { return c.id; }), target_level: targetLevel
        }, { silent: true });
      } catch (e) {
        this[optionsKey] = [];
        this.showToast('Could not load related capabilities', 'error');
      }
      finally { this[loadingKey] = false; }
    },

    async loadAPQCL1() {
      this.loadingProcL1 = true;
      try {
        this.procOptionsL1 = await Platform.fetch.get('/dashboard/api/apqc-processes', { level: 1 }, { silent: true });
      } catch (e) {
        this.procOptionsL1 = [];
        this.showToast('Could not load APQC processes', 'error');
      }
      finally { this.loadingProcL1 = false; }
    },

    async loadProcChildren(parents, targetLevel, optionsKey, loadingKey) {
      this[loadingKey] = true;
      try {
        this[optionsKey] = await Platform.fetch.post('/dashboard/api/apqc-processes/children', {
          parent_ids: parents.map(function(p) { return p.id; }), target_level: targetLevel
        }, { silent: true });
      } catch (e) {
        this[optionsKey] = [];
        this.showToast('Could not load related processes', 'error');
      }
      finally { this[loadingKey] = false; }
    },

    async loadValueStreamOptions() {
      try {
        let url = '/dashboard/api/value-streams';
        if (this.vsFilterDomain) url += '?domain_id=' + this.vsFilterDomain;
        this.vsOptions = await Platform.fetch.get(url, null, { silent: true });
      } catch (e) {
        this.vsOptions = [];
        this.showToast('Could not load value streams', 'error');
      }
    },

    onValueStreamSelected() {
      /* vsSelectedStream is bound to the multi-select */
    },

    async loadExistingAnalysis() {
      try {
        // Otherwise the panel just stays empty and reads as "no analysis yet"
        // instead of "the load failed".
        this.analysis = await Platform.fetch.get('/dashboard/api/vendor-analysis/' + this.analysisId, null, { silent: true });
        this.form.name = (this.analysis.name != null) ? String(this.analysis.name) : '';
        this.form.orgSize = this.analysis.organization_size || 'enterprise';
        this.form.industry = this.analysis.industry_vertical || '';
        if (this.analysis.criteria_weights) {
          const w = this.analysis.criteria_weights;
          for (let k in w) { if (this.weights.hasOwnProperty(k)) this.weights[k] = Math.round(w[k] * 100); }
        }
        this.rankedVendors = this.analysis.vendors || [];
        this.comparisonVendors = this.analysis.vendors || [];
      } catch (e) {
        this.showToast('Could not load this analysis', 'error');
      }
    },

    /* ============================================================ */
    /* Vendor discovery                                              */
    /* ============================================================ */

    async discoverVendorsUnified() {
      if (this.selectionMode === 'capabilities') {
        await this.discoverVendorsByCaps();
      } else if (this.selectionMode === 'processes') {
        await this.discoverVendorsByProcs();
      } else if (this.selectionMode === 'valuestreams') {
        await this.discoverVendorsByValueStreams();
      } else {
        await this.discoverVendorsByCaps();
      }
    },

    async discoverVendorsByCaps() {
      if (this.allCapabilities.length === 0) return;
      this.loadingVendors = true;
      try {
        this.discoveredVendors = await Platform.fetch.post('/dashboard/api/vendors/by-capabilities', {
          capability_ids: this.allCapabilities.map(function(c) { return parseInt(c.id); })
        }, { silent: true });
        this.vendorSearchDone = true;
        this.selectedVendorIds = new Set();
        this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      } catch (e) {
        this.showToast(e && e.type === 'NetworkError' ? 'Vendor discovery error' : 'Vendor discovery failed', 'error');
      }
      finally { this.loadingVendors = false; }
    },

    async discoverVendorsByProcs() {
      let self = this;
      if (this.allProcesses.length === 0) return;
      this.loadingVendors = true;
      try {
        const data = await Platform.fetch.post('/dashboard/api/vendors/by-processes', {
          process_ids: this.allProcesses.map(function(p) { return parseInt(p.id); })
        }, { silent: true });
        this.discoveredVendors = data.map(function(v) {
          return {
            id: v.id,
            name: v.name,
            supported_capabilities: v.supported_capabilities || v.supported_process_count || 0,
            total_capabilities: v.total_capabilities || self.allProcesses.length,
            total_products: v.total_products || v.product_count || 0
          };
        });
        this.vendorSearchDone = true;
        this.selectedVendorIds = new Set();
        this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      } catch (e) {
        this.showToast(e && e.type === 'NetworkError' ? 'Vendor discovery error' : 'Vendor discovery failed', 'error');
      }
      finally { this.loadingVendors = false; }
    },

    async discoverVendorsByValueStreams() {
      if (this.vsSelectedStream.length === 0) return;
      this.loadingVendors = true;
      try {
        this.discoveredVendors = await Platform.fetch.post('/dashboard/api/vendors/by-value-streams', {
          value_stream_ids: this.vsSelectedStream.map(function(id) { return parseInt(id); })
        }, { silent: true });
        this.vendorSearchDone = true;
        this.selectedVendorIds = new Set();
        this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      } catch (e) {
        this.showToast(e && e.type === 'NetworkError' ? 'Vendor discovery error' : 'Vendor discovery failed', 'error');
      }
      finally { this.loadingVendors = false; }
    },

    vendorMatchPct(v) {
      const sup = parseInt(v.supported_capabilities) || 0;
      const tot = parseInt(v.total_capabilities) || 1;
      return Math.round((sup / Math.max(tot, 1)) * 100);
    },

    toggleVendor(v, checked) {
      if (checked) this.selectedVendorIds.add(v.id);
      else this.selectedVendorIds.delete(v.id);
      this.selectedVendorIds = new Set(this.selectedVendorIds);
    },

    /* ============================================================ */
    /* Weight presets                                                 */
    /* ============================================================ */

    applyWeightPreset(preset) {
      switch (preset) {
        case 'balanced':
          this.weights = { cost: 20, capability_coverage: 20, risk: 20, strategic_fit: 20, implementation: 20 };
          break;
        case 'cost_focused':
          this.weights = { cost: 40, capability_coverage: 20, risk: 15, strategic_fit: 10, implementation: 15 };
          break;
        case 'capability_focused':
          this.weights = { cost: 15, capability_coverage: 35, risk: 15, strategic_fit: 20, implementation: 15 };
          break;
        case 'risk_averse':
          this.weights = { cost: 15, capability_coverage: 20, risk: 35, strategic_fit: 15, implementation: 15 };
          break;
        case 'strategic':
          this.weights = { cost: 10, capability_coverage: 20, risk: 15, strategic_fit: 35, implementation: 20 };
          break;
      }
    },

    resetWeights() {
      this.weights = { cost: 25, capability_coverage: 25, risk: 20, strategic_fit: 15, implementation: 15 };
    },

    /* ============================================================ */
    /* Analysis CRUD + Scoring                                       */
    /* ============================================================ */

    getCsrfToken() {
      const meta = document.querySelector('meta[name=csrf-token]');
      return meta ? meta.content : '';
    },

    async resolveProcessCapabilityIds() {
      if (this.selectionMode !== 'processes' || this.allProcesses.length === 0) return [];
      try {
        const caps = await Platform.fetch.post('/dashboard/api/process-capabilities', {
          process_ids: this.allProcesses.map(function(p) { return parseInt(p.id); })
        }, { silent: true });
        return caps.map(function(c) { return c.id; });
      } catch (e) {
        // A failure is NOT "no capabilities matched" -- returning [] here made the
        // caller toast "No capabilities could be resolved from selected processes",
        // which misdiagnoses a network/server failure as an empty result. Return
        // null so the caller aborts, and say what actually went wrong.
        this.showToast((e && e.data && e.data.error) || 'Could not resolve capabilities for the selected processes', 'error');
        return null;
      }
    },

    async resolveValueStreamCapabilityIds() {
      if (this.selectionMode !== 'valuestreams' || this.vsSelectedStream.length === 0) return [];
      try {
        const caps = await Platform.fetch.post('/dashboard/api/value-stream-capabilities', {
          value_stream_ids: this.vsSelectedStream.map(function(id) { return parseInt(id); })
        }, { silent: true });
        return caps.map(function(c) { return c.id; });
      } catch (e) {
        // Same reasoning as resolveProcessCapabilityIds: a failed lookup is not
        // "nothing mapped", so report it and let the caller abort rather than
        // read an empty array as an empty result.
        this.showToast((e && e.data && e.data.error) || 'Could not resolve capabilities for the selected value streams', 'error');
        return null;
      }
    },

    async getCapabilityIdsForPayload() {
      if (this.selectionMode === 'capabilities') {
        const caps = this.allCapabilities;
        if (caps.length === 0) { this.showToast('Select at least one capability', 'error'); return null; }
        return caps.map(function(c) { return parseInt(c.id); });
      }
      if (this.selectionMode === 'processes') {
        const resolved = await this.resolveProcessCapabilityIds();
        if (resolved === null) return null;   // load failed; already reported
        if (resolved.length === 0) {
          this.showToast('No capabilities could be resolved from selected processes', 'error');
          return null;
        }
        return resolved;
      }
      if (this.selectionMode === 'valuestreams') {
        if (this.vsSelectedStream.length === 0) { this.showToast('Select at least one value stream', 'error'); return null; }
        const resolved = await this.resolveValueStreamCapabilityIds();
        if (resolved === null) return null;   // load failed; already reported
        if (resolved.length === 0) {
          this.showToast('No capabilities could be resolved from selected value streams', 'error');
          return null;
        }
        return resolved;
      }
      this.showToast('Select capabilities, processes, or value streams', 'error');
      return null;
    },

    getWeightsDecimal() {
      const w = {};
      for (let k in this.weights) w[k] = this.weights[k] / 100;
      return w;
    },

    async saveDraft() {
      if (!this.form.name.trim()) { this.showToast('Analysis name is required', 'error'); return; }
      if (this.analysisId) {
        await this.updateAnalysis();
      } else {
        const capIds = await this.getCapabilityIdsForPayload();
        if (!capIds) return;
        const selectedVids = Array.from(this.selectedVendorIds).map(function(id) { return parseInt(id); });
        const payload = {
          name: this.form.name,
          capability_ids: capIds,
          criteria_weights: this.getWeightsDecimal(),
          organization_size: this.form.orgSize,
          industry_vertical: this.form.industry,
          vendor_org_ids: selectedVids
        };
        try {
          const result = await Platform.fetch.post('/dashboard/api/vendor-analysis/create', payload, { silent: true });
          if (result && result.analysis_id) {
            this.analysisId = result.analysis_id;
            this.analysis.id = result.analysis_id;
            window.history.replaceState({}, '', '/dashboard/vendor-analysis/' + result.analysis_id);
            this.showToast('Draft saved successfully', 'success');
          } else {
            this.showToast((result && result.error) || 'Failed to save draft', 'error');
          }
        } catch (e) { this.showToast((e && e.data && e.data.error) || 'Error saving draft', 'error'); }
      }
    },

    async updateAnalysis() {
      try {
        await Platform.fetch.patch('/dashboard/api/vendor-analysis/' + this.analysisId, {
          name: this.form.name,
          organization_size: this.form.orgSize,
          industry_vertical: this.form.industry,
          criteria_weights: this.getWeightsDecimal()
        }, { silent: true });
        this.showToast('Analysis updated', 'success');
      } catch (e) { this.showToast('Failed to update', 'error'); }
    },

    async runAnalysis() {
      if (!this.form.name.trim()) { this.showToast('Analysis name is required', 'error'); return; }
      if (this.weightTotal !== 100) { this.showToast('Weights must sum to 100%', 'error'); return; }

      this.runningAnalysis = true;
      try {
        if (this.analysisId) {
          try {
            await Platform.fetch.post('/dashboard/api/vendor-analysis/' + this.analysisId + '/run-scoring', {
              criteria_weights: this.getWeightsDecimal()
            }, { silent: true });
            this.completedSteps.add('setup');
            this.completedSteps.add('scoring');
            this.completedSteps = new Set(this.completedSteps);
            this.showToast('Scoring complete', 'success');
            this.switchTab('comparison');
          } catch (e) {
            this.showToast((e && e.data && e.data.error) || 'Scoring failed', 'error');
          }
        } else {
          const capIds = await this.getCapabilityIdsForPayload();
          if (!capIds) { this.runningAnalysis = false; return; }

          const selectedVids = Array.from(this.selectedVendorIds).map(function(id) { return parseInt(id); });
          const payload = {
            name: this.form.name,
            capability_ids: capIds,
            criteria_weights: this.getWeightsDecimal(),
            organization_size: this.form.orgSize,
            industry_vertical: this.form.industry,
            vendor_org_ids: selectedVids
          };

          try {
            const result = await Platform.fetch.post('/dashboard/api/vendor-analysis/create', payload, { silent: true });
            if (result && result.analysis_id) {
              this.analysisId = result.analysis_id;
              this.analysis.id = result.analysis_id;
              window.history.replaceState({}, '', '/dashboard/vendor-analysis/' + result.analysis_id);
              this.completedSteps.add('setup');
              this.completedSteps.add('scoring');
              this.completedSteps = new Set(this.completedSteps);
              this.showToast('Analysis created and scored', 'success');
              await this.loadExistingAnalysis();
              this.switchTab('comparison');
            } else {
              this.showToast((result && result.error) || 'Analysis failed', 'error');
            }
          } catch (e) {
            this.showToast((e && e.data && e.data.error) || 'Analysis failed', 'error');
          }
        }
      } catch (e) {
        this.showToast('Error running analysis', 'error');
      } finally {
        this.runningAnalysis = false;
      }
    },

    /* ============================================================ */
    /* Comparison + Results + Charts                                 */
    /* ============================================================ */

    async loadComparison() {
      if (!this.analysisId) return;
      this.loadingComparison = true;
      try {
        const data = await Platform.fetch.get('/dashboard/api/vendor-analysis/' + this.analysisId + '/comparison', null, { silent: true });
        this.comparisonVendors = data.vendors || [];
        const self = this;
        this.$nextTick(function() { self.renderCharts(); });
      } catch (e) {
        this.showToast('Could not load the comparison data', 'error');
      } finally {
        this.loadingComparison = false;
      }
    },

    async loadResults() {
      if (!this.analysisId) return;
      this.loadingDecision = true;
      try {
        const data = await Platform.fetch.get('/dashboard/api/vendor-analysis/' + this.analysisId + '/results', null, { silent: true });
        this.rankedVendors = data.vendors || [];
        this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      } catch (e) {
        this.showToast('Could not load the ranked results', 'error');
      } finally {
        this.loadingDecision = false;
      }
    },

    formatCell(value, format) {
      if (value == null) return 'Not recorded';
      if (format === 'score') return parseFloat(value).toFixed(1);
      if (format === 'currency') {
        if (window.currencyManager) return window.currencyManager.format(parseFloat(value));
        return parseFloat(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
      }
      return value;
    },

    /* ============================================================ */
    /* Manual Score Override                                        */
    /* ============================================================ */

    manualScoreGroups: [
      {
        key: 'risk', label: 'Risk Assessment', hint: 'Lower is better (1 = minimal risk, 10 = critical risk)',
        fields: [
          { key: 'vendor_lock_in_risk', label: 'Vendor Lock-in', rubric: '1 = Open standards, easy exit · 5 = Some proprietary elements · 10 = Fully proprietary, no exit path' },
          { key: 'market_position_risk', label: 'Market Position', rubric: '1 = Market leader, growing · 5 = Stable mid-market · 10 = Declining, acquisition risk' },
          { key: 'support_continuity_risk', label: 'Support Continuity', rubric: '1 = 24/7 enterprise support, SLA-backed · 5 = Business hours, community forums · 10 = No guaranteed support' },
          { key: 'technology_maturity_risk', label: 'Technology Maturity', rubric: '1 = Mature, battle-tested · 5 = Established but evolving · 10 = Pre-release or unproven at scale' },
          { key: 'compliance_risk', label: 'Compliance', rubric: '1 = SOC2 + ISO27001 + industry certs · 5 = Basic compliance, some gaps · 10 = No compliance certifications' }
        ]
      },
      {
        key: 'strategic', label: 'Strategic Fit', hint: 'Higher is better (1 = poor fit, 10 = perfect alignment)',
        fields: [
          { key: 'technology_alignment', label: 'Technology Alignment', rubric: '1 = Incompatible stack · 5 = Requires adapters · 10 = Native integration with our tech stack' },
          { key: 'roadmap_alignment', label: 'Roadmap Alignment', rubric: '1 = Diverging roadmap · 5 = Partial overlap · 10 = Roadmap fully aligned with our strategy' },
          { key: 'vendor_relationship', label: 'Vendor Relationship', rubric: '1 = No existing relationship · 5 = Some engagement · 10 = Strategic partner, executive sponsor' },
          { key: 'future_proofing', label: 'Future Proofing', rubric: '1 = Legacy, no innovation · 5 = Steady improvement · 10 = Industry-leading innovation, AI/cloud native' },
          { key: 'ecosystem_fit', label: 'Ecosystem Fit', rubric: '1 = Isolated, no integrations · 5 = Common integrations available · 10 = Rich marketplace, open APIs' }
        ]
      },
      {
        key: 'implementation', label: 'Implementation & Technical', hint: 'Mixed — see individual rubrics',
        fields: [
          { key: 'implementation_complexity', label: 'Complexity', rubric: '1 = Plug-and-play SaaS · 5 = Moderate config needed · 10 = Multi-year custom implementation (lower is better)' },
          { key: 'skill_availability', label: 'Skill Availability', rubric: '1 = Rare specialists needed · 5 = Available but competitive · 10 = Abundant talent pool (higher is better)' },
          { key: 'scalability_rating', label: 'Scalability', rubric: '1 = Single-tenant, limited · 5 = Scales with effort · 10 = Auto-scaling, enterprise-proven (higher is better)' },
          { key: 'security_rating', label: 'Security', rubric: '1 = Basic auth only · 5 = Standard security · 10 = Zero-trust, encryption at rest+transit, RBAC (higher is better)' },
          { key: 'performance_rating', label: 'Performance', rubric: '1 = Slow, frequent outages · 5 = Acceptable SLAs · 10 = Sub-second response, 99.99% uptime (higher is better)' }
        ]
      }
    ],

    getManualScore(vendor, field) {
      if (!vendor.manual_scores) return 0;
      return vendor.manual_scores[field] || 0;
    },

    async setManualScore(vendorId, field, value) {
      let self = this;
      let saveKey = vendorId + '_' + field;
      this.manualScoreSaving[saveKey] = 'saving';

      // Optimistic update on the local data
      let vendor = this.comparisonVendors.find(function(v) { return v.id === vendorId; });
      if (vendor) {
        if (!vendor.manual_scores) vendor.manual_scores = {};
        vendor.manual_scores[field] = value;
      }

      try {
        let payload = {};
        payload[field] = value;
        let data = await Platform.fetch.patch('/dashboard/api/vendor-analyses/' + this.analysisId + '/options/' + vendorId + '/scores', payload, { silent: true });
        // Update recalculated aggregate scores on the vendor
        if (vendor && data.recalculated) {
          if (data.recalculated.risk_score != null) vendor.risk_score = data.recalculated.risk_score;
          if (data.recalculated.strategic_fit_score != null) vendor.strategic_fit_score = data.recalculated.strategic_fit_score;
          if (data.recalculated.implementation_score != null) vendor.implementation_score = data.recalculated.implementation_score;
          if (data.recalculated.total_score != null) vendor.total_score = data.recalculated.total_score;
        }
        self.manualScoreSaving[saveKey] = 'saved';
        self.$nextTick(function() { self.renderCharts(); });
        setTimeout(function() { self.manualScoreSaving[saveKey] = ''; }, 1500);
      } catch (e) {
        self.showToast((e && e.data && e.data.error) || 'Failed to save score', 'error');
        self.manualScoreSaving[saveKey] = 'error';
        // Only a network failure resets the pill back to idle; an HTTP error from the
        // server leaves it on 'error' until the user retries, matching prior behavior.
        if (e && e.type === 'NetworkError') {
          setTimeout(function() { self.manualScoreSaving[saveKey] = ''; }, 3000);
        }
      }
    },

    clearManualScore(vendorId, field) {
      let self = this;
      let vendor = this.comparisonVendors.find(function(v) { return v.id === vendorId; });
      if (vendor && vendor.manual_scores) {
        vendor.manual_scores[field] = null;
      }
      let saveKey = vendorId + '_' + field;
      this.manualScoreSaving[saveKey] = 'saving';
      let payload = {};
      payload[field] = null;
      Platform.fetch.patch('/dashboard/api/vendor-analyses/' + this.analysisId + '/options/' + vendorId + '/scores', payload, { silent: true })
        .then(function(data) {
          self.manualScoreSaving[saveKey] = 'saved';
          setTimeout(function() { self.manualScoreSaving[saveKey] = ''; }, 1500);
          if (data && data.recalculated && vendor) {
            if (data.recalculated.risk_score != null) vendor.risk_score = data.recalculated.risk_score;
            if (data.recalculated.strategic_fit_score != null) vendor.strategic_fit_score = data.recalculated.strategic_fit_score;
            if (data.recalculated.implementation_score != null) vendor.implementation_score = data.recalculated.implementation_score;
            if (data.recalculated.total_score != null) vendor.total_score = data.recalculated.total_score;
          }
          self.$nextTick(function() { self.renderCharts(); });
        })
        .catch(function(e) {
          self.manualScoreSaving[saveKey] = 'error';
          self.showToast((e && e.data && e.data.error) || 'Failed to clear score', 'error');
        });
    },

    /* ============================================================ */
    /* Requirements Matrix                                           */
    /* ============================================================ */

    async loadRequirements() {
      if (!this.analysisId) return;
      try {
        let data = await Platform.fetch.get('/dashboard/api/vendor-analysis/' + this.analysisId + '/requirements', null, { silent: true });
        this.requirements = data.requirements || [];
        this.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      } catch (e) {
        this.showToast('Could not load requirements', 'error');
      }
    },

    async addRequirement() {
      let name = (this.newReqName || '').trim();
      if (!name) return;
      let self = this;
      try {
        let data = await Platform.fetch.post('/dashboard/api/vendor-analysis/' + this.analysisId + '/requirements', {
          capability_name: name,
          importance: this.newReqImportance || 'medium',
          must_have: this.newReqMustHave || false
        }, { silent: true });
        if (data.success) {
          self.requirements.push(data.requirement);
          self.newReqName = '';
          self.newReqMustHave = false;
          self.showToast('Requirement added', 'success');
          self.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
        } else {
          self.showToast(data.error || 'Failed to add requirement', 'error');
        }
      } catch (e) {
        self.showToast((e && e.data && e.data.error) || 'Error adding requirement', 'error');
      }
    },

    startEditReq(req) {
      this.editingReqId = req.id;
      this.editingReqName = req.capability_name;
    },

    cancelEditReq() {
      this.editingReqId = null;
      this.editingReqName = '';
    },

    async saveEditReq(reqId) {
      let name = (this.editingReqName || '').trim();
      if (!name) { this.cancelEditReq(); return; }
      let self = this;
      try {
        let data = await Platform.fetch.patch('/dashboard/api/vendor-analysis/' + this.analysisId + '/requirements/' + reqId, {
          capability_name: name
        }, { silent: true });
        if (data.success) {
          let idx = self.requirements.findIndex(function(r) { return r.id === reqId; });
          if (idx >= 0) self.requirements[idx] = data.requirement;
        } else {
          self.showToast(data.error || 'Failed to update', 'error');
        }
      } catch (e) {
        self.showToast((e && e.data && e.data.error) || 'Error updating requirement', 'error');
      }
      this.editingReqId = null;
      this.editingReqName = '';
    },

    async deleteRequirement(reqId) {
      if (!(await Platform.modal.confirm('Delete this requirement?'))) return;
      let self = this;
      try {
        await Platform.fetch.delete('/dashboard/api/vendor-analysis/' + this.analysisId + '/requirements/' + reqId, { silent: true });
        self.requirements = self.requirements.filter(function(r) { return r.id !== reqId; });
        self.showToast('Requirement deleted', 'success');
      } catch (e) {
        self.showToast((e && e.data && e.data.error) || 'Failed to delete', 'error');
      }
    },

    getFulfillment(req, vendorId) {
      if (!req.fulfillment) return null;
      return req.fulfillment[String(vendorId)] || null;
    },

    getFulfillmentLabel(req, vendorId) {
      let status = this.getFulfillment(req, vendorId);
      if (status === 'met') return 'Met - Click to change';
      if (status === 'partial') return 'Partially met - Click to change';
      if (status === 'not_met') return 'Not met - Click to change';
      return 'Not assessed - Click to set';
    },

    async cycleFulfillment(reqId, vendorId) {
      let req = this.requirements.find(function(r) { return r.id === reqId; });
      if (!req) return;
      let current = this.getFulfillment(req, vendorId);
      let cycle = [null, 'met', 'partial', 'not_met'];
      let idx = cycle.indexOf(current);
      let next = cycle[(idx + 1) % cycle.length];

      // Optimistic update
      if (!req.fulfillment) req.fulfillment = {};
      req.fulfillment[String(vendorId)] = next;

      let self = this;
      try {
        await Platform.fetch.post('/dashboard/api/vendor-analysis/' + this.analysisId + '/requirements/' + reqId + '/fulfillment', {
          vendor_option_id: vendorId, status: next
        }, { silent: true });
        self.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      } catch (e) {
        req.fulfillment[String(vendorId)] = current;
        self.showToast((e && e.data && e.data.error) || 'Error saving fulfillment', 'error');
        self.$nextTick(function() { if (typeof lucide !== 'undefined') lucide.createIcons(); });
      }
    },

    getVendorCoverage(vendorId) {
      if (!this.requirements.length) return '0%';
      let total = this.requirements.length;
      let met = 0;
      let partial = 0;
      let strVid = String(vendorId);
      for (let i = 0; i < this.requirements.length; i++) {
        let f = this.requirements[i].fulfillment;
        if (f && f[strVid] === 'met') met++;
        else if (f && f[strVid] === 'partial') partial += 0.5;
      }
      let pct = Math.round(((met + partial) / total) * 100);
      return pct + '% (' + met + ' met, ' + Math.round(partial * 2) + ' partial)';
    },

    /* ============================================================ */
    /* Scenario Comparison                                           */
    /* ============================================================ */

    get newScenarioWeightTotal() {
      let w = this.newScenarioWeights;
      return (parseInt(w.cost) || 0) + (parseInt(w.capability_coverage) || 0) +
        (parseInt(w.risk) || 0) + (parseInt(w.strategic_fit) || 0) + (parseInt(w.implementation) || 0);
    },

    async loadScenarios() {
      if (!this.analysisId) return;
      this.loadingScenarios = true;
      try {
        let data = await Platform.fetch.get('/dashboard/api/vendor-analysis/' + this.analysisId + '/scenarios', null, { silent: true });
        if (data.success) {
          this.scenarios = data.scenarios || [];
        }
      } catch (e) {
        this.showToast('Could not load saved scenarios', 'error');
      }
      this.loadingScenarios = false;
    },

    async saveCurrentAsScenario() {
      let name = (this.saveScenarioName || '').trim();
      if (!name) { this.showToast('Enter a scenario name', 'error'); return; }
      try {
        let data = await Platform.fetch.post('/dashboard/api/vendor-analysis/' + this.analysisId + '/scenarios/save-current', {
          scenario_name: name
        }, { silent: true });
        if (data.success) {
          this.scenarios.push(data.scenario);
          this.saveScenarioName = '';
          this.showToast('Scenario saved', 'success');
        } else {
          this.showToast(data.error || 'Failed to save scenario', 'error');
        }
      } catch (e) { this.showToast((e && e.data && e.data.error) || 'Error saving scenario', 'error'); }
    },

    async createScenario() {
      let name = (this.newScenarioName || '').trim();
      if (!name) { this.showToast('Enter a scenario name', 'error'); return; }
      if (this.newScenarioWeightTotal !== 100) { this.showToast('Weights must total 100%', 'error'); return; }
      let w = this.newScenarioWeights;
      let weights = {
        cost: parseInt(w.cost) / 100,
        capability_coverage: parseInt(w.capability_coverage) / 100,
        risk: parseInt(w.risk) / 100,
        strategic_fit: parseInt(w.strategic_fit) / 100,
        implementation: parseInt(w.implementation) / 100
      };
      try {
        let data = await Platform.fetch.post('/dashboard/api/vendor-analysis/' + this.analysisId + '/scenarios', {
          scenario_name: name, criteria_weights: weights
        }, { silent: true });
        if (data.success) {
          this.scenarios.push(data.scenario);
          this.newScenarioName = '';
          this.showNewScenarioForm = false;
          this.newScenarioWeights = { cost: 20, capability_coverage: 20, risk: 20, strategic_fit: 20, implementation: 20 };
          this.showToast('Scenario created', 'success');
        } else {
          this.showToast(data.error || 'Failed to create scenario', 'error');
        }
      } catch (e) { this.showToast((e && e.data && e.data.error) || 'Error creating scenario', 'error'); }
    },

    async deleteScenario(scenarioId) {
      try {
        let data = await Platform.fetch.delete('/dashboard/api/vendor-analysis/' + this.analysisId + '/scenarios/' + scenarioId, { silent: true });
        if (data.success) {
          this.scenarios = this.scenarios.filter(function(s) { return s.id !== scenarioId; });
          this.showToast('Scenario deleted', 'success');
        } else {
          this.showToast(data.error || 'Failed to delete', 'error');
        }
      } catch (e) { this.showToast((e && e.data && e.data.error) || 'Error deleting scenario', 'error'); }
    },

    scenarioVendorNames() {
      /* Collect unique vendor names across all scenarios */
      let names = {};
      this.scenarios.forEach(function(s) {
        (s.vendor_rankings || []).forEach(function(r) {
          names[r.vendor_option_id] = r.vendor_name;
        });
      });
      return Object.keys(names).map(function(id) { return { id: parseInt(id), name: names[id] }; });
    },

    scenarioScore(scenario, vendorId) {
      let entry = (scenario.vendor_rankings || []).find(function(r) { return r.vendor_option_id === vendorId; });
      return entry ? entry.total_score : null;
    },

    scenarioRank(scenario, vendorId) {
      let entry = (scenario.vendor_rankings || []).find(function(r) { return r.vendor_option_id === vendorId; });
      return entry ? entry.ranking : null;
    },

    scenarioWinnerChanged(scenario, baselineScenario) {
      if (!baselineScenario || !scenario) return false;
      return scenario.recommended_vendor_id !== baselineScenario.recommended_vendor_id;
    },

    renderCharts() {
      const vendors = this.comparisonVendors;
      if (!vendors.length || typeof Chart === 'undefined') return;

      const fg = getComputedStyle(document.documentElement).getPropertyValue('--foreground').trim();
      const chartFontColor = fg ? 'hsl(' + fg + ')' : '#888';
      const chartColors = [
        'rgba(59, 130, 246, 0.7)', 'rgba(34, 197, 94, 0.7)', 'rgba(249, 115, 22, 0.7)',
        'rgba(168, 85, 247, 0.7)', 'rgba(236, 72, 153, 0.7)', 'rgba(14, 165, 233, 0.7)'
      ];
      const chartBorders = chartColors.map(function(c) { return c.replace('0.7', '1'); });

      const scoreCtx = document.getElementById('score-chart');
      if (scoreCtx) {
        if (this.scoreChart) this.scoreChart.destroy();
        this.scoreChart = new Chart(scoreCtx, {
          type: 'bar',
          data: {
            labels: vendors.map(function(v) { return v.name; }),
            datasets: [{
              label: 'Total Score',
              data: vendors.map(function(v) { return v.total_score || 0; }),
              backgroundColor: chartColors.slice(0, vendors.length),
              borderColor: chartBorders.slice(0, vendors.length),
              borderWidth: 1
            }]
          },
          options: { responsive: true, scales: { y: { beginAtZero: true, max: 100 } }, plugins: { legend: { display: false } } }
        });
      }

      const tcoCtx = document.getElementById('tco-chart');
      if (tcoCtx) {
        if (this.tcoChart) this.tcoChart.destroy();
        this.tcoChart = new Chart(tcoCtx, {
          type: 'bar',
          data: {
            labels: vendors.map(function(v) { return v.name; }),
            datasets: [{
              label: 'TCO (5yr)',
              data: vendors.map(function(v) { return parseFloat(v.tco_total) || 0; }),
              backgroundColor: chartColors.slice(0, vendors.length),
              borderColor: chartBorders.slice(0, vendors.length),
              borderWidth: 1
            }]
          },
          options: { responsive: true, scales: { y: { beginAtZero: true } }, plugins: { legend: { display: false } } }
        });
      }

      const radarCtx = document.getElementById('radar-chart');
      if (radarCtx) {
        if (this.radarChart) this.radarChart.destroy();
        const radarLabels = ['Cost', 'Capability', 'Risk', 'Strategic Fit', 'Implementation'];
        const radarDatasets = vendors.map(function(v, i) {
          return {
            label: v.name,
            data: [
              v.cost_score || 0, v.capability_score || 0, v.risk_score || 0,
              v.strategic_fit_score || 0, v.implementation_score || 0
            ],
            borderColor: chartBorders[i % chartBorders.length],
            backgroundColor: chartColors[i % chartColors.length].replace('0.7', '0.15'),
            borderWidth: 2,
            pointBackgroundColor: chartBorders[i % chartBorders.length]
          };
        });
        this.radarChart = new Chart(radarCtx, {
          type: 'radar',
          data: { labels: radarLabels, datasets: radarDatasets },
          options: {
            responsive: true,
            scales: { r: { beginAtZero: true, max: 100, ticks: { stepSize: 20 } } },
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 12 } } }
          }
        });
      }
    },

    /* ============================================================ */
    /* Decision                                                      */
    /* ============================================================ */

    confirmDecision() {
      if (!this.decision.status) { this.showToast('Select a decision', 'error'); return; }
      if (!this.decision.rationale.trim()) { this.showToast('Enter a rationale', 'error'); return; }
      const self = this;
      window.dispatchEvent(new CustomEvent('open-confirm-vendor-decision', {
        detail: { callback: function() { self.submitDecision(); } }
      }));
    },

    async submitDecision() {
      if (!this.decision.status || !this.decision.rationale.trim()) return;
      try {
        const result = await Platform.fetch.post('/dashboard/api/vendor-analysis/' + this.analysisId + '/decision', {
          decision: this.decision.status,
          rationale: this.decision.rationale,
          selected_vendor_option_id: this.decision.vendorId ? parseInt(this.decision.vendorId) : null
        }, { silent: true });
        this.analysis.approval_status = result.approval_status;
        this.showToast('Decision recorded successfully', 'success');
      } catch (e) { this.showToast((e && e.data && e.data.error) || 'Error recording decision', 'error'); }
    },

    /* ============================================================ */
    /* Export                                                         */
    /* ============================================================ */

    async exportCSV() {
      if (!this.analysisId) return;
      try {
        // Platform.fetch returns a parsed body (json/text), which would discard the blob.
        const resp = await fetch('/dashboard/api/vendor-analysis/' + this.analysisId + '/export?format=csv'); // raw-fetch-ok: downloads a blob (resp.blob()) and needs the raw Response object
        if (!resp.ok) throw new Error('exportCSV: ' + resp.status);
        const blob = await resp.blob();
        let url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'vendor_analysis_' + this.analysisId + '_' + new Date().toISOString().split('T')[0] + '.csv';
        document.body.appendChild(a); a.click(); document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        this.showToast('CSV exported', 'success');
      } catch (e) { this.showToast('Export failed', 'error'); }
    },

    /* ============================================================ */
    /* Toast notifications                                           */
    /* ============================================================ */

    showToast(message, type) {
      let container = document.getElementById('toast-container');
      if (!container) return;
      let toast = document.createElement('div');
      let borderClass = type === 'success' ? 'border-success/30 bg-success/5' : type === 'error' ? 'border-destructive/30 bg-destructive/5' : 'border-primary/30 bg-primary/5';
      toast.className = 'rounded-lg border p-4 pr-10 max-w-xs w-full shadow-lg relative ' + borderClass;
      toast.setAttribute('role', 'alert');

      let span = document.createElement('span');
      span.className = 'text-foreground text-sm';
      span.textContent = message;
      toast.appendChild(span);

      let btn = document.createElement('button');
      btn.className = 'absolute top-2 right-2 text-muted-foreground hover:text-foreground';
      btn.setAttribute('aria-label', 'Dismiss notification');
      btn.innerHTML = '<i data-lucide="x" class="w-4 h-4"></i>';
      btn.addEventListener('click', function() { toast.remove(); });
      toast.appendChild(btn);

      container.appendChild(toast);
      if (typeof lucide !== 'undefined') lucide.createIcons();
      setTimeout(function() { if (toast.parentNode) toast.remove(); }, 5000);
    },

    // --- Stakeholder multi-rater ---
    async loadStakeholders() {
      if (!this.analysisId) return;
      this.loadingStakeholders = true;
      try {
        let data = await Platform.fetch.get('/dashboard/api/vendor-analysis/' + this.analysisId + '/stakeholders', null, { silent: true });
        if (data.success) this.stakeholders = data.stakeholders || [];
        // Also load consensus
        let cData = await Platform.fetch.get('/dashboard/api/vendor-analysis/' + this.analysisId + '/stakeholders/consensus', null, { silent: true });
        if (cData.success) this.stakeholderConsensus = cData;
      } catch (e) {
        this.showToast('Could not load stakeholders', 'error');
      }
      this.loadingStakeholders = false;
    },

    async inviteStakeholder() {
      if (!this.inviteUserId || !this.inviteRole) return;
      try {
        let data = await Platform.fetch.post('/dashboard/api/vendor-analysis/' + this.analysisId + '/stakeholders', {
          stakeholder_id: parseInt(this.inviteUserId),
          stakeholder_role: this.inviteRole
        }, { silent: true });
        if (data.success) {
          this.stakeholders.push(data.stakeholder);
          this.inviteUserId = '';
          this.showInviteForm = false;
          this.showToast('Stakeholder invited', 'success');
        } else {
          this.showToast(data.error || 'Failed to invite', 'error');
        }
      } catch (e) {
        this.showToast((e && e.data && e.data.error) || 'Error inviting stakeholder', 'error');
      }
    },

    async removeStakeholder(inputId) {
      try {
        let data = await Platform.fetch.delete('/dashboard/api/vendor-analysis/' + this.analysisId + '/stakeholders/' + inputId, { silent: true });
        if (data.success) {
          this.stakeholders = this.stakeholders.filter(function(s) { return s.id !== inputId; });
          this.showToast('Stakeholder removed', 'success');
        }
      } catch (e) {
        this.showToast((e && e.data && e.data.error) || 'Error removing stakeholder', 'error');
      }
    },

    getConsensusScore(vendorId, dimension) {
      if (!this.stakeholderConsensus || !this.stakeholderConsensus.vendor_consensus) return null;
      let vc = this.stakeholderConsensus.vendor_consensus[String(vendorId)];
      return vc ? vc[dimension] : null;
    },

    getPreferenceCount(vendorId) {
      if (!this.stakeholderConsensus || !this.stakeholderConsensus.vendor_preferences) return 0;
      return this.stakeholderConsensus.vendor_preferences[String(vendorId)] || 0;
    }
  };
}

;(function() {
  const el = document.querySelector('[data-analysis-id]');
  const raw = el ? el.dataset.analysisId : 'null';
  let id = (raw === 'null' || raw === '' || raw === 'undefined') ? null : parseInt(raw, 10);
  document.addEventListener('alpine:init', function() {
    if (typeof Alpine !== 'undefined' && Alpine.data) {
      Alpine.data('vendorAnalysisDetail', function() { return vendorAnalysisDetail(id); });
    }
  });
})();
