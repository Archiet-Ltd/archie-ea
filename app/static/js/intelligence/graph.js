/* The Twin map drawing: a banded layout, not a force layout.
 *
 * Six bands run top to bottom (Goals, Strategy, Business, Systems, Technology,
 * Delivery) and each element sits in the band its layer names. Band colour comes
 * from the --layer-* design tokens only.
 *
 * Two layers share one box:
 *   - an SVG holding the bands and the connections. An explicit connection is
 *     a solid line; a worked-out connection is a dashed line and carries a
 *     badge, so the difference survives greyscale.
 *   - a layer of real buttons, one per element, placed over the drawing. They
 *     are what a keyboard or a pointer reaches, so they stay outside the
 *     element that describes the picture to a screen reader.
 *
 * All text is written with .text(); nothing here builds markup from data.
 */
(function (global) {
    'use strict';

    var NODE_H = 48;
    var NODE_GAP = 20;
    var ROW_GAP = 14;
    var BAND_PAD = 12;
    var BAND_HEADER = 26;
    var EMPTY_BAND = BAND_HEADER + 12;
    var DASH = '5,5';

    /* Full class names, written out so the stylesheet build can see them. */
    var BAND_CLASS = {
        motivation: 'fill-layer-motivation/10 stroke-layer-motivation/40',
        strategy: 'fill-layer-strategy/10 stroke-layer-strategy/40',
        business: 'fill-layer-business/10 stroke-layer-business/40',
        application: 'fill-layer-application/10 stroke-layer-application/40',
        technology: 'fill-layer-technology/10 stroke-layer-technology/40',
        implementation: 'fill-layer-implementation/10 stroke-layer-implementation/40',
        unplaced: 'fill-muted/40 stroke-border'
    };
    var NODE_BORDER = {
        motivation: 'border-layer-motivation',
        strategy: 'border-layer-strategy',
        business: 'border-layer-business',
        application: 'border-layer-application',
        technology: 'border-layer-technology',
        implementation: 'border-layer-implementation',
        unplaced: 'border-border'
    };
    var NODE_CLASS = 'absolute flex items-center justify-center rounded-md border-2 bg-background px-2 text-xs text-foreground shadow-sm ' +
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background';

    /* The two glyphs drawn beside the badge text, on the same 24 by 24 grid as the
       icons the rest of the page uses: the connected-dots mark for "worked out"
       (the same one the drawer title and the Ask rows carry) and a clock for
       "may be out of date". */
    var GLYPH_WORKED_OUT = [
        ['circle', { cx: 12, cy: 4.5, r: 2.5 }], ['path', { d: 'm10.2 6.3-3.9 3.9' }],
        ['circle', { cx: 4.5, cy: 12, r: 2.5 }], ['path', { d: 'M7 12h10' }],
        ['circle', { cx: 19.5, cy: 12, r: 2.5 }], ['path', { d: 'm13.8 17.7 3.9-3.9' }],
        ['circle', { cx: 12, cy: 19.5, r: 2.5 }]
    ];
    var GLYPH_CLOCK = [['circle', { cx: 12, cy: 12, r: 10 }], ['polyline', { points: '12 6 12 12 16 14' }]];

    function byDepthThenName(a, b) {
        if (a.depth !== b.depth) return (a.depth || 0) - (b.depth || 0);
        return String(a.name || '').localeCompare(String(b.name || ''));
    }

    function layout(model, width) {
        var bands = global.Intelligence.BANDS.slice();
        var unplaced = model.nodes.some(function (n) { return n.band === 'unplaced'; });
        if (unplaced) bands.push(global.Intelligence.UNPLACED_BAND);

        var nodeW = Math.max(96, Math.min(168, width - 2 * BAND_PAD));
        var perRow = Math.max(1, Math.floor((width - 2 * BAND_PAD + NODE_GAP) / (nodeW + NODE_GAP)));
        var placed = {};
        var bandRects = [];
        var top = 0;

        bands.forEach(function (band) {
            var members = model.nodes.filter(function (n) { return n.band === band.layer; }).sort(byDepthThenName);
            var rows = Math.ceil(members.length / perRow);
            var height = members.length
                ? BAND_HEADER + BAND_PAD + rows * NODE_H + (rows - 1) * ROW_GAP + BAND_PAD
                : EMPTY_BAND;
            members.forEach(function (node, i) {
                var r = Math.floor(i / perRow);
                var inRow = Math.min(perRow, members.length - r * perRow);
                var rowWidth = inRow * nodeW + (inRow - 1) * NODE_GAP;
                var start = (width - rowWidth) / 2;
                placed[node.id] = {
                    x: start + (i % perRow) * (nodeW + NODE_GAP) + nodeW / 2,
                    y: top + BAND_HEADER + BAND_PAD + r * (NODE_H + ROW_GAP) + NODE_H / 2
                };
            });
            bandRects.push({ layer: band.layer, label: band.label, y: top, height: height });
            top += height;
        });
        return { nodeW: nodeW, positions: placed, bands: bandRects, height: top };
    }

    function edgePath(edge, from, to, nodeW) {
        var derivedShift = edge.kind === 'derived' ? 14 : 0;
        var sx = from.x + derivedShift;
        var tx = to.x + derivedShift;
        if (Math.abs(to.y - from.y) < NODE_H / 2) {
            // Same row: arc over the top so the line does not run through a
            // neighbouring node.
            var lift = 34 + derivedShift;
            var sy = from.y - NODE_H / 2;
            var ty = to.y - NODE_H / 2;
            return {
                d: 'M' + sx + ',' + sy + ' C' + sx + ',' + (sy - lift) + ' ' + tx + ',' + (ty - lift) + ' ' + tx + ',' + (ty - 4),
                mx: (sx + tx) / 2,
                my: sy - lift * 0.75
            };
        }
        var down = to.y > from.y;
        var y1 = from.y + (down ? NODE_H / 2 : -NODE_H / 2);
        var y2 = to.y + (down ? -NODE_H / 2 - 4 : NODE_H / 2 + 4);
        var mid = (y1 + y2) / 2;
        return {
            d: 'M' + sx + ',' + y1 + ' C' + sx + ',' + mid + ' ' + tx + ',' + mid + ' ' + tx + ',' + y2,
            mx: (sx + tx) / 2,
            my: mid
        };
    }

    function drawGlyph(group, parts, x, label) {
        var glyph = group.append('g')
            .attr('transform', 'translate(' + x + ',-6) scale(0.5)')
            .attr('fill', 'none').attr('class', 'stroke-muted-foreground')
            .attr('stroke-width', 2).attr('stroke-linecap', 'round').attr('stroke-linejoin', 'round');
        if (label) {
            glyph.attr('role', 'img').attr('aria-label', label);
            glyph.append('title').text(label);
        }
        parts.forEach(function (part) {
            var shape = glyph.append(part[0]);
            Object.keys(part[1]).forEach(function (key) { shape.attr(key, part[1][key]); });
        });
    }

    /* The badge on a worked-out connection: words plus a mark, never colour alone.
       A connection that may be out of date says so in the same badge. Rebuilt on
       every draw because the same connection can go from current to out of date
       and back without changing its key. */
    function drawBadge(edgeGroup, d) {
        var badge = edgeGroup.selectAll('g.intel-badge').data(d.edge.kind === 'derived' ? [d] : []);
        badge.exit().remove();
        badge = badge.enter().append('g').attr('class', 'intel-badge').merge(badge);
        if (badge.empty()) return;
        badge.selectAll('*').remove();
        var stale = d.edge.stale === true;
        var label = stale ? global.Intelligence.WORKED_OUT_STALE : global.Intelligence.WORKED_OUT;
        badge.append('title').text(stale ? 'Worked out \u2014 may be out of date' : 'Worked out \u2014 nobody drew it directly');
        var box = badge.append('rect').attr('rx', 4).attr('height', 20).attr('y', -10)
            .attr('class', 'fill-background stroke-muted-foreground');
        var text = badge.append('text').attr('class', 'fill-foreground text-xs').attr('y', 4).text(label);
        var textWidth = text.node().getComputedTextLength() || label.length * 6.2;
        var glyphs = stale ? 2 : 1;
        var width = 8 + glyphs * 16 + textWidth + 8;
        var x = -width / 2 + 8;
        box.attr('x', -width / 2).attr('width', width);
        drawGlyph(badge, GLYPH_WORKED_OUT, x);
        x += 16;
        if (stale) {
            drawGlyph(badge, GLYPH_CLOCK, x, 'Last worked out');
            x += 16;
        }
        text.attr('x', x);
    }

    /* Draws into `canvas`, which holds [data-graph-svg] (the drawing, inside the
       element that carries role="img") and [data-graph-nodes] (the buttons). */
    function create(canvas, handlers) {
        var d3 = global.d3;
        var svgHost = canvas.querySelector('[data-graph-svg]');
        var nodeHost = canvas.querySelector('[data-graph-nodes]');
        var svg = d3.select(svgHost).select('svg');
        var buttons = d3.select(nodeHost);
        var current = null;
        var selectedId = null;
        var pathKeys = {};
        var drawnWidth = 0;

        function defs() {
            var defsSel = svg.selectAll('defs').data([0]).join('defs');
            var markers = defsSel.selectAll('marker').data(['explicit', 'derived'], function (d) { return d; });
            markers.join(function (enter) {
                var marker = enter.append('marker')
                    .attr('id', function (d) { return 'intel-arrow-' + d; })
                    .attr('viewBox', '0 0 10 10')
                    .attr('refX', 9)
                    .attr('refY', 5)
                    .attr('markerWidth', 7)
                    .attr('markerHeight', 7)
                    .attr('orient', 'auto');
                marker.append('path').attr('d', 'M0,0 L10,5 L0,10 z').attr('class', 'fill-muted-foreground');
                return marker;
            });
        }

        function draw() {
            if (!current) return;
            var width = Math.floor(canvas.clientWidth);
            if (width < 40) return;
            drawnWidth = width;
            var plan = layout(current, width);
            if (canvas.style.height !== plan.height + 'px') canvas.style.height = plan.height + 'px';
            svg.attr('width', width).attr('height', plan.height).attr('viewBox', '0 0 ' + width + ' ' + plan.height);
            defs();

            // Bands
            var bandSel = svg.selectAll('g.intel-band').data(plan.bands, function (d) { return d.layer; });
            var bandEnter = bandSel.enter().append('g').attr('class', 'intel-band');
            bandEnter.append('rect').attr('rx', 8);
            bandEnter.append('text').attr('class', 'fill-foreground text-xs font-medium');
            bandSel.exit().remove();
            var bandAll = bandEnter.merge(bandSel);
            bandAll.attr('data-layer', function (d) { return d.layer; });
            bandAll.select('rect')
                .attr('class', function (d) { return BAND_CLASS[d.layer]; })
                .attr('x', 1).attr('y', function (d) { return d.y + 1; })
                .attr('width', width - 2).attr('height', function (d) { return d.height - 2; });
            bandAll.select('text')
                .attr('x', BAND_PAD).attr('y', function (d) { return d.y + 18; })
                .text(function (d) { return d.label; });

            // Connections: solid when explicit, dashed and badged when worked out.
            var edgeData = current.edges.filter(function (e) {
                return plan.positions[e.from] && plan.positions[e.to];
            }).map(function (e) {
                var geometry = edgePath(e, plan.positions[e.from], plan.positions[e.to], plan.nodeW);
                return { edge: e, geometry: geometry };
            });
            var edgeSel = svg.selectAll('g.intel-edge').data(edgeData, function (d) { return d.edge.key; });
            var edgeEnter = edgeSel.enter().append('g').attr('class', 'intel-edge');
            edgeEnter.append('path').attr('fill', 'none');
            edgeSel.exit().remove();
            var edgeAll = edgeEnter.merge(edgeSel);
            edgeAll.each(function (d) { drawBadge(d3.select(this), d); });
            edgeAll.attr('data-kind', function (d) { return d.edge.kind; })
                .attr('data-edge', function (d) { return d.edge.key; });
            edgeAll.select('path')
                .attr('d', function (d) { return d.geometry.d; })
                .attr('data-kind', function (d) { return d.edge.kind; })
                .attr('stroke-dasharray', function (d) { return d.edge.kind === 'derived' ? DASH : null; })
                .attr('marker-end', function (d) { return 'url(#intel-arrow-' + d.edge.kind + ')'; });
            edgeAll.select('g.intel-badge')
                .attr('transform', function (d) { return 'translate(' + d.geometry.mx + ',' + d.geometry.my + ')'; });
            edgeAll.sort(function (a, b) { return (a.edge.kind === 'derived') - (b.edge.kind === 'derived'); });

            // Elements: real buttons over the drawing.
            var nodeSel = buttons.selectAll('button.intel-node').data(current.nodes, function (d) { return d.id; });
            var nodeEnter = nodeSel.enter().append('button')
                .attr('type', 'button')
                .attr('class', 'intel-node ' + NODE_CLASS)
                .on('click', function (event, d) { handlers.onSelect(d.id); });
            nodeEnter.append('span').attr('class', 'intel-node-name line-clamp-2 text-center');
            nodeEnter.append('span').attr('class', 'intel-node-centre sr-only');
            nodeSel.exit().remove();
            var nodeAll = nodeEnter.merge(nodeSel);
            nodeAll
                .attr('data-node', function (d) { return d.id; })
                .attr('data-layer', function (d) { return d.band; })
                .attr('class', function (d) {
                    return 'intel-node ' + NODE_CLASS + ' ' + NODE_BORDER[d.band] +
                        (d.id === current.centreId ? ' font-semibold' : '');
                })
                .style('left', function (d) { return (plan.positions[d.id].x - plan.nodeW / 2) + 'px'; })
                .style('top', function (d) { return (plan.positions[d.id].y - NODE_H / 2) + 'px'; })
                .style('width', plan.nodeW + 'px')
                .style('height', NODE_H + 'px')
                .attr('title', function (d) { return d.name || null; });
            // Tab order follows the picture: top band first, left to right.
            nodeAll.sort(function (a, b) {
                var pa = plan.positions[a.id];
                var pb = plan.positions[b.id];
                return (pa.y - pb.y) || (pa.x - pb.x);
            });
            nodeAll.select('span.intel-node-name').text(function (d) { return d.name || 'Not recorded'; });
            nodeAll.select('span.intel-node-centre').text(function (d) { return d.id === current.centreId ? ' (centre of the map)' : ''; });
            paint();
        }

        /* Selection and the highlighted path change without a new layout. */
        function paint() {
            if (!current) return;
            svg.selectAll('g.intel-edge').select('path')
                .attr('stroke-width', function (d) { return pathKeys[d.edge.key] ? 3 : 1.5; })
                .attr('class', function (d) { return pathKeys[d.edge.key] ? 'stroke-primary' : 'stroke-muted-foreground'; });
            buttons.selectAll('button.intel-node')
                .attr('aria-pressed', function (d) { return d.id === selectedId ? 'true' : 'false'; })
                .classed('bg-accent', function (d) { return d.id === selectedId; });
        }

        var observer = null;
        if (global.ResizeObserver) {
            // Redraw on the next frame, not inside the observer callback: the drawing
            // sets the height of the element being observed, and doing that in the
            // callback makes the browser report a resize loop.
            observer = new global.ResizeObserver(function () {
                if (!current || Math.floor(canvas.clientWidth) === drawnWidth) return;
                global.requestAnimationFrame(draw);
            });
            observer.observe(canvas);
        }

        return {
            render: function (model, selected, path) {
                current = model;
                selectedId = selected;
                pathKeys = path || {};
                draw();
            },
            select: function (selected, path) {
                selectedId = selected;
                pathKeys = path || {};
                paint();
            },
            resize: function () { draw(); },
            focus: function (id) {
                var target = nodeHost.querySelector('button[data-node="' + id + '"]');
                if (target) target.focus();
            }
        };
    }

    global.IntelligenceGraph = { create: create };
})(window);
