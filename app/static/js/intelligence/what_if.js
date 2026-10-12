/* The What-if page.
 *
 * The comparison is rendered on the server. This component is the shared
 * element picker, which adds the chosen element to the address and reloads, so
 * the chosen elements live in the link and a reload or a shared link shows the
 * same comparison.
 *
 * Referenced as x-data="whatIfSurface()"; a top-level window factory like the
 * other intelligence pages.
 */
function whatIfSurface() {
    var Intelligence = window.Intelligence;

    return Object.assign(Intelligence.picker('whatif'), {
        pageUrl: '',
        chosen: [],

        init() {
            this.pageUrl = this.$el.getAttribute('data-page-url') || window.location.pathname;
            var raw = this.$el.getAttribute('data-chosen') || '';
            this.chosen = raw ? raw.split(',') : [];
        },

        onSelect(option) {
            var id = String(option.id);
            var next = this.chosen.indexOf(id) === -1 ? this.chosen.concat([id]) : this.chosen;
            var query = next.map(function (value) { return 'element=' + encodeURIComponent(value); }).join('&');
            window.location.assign(this.pageUrl + '?' + query);
        }
    });
}
