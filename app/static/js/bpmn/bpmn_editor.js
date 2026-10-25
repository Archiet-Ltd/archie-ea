/* BPMN 2.0 process diagram on the JointJS engine.
 *
 * The flow (pools, lanes, shapes, flows) is one JSON document stored on the business process. This file draws it,
 * edits it and saves it; the lanes are business roles of the model and a sub-process shape is a child process.
 * The page's header, status line and toolbar buttons read the state names below (viewTitle, shellElementTotal, ...).
 */
(function () {
    'use strict';

    var LANE_HEIGHT = 160, POOL_GAP = 40, POOL_HEADER = 30, POOL_MIN_WIDTH = 700, POOL_PAD = 40, GRID = 10;
    var SIZES = {
        start: [36, 36], intermediate: [36, 36], end: [36, 36], task: [110, 64], subprocess: [110, 64],
        xor: [50, 50], and: [50, 50], event: [50, 50]
    };
    var NAMES = {
        start: 'Start event', intermediate: 'Intermediate event', end: 'End event', task: 'Task', subprocess: 'Sub-process',
        xor: 'Exclusive gateway', and: 'Parallel gateway', event: 'Event-based gateway'
    };
    var TEXTY = { task: true, subprocess: true };

    function snap(v) { return Math.round(v / GRID) * GRID; }
    function clone(o) { return JSON.parse(JSON.stringify(o)); }

    // ── JointJS shapes ───────────────────────────────────────────────────────────────────────────
    var J = window.joint, NS = {};
    function define(name, size, markup, attrs) {
        NS[name] = J.dia.Element.define('bpmn.' + name, { size: { width: size[0], height: size[1] }, attrs: attrs }, { markup: markup });
    }
    define('Event', [36, 36],
        [{ tagName: 'circle', selector: 'outer' }, { tagName: 'circle', selector: 'inner' }, { tagName: 'text', selector: 'caption' }],
        { outer: { cx: 18, cy: 18, r: 17, class: 'bpmn-shape bpmn-node' }, inner: { cx: 18, cy: 18, r: 14, class: 'bpmn-inner' },
          caption: { x: 18, y: 52, class: 'bpmn-caption' } });
    define('Task', [110, 64],
        [{ tagName: 'rect', selector: 'body' }, { tagName: 'rect', selector: 'plus' }, { tagName: 'path', selector: 'plusMark' }, { tagName: 'text', selector: 'label' }],
        { body: { width: 110, height: 64, rx: 8, ry: 8, class: 'bpmn-shape bpmn-node' }, plus: { x: 49, y: 48, width: 12, height: 12, class: 'bpmn-box-mark' },
          plusMark: { d: 'M 55 51 V 57 M 52 54 H 58', class: 'bpmn-box-mark' }, label: { x: 55, y: 30, class: 'bpmn-text' } });
    define('Gateway', [50, 50],
        [{ tagName: 'polygon', selector: 'body' }, { tagName: 'path', selector: 'mark' }, { tagName: 'circle', selector: 'ring' },
         { tagName: 'circle', selector: 'ring2' }, { tagName: 'text', selector: 'caption' }],
        { body: { points: '25,0 50,25 25,50 0,25', class: 'bpmn-shape bpmn-node' }, mark: { d: '', class: 'bpmn-mark' },
          ring: { cx: 25, cy: 25, r: 0, class: 'bpmn-inner' }, ring2: { cx: 25, cy: 25, r: 0, class: 'bpmn-inner' },
          caption: { x: 25, y: 66, class: 'bpmn-caption' } });
    define('Band', [10, 10],
        [{ tagName: 'rect', selector: 'body' }, { tagName: 'text', selector: 'label' }],
        { body: { width: 'calc(w)', height: 'calc(h)', class: 'bpmn-lane' }, label: { text: '', class: 'bpmn-lane-text' } });
    var MARKS = { xor: 'M 17 17 L 33 33 M 33 17 L 17 33', and: 'M 25 12 V 38 M 12 25 H 38', event: '' };

    function wrap(text, width) {
        return J.util.breakText(text || '', { width: width, height: 56 }, { 'font-size': 12 }, { ellipsis: true });
    }

    // ── The component ────────────────────────────────────────────────────────────────────────────
    window.bpmnEditor = function (cfg) {
        var graph, paper, paperEl, surfaceEl, saveTimer = null, saving = false, dirtyWhileSaving = false, panning = null;
        var undoStack = [], redoStack = [];

        return {
            // state read by the shared header and status line
            viewTitle: cfg.title, activeViewpoint: 'bpmn', shellElementTotal: 0, shellRelTotal: 0, selectedNode: null,
            statusText: 'Ready', showShortcutsModal: false,
            saveIndicator: { tone: 'text-muted-foreground', icon: 'check', text: 'Saved' },
            // state of the editor
            canEdit: cfg.canEdit, flow: cfg.flow, urls: cfg.urls, selected: null, tool: 'select', connectFrom: null,
            railOpen: window.innerWidth >= 1536, railTab: 'add', finderQuery: '', zoomPercent: 100,
            roleQuery: '', roleResults: [], roleError: '', roleOpen: false, importOpen: false, importBusy: false,
            importError: '', importNotes: [], canUndo: false, canRedo: false, roleTimer: null,

            init: function () {
                var self = this;
                paperEl = this.$refs.paper; surfaceEl = this.$refs.surface;
                graph = new J.dia.Graph({}, { cellNamespace: Object.assign({}, J.shapes, { bpmn: NS }) });
                paper = new J.dia.Paper({
                    el: surfaceEl, model: graph, width: paperEl.clientWidth || 800, height: paperEl.clientHeight || 500,
                    gridSize: GRID, async: false, background: { color: 'transparent' }, clickThreshold: 4,
                    cellViewNamespace: Object.assign({}, J.shapes, { bpmn: NS }),
                    interactive: function (view) { return !!self.canEdit && view.model.get('kind') === 'node' && self.tool === 'select' ? { elementMove: true } : false; },
                    defaultConnector: { name: 'rounded' }, defaultRouter: { name: 'orthogonal' }
                });
                paper.on('element:pointerclick', function (view) {
                    var kind = view.model.get('kind');
                    if (kind === 'node') { self.clickNode(view.model.get('nodeId')); }
                    else if (kind === 'lane' && self.tool === 'select') { self.select('lane', view.model.get('laneId')); }
                    else if (kind === 'poolhead' && self.tool === 'select') { self.select('lane', self.poolFirstLane(view.model.get('poolId'))); }
                });
                paper.on('element:pointerdblclick', function (view) { self.openChild(view.model.get('nodeId')); });
                paper.on('element:pointerup', function (view) { self.dropNode(view); });
                paper.on('link:pointerclick', function (view) { self.select('flow', view.model.get('flowId')); });
                paper.on('blank:pointerdown', function (evt) { self.blankDown(evt); });
                paper.on('element:pointerdown', function (view, evt) {
                    var kind = view.model.get('kind');
                    if (kind === 'band' || self.tool === 'pan') { self.blankDown(evt); }
                });
                var move = function (evt) { if (panning) { var t = paper.translate(); paper.translate(t.tx + evt.clientX - panning.x, t.ty + evt.clientY - panning.y); panning = { x: evt.clientX, y: evt.clientY }; } };
                var up = function () { panning = null; };
                document.addEventListener('mousemove', move); document.addEventListener('mouseup', up);
                paperEl.addEventListener('dragover', function (e) { if (self.canEdit) { e.preventDefault(); } });
                paperEl.addEventListener('drop', function (e) { self.dropFromPalette(e); });
                document.addEventListener('keydown', function (e) { self.key(e); });
                window.addEventListener('resize', function () { self.resize(); });
                window.addEventListener('beforeunload', function () { if (saveTimer) { self.flush(); } });
                this.$nextTick(function () { self.resize(); self.draw(); self.fit(); });
            },

            // ── geometry, the same arithmetic as the server ────────────────────────────────────
            boxes: function () {
                var width = Math.max.apply(null, [POOL_MIN_WIDTH].concat(this.flow.nodes.map(function (n) { return n.x + n.w + POOL_PAD; })));
                var out = {}, y = 0;
                this.flow.pools.forEach(function (p) {
                    var h = LANE_HEIGHT * p.lanes.length, lanes = {};
                    p.lanes.forEach(function (l, i) { lanes[l.id] = { y: y + i * LANE_HEIGHT, h: LANE_HEIGHT }; });
                    out[p.id] = { x: 0, y: y, w: width, h: h, lanes: lanes };
                    y += h + POOL_GAP;
                });
                return out;
            },
            laneAt: function (poolId, cy) {
                var b = this.boxes()[poolId], found = null;
                Object.keys(b.lanes).forEach(function (id) { if (cy >= b.lanes[id].y && cy < b.lanes[id].y + b.lanes[id].h) { found = id; } });
                return found;
            },
            node: function (id) { return this.flow.nodes.filter(function (n) { return n.id === id; })[0]; },
            flowOf: function (id) { return this.flow.flows.filter(function (f) { return f.id === id; })[0]; },
            lane: function (id) { var r = null; this.flow.pools.forEach(function (p) { p.lanes.forEach(function (l) { if (l.id === id) { r = { lane: l, pool: p }; } }); }); return r; },
            nextId: function (prefix) {
                var used = {}, n = 1;
                this.flow.pools.forEach(function (p) { used[p.id] = 1; p.lanes.forEach(function (l) { used[l.id] = 1; }); });
                this.flow.nodes.forEach(function (x) { used[x.id] = 1; }); this.flow.flows.forEach(function (x) { used[x.id] = 1; });
                while (used[prefix + n]) { n += 1; }
                return prefix + n;
            },

            // ── drawing ───────────────────────────────────────────────────────────────────────
            draw: function () {
                var self = this, boxes = this.boxes(), cells = [];
                graph.clear();
                this.flow.pools.forEach(function (pool) {
                    var b = boxes[pool.id];
                    cells.push(self.band(pool.id, null, b.x, b.y, b.w, b.h, 'bpmn-pool', null));
                    pool.lanes.forEach(function (lane) {
                        var lb = b.lanes[lane.id];
                        cells.push(self.band(pool.id, lane, b.x + POOL_HEADER, lb.y, b.w - POOL_HEADER, lb.h, 'bpmn-lane', null));
                    });
                    cells.push(self.poolHead(pool, b));
                    pool.lanes.forEach(function (lane) { cells.push(self.laneHead(pool, lane, b.lanes[lane.id], b)); });
                });
                this.flow.nodes.forEach(function (n) { cells.push(self.shape(n)); });
                graph.addCells(cells);
                this.flow.flows.forEach(function (f) { var l = self.link(f); if (l) { graph.addCell(l); } });
                this.shellElementTotal = this.flow.nodes.length;
                this.shellRelTotal = this.flow.flows.length;
                this.highlight();
                this.$nextTick(function () { if (window.lucide) { window.lucide.createIcons(); } });
            },
            band: function (poolId, lane, x, y, w, h, cls) {
                var r = new NS.Band({ position: { x: x, y: y }, size: { width: w, height: h }, z: 0, kind: 'band' });
                r.attr('body/class', cls);
                return r;
            },
            head: function (kind, x, y, w, h, text, cls, extra) {
                var r = new NS.Band(Object.assign({ position: { x: x, y: y }, size: { width: w, height: h }, z: 1, kind: kind }, extra || {}));
                r.attr('body/class', 'bpmn-lane-head');
                r.attr('label/text', wrap(text, h - 12));
                r.attr('label/class', cls);
                r.attr('label/x', w / 2); r.attr('label/y', h / 2);
                r.attr('label/transform', 'rotate(-90 ' + (w / 2) + ' ' + (h / 2) + ')');
                return r;
            },
            poolHead: function (pool, b) {
                var r = this.head('poolhead', b.x, b.y, POOL_HEADER, b.h, pool.name, 'bpmn-lane-text', { poolId: pool.id });
                r.attr('root/data-pool-id', pool.id);
                return r;
            },
            laneHead: function (pool, lane, lb, b) {
                var r = this.head('lane', b.x + POOL_HEADER, lb.y, 28, lb.h, lane.name, 'bpmn-lane-text' + (lane.roleId ? '' : ' bpmn-lane-unlinked'), { laneId: lane.id });
                r.attr('root/data-lane-id', lane.id); r.attr('root/data-lane-role', lane.roleId || '');
                return r;
            },
            shape: function (n) {
                var cell, self = this;
                if (n.type === 'start' || n.type === 'intermediate' || n.type === 'end') {
                    cell = new NS.Event({ position: { x: n.x, y: n.y }, z: 5 });
                    cell.attr('outer/class', 'bpmn-shape bpmn-node' + (n.type === 'end' ? ' bpmn-end' : ''));
                    cell.attr('inner/r', n.type === 'intermediate' ? 14 : 0);
                    cell.attr('caption/text', n.label);
                } else if (TEXTY[n.type]) {
                    cell = new NS.Task({ position: { x: n.x, y: n.y }, z: 5 });
                    cell.attr('label/text', wrap(n.label, 98));
                    cell.attr('plus/display', n.type === 'subprocess' ? 'block' : 'none');
                    cell.attr('plusMark/display', n.type === 'subprocess' ? 'block' : 'none');
                } else {
                    cell = new NS.Gateway({ position: { x: n.x, y: n.y }, z: 5 });
                    cell.attr('mark/d', MARKS[n.type]);
                    cell.attr('ring/r', n.type === 'event' ? 11 : 0); cell.attr('ring2/r', n.type === 'event' ? 8 : 0);
                    cell.attr('caption/text', n.label);
                }
                cell.set({ kind: 'node', nodeId: n.id });
                cell.attr('root/data-node-id', n.id); cell.attr('root/data-node-type', n.type);
                return cell;
            },
            link: function (f) {
                var src = this.findCell('node', f.source), dst = this.findCell('node', f.target);
                if (!src || !dst) { return null; }
                var message = f.type === 'message';
                var l = new J.shapes.standard.Link({
                    source: { id: src.id }, target: { id: dst.id }, z: 3, kind: 'flow', flowId: f.id,
                    router: message ? { name: 'normal' } : { name: 'manhattan', args: { padding: 12, step: 10, startDirections: ['right', 'bottom', 'top'], endDirections: ['left', 'top', 'bottom'] } }, connector: { name: 'rounded' },
                    attrs: {
                        line: { class: 'bpmn-flow' + (message ? ' bpmn-flow-message' : ''),
                            targetMarker: message ? { type: 'path', d: 'M 10 -5 0 0 10 5 z', class: 'bpmn-arrow-open' } : { type: 'path', d: 'M 10 -5 0 0 10 5 z', class: 'bpmn-arrow' },
                            sourceMarker: message ? { type: 'circle', r: 4, cx: 4, class: 'bpmn-arrow-open' } : { type: 'path', d: '' } },
                        root: { 'data-flow-id': f.id, 'data-flow-type': f.type }
                    },
                    labels: f.label ? [{ position: 0.5, attrs: { text: { text: f.label, class: 'bpmn-flow-text' }, rect: { class: 'bpmn-label-bg' } } }] : []
                });
                return l;
            },
            findCell: function (kind, id) {
                var key = kind === 'node' ? 'nodeId' : 'flowId', r = null;
                graph.getCells().forEach(function (c) { if (c.get('kind') === kind && c.get(key) === id) { r = c; } });
                return r;
            },
            highlight: function () {
                var self = this;
                graph.getCells().forEach(function (c) {
                    var v = paper.findViewByModel(c); if (!v) { return; }
                    var kind = c.get('kind'), on = false, src = false;
                    if (kind === 'node') { on = self.selected && self.selected.kind === 'node' && self.selected.id === c.get('nodeId'); src = self.connectFrom === c.get('nodeId'); }
                    if (kind === 'flow') { on = self.selected && self.selected.kind === 'flow' && self.selected.id === c.get('flowId'); }
                    if (kind === 'lane') { on = self.selected && self.selected.kind === 'lane' && self.selected.id === c.get('laneId'); }
                    var cls = kind === 'flow' ? 'bpmn-flow-selected' : (kind === 'lane' ? 'bpmn-lane-selected' : 'bpmn-selected');
                    v.el.classList.toggle(cls, !!on);
                    v.el.classList.toggle('bpmn-source', !!src);
                });
                var n = this.selected && this.selected.kind === 'node' ? this.node(this.selected.id) : null;
                this.selectedNode = n ? { label: n.label || NAMES[n.type] } : null;
            },

            // ── selection and tools ───────────────────────────────────────────────────────────
            select: function (kind, id) {
                this.selected = kind ? { kind: kind, id: id } : null;
                this.roleOpen = false; this.roleQuery = ''; this.roleResults = [];
                this.highlight();
            },
            clickNode: function (id) {
                if (this.tool === 'connect' && this.canEdit) {
                    if (!this.connectFrom) { this.connectFrom = id; this.statusText = 'Now click the shape this flow goes to.'; this.highlight(); return; }
                    if (this.connectFrom !== id) { this.addFlow(this.connectFrom, id); }
                    this.connectFrom = null; this.highlight(); return;
                }
                this.select('node', id);
                if (this.canEdit) { this.focusLabel(); }
            },
            setTool: function (tool) {
                this.tool = tool; this.connectFrom = null;
                this.statusText = tool === 'connect' ? 'Click the shape a flow starts from.' : 'Ready';
                paperEl.style.cursor = tool === 'pan' ? 'grab' : '';
                this.highlight();
            },
            focusLabel: function () {
                var self = this;
                // the panel for the new selection is drawn a tick later; wait for its name field
                var tries = 0, look = function () {
                    var el = paperEl.closest('[data-testid="bpmn-editor"]').querySelector('[data-testid="bpmn-properties"] [x-ref="label"]');
                    if (el && el.offsetParent !== null) { el.focus(); el.select(); } else if (tries++ < 20) { setTimeout(look, 25); }
                };
                this.$nextTick(look);
            },
            blankDown: function (evt) {
                var e = evt.originalEvent || evt;
                if (this.tool === 'pan' || e.button === 1 || e.shiftKey) { panning = { x: e.clientX, y: e.clientY }; return; }
                if (this.tool === 'connect') { this.connectFrom = null; this.highlight(); return; }
                this.select(null);
            },
            poolFirstLane: function (poolId) { return this.flow.pools.filter(function (p) { return p.id === poolId; })[0].lanes[0].id; },

            // ── editing, every change goes through change() ──────────────────────────────────
            change: function (fn, redraw) {
                undoStack.push(JSON.stringify(this.flow)); if (undoStack.length > 100) { undoStack.shift(); }
                redoStack = [];
                fn();
                this.afterChange(redraw !== false);
            },
            afterChange: function (redraw) {
                this.canUndo = undoStack.length > 0; this.canRedo = redoStack.length > 0;
                if (redraw) { this.draw(); } else { this.shellElementTotal = this.flow.nodes.length; this.shellRelTotal = this.flow.flows.length; }
                this.markDirty();
            },
            undo: function () {
                if (!undoStack.length) { return; }
                redoStack.push(JSON.stringify(this.flow)); this.flow = JSON.parse(undoStack.pop());
                this.keepSelection(); this.afterChange(true);
            },
            redo: function () {
                if (!redoStack.length) { return; }
                undoStack.push(JSON.stringify(this.flow)); this.flow = JSON.parse(redoStack.pop());
                this.keepSelection(); this.afterChange(true);
            },
            keepSelection: function () {
                var s = this.selected;
                if (s && ((s.kind === 'node' && !this.node(s.id)) || (s.kind === 'flow' && !this.flowOf(s.id)) || (s.kind === 'lane' && !this.lane(s.id)))) { this.selected = null; }
            },
            targetLane: function () {
                var s = this.selected;
                if (s && s.kind === 'lane') { return this.lane(s.id); }
                if (s && s.kind === 'node') { var n = this.node(s.id); return n ? this.lane(n.lane) : null; }
                var p = this.flow.pools[0]; return { pool: p, lane: p.lanes[0] };
            },
            addNode: function (type, laneInfo, atX) {
                var self = this, info = laneInfo || this.targetLane(), boxes = this.boxes(), size = SIZES[type];
                var top = boxes[info.pool.id].lanes[info.lane.id].y, x = atX;
                var y = top + Math.round((LANE_HEIGHT - size[1]) / 2);
                if (x == null) { x = this.freeX(info.lane.id, size, y); }
                var id = this.nextId('n');
                this.change(function () {
                    self.flow.nodes.push({ id: id, type: type, label: TEXTY[type] || type === 'start' || type === 'end' ? NAMES[type] : '', pool: info.pool.id, lane: info.lane.id,
                        x: snap(x), y: y, w: size[0], h: size[1], processId: null });
                });
                this.select('node', id); this.focusLabel();
                this.statusText = 'Added a ' + NAMES[type].toLowerCase() + '. Type its name.';
            },
            // the first place in the lane, nearest the middle of what the person is looking at, where the new shape touches no other
            freeX: function (laneId, size, y) {
                var t = paper.translate(), sc = paper.scale().sx, mid = (paperEl.clientWidth / 2 - t.tx) / sc - size[0] / 2;
                var others = this.flow.nodes.filter(function (n) { return n.lane === laneId; });
                var free = function (x) {
                    return !others.some(function (n) { return x < n.x + n.w + 24 && x + size[0] + 24 > n.x && y < n.y + n.h + 12 && y + size[1] + 12 > n.y; });
                };
                var min = POOL_HEADER + 28 + 12, step = 20;
                var left = -t.tx / sc + 8, right = (paperEl.clientWidth - t.tx) / sc - size[0] - 8;
                for (var pass = 0; pass < 2; pass++) {
                    for (var k = 0; k < 400; k++) {
                        var x = snap(mid) + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * step;
                        if (x >= min && free(x) && (pass === 1 || (x >= left && x <= right))) { return x; }
                    }
                }
                return snap(Math.max.apply(null, [min].concat(others.map(function (n) { return n.x + n.w + 24; }))));
            },
            addFlow: function (a, b) {
                var self = this, na = this.node(a), nb = this.node(b);
                if (this.flow.flows.some(function (f) { return f.source === a && f.target === b; })) { this.statusText = 'Those two shapes are already joined.'; return; }
                var type = na.pool === nb.pool ? 'sequence' : 'message', id = this.nextId('f');
                this.change(function () { self.flow.flows.push({ id: id, type: type, source: a, target: b, label: '' }); });
                this.select('flow', id); this.focusLabel();
                this.statusText = (type === 'message' ? 'Message flow' : 'Sequence flow') + ' added. Type a label if it needs one.';
            },
            setLabel: function (value) {
                var s = this.selected; if (!s || !this.canEdit) { return; }
                this.typingStart();
                if (s.kind === 'node') { this.node(s.id).label = value; }
                else if (s.kind === 'flow') { this.flowOf(s.id).label = value; }
                else if (s.kind === 'lane') { this.lane(s.id).lane.name = value; }
                this.liveEdit();
            },
            // typing a name is one undo step: the state is kept before the first keystroke, until the field is left
            typingStart: function () {
                if (!this.typing) { this.typing = true; undoStack.push(JSON.stringify(this.flow)); redoStack = []; this.canUndo = true; this.canRedo = false; }
            },
            typingEnd: function () { this.typing = false; },
            liveEdit: function () { this.draw(); this.markDirty(); },
            setNodeType: function (type) {
                var n = this.node(this.selected.id), self = this;
                this.change(function () {
                    var cx = n.x + n.w / 2, cy = n.y + n.h / 2, size = SIZES[type];
                    n.type = type; n.w = size[0]; n.h = size[1]; n.x = snap(cx - size[0] / 2); n.y = Math.round(cy - size[1] / 2);
                    if (type !== 'subprocess') { n.processId = n.processId; }
                });
            },
            setNodeLane: function (laneId) {
                var n = this.node(this.selected.id), boxes = this.boxes();
                this.change(function () { n.lane = laneId; n.y = boxes[n.pool].lanes[laneId].y + Math.round((LANE_HEIGHT - n.h) / 2); });
            },
            dropNode: function (view) {
                var cell = view.model; if (cell.get('kind') !== 'node') { return; }
                var n = this.node(cell.get('nodeId')), p = cell.position(), boxes = this.boxes();
                var x = Math.max(POOL_HEADER + 28 + 10, snap(p.x)), b = boxes[n.pool];
                var cy = Math.min(Math.max(p.y + n.h / 2, b.y + 1), b.y + b.h - 1);
                var lane = this.laneAt(n.pool, cy), y = Math.round(Math.min(Math.max(p.y, b.y), b.y + b.h - n.h));
                if (x === n.x && y === n.y && lane === n.lane) { return; }
                this.change(function () { n.x = x; n.y = y; n.lane = lane; });
            },
            deleteSelected: function () {
                var s = this.selected, self = this; if (!s || !this.canEdit) { return; }
                if (s.kind === 'node') {
                    this.change(function () {
                        self.flow.nodes = self.flow.nodes.filter(function (n) { return n.id !== s.id; });
                        self.flow.flows = self.flow.flows.filter(function (f) { return f.source !== s.id && f.target !== s.id; });
                    });
                    this.statusText = 'Deleted the shape and the flows that touched it.';
                } else if (s.kind === 'flow') {
                    this.change(function () { self.flow.flows = self.flow.flows.filter(function (f) { return f.id !== s.id; }); });
                    this.statusText = 'Deleted the flow.';
                } else if (s.kind === 'lane') { this.removeLane(s.id); return; }
                this.selected = null; this.highlight();
            },
            laneIsEmpty: function (id) { return !this.flow.nodes.some(function (n) { return n.lane === id; }); },
            addLane: function () {
                var info = this.targetLane(), self = this, id = this.nextId('lane');
                this.change(function () { info.pool.lanes.push({ id: id, name: 'New lane', roleId: null }); });
                this.select('lane', id); this.focusLabel();
                this.statusText = 'Added a lane. Link it to a business role.';
            },
            removeLane: function (id) {
                var info = this.lane(id), self = this;
                if (info.pool.lanes.length < 2) { this.statusText = 'A pool keeps at least one lane.'; return; }
                if (!this.laneIsEmpty(id)) { this.statusText = 'Move or delete the shapes in this lane first.'; return; }
                this.change(function () { info.pool.lanes = info.pool.lanes.filter(function (l) { return l.id !== id; }); });
                this.selected = null; this.highlight(); this.statusText = 'Removed the lane.';
            },
            addPool: function () {
                var self = this, pid = this.nextId('pool'), lid = this.nextId('lane');
                this.change(function () { self.flow.pools.push({ id: pid, name: 'New pool', lanes: [{ id: lid, name: 'New lane', roleId: null }] }); });
                this.statusText = 'Added a pool. Message flows join shapes in different pools.';
                this.select('lane', lid); this.fit();
            },
            setPoolName: function (value) { var i = this.lane(this.selected.id); this.typingStart(); i.pool.name = value; this.liveEdit(); },

            // ── lanes are business roles of the model ────────────────────────────────────────
            searchRoles: function () {
                var self = this, q = this.roleQuery.trim();
                clearTimeout(this.roleTimer);
                this.roleTimer = setTimeout(function () {
                    Platform.fetch.get('/archimate/api/elements/search?type=BusinessRole&limit=20&q=' + encodeURIComponent(q), { silent: true })
                        .then(function (data) {
                            var rows = Array.isArray(data) ? data : (data.data || data.elements || []);
                            self.roleResults = rows.map(function (r) { return { id: r.id, name: r.name }; });
                            self.roleError = '';
                            self.roleOpen = true;
                        })
                        .catch(function (err) { self.roleResults = []; self.roleError = err.message || 'The business roles could not be loaded.'; self.roleOpen = true; });
                }, 300);
            },
            pickRole: function (role) {
                var info = this.lane(this.selected.id);
                this.change(function () { info.lane.roleId = role.id; info.lane.name = role.name; });
                this.roleOpen = false; this.roleQuery = ''; this.roleResults = [];
                this.statusText = 'The lane is now the business role ' + role.name + '.';
            },
            clearRole: function () {
                var info = this.lane(this.selected.id);
                this.change(function () { info.lane.roleId = null; });
            },

            // ── adding from the palette by dragging ──────────────────────────────────────────
            dragStart: function (evt, type) { evt.dataTransfer.setData('text/plain', type); evt.dataTransfer.effectAllowed = 'copy'; },
            dropFromPalette: function (evt) {
                if (!this.canEdit) { return; }
                var type = evt.dataTransfer && evt.dataTransfer.getData('text/plain');
                if (!SIZES[type]) { return; }
                evt.preventDefault();
                var rect = paperEl.getBoundingClientRect(), t = paper.translate(), s = paper.scale().sx;
                var x = (evt.clientX - rect.left - t.tx) / s, y = (evt.clientY - rect.top - t.ty) / s, boxes = this.boxes(), hit = null, self = this;
                this.flow.pools.forEach(function (p) {
                    var b = boxes[p.id]; if (y >= b.y && y < b.y + b.h) { var l = self.laneAt(p.id, y); hit = { pool: p, lane: self.lane(l).lane }; }
                });
                if (!hit) { this.statusText = 'Drop the shape inside a lane.'; return; }
                this.addNode(type, hit, Math.max(POOL_HEADER + 38, snap(x - SIZES[type][0] / 2)));
            },

            // ── the finder ───────────────────────────────────────────────────────────────────
            finderRows: function () {
                var q = this.finderQuery.trim().toLowerCase(), self = this;
                return this.flow.nodes.filter(function (n) { return !q || (n.label || NAMES[n.type]).toLowerCase().indexOf(q) >= 0; })
                    .map(function (n) { var l = self.lane(n.lane); return { id: n.id, label: n.label || NAMES[n.type], kind: NAMES[n.type], lane: l ? l.lane.name : '' }; });
            },
            reveal: function (id) {
                this.select('node', id);
                var n = this.node(id), s = paper.scale().sx;
                paper.translate(paperEl.clientWidth / 2 - (n.x + n.w / 2) * s, paperEl.clientHeight / 2 - (n.y + n.h / 2) * s);
            },
            laneOptions: function () {
                var n = this.selected && this.selected.kind === 'node' ? this.node(this.selected.id) : null;
                if (!n) { return []; }
                var pool = this.flow.pools.filter(function (p) { return p.id === n.pool; })[0];
                return pool ? pool.lanes : [];
            },
            selectedFlowEnds: function () {
                var f = this.selected && this.selected.kind === 'flow' ? this.flowOf(this.selected.id) : null;
                if (!f) { return ''; }
                var a = this.node(f.source), b = this.node(f.target);
                return 'From ' + (a.label || NAMES[a.type]) + ' to ' + (b.label || NAMES[b.type]);
            },
            selectedNodeData: function () { return this.selected && this.selected.kind === 'node' ? this.node(this.selected.id) : null; },
            selectedFlowData: function () { return this.selected && this.selected.kind === 'flow' ? this.flowOf(this.selected.id) : null; },
            selectedLaneData: function () { return this.selected && this.selected.kind === 'lane' ? this.lane(this.selected.id) : null; },
            childUrl: function (n) { return n && n.processId ? this.urls.diagramBase + n.processId + '/diagram' : null; },
            openChild: function (id) {
                var n = this.node(id), url = this.childUrl(n);
                if (n && n.type === 'subprocess' && url && !this.dirty) { window.location.assign(url); }
            },

            toggleRail: function () {
                var self = this; this.railOpen = !this.railOpen;
                this.$nextTick(function () { self.resize(); self.fit(); });
            },

            // ── view ─────────────────────────────────────────────────────────────────────────
            resize: function () { if (paper) { paper.setDimensions(paperEl.clientWidth, paperEl.clientHeight); } },
            zoomBy: function (factor) {
                var s = Math.min(2, Math.max(0.25, paper.scale().sx * factor));
                paper.scale(s, s); this.zoomPercent = Math.round(s * 100);
            },
            fit: function () {
                var b = graph.getBBox(); if (!b || !paperEl.clientWidth) { return; }
                var pad = 24, narrow = paperEl.clientWidth < 600;
                var s = Math.min(1.25, (paperEl.clientWidth - pad * 2) / b.width, (paperEl.clientHeight - pad * 2) / b.height);
                // on a phone a whole-diagram fit is too small to read, so it fits the first lanes and the person pans
                s = Math.max(narrow ? 0.5 : 0.5, s); paper.scale(s, s);
                var tx = narrow && b.width * s > paperEl.clientWidth ? pad - b.x * s : Math.round((paperEl.clientWidth - b.width * s) / 2 - b.x * s);
                paper.translate(tx, Math.round(Math.max(pad / 2, (paperEl.clientHeight - b.height * s) / 2) - b.y * s));
                this.zoomPercent = Math.round(s * 100);
            },
            key: function (e) {
                var tag = (e.target && e.target.tagName || '').toLowerCase();
                var typing = tag === 'input' || tag === 'textarea' || tag === 'select';
                if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z') && !typing) { e.preventDefault(); if (e.shiftKey) { this.redo(); } else { this.undo(); } return; }
                if ((e.key === 'Delete' || e.key === 'Backspace') && !typing && this.selected) { e.preventDefault(); this.deleteSelected(); }
                if (e.key === 'Escape' && this.connectFrom) { this.connectFrom = null; this.highlight(); }
            },

            // ── saving ───────────────────────────────────────────────────────────────────────
            dirty: false, typing: false,
            markDirty: function () {
                var self = this; this.dirty = true;
                this.saveIndicator = { tone: 'text-muted-foreground', icon: 'loader', text: 'Saving…' };
                clearTimeout(saveTimer); saveTimer = setTimeout(function () { self.save(); }, 400);
            },
            flush: function () { if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; return this.save(); } return Promise.resolve(); },
            save: function () {
                var self = this; saveTimer = null;
                if (saving) { dirtyWhileSaving = true; return Promise.resolve(); }
                saving = true; this.dirty = false;
                return Platform.fetch.put(this.urls.flow, this.flow, { silent: true }).then(function (saved) {
                    // the server gives each new sub-process shape its child process
                    saved.nodes.forEach(function (sn) { var n = self.node(sn.id); if (n && n.type === 'subprocess' && sn.processId) { n.processId = sn.processId; } });
                    if (!dirtyWhileSaving && !saveTimer) { self.saveIndicator = { tone: 'text-success', icon: 'check', text: 'Saved' }; }
                    self.statusText = self.statusText === 'Ready' ? 'Ready' : self.statusText;
                }).catch(function (err) {
                    self.dirty = true;
                    self.saveIndicator = { tone: 'text-destructive', icon: 'alert-triangle', text: 'Not saved' };
                    self.statusText = (err && err.message) || 'The diagram could not be saved.';
                }).then(function () {
                    saving = false;
                    if (dirtyWhileSaving) { dirtyWhileSaving = false; return self.save(); }
                });
            },

            // ── XML ──────────────────────────────────────────────────────────────────────────
            exportXml: function () {
                var self = this;
                return this.flush().then(function () { window.location.assign(self.urls.export); });
            },
            importXml: function () {
                var self = this, input = this.$refs.importFile, file = input && input.files && input.files[0];
                if (!file) { this.importError = 'Choose a BPMN 2.0 file first.'; return; }
                this.importBusy = true; this.importError = ''; this.importNotes = [];
                var body = new FormData(); body.append('file', file);
                Platform.fetch.post(this.urls.import, body, { silent: true }).then(function (res) {
                    undoStack.push(JSON.stringify(self.flow)); redoStack = [];
                    self.flow = res.flow; self.selected = null; self.canUndo = true; self.canRedo = false;
                    self.draw(); self.fit();
                    self.saveIndicator = { tone: 'text-success', icon: 'check', text: 'Saved' };
                    self.importNotes = res.notes || [];
                    self.statusText = 'Imported ' + res.flow.nodes.length + ' steps and ' + res.flow.flows.length + ' flows.';
                    if (!self.importNotes.length) { self.importOpen = false; }
                }).catch(function (err) { self.importError = (err && err.message) || 'The file could not be imported.'; })
                    .then(function () { self.importBusy = false; });
            }
        };
    };
})();
