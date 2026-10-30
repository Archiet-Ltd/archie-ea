/*
 * The sidebar's search box -> every page the user may open, labelled with the
 * sidebar entry it sits under, and (with includeRecords) the records that match.
 *
 * The box used to filter whole zones (a hit on one label kept every link in that zone) and had no spelling
 * tolerance. It now asks /api/sidebar/search, which matches spelling-blind (en-GB/en-US variants fold both
 * ways) and labels each hit with the zone it lives in for this user, or "All modules" for a page reachable
 * only via the directory. The Ctrl+K modal reads the same endpoint, so there is one index and one visibility
 * rule (visible_module_links()).
 *
 * Behaviour, each pinned by tests/journeys/test_journey_sidebar_search_rendered.py:
 *   - under 2 characters: no request (the endpoint answers 400 below that);
 *   - typing is debounced to one request for the final query;
 *   - a slow earlier response never overwrites a newer one;
 *   - a failed request is reported as `error`, never as "no results".
 */
(function (root) {
    'use strict';

    var MIN_LENGTH = 2;
    var DEFAULT_DELAY_MS = 250;

    function create(options) {
        var fetchJson = options.fetchJson;
        var onChange = options.onChange;
        var delay = options.delay === undefined ? DEFAULT_DELAY_MS : options.delay;
        // The sidebar's one search box also lists records (applications, capabilities ...).
        var includeRecords = !!options.includeRecords;
        var timer = null;
        var latest = 0;

        function publish(query, results, loading, error, records) {
            onChange({ query: query, results: results, loading: loading, error: error, records: records || [] });
        }

        function search(raw) {
            var query = (raw || '').trim();
            clearTimeout(timer);
            var ticket = ++latest;
            if (query.length < MIN_LENGTH) {
                publish(query, [], false, false);
                return;
            }
            publish(query, [], true, false);
            timer = setTimeout(function () {
                Promise.resolve()
                    .then(function () { return fetchJson(query); })
                    .then(function (body) {
                        if (ticket !== latest) return;
                        var hits = (body && body.results) || [];
                        var modules = hits
                            .filter(function (hit) { return hit.type === 'module'; })
                            .map(function (hit) { return { name: hit.name, url: hit.url, zone: hit.zone || '' }; });
                        var records = includeRecords
                            ? hits
                                .filter(function (hit) { return hit.type !== 'module' && hit.url; })
                                .map(function (hit) { return { name: hit.name, url: hit.url, type: String(hit.type || '').replace(/_/g, ' ') }; })
                            : [];
                        publish(query, modules, false, false, records);
                    })
                    .catch(function () {
                        if (ticket !== latest) return;
                        publish(query, [], false, true);
                    });
            }, delay);
        }

        return { search: search };
    }

    root.SidebarModuleSearch = { create: create };
})(window);
