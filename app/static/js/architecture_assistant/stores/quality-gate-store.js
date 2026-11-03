/**
 * Quality Gate Alpine Store — manages step quality assessment state.
 *
 * Used by the quality gate overlay to show scores, dimensions, and
 * failing items. Talks to /api/wizard/<solution_id>/quality/* routes.
 */
document.addEventListener('alpine:init', () => {
    Alpine.store('qualityGate', {
        // State
        loading: false,
        visible: false,
        assessment: null,
        canAdvance: true,
        error: null,

        // Current context
        solutionId: null,
        currentStep: null,

        init() {
            // Initialized by journey_v2 component
        },

        /**
         * Assess quality for the current step.
         * @param {number} solutionId
         * @param {number} step
         * @param {object} stepData — current step fields
         * @returns {Promise<object>} assessment result
         */
        async assess(solutionId, step, stepData) {
            this.solutionId = solutionId;
            this.currentStep = step;
            this.loading = true;
            this.error = null;

            try {
                /* Unguarded, a 500 parsed to `{}`: `passed` and `hard_block` were both
                   undefined, so `canAdvance` came out true and the overlay was shown
                   with a blank score — an assessment that never ran, displayed as a
                   passing one. */
                const data = await Platform.fetch.post(`/api/wizard/${solutionId}/quality/assess`, {
                    step,
                    step_data: stepData,
                }, { silent: true }); // silent: true because we handle inline error state

                this.assessment = data;
                this.canAdvance = data.passed || !data.hard_block;
                this.visible = true;
                return data;

            } catch (e) {
                this.error = 'Quality assessment unavailable';
                // No assessment exists, so nothing may be rendered as one.
                this.assessment = null;
                this.visible = false;
                // Degrade gracefully — allow advancement, but say why the gate is absent.
                this.canAdvance = true;
                if (window.Platform && window.Platform.toast) {
                    window.Platform.toast.warning('Quality assessment is unavailable — this step was not checked. You can continue, but it has not been reviewed.');
                }
                return null;
            } finally {
                this.loading = false;
            }
        },

        /**
         * Check if advancement is allowed.
         */
        async checkAdvance(solutionId, step, stepData) {
            this.solutionId = solutionId;
            this.currentStep = step;
            this.loading = true;

            try {
                const data = await Platform.fetch.post(`/api/wizard/${solutionId}/quality/can-advance`, {
                    step,
                    step_data: stepData,
                }, { silent: true }); // silent: true because we handle inline error state

                // A 500 here parsed to `{}`, leaving can_advance undefined and the
                // overlay hidden — the gate silently did not run.
                this.assessment = data.assessment;
                this.canAdvance = data.can_advance;

                // Show overlay if not passing
                if (this.assessment && !this.assessment.passed) {
                    this.visible = true;
                }

                return data.can_advance;

            } catch (e) {
                // Platform.fetch throws on non-ok responses, including network errors.
                // We need to detect session timeout (redirect to login) which would have been a non-JSON response.
                // Since Platform.fetch throws before parsing, we cannot inspect headers directly.
                // However, the original code redirected on 401/403 or non-JSON content-type.
                // We'll treat any error as a potential login redirect if the error type indicates a network or HTTP error.
                if (e.type === 'HttpError' && (e.status === 401 || e.status === 403)) {
                    window.location.href = '/account/login';
                    return false;
                }
                // If the error is a network error (type 'NetworkError'), it could be a redirect.
                // The original code checked for redirected or non-JSON content-type.
                // We'll assume network errors could be due to a redirect and redirect to login.
                if (e.type === 'NetworkError') {
                    window.location.href = '/account/login';
                    return false;
                }
                this.canAdvance = true;
                if (window.Platform && window.Platform.toast) {
                    window.Platform.toast.warning('The quality gate could not be checked — you are being let through unchecked.');
                }
                return true;
            } finally {
                this.loading = false;
            }
        },

        /**
         * Record that user skipped a soft-block gate. This is an audit-trail write
         * only — canAdvance was already set true before this is called, so the user's
         * ability to proceed never depends on it, and a failure must not stop them.
         * It must still be reported: the skip is the governance record of a quality
         * gate being overridden, and an override nobody can see later is worse than
         * one that was refused.
         */
        async recordSkip() {
            if (!this.assessment || !this.solutionId) return;

            try {
                // The native API resolves on 4xx/5xx, so without the wrapper the audit
                // fail with a 500 and look exactly like a successful one.
                await Platform.fetch.post(`/api/wizard/${this.solutionId}/quality/skip`, {
                    step: this.currentStep,
                    overall_score: this.assessment.overall_score,
                    threshold: this.assessment.threshold,
                }, { silent: true }); // silent: true because we handle the error inline
            } catch (e) {
                if (window.Platform && window.Platform.toast) {
                    window.Platform.toast.warning(
                        'You can continue, but skipping this quality gate was not recorded for governance review: '
                        + (e.message || 'request failed') + '.'
                    );
                }
            }

            this.dismiss();
        },

        /**
         * Dismiss the overlay.
         */
        dismiss() {
            this.visible = false;
            this.assessment = null;
        },

        // Computed helpers for the template
        get scoreColor() {
            if (!this.assessment) return 'text-muted-foreground';
            if (this.assessment.passed) return 'text-emerald-600';
            if (this.assessment.overall_score >= this.assessment.threshold - 10) return 'text-amber-600';
            return 'text-destructive';
        },

        get scorePercentage() {
            return this.assessment?.overall_score ?? 0; // zero-fallback-ok: the score ring is only drawn once an assessment has returned
        },

        get failingItems() {
            return this.assessment?.failing_items || [];
        },

        get dimensions() {
            return this.assessment?.dimensions || [];
        },

        get isHardBlock() {
            return this.assessment?.hard_block && !this.assessment?.passed;
        },
    });
});
