/* The model map's find box and zoom buttons.
 *
 * The find box is the shared element picker (Intelligence.picker), fed from the elements the map has already
 * drawn, so it finds exactly what a person can see and works with one layer open. Choosing a name centres that
 * element on the map and marks it. The zoom buttons drive the same map (window.modelMapView, set by
 * impact_graph.js once the map is drawn).
 *
 * Referenced as x-data="modelMapSearch()"; a top-level window factory, like the other picker pages.
 */
function modelMapSearch() {
    var Intelligence = window.Intelligence;

    return Object.assign(Intelligence.picker('map'), {
        searchOptions: function (typed) {
            var view = window.modelMapView;
            var wanted = typed.toLowerCase();
            var found = (view ? view.nodes : []).filter(function (n) {
                return !n.cluster && (n.name || '').toLowerCase().indexOf(wanted) !== -1;
            }).slice(0, 10).map(function (n) { return { id: n.id, name: n.name }; });
            return Promise.resolve(found);
        },

        onSelect: function (option) {
            var view = window.modelMapView;
            if (view && view.locate(option.id)) {
                this.statusText = option.name + ' is centred and marked on the map.';
            }
        },

        zoomIn: function () { if (window.modelMapView) window.modelMapView.zoomIn(); },
        zoomOut: function () { if (window.modelMapView) window.modelMapView.zoomOut(); },
        fitAll: function () { if (window.modelMapView) window.modelMapView.fit(); }
    });
}
