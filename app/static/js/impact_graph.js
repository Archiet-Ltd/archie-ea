/* Impact / dependency graph — interactive blast-radius view, and the whole-model map.
 *
 * Whole-model mode (the svg carries data-mode="model"): every element of the organisation's model is
 * drawn at once, coloured by ArchiMate layer from the layer tokens. Hovering or focusing a node shows its
 * name, type, source and date; clicking it opens that element's page. Over the node limit the server
 * sends one node per layer with its count, and a click opens that layer.
 *
 * Impact mode:
 *
 * Renders the nodes+edges from /archimate/api/element/<id>/impact-graph as a
 * d3 force-directed graph: the chosen element at the centre, everything that
 * depends on it or that it depends on around it, coloured by ArchiMate layer.
 * Drag to rearrange, scroll to zoom, click a node to re-centre on it, and pick
 * a depth (1–3 hops). Pure vanilla + d3; no build step. */
(function () {
    'use strict';
    var svgEl = document.getElementById('impact-svg');
    if (!svgEl || typeof d3 === 'undefined') return;

    var ENDPOINT = svgEl.getAttribute('data-endpoint');
    var PAGE_TMPL = svgEl.getAttribute('data-page');   // …/elements/0/impact
    // A layer is drawn in its ArchiMate notation tokens (fill and stroke): the same tokens the composer and
    // the legend use, so the dark theme is the same palette on dark tokens. The layer name is the token name.
    var LAYERS = ['strategy', 'business', 'application', 'technology', 'physical', 'motivation', 'implementation', 'other'];
    function notation(layer, part) {
        var key = (layer || 'other').toLowerCase();
        if (LAYERS.indexOf(key) < 0) key = 'other';
        return 'hsl(var(--notation-' + key + part + '))';
    }
    function tokenFill(layer) { return notation(layer, ''); }
    function tokenStroke(layer) { return notation(layer, '-stroke'); }

    // The one force simulation, used by the whole-model map and by the impact graph. Besides keeping circles
    // apart it keeps labels apart: where two labels would overlap it nudges the two elements up and down.
    function startSimulation(nodes, edges, o) {
        var ROW = 14;
        function labels(alpha) {
            if (!o.label) return;
            for (var i = 0; i < nodes.length; i++) {
                var a = nodes[i], la = o.label(a);
                for (var j = i + 1; j < nodes.length; j++) {
                    var b = nodes[j], lb = o.label(b);
                    var dy = b.y - a.y;
                    if (Math.abs(dy) >= ROW) continue;
                    if (a.x + la.dx >= b.x + lb.dx + lb.w || b.x + lb.dx >= a.x + la.dx + la.w) continue;
                    var push = (ROW - Math.abs(dy)) * 0.5 * alpha * 2;
                    var dir = dy === 0 ? (i % 2 ? 1 : -1) : (dy > 0 ? 1 : -1);
                    a.vy -= dir * push; b.vy += dir * push;
                }
            }
        }
        var sim = d3.forceSimulation(nodes)
            .force('link', d3.forceLink(edges).id(function (d) { return d.id; }).distance(o.distance).strength(o.linkStrength))
            .force('charge', d3.forceManyBody().strength(o.charge))
            .force('center', d3.forceCenter(o.width / 2, o.height / 2))
            .force('collide', d3.forceCollide().radius(o.collide))
            .force('labels', labels)
            .on('tick', o.tick);
        if (o.gravity) {
            // gravity keeps unconnected elements in view instead of drifting off the canvas
            sim.force('x', d3.forceX(o.width / 2).strength(o.gravity)).force('y', d3.forceY(o.height / 2).strength(o.gravity));
        }
        if (o.alphaDecay) sim.alphaDecay(o.alphaDecay);
        if (o.end) sim.on('end', o.end);
        return sim;
    }
    function labelBox(d, offset, shown) {
        var n = shown ? shown(d).length : 0;
        return { dx: offset, w: Math.min(n, 26) * 6.2 };
    }

    function modelMap() {
        var svg = d3.select(svgEl);
        var width = svgEl.clientWidth || 800;
        var height = svgEl.clientHeight || 600;
        var started = (window.performance && performance.now) ? performance.now() : Date.now();
        var root = svg.append('g');
        var linkG = root.append('g');
        var nodeG = root.append('g');
        var zoomer = d3.zoom().scaleExtent([0.1, 6]).on('zoom', function (ev) { root.attr('transform', ev.transform); });
        svg.call(zoomer);

        function text(id, t) { var e = document.getElementById(id); if (e) e.textContent = t; }
        function hide(id) { var e = document.getElementById(id); if (e) e.classList.add('hidden'); }
        function show(id) { var e = document.getElementById(id); if (e) e.classList.remove('hidden'); }

        function selected(d) {
            var box = document.getElementById('impact-selected');
            if (!box) return;
            while (box.firstChild) box.removeChild(box.firstChild);
            function line(cls, t) {
                var p = document.createElement('p'); p.className = cls; p.textContent = t; box.appendChild(p);
            }
            if (d.cluster) {
                line('font-medium', d.name);
                line('text-xs text-muted-foreground', d.count + ' elements. Click to draw this layer.');
                return;
            }
            line('font-medium break-words', d.name);
            line('text-xs text-muted-foreground', d.type);
            line('text-xs text-muted-foreground mt-1', 'Source: ' + d.source);
            line('text-xs text-muted-foreground', d.as_of ? 'Recorded: ' + d.as_of : 'No date recorded.');
            var a = document.createElement('a');
            a.href = d.href; a.className = 'mt-2 inline-flex text-xs font-medium text-primary hover:underline';
            a.textContent = 'Open ' + d.name;
            box.appendChild(a);
        }

        function describe(d) {
            if (d.cluster) return d.name + ', ' + d.count + ' elements. Press Enter to draw this layer.';
            return d.name + ', ' + d.type + '. Source: ' + d.source + '. ' + (d.as_of ? 'Recorded ' + d.as_of + '.' : 'No date recorded.');
        }

        function open(d) {
            window.location.href = d.cluster
                ? PAGE_TMPL + '?layer=' + encodeURIComponent(d.layer)
                : d.href;
        }

        // "1547 of 1562" when the register holds elements the map does not draw, else just the figure.
        function ofTotal(drawn, total) {
            return total && total > drawn ? drawn + ' of ' + total : String(drawn);
        }

        // The figure the dashboard gives for what is on screen: the asked layer's, or the whole model's.
        function layerTotal(data) {
            if (!data.layer) return data.store_total;
            var row = (data.counts || []).filter(function (c) { return c.layer === data.layer; })[0];
            return row ? row.total : 0;
        }

        function render(data) {
            hide('impact-loading');
            var nodes = data.nodes || [];
            if (!nodes.length) { show('impact-empty'); text('impact-counts', 'No elements in this layer.'); return; }
            var clusters = data.mode === 'clusters';
            var edges = data.edges || [];

            if (clusters) {
                text('impact-counts', ofTotal(data.total, data.store_total) + ' elements, drawn one circle per layer. Click a layer to open it.');
            } else {
                text('impact-counts', ofTotal(nodes.length, layerTotal(data)) + ' elements · ' + edges.length + ' relationships'
                    + (data.truncated ? ' · first ' + data.max_nodes + ' shown, pick a layer to narrow it' : ''));
            }

            var link = linkG.selectAll('line').data(edges).enter().append('line')
                .attr('stroke', 'currentColor').attr('class', 'text-border').attr('stroke-width', 1);

            var radius = function (d) { return d.cluster ? 18 + Math.sqrt(d.count) : 7; };
            var labelText = function (d) {
                var n = d.cluster ? d.name + ' · ' + d.count : d.name;
                return n.length > 26 ? n.slice(0, 25) + '…' : n;
            };
            var many = nodes.length > 150;
            var node = nodeG.selectAll('g').data(nodes).enter().append('g')
                .attr('tabindex', 0).attr('role', 'link').attr('data-node-id', function (d) { return d.id; })
                .attr('data-layer', function (d) { return d.layer; })
                .attr('aria-label', describe)
                .style('cursor', 'pointer')
                .call(d3.drag()
                    .on('start', function (ev, d) { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
                    .on('drag', function (ev, d) { d.fx = ev.x; d.fy = ev.y; })
                    .on('end', function (ev, d) { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

            node.append('title').text(describe);
            node.append('circle').attr('r', radius)
                .style('fill', function (d) { return tokenFill(d.layer); })
                .style('stroke', function (d) { return tokenStroke(d.layer); }).attr('stroke-width', 2);
            if (clusters || !many) {
                node.append('text')
                    .text(labelText)
                    .attr('x', function (d) { return radius(d) + 4; }).attr('y', 4).attr('font-size', 11)
                    .attr('fill', 'currentColor').attr('class', 'text-foreground')
                    .style('paint-order', 'stroke').attr('stroke', 'var(--card)').attr('stroke-width', 3);
            }

            node.on('mouseover focus', function (ev, d) { selected(d); })
                .on('click', function (ev, d) { open(d); })
                .on('keydown', function (ev, d) {
                    var all = node.nodes(), i = all.indexOf(this);
                    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(d); }
                    else if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') { ev.preventDefault(); all[Math.min(i + 1, all.length - 1)].focus(); }
                    else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp') { ev.preventDefault(); all[Math.max(i - 1, 0)].focus(); }
                });

            // Place the nodes on a ring before the first paint so the map is readable at once; the
            // simulation then settles them while the person is already hovering and clicking.
            nodes.forEach(function (d, i) {
                var a = (i / nodes.length) * 2 * Math.PI, r = Math.min(width, height) * 0.4;
                d.x = width / 2 + r * Math.cos(a); d.y = height / 2 + r * Math.sin(a);
            });
            function paint() {
                link.attr('x1', function (d) { return d.source.x; }).attr('y1', function (d) { return d.source.y; })
                    .attr('x2', function (d) { return d.target.x; }).attr('y2', function (d) { return d.target.y; });
                node.attr('transform', function (d) { return 'translate(' + d.x + ',' + d.y + ')'; });
            }
            sim = startSimulation(nodes, edges, {
                width: width, height: height, tick: paint,
                distance: clusters ? 120 : 40, linkStrength: 0.5,
                charge: clusters ? -400 : (many ? -30 : -120),
                collide: function (d) { return radius(d) + 2; },
                gravity: 0.06, alphaDecay: many ? 0.06 : 0.04,
                label: (clusters || !many) ? function (d) { return labelBox(d, radius(d) + 4, labelText); } : null,
                end: function () { fit(nodes); svgEl.setAttribute('data-settled', '1'); }
            });
            paint();
            // When the layout settles, zoom to show every element with a margin.
            function fit(list) {
                var x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
                list.forEach(function (d) {
                    var r = radius(d);
                    x0 = Math.min(x0, d.x - r); y0 = Math.min(y0, d.y - r);
                    x1 = Math.max(x1, d.x + r + (many ? 0 : 150)); y1 = Math.max(y1, d.y + r);
                });
                var w = x1 - x0, h = y1 - y0;
                if (!(w > 0 && h > 0)) return;
                var k = Math.max(0.1, Math.min(1, 0.92 * Math.min(width / w, height / h)));
                var t = d3.zoomIdentity.translate(width / 2 - k * (x0 + x1) / 2, height / 2 - k * (y0 + y1) / 2).scale(k);
                svg.transition().duration(250).call(zoomer.transform, t);
            }
            var done = function () {
                var ms = ((window.performance && performance.now) ? performance.now() : Date.now()) - started;
                svgEl.setAttribute('data-rendered-ms', String(Math.round(ms)));
                svgEl.setAttribute('data-node-count', String(nodes.length));
            };
            if (window.requestAnimationFrame) window.requestAnimationFrame(done); else done();
        }

        var sim = null;
        var layer = svgEl.getAttribute('data-layer');
        Platform.fetch(ENDPOINT + (layer ? '?layer=' + encodeURIComponent(layer) : ''))
            .then(render)
            .catch(function () {
                hide('impact-loading');
                text('impact-counts', 'Could not load the model map.');
            });
    }
    if (svgEl.getAttribute('data-mode') === 'model') { modelMap(); return; }

    var svg = d3.select(svgEl);
    var width = svgEl.clientWidth || 800;
    var height = svgEl.clientHeight || 600;
    var root = svg.append('g');            // zoom/pan target
    var linkG = root.append('g');
    var nodeG = root.append('g');

    // arrowhead for "depends on" direction
    svg.append('defs').append('marker')
        .attr('id', 'impact-arrow').attr('viewBox', '0 -5 10 10')
        .attr('refX', 22).attr('refY', 0).attr('markerWidth', 6).attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', 'currentColor')
        .attr('class', 'text-muted-foreground');

    svg.call(d3.zoom().scaleExtent([0.2, 3]).on('zoom', function (ev) {
        root.attr('transform', ev.transform);
    }));

    var sim = null;
    function _show(id, on) { var e = document.getElementById(id); if (e) e.classList.toggle('hidden', !on); }
    function _text(id, t) { var e = document.getElementById(id); if (e) e.textContent = t; }

    // The same answer as a readable list: who depends on this element, and
    // what it depends on, each marked direct or indirect with its hop count.
    function renderList(key, items, depth) {
        var list = document.getElementById('impact-' + key + '-list');
        var status = document.getElementById('impact-' + key + '-status');
        if (!list || !status) return;
        while (list.firstChild) list.removeChild(list.firstChild);
        items = items || [];
        items.forEach(function (item) {
            var li = document.createElement('li');
            li.className = 'flex items-center justify-between gap-3 py-2 text-sm';
            li.setAttribute('data-element-id', String(item.id));
            var left = document.createElement('div');
            left.className = 'min-w-0';
            var link = document.createElement('a');
            link.className = 'font-medium text-foreground hover:underline break-words';
            link.href = PAGE_TMPL.replace(/\/0\/impact$/, '/' + item.id + '/impact');
            link.textContent = item.name;
            var type = document.createElement('span');
            type.className = 'block text-xs text-muted-foreground';
            type.textContent = item.type || 'Element';
            left.appendChild(link);
            left.appendChild(type);
            var badge = document.createElement('span');
            badge.className = 'shrink-0 inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium '
                + (item.hops === 1 ? 'border-primary/30 bg-primary/10 text-primary' : 'border-border bg-muted text-muted-foreground');
            badge.textContent = item.hops === 1 ? 'direct' : 'indirect (' + item.hops + ' hops)';
            li.appendChild(left);
            li.appendChild(badge);
            list.appendChild(li);
        });
        status.textContent = items.length ? '' : 'None recorded within ' + depth + ' hop' + (depth === 1 ? '' : 's') + '.';
        status.classList.toggle('hidden', items.length > 0);
    }

    function listStatus(text) {
        ['consumers', 'providers'].forEach(function (key) {
            var list = document.getElementById('impact-' + key + '-list');
            if (list) while (list.firstChild) list.removeChild(list.firstChild);
            var status = document.getElementById('impact-' + key + '-status');
            if (status) { status.textContent = text; status.classList.remove('hidden'); }
        });
    }

    function render(data) {
        renderList('consumers', data.consumers, data.depth);
        renderList('providers', data.providers, data.depth);
        _show('impact-loading', false);
        linkG.selectAll('*').remove();
        nodeG.selectAll('*').remove();

        if (!data.nodes || data.nodes.length <= 1 && (!data.edges || !data.edges.length)) {
            _show('impact-empty', true);
            _text('impact-counts', 'No relationships recorded.');
            return;
        }
        _show('impact-empty', false);

        var c = data.counts || {};
        _text('impact-counts',
            (c.upstream || 0) + ' depend on it · it depends on ' + (c.downstream || 0)
            + (data.truncated ? ' · view capped at 160 nodes' : ''));

        var link = linkG.selectAll('line').data(data.edges).enter().append('line')
            .attr('stroke', 'currentColor').attr('class', 'text-border')
            .attr('stroke-width', 1.4).attr('marker-end', 'url(#impact-arrow)');

        var node = nodeG.selectAll('g').data(data.nodes).enter().append('g')
            .style('cursor', 'pointer')
            .call(d3.drag()
                .on('start', function (ev, d) { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
                .on('drag', function (ev, d) { d.fx = ev.x; d.fy = ev.y; })
                .on('end', function (ev, d) { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

        node.append('circle')
            .attr('r', function (d) { return d.is_center ? 13 : 8; })
            .style('fill', function (d) { return tokenFill(d.layer); })
            .style('stroke', function (d) { return tokenStroke(d.layer); })
            .attr('stroke-width', function (d) { return d.is_center ? 3 : 2; });

        node.append('text')
            .text(function (d) { return d.name.length > 26 ? d.name.slice(0, 25) + '…' : d.name; })
            .attr('x', function (d) { return d.is_center ? 17 : 12; }).attr('y', 4)
            .attr('font-size', function (d) { return d.is_center ? 13 : 11; })
            .attr('fill', 'currentColor').attr('class', 'text-foreground')
            .style('paint-order', 'stroke').attr('stroke', 'var(--card)').attr('stroke-width', 3);

        node.on('mouseover', function (ev, d) {
            _text('impact-selected', d.name + ' — ' + (d.type || 'element')
                + (d.is_center ? ' (centre)' : ' · ' + d.distance + ' hop' + (d.distance === 1 ? '' : 's') + ' away'));
        }).on('click', function (ev, d) {
            if (d.is_center) return;
            window.location.href = PAGE_TMPL.replace(/\/0\/impact$/, '/' + d.id + '/impact');
        });

        function paint() {
            link.attr('x1', function (d) { return d.source.x; }).attr('y1', function (d) { return d.source.y; })
                .attr('x2', function (d) { return d.target.x; }).attr('y2', function (d) { return d.target.y; });
            node.attr('transform', function (d) { return 'translate(' + d.x + ',' + d.y + ')'; });
        }
        sim = startSimulation(data.nodes, data.edges, {
            width: width, height: height, tick: paint,
            distance: 90, linkStrength: 0.6, charge: -260,
            collide: function (d) { return d.is_center ? 26 : 16; },
            label: function (d) { return labelBox(d, d.is_center ? 17 : 12, function (n) { return n.name; }); }
        });
    }

    function load(depth) {
        _show('impact-empty', false);
        _show('impact-loading', true);
        listStatus('Loading…');
        // Platform.fetch returns the parsed body and throws on a non-2xx, so the
        // old two-step response check is unnecessary (and keeps this off the
        // raw-fetch-sites gate).
        Platform.fetch(ENDPOINT + '?depth=' + depth)
            .then(render)
            .catch(function () {
                _show('impact-loading', false);
                _text('impact-counts', 'Could not load the impact graph.');
                listStatus('Could not load dependencies.');
            });
    }

    // depth buttons
    var depthBtns = document.querySelectorAll('.impact-depth-btn');
    function setDepth(d) {
        depthBtns.forEach(function (b) {
            var on = b.getAttribute('data-depth') === String(d);
            b.classList.toggle('bg-primary', on);
            b.classList.toggle('text-primary-foreground', on);
        });
        load(d);
    }
    depthBtns.forEach(function (b) {
        b.addEventListener('click', function () { setDepth(Number(b.getAttribute('data-depth'))); });
    });

    setDepth(2);
})();
