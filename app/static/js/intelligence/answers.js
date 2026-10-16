/* The Answers page: ask a question about one or more elements and open the
 * stored answer at its permanent address.
 *
 * The answer itself is computed, stored and rendered on the server. This
 * component only collects the chosen elements with the shared element picker,
 * posts the question to the answers endpoint and goes to the address that
 * comes back. Nothing is computed in the browser.
 *
 * Referenced as x-data="answersSurface()"; a top-level window factory, like
 * the other intelligence pages (the CSP-safe interpreter never consults
 * Alpine.data() registrations).
 */
function answersSurface() {
    var Intelligence = window.Intelligence;
    var ANSWERS_URL = '/api/v1/intelligence/answers';

    return Object.assign(Intelligence.picker('answers'), {
        question: 'set_impact',
        selected: [],
        asking: false,
        askError: '',

        onSelect(option) {
            this.askError = '';
            this.term = '';
            for (var i = 0; i < this.selected.length; i++) {
                if (this.selected[i].id === option.id) return;
            }
            if (this.question === 'risk_propagation') {
                this.selected = [{ id: option.id, name: option.name }];
                return;
            }
            this.selected = this.selected.concat([{ id: option.id, name: option.name }]);
        },

        removeSelected(event) {
            var id = parseInt(event.currentTarget.getAttribute('data-element-id'), 10);
            this.selected = this.selected.filter(function (item) { return item.id !== id; });
        },

        chooseQuestion(event) {
            this.question = event.currentTarget.value;
            if (this.question === 'risk_propagation' && this.selected.length > 1) {
                this.selected = [this.selected[0]];
            }
        },

        hasSelection() {
            return this.selected.length > 0;
        },

        cannotAsk() {
            return this.asking || this.selected.length === 0;
        },

        async ask() {
            if (this.cannotAsk()) return;
            this.asking = true;
            this.askError = '';
            var ids = this.selected.map(function (item) { return item.id; });
            var body = { question: this.question };
            if (this.question === 'risk_propagation') {
                body.element_id = ids[0];
            } else {
                body.element_ids = ids;
            }
            var resp;
            try {
                resp = await Platform.fetch.post(ANSWERS_URL, body, { silent: true });
            } catch (err) {
                this.asking = false;
                this.askError = 'We could not answer that just now: ' + (err && err.message ? err.message : 'the server refused it') + '.';
                return;
            }
            if (!resp || !resp.data || !resp.data.page_url) {
                this.asking = false;
                this.askError = 'We could not answer that just now.';
                return;
            }
            window.location.assign(resp.data.page_url);
        }
    });
}
