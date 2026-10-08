/* Impact / dependency graph — interactive blast-radius view.
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
    var LAYER = {
        business: '#B9770E', application: '#1E7FB4', technology: '#2E8B57',
        motivation: '#7C5CBF', strategy: '#C2683A', implementation: '#B4487F',
        physical: '#2E8B57', other: '#64748B'
    };
    function colour(layer) { return LAYER[(layer || 'other').toLowerCase()] || LAYER.other; }

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
            .attr('fill', function (d) { return colour(d.layer); })
            .attr('stroke', function (d) { return d.is_center ? '#111827' : '#ffffff'; })
            .attr('stroke-width', function (d) { return d.is_center ? 3 : 1.5; });

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

        sim = d3.forceSimulation(data.nodes)
            .force('link', d3.forceLink(data.edges).id(function (d) { return d.id; }).distance(90).strength(0.6))
            .force('charge', d3.forceManyBody().strength(-260))
            .force('center', d3.forceCenter(width / 2, height / 2))
            .force('collide', d3.forceCollide().radius(function (d) { return d.is_center ? 26 : 16; }))
            .on('tick', function () {
                link.attr('x1', function (d) { return d.source.x; }).attr('y1', function (d) { return d.source.y; })
                    .attr('x2', function (d) { return d.target.x; }).attr('y2', function (d) { return d.target.y; });
                node.attr('transform', function (d) { return 'translate(' + d.x + ',' + d.y + ')'; });
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
