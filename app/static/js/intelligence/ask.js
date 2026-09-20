/* The Ask page.
 *
 * A person opens the question card, types a business noun, chooses a match and
 * gets the impact answer for it: one row per connection, each with an owner or
 * a plain "Not recorded", a "Why?" that opens the provenance drawer and a
 * link that carries the element over to the Twin map.
 *
 * Registered as a top-level window factory and referenced as
 * x-data="askSurface()"; the CSP-safe expression interpreter resolves names
 * against the component scope and window, never against Alpine.data().
 */
function askSurface() {
    var Intelligence = window.Intelligence;
    return Object.assign(Intelligence.picker('ask'), Intelligence.drawerState(), {
        questionOpen: false,
        state: 'idle',
        busy: false,
        centreId: null,
        rows: [],
        notComputed: false,
        twinMapUrl: '',
        recomputing: false,
        recomputeLine: '',
        recomputeFailed: false,

        init() {
            this.twinMapUrl = this.$el.getAttribute('data-twin-map-url') || '';
        },

        toggleQuestion() {
            this.questionOpen = !this.questionOpen;
            if (this.questionOpen) {
                var self = this;
                this.$nextTick(function () { self.$refs.pickerInput.focus(); });
            }
        },

        twinMapHref(row) {
            return this.twinMapUrl + '?element=' + row.elementId;
        },

        onSelect(option) {
            this.load(option.id);
        },

        async load(elementId) {
            this.centreId = elementId;
            this._loadSeq = (this._loadSeq || 0) + 1;
            var seq = this._loadSeq;
            this.busy = true;
            this.state = 'loading';
            this.notComputed = false;
            this.syncDrawer();
            try {
                var payload = await Intelligence.fetchImpact(elementId, { maxDepth: 3, includeDerived: true });
                if (seq !== this._loadSeq) return;
                var summary = payload.summary || {};
                this.rows = Intelligence.buildRows(payload, elementId);
                this.notComputed = summary.derivation_state === 'not_computed' && this.rows.length > 0;
                this.state = this.rows.length ? 'ready' : 'empty';
                this.statusText = this.rows.length + ' results for ' + this.term.trim();
            } catch (err) {
                if (seq !== this._loadSeq) return;
                this.rows = [];
                this.state = 'error';
            }
            this.busy = false;
            this.syncDrawer();
            this.$nextTick(function () { Intelligence.refreshIcons(); });
        },

        async recomputeNow() {
            this.recomputing = true;
            this.recomputeLine = '';
            this.recomputeFailed = false;
            try {
                await Intelligence.recompute();
            } catch (err) {
                this.recomputing = false;
                if (Intelligence.failureStatus(err) === 409) {
                    this.recomputeLine = Intelligence.BUSY_LINE;
                } else {
                    this.recomputeLine = Intelligence.ERROR_LINE;
                    this.recomputeFailed = true;
                }
                return;
            }
            this.recomputing = false;
            await this.load(this.centreId);
            var heading = this.$refs.resultsHeading;
            if (heading && (!document.activeElement || document.activeElement === document.body)) {
                heading.focus();
            }
        }
    });
}
window.askSurface = askSurface;
