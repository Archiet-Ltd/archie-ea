// Top-level (window) factory used as x-data="architectureJourneyHub()".
// The CSP-safe Alpine evaluator (app/static/js/csp/csp-evaluator.js) resolves a
// bare x-data identifier against the component scope and then window; it never
// consults Alpine.data() registrations, so the Alpine.data() + bare-name form
// left this hub inert. See scripts/check_alpine_data_binding.py.
function architectureJourneyHub() {
  return {
    title: '',
    intent: document.body.dataset.requestedJourneyIntent || '',
    layers: [],
    deliverables: [],
    outcomeType: 'undecided',
    programmeType: '',
    programmeTypeOptions: [],
    starting: false,
    startingSolution: false,
    error: '',

    init() {
      const params = new URLSearchParams(window.location.search);
      const requested = (params.get('intent') || '').replaceAll('-', '_');
      if (requested) this.intent = requested;
      if (requested === 'business_transformation') {
        this.layers = ['motivation', 'strategy', 'business', 'data', 'implementation', 'governance'];
        this.deliverables = ['capability_map', 'value_stream', 'operating_model', 'roadmap'];
        this.outcomeType = 'undecided';
      }
      // R1-04: counts shown on selection come from the loaded template via
      // this server-rendered data attribute, never a literal in the markup.
      const el = this.$root || document.querySelector('[data-programme-type-options]');
      if (el && el.dataset.programmeTypeOptions) {
        try {
          this.programmeTypeOptions = JSON.parse(el.dataset.programmeTypeOptions) || [];
        } catch (e) {
          this.programmeTypeOptions = [];
        }
      }
    },

    get selectedProgrammeType() {
      if (!this.programmeType) return null;
      return this.programmeTypeOptions.find((option) => option.key === this.programmeType) || null;
    },

    get canStart() {
      return Boolean(this.title && this.intent && this.layers.length);
    },

    async startJourney() {
      if (!this.canStart || this.starting) return;
      this.starting = true;
      this.error = '';
      try {
        // Platform.fetch will automatically inject CSRF token and serialize plain object to JSON.
        // It throws a structured PlatformError on non-ok responses, which we catch below.
        const payload = await Platform.fetch.post('/architecture-journey/start-architecture', {
          title: this.title,
          intent: this.intent,
          selected_layers: this.layers,
          selected_deliverables: this.deliverables,
          outcome_type: this.outcomeType,
          programme_type: this.programmeType || null,
        }, { silent: true }); // silent: true because we paint our own inline error state
        // Platform.fetch returns the parsed response body directly (already JSON).
        // The server returns a { data: { redirect: ... } } structure on success.
        window.location.assign(payload.data.redirect);
      } catch (error) {
        // Platform.fetch throws a PlatformError with a message suitable for user display.
        // We preserve the existing inline error painting.
        this.error = error.message || 'The journey could not be created.';
        this.starting = false;
      }
    },

    async startSolution() {
      if (this.startingSolution) return;
      this.startingSolution = true;
      this.error = '';
      try {
        // Platform.fetch will automatically inject CSRF token and serialize plain object to JSON.
        // Passing an empty object as body ensures correct JSON serialization.
        const payload = await Platform.fetch.post('/architecture-journey/start', {}, { silent: true });
        // Platform.fetch returns the parsed response body directly (already JSON).
        // The server returns a { data: { redirect: ... } } structure on success.
        window.location.assign(payload.data.redirect);
      } catch (error) {
        // Platform.fetch throws a PlatformError with a message suitable for user display.
        // We preserve the existing inline error painting.
        this.error = error.message || 'Solution design could not be started.';
        this.startingSolution = false;
      }
    },
  };
}
window.architectureJourneyHub = architectureJourneyHub;
