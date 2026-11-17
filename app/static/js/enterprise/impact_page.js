/* The Impact Analysis page: choosing an element with the shared picker reloads
 * the page at ?element=<id>, where the server computes and renders the
 * answer. Nothing is computed in the browser; this only carries the choice
 * into the URL, the same contract a direct link (a chat answer's "Open this
 * answer", an application's page) already uses.
 *
 * Referenced as x-data="impactPageSurface()"; a top-level window factory, like
 * the other intelligence pages (the CSP-safe interpreter never consults
 * Alpine.data() registrations).
 */
function impactPageSurface() {
    return Object.assign(window.Intelligence.picker('impact-page'), {
        onSelect(option) {
            var url = new URL(window.location.href);
            url.searchParams.set('element', option.id);
            window.location.assign(url.pathname + '?' + url.searchParams.toString());
        }
    });
}
