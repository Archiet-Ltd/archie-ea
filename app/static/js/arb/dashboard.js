/**
 * arb/dashboard.js
 * Alpine.js component factory for the "Create ARB Review" modal.
 * Capability-based governance: decision_sought, capability impacts, application linkage.
 */

function arbReviewCreateModal() {
  return {
    formData: {
      title: '',
      description: '',
      review_type: '',
      decision_sought: '',
      alternatives_considered: '',
      togaf_phase: '',
      archimate_layer: '',
      priority: 'medium',
      business_impact: 'medium',
      estimated_effort: 'medium',
      solution_id: '',
      adr_id: '',
      architecture_model_id: '',
      application_ids: [],
      capability_ids: [],
      capability_impact_type: 'modifies',
      acknowledge_no_subject: false
    },
    formOptions: {
      solutions: [],
      review_types: [],
      loadError: false,
      adrs: [],
      architecture_models: [],
      applications: [],
      capabilities: [],
      decision_types: [],
      impact_types: [],
      capability_required_review_types: []
    },
    formDataLoaded: false,
    submitting: false,
    errorMsg: '',

    init() {
      this.loadFormData();
    },

    isCapabilityRequired() {
      const rt = this.formData.review_type;
      return rt && this.formOptions.capability_required_review_types.indexOf(rt) >= 0;
    },

    loadFormData() {
      const url = window.__ARB_CONFIG__?.formDataUrl;
      if (this.formDataLoaded) return;
      // A missing URL used to be a SILENT return, so a page that included this
      // modal without declaring formDataUrl rendered permanently empty Review
      // Type and Decision Type dropdowns with loadError false -- the app could
      // not tell it had failed, and neither could the user. That is how ARB
      // review creation was blocked on /arb/reviews for as long as it was.
      // Absent configuration is a failure, and it says so.
      if (!url) {
        this.formOptions.loadError = true;
        this.errorMsg = 'This page did not provide the review form data URL, so the '
          + 'review options could not be loaded. Please report this.';
        return;
      }
      // Platform.fetch throws on non-ok responses; we catch to paint inline error state
      Platform.fetch.get(url, { silent: true })
        .then(data => {
          // An envelope that is not success:true is a failure, not a no-op. This
          // branch had no else, so an unexpected shape left every dropdown empty
          // and loadError false.
          if (!data || !data.success) {
            this.formOptions.loadError = true;
            this.errorMsg = (data && data.error)
              || 'The review form options could not be loaded. Please refresh.';
            return;
          }
          this.formOptions.solutions = data.solutions || [];
          this.formOptions.review_types = data.review_types || [];
          this.formOptions.adrs = data.adrs || [];
          this.formOptions.architecture_models = data.architecture_models || [];
          this.formOptions.capabilities = data.capabilities || [];
          this.formOptions.applications = data.applications || [];
          this.formOptions.decision_types = data.decision_types || [];
          this.formOptions.impact_types = data.impact_types || [{ value: 'modifies', label: 'Modifies' }];
          this.formOptions.capability_required_review_types = data.capability_required_review_types || [];
          if (data.impact_types && data.impact_types.length > 0) {
            this.formData.capability_impact_type = data.impact_types[0].value;
          }
          this.formDataLoaded = true;
        })
        .catch(() => {
          this.formOptions.loadError = true;
          this.errorMsg = 'Failed to load form options. Please refresh.';
        });
    },

    submitCreateReview() {
      // FAR-017: Prevent double-click duplicates
      if (this.submitting) return;
      if (!this.formData.title.trim()) { this.errorMsg = 'Title is required.'; return; }
      if (!this.formData.review_type) { this.errorMsg = 'Review type is required.'; return; }
      if (!this.formData.decision_sought) { this.errorMsg = 'Decision sought is required.'; return; }
      const capIds = Array.isArray(this.formData.capability_ids)
        ? this.formData.capability_ids.map(id => parseInt(id, 10)).filter(n => !isNaN(n))
        : [];
      if (this.isCapabilityRequired() && capIds.length === 0) {
        this.errorMsg = 'At least one capability is required for this review type.';
        return;
      }
      // F-06 (2 Sep 2026): without a linked ADR/Architecture Model the review
      // carries no evidence snapshot — require the explicit acknowledgement shown
      // in the warning banner rather than letting it through silently.
      if (!this.formData.adr_id && !this.formData.architecture_model_id && !this.formData.acknowledge_no_subject) {
        this.errorMsg = 'Link an ADR or Architecture Model, or confirm you understand this will be a record-only item: the board can decide it, but the decision is stored on this review alone.';
        return;
      }
      this.submitting = true;
      this.errorMsg = '';
      const url = window.__ARB_CONFIG__?.createReviewUrl || '/arb/reviews/create';
      const impactType = this.formData.capability_impact_type || 'modifies';
      const capability_impacts = capIds.map(capId => ({
        capability_id: capId,
        impact_type: impactType,
        impact_level: 'medium'
      }));
      const appIds = Array.isArray(this.formData.application_ids)
        ? this.formData.application_ids.map(id => parseInt(id, 10)).filter(n => !isNaN(n))
        : [];
      const payload = {
        title: this.formData.title.trim(),
        description: this.formData.description?.trim() || '',
        review_type: this.formData.review_type,
        decision_sought: this.formData.decision_sought || null,
        alternatives_considered: this.formData.alternatives_considered?.trim() || null,
        togaf_phase: this.formData.togaf_phase || null,
        archimate_layer: this.formData.archimate_layer || null,
        priority: this.formData.priority,
        business_impact: this.formData.business_impact,
        estimated_effort: this.formData.estimated_effort,
        solution_id: this.formData.solution_id ? parseInt(this.formData.solution_id, 10) : null,
        adr_id: this.formData.adr_id ? parseInt(this.formData.adr_id, 10) : null,
        architecture_model_id: this.formData.architecture_model_id ? parseInt(this.formData.architecture_model_id, 10) : null,
        application_ids: appIds,
        capability_impacts: capability_impacts,
        capability_impact_type: impactType
      };
      // Platform.fetch.post serialises plain‑object body to JSON and injects CSRF automatically
      Platform.fetch.post(url, payload, { silent: true })
        .then(data => {
          this.submitting = false;
          if (!data.success) {
            this.errorMsg = Object.values(data.errors || {}).join(' ') || (data.error || 'An error occurred.');
            return;
          }
          Platform.modal.close('create-arb-review');
          if (typeof showToast === 'function') {
            showToast({ title: 'Review submitted successfully', variant: 'default' });
          }
          setTimeout(() => window.location.reload(), 800);
        })
        .catch(err => {
          this.submitting = false;
          // Platform.fetch throws a structured HttpError on a non-ok response:
          // err.data is the parsed JSON body, err.message the flattened field
          // errors, err.status the HTTP code. A real 400 (e.g. the solution
          // evidence-gate rejection) must reach the user verbatim, not be
          // relabelled as a network failure. Only a genuine transport error
          // (no response) falls through to the generic message.
          if (err && err.type === 'HttpError') {
            const data = err.data || {};
            this.errorMsg = Object.values(data.errors || {}).join(' ')
              || data.error
              || err.message
              || ('Request failed (HTTP ' + (err.status || '?') + ').');
          } else {
            this.errorMsg = (err && err.message) || 'Network error. Please try again.';
          }
        });
    }
  };
}

document.addEventListener('alpine:init', () => {
  if (window.Alpine) {
    window.Alpine.data('arbReviewCreateModal', arbReviewCreateModal);
  }
});

window.arbReviewCreateModal = arbReviewCreateModal;
