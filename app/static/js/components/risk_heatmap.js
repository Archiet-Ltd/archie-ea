/**
 * Risk heat map: edit one risk of a solution from the grid.
 * The grid and the per-cell lists are rendered by the server; this only
 * drives the edit form and saves it through the solution's own risk route.
 */
function riskHeatmap(risks) {
    var LEVELS = { 1: 'very_low', 2: 'low', 3: 'medium', 4: 'high', 5: 'critical' };
    return {
        risks: Array.isArray(risks) ? risks : [],
        form: { id: null, legacy: false, title: '', probability: 3, impact: 3, status: 'open', owner: '', mitigation: '' },
        saving: false,
        error: '',

        edit: function (id, legacy) {
            var risk = this.risks.find(function (r) { return r.id === id && r.legacy === legacy; });
            if (!risk) return;
            this.form = {
                id: risk.id, legacy: risk.legacy, title: risk.title,
                probability: risk.probability, impact: risk.impact,
                status: risk.status, owner: risk.owner, mitigation: risk.mitigation
            };
            this.error = '';
            document.querySelectorAll('[data-modal-open]').forEach(function (el) {
                var target = el.getAttribute('data-modal-open');
                if (target && target.indexOf('cell-') === 0) Platform.modal.close(target);
            });
            Platform.modal.open('risk-edit');
        },

        save: async function () {
            if (this.saving) return;
            this.saving = true;
            this.error = '';
            var solutionId = window.location.pathname.split('/')[2];
            try {
                await Platform.fetch.put('/solutions/' + solutionId + '/risks/' + this.form.id, {
                    probability: LEVELS[this.form.probability],
                    impact: LEVELS[this.form.impact],
                    status: this.form.status,
                    owner: this.form.owner,
                    mitigation: this.form.mitigation
                }, { silent: true });
                window.location.reload();
            } catch (e) {
                this.error = (e && e.data && e.data.error) || (e && e.message) || 'The risk could not be saved.';
            } finally {
                this.saving = false;
            }
        }
    };
}
