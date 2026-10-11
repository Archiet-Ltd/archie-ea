/* The Traceability page.
 *
 * The answer itself is rendered on the server. This component does two things
 * in the browser: the shared element picker, which opens the page on the
 * chosen element, and the "Add this relationship" control on a candidate,
 * which writes through the existing relationship endpoint and then reloads
 * the page so the check runs again on what is now recorded.
 *
 * Referenced as x-data="traceabilitySurface()"; a top-level window factory,
 * like the other intelligence pages (the CSP-safe interpreter never consults
 * Alpine.data() registrations).
 */
function traceabilitySurface() {
    var Intelligence = window.Intelligence;

    return Object.assign(Intelligence.picker('trace'), {
        adding: null,
        addError: '',
        pageUrl: '',

        init() {
            this.pageUrl = this.$el.getAttribute('data-page-url') || window.location.pathname;
        },

        onSelect(option) {
            window.location.assign(this.pageUrl + '?element=' + encodeURIComponent(option.id));
        },

        async addCandidate(event) {
            var button = event.currentTarget;
            var key = button.getAttribute('data-candidate-key');
            if (this.adding) return;
            this.adding = key;
            this.addError = '';
            try {
                await Platform.fetch.post('/archimate/api/relationships', {
                    source_element_id: parseInt(button.getAttribute('data-source-id'), 10),
                    target_element_id: parseInt(button.getAttribute('data-target-id'), 10),
                    relationship_type: button.getAttribute('data-relationship-type')
                }, { silent: true });
            } catch (err) {
                this.adding = null;
                this.addError = 'The relationship could not be added: ' + (err && err.message ? err.message : 'the server refused it') + '.';
                return;
            }
            window.location.reload();
        }
    });
}
