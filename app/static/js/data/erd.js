/**
 * Entity relationship diagram of one data domain, drawn on JointJS.
 *
 * The toolbar, finder, legend and status line are the shared diagram components; this file is the state they read
 * (the same property names the Composer exposes) and the drawing of entities and crow's foot relationship ends.
 * Every change is written through /architecture/api/data-erd: the diagram holds no data of its own.
 */
function erdApp() {
    'use strict';

    let CFG = window.__ERD_CONFIG__ || {};
    let ENTITY_W = 240;
    let HEADER_H = 32;
    let ROW_H = 22;
    let MAX_NAME = 22;

    let END_LABELS = {
        exactly_one: 'Exactly one',
        zero_or_one: 'Zero or one',
        one_or_many: 'One or many',
        zero_or_many: 'Zero or many',
    };
    let PRESET_ENDS = {
        'one-to-one': ['exactly_one', 'exactly_one'],
        'one-to-many': ['exactly_one', 'zero_or_many'],
        'many-to-one': ['zero_or_many', 'exactly_one'],
        'many-to-many': ['zero_or_many', 'zero_or_many'],
    };

    /* Crow's foot ends. The origin is the point where the line meets the entity; +x runs back along the line. */
    let FORK = 'M 12 0 L 0 -7 M 12 0 L 0 7 M 12 0 L 0 0';
    let BAR = function(x) { return 'M ' + x + ' -7 L ' + x + ' 7'; };
    let RING = function(x) { return 'M ' + (x + 4) + ' 0 a 4 4 0 1 0 -8 0 a 4 4 0 1 0 8 0'; };
    let END_PATHS = {
        exactly_one: { d: BAR(6) + ' ' + BAR(11), ring: false },
        zero_or_one: { d: BAR(6) + ' ' + RING(15), ring: true },
        one_or_many: { d: FORK + ' ' + BAR(16), ring: false },
        zero_or_many: { d: FORK + ' ' + RING(18), ring: true },
    };

    /* Named shapes of this diagram. They do not reuse JointJS's own erd.* shapes, which are different drawings. */
    joint.shapes.dataerd = joint.shapes.dataerd || {};
    joint.dia.Element.define('dataerd.Entity', {}, {}, joint.shapes.dataerd);
    joint.shapes.standard.Link.define('dataerd.Relationship', {}, {}, joint.shapes.dataerd);

    function token(name, fallback) {
        let raw = '';
        try { raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); } catch (e) { raw = ''; }
        return raw ? 'hsl(' + raw + ')' : fallback;
    }

    function colours() {
        return {
            card: token('--card', 'white'),
            border: token('--border', 'gray'),
            foreground: token('--foreground', 'black'),
            muted: token('--muted-foreground', 'gray'),
            primary: token('--primary', 'blue'),
            accent: token('--accent', 'white'),
            mutedBg: token('--muted', 'white'),
            ring: token('--ring', 'blue'),
        };
    }

    function clip(text, max) {
        text = String(text || '');
        return text.length > max ? text.slice(0, max - 1) + '…' : text;
    }

    function csrf() {
        return (document.querySelector('meta[name=csrf-token]') || {}).content || '';
    }

    function entityHeight(entity) {
        return HEADER_H + Math.max(1, entity.attributes.length) * ROW_H + 8;
    }

    function keyLabel(attr) {
        if (attr.is_primary_key && attr.is_foreign_key) return 'PK FK';
        if (attr.is_primary_key) return 'PK';
        if (attr.is_foreign_key) return 'FK';
        return '';
    }

    function blankAttribute() {
        return { id: null, name: '', data_type: 'varchar', is_primary_key: false, is_foreign_key: false, is_nullable: true };
    }

    return {
        /* ── state the shared components read ───────────── */
        mode: 'edit',
        shellRailOpen: true,
        shellRailTab: 'view',
        shellTool: 'select',
        connectorStyle: 'manhattan',
        zoomPercent: 100,
        finderFilter: '',
        finderItems: [],
        shellElementTotal: 0,
        shellRelTotal: 0,
        legendTypes: [],
        selectedNode: null,
        statusText: 'Ready',
        saveIndicator: { tone: 'text-muted-foreground', icon: 'check', text: 'Saved' },
        showShortcutsModal: false,

        /* ── state of this page ─────────────────────────── */
        domainId: CFG.domainId,
        entities: [],
        relationships: [],
        graph: null,
        paper: null,
        selected: null,
        pendingSource: null,
        busy: false,
        panning: null,
        spaceHeld: false,

        editOpen: false,
        editError: '',
        edit: { id: null, name: '', description: '', attributes: [] },
        dataTypes: CFG.dataTypes || [],

        relOpen: false,
        relError: '',
        rel: { id: null, source_id: null, target_id: null, sourceName: '', targetName: '', preset: 'one-to-many',
               source_cardinality: 'exactly_one', target_cardinality: 'zero_or_many', label: '' },

        get hasSelection() { return !!this.selected; },
        get hasProperties() { return !!this.selected; },
        get canUndoNow() { return false; },
        get canRedoNow() { return false; },
        get finderRows() {
            let q = (this.finderFilter || '').trim().toLowerCase();
            if (!q) return this.finderItems;
            return this.finderItems.filter(function(r) { return r.name.toLowerCase().indexOf(q) !== -1; });
        },
        get editTitle() { return this.edit.id ? 'Edit entity' : 'Add entity'; },
        /* The selected entity, with the address of its record, for the selection bar and the edit dialog. */
        get selectedEntity() {
            if (!this.selected || this.selected.kind !== 'entity') return null;
            return this.entityById(this.selected.id) || null;
        },
        get selectedRelationship() {
            if (!this.selected || this.selected.kind !== 'relationship') return null;
            let id = this.selected.id;
            let r = this.relationships.find(function(x) { return x.id === id; });
            if (!r) return null;
            let s = this.entityById(r.source_id), t = this.entityById(r.target_id);
            return { id: r.id, text: (s ? s.name : '') + ' ' + (r.label || 'relates to') + ' ' + (t ? t.name : '') };
        },
        get editRecordUrl() {
            let e = this.edit.id ? this.entityById(this.edit.id) : null;
            return e ? e.url : '';
        },
        get relSummary() {
            return this.rel.sourceName + ' to ' + this.rel.targetName;
        },

        /* ── start ──────────────────────────────────────── */
        init: function() {
            let self = this;
            self.shellRailOpen = window.matchMedia('(min-width: 768px)').matches;
            self.entities = (CFG.diagram.entities || []).slice();
            self.relationships = (CFG.diagram.relationships || []).slice();
            self.$nextTick(function() {
                self.buildPaper();
                self.draw();
                self.fitCanvas();
            });
            new MutationObserver(function() { self.draw(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
            window.addEventListener('resize', function() { if (self.paper) self.fitCanvas(); });
            document.addEventListener('keydown', function(ev) {
                let tag = (ev.target && ev.target.tagName || '').toLowerCase();
                if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
                if (ev.key === 'Delete' && self.selected) { ev.preventDefault(); self.deleteSelected(); }
                if (ev.key === 'Escape') { self.pendingSource = null; self.shellSetTool('select'); self.say('Ready'); }
                if (ev.key === 'Enter' && self.selected && !self.editOpen && !self.relOpen) { ev.preventDefault(); self.shellOpenProperties(); }
                if (ev.key === '?' && !self.editOpen && !self.relOpen) { ev.preventDefault(); self.showShortcutsModal = true; }
                if (ev.key === ' ' && !self.spaceHeld) { ev.preventDefault(); self.spaceHeld = true; }
            });
            document.addEventListener('keyup', function(ev) {
                if (ev.key === ' ') { self.spaceHeld = false; self.panning = null; }
            });
        },

        say: function(text) { this.statusText = text; },

        /* ── drawing ────────────────────────────────────── */
        buildPaper: function() {
            let self = this;
            let el = document.getElementById('erd-canvas');
            self.graph = new joint.dia.Graph();
            self.paper = new joint.dia.Paper({
                el: el,
                model: self.graph,
                width: '100%',
                height: '100%',
                gridSize: 10,
                drawGrid: false,
                background: { color: 'transparent' },
                interactive: function(view) { return view.model.isElement() && self.shellTool === 'select'; },
                async: false,
                defaultConnectionPoint: { name: 'boundary' },
                linkPinning: false,
            });
            self.paper.on('element:pointerclick', function(view) { self.onEntityClick(view.model); });
            self.paper.on('element:pointerdblclick', function(view) { self.openEdit(view.model.get('entityId')); });
            self.paper.on('link:pointerclick', function(view) { self.selectRelationship(view.model.get('relId')); });
            self.paper.on('blank:pointerclick', function() { self.clearSelection(); });
            self.paper.on('blank:pointerdown', function(ev) {
                if (self.shellTool === 'pan' || self.spaceHeld) {
                    self.panning = { x: ev.clientX, y: ev.clientY, tx: self.paper.translate().tx, ty: self.paper.translate().ty };
                }
            });
            el.addEventListener('mousemove', function(ev) {
                if (!self.panning) return;
                self.paper.translate(self.panning.tx + ev.clientX - self.panning.x, self.panning.ty + ev.clientY - self.panning.y);
            });
            window.addEventListener('mouseup', function() { self.panning = null; });
            self.graph.on('change:position', function(cell) {
                if (cell.isElement()) self.rememberPosition(cell);
            });
        },

        storageKey: function() { return 'archie.erd.positions.' + this.domainId; },

        savedPositions: function() {
            try { return JSON.parse(window.localStorage.getItem(this.storageKey()) || '{}'); } catch (e) { return {}; }
        },

        rememberPosition: function(cell) {
            let all = this.savedPositions();
            all[cell.get('entityId')] = cell.position();
            try { window.localStorage.setItem(this.storageKey(), JSON.stringify(all)); } catch (e) { /* swallow-ok: localStorage is unavailable in private browsing; the layout is simply recomputed next time */ }
        },

        layoutPositions: function() {
            let g = new dagre.graphlib.Graph();
            g.setGraph({ rankdir: 'LR', nodesep: 50, ranksep: 200, marginx: 30, marginy: 30 });
            g.setDefaultEdgeLabel(function() { return {}; });
            this.entities.forEach(function(e) { g.setNode(String(e.id), { width: ENTITY_W, height: entityHeight(e) }); });
            this.relationships.forEach(function(r) {
                if (r.source_id !== r.target_id) g.setEdge(String(r.source_id), String(r.target_id));
            });
            dagre.layout(g);
            let out = {};
            g.nodes().forEach(function(id) {
                let n = g.node(id);
                out[id] = { x: n.x - n.width / 2, y: n.y - n.height / 2 };
            });
            return out;
        },

        entityCell: function(entity, position, c) {
            let rows = entity.attributes.length ? entity.attributes : [];
            let h = entityHeight(entity);
            let markup = [
                { tagName: 'rect', selector: 'body' },
                { tagName: 'rect', selector: 'header' },
                { tagName: 'text', selector: 'title' },
                { tagName: 'text', selector: 'count' },
                { tagName: 'path', selector: 'rule' },
            ];
            let attrs = {
                body: { width: ENTITY_W, height: h, rx: 6, ry: 6, fill: c.card, stroke: c.border, strokeWidth: 1 },
                header: { width: ENTITY_W, height: HEADER_H, rx: 6, ry: 6, fill: c.mutedBg, stroke: c.border, strokeWidth: 1 },
                title: { text: clip(entity.name, MAX_NAME), x: 12, y: HEADER_H / 2 + 5, fill: c.foreground, fontSize: 14,
                         fontWeight: 600, fontFamily: 'Inter, system-ui, sans-serif' },
                count: { text: '', x: ENTITY_W - 12, y: HEADER_H / 2 + 4, textAnchor: 'end', fill: c.muted, fontSize: 11,
                         fontFamily: 'Inter, system-ui, sans-serif' },
                rule: { d: 'M 0 ' + HEADER_H + ' H ' + ENTITY_W, stroke: c.border, strokeWidth: 1 },
            };
            rows.forEach(function(a, i) {
                let y = HEADER_H + 8 + i * ROW_H + 14;
                markup.push({ tagName: 'text', selector: 'key' + i }, { tagName: 'text', selector: 'name' + i },
                            { tagName: 'text', selector: 'type' + i });
                attrs['key' + i] = { text: keyLabel(a), x: 12, y: y, fill: c.primary, fontSize: 10, fontWeight: 700,
                                     fontFamily: 'ui-monospace, monospace' };
                attrs['name' + i] = { text: clip(a.name, 20) + (a.is_nullable ? ' ?' : ''), x: 50, y: y, fill: c.foreground,
                                      fontSize: 12, fontWeight: a.is_primary_key ? 600 : 400, fontFamily: 'ui-monospace, monospace' };
                attrs['type' + i] = { text: clip(a.data_type || '', 10), x: ENTITY_W - 12, y: y, textAnchor: 'end', fill: c.muted,
                                      fontSize: 11, fontFamily: 'ui-monospace, monospace' };
            });
            if (!rows.length) {
                markup.push({ tagName: 'text', selector: 'none' });
                attrs.none = { text: 'No attributes yet', x: 12, y: HEADER_H + 8 + 14, fill: c.muted, fontSize: 12,
                               fontFamily: 'Inter, system-ui, sans-serif' };
            }
            return new joint.shapes.dataerd.Entity({
                entityId: entity.id,
                position: position,
                size: { width: ENTITY_W, height: h },
                markup: markup,
                attrs: attrs,
            });
        },

        endMarker: function(kind, c) {
            let def = END_PATHS[kind] || END_PATHS.exactly_one;
            return { type: 'path', d: def.d, fill: def.ring ? c.card : 'none', stroke: c.foreground, 'stroke-width': 1.5 };
        },

        linkCell: function(rel, c) {
            let router = this.connectorStyle === 'manhattan' ? { name: 'manhattan', args: { padding: 20 } } : { name: 'normal' };
            let connector = this.connectorStyle === 'smooth' ? { name: 'smooth' } : { name: 'rounded', args: { radius: 6 } };
            let link = new joint.shapes.dataerd.Relationship({
                relId: rel.id,
                source: { id: this.cellFor(rel.source_id).id },
                target: { id: this.cellFor(rel.target_id).id },
                router: router,
                connector: connector,
                attrs: {
                    line: {
                        stroke: c.foreground, strokeWidth: 1.5, fill: 'none',
                        sourceMarker: this.endMarker(rel.source_cardinality, c),
                        targetMarker: this.endMarker(rel.target_cardinality, c),
                    },
                },
            });
            if (rel.label) {
                link.set('labels', [{ position: 0.5, attrs: {
                    text: { text: clip(rel.label, 24), fill: c.muted, fontSize: 11, fontFamily: 'Inter, system-ui, sans-serif' },
                    rect: { fill: c.card, stroke: 'none' },
                } }]);
            }
            return link;
        },

        cellFor: function(entityId) {
            return this.graph.getElements().find(function(e) { return e.get('entityId') === entityId; });
        },

        draw: function() {
            if (!this.graph) return;
            let c = colours();
            let saved = this.savedPositions();
            let computed = null;
            let self = this;
            let oldPositions = {};
            this.graph.getElements().forEach(function(e) { oldPositions[e.get('entityId')] = e.position(); });
            this.graph.clear();
            this.entities.forEach(function(e) {
                let pos = oldPositions[e.id] || saved[e.id];
                if (!pos) {
                    computed = computed || self.layoutPositions();
                    pos = computed[String(e.id)];
                }
                self.graph.addCell(self.entityCell(e, pos, c));
            });
            this.relationships.forEach(function(r) {
                if (self.cellFor(r.source_id) && self.cellFor(r.target_id)) self.graph.addCell(self.linkCell(r, c));
            });
            this.refreshState();
            this.paintSelection();
        },

        refreshState: function() {
            let self = this;
            this.shellElementTotal = this.graph.getElements().length;
            this.shellRelTotal = this.graph.getLinks().length;
            this.finderItems = this.entities.map(function(e) {
                let n = e.attributes.length;
                return { id: e.id, name: e.name, type: n + (n === 1 ? ' attribute' : ' attributes'), elType: 'DataObject', layer: 'application' };
            });
            let kinds = {};
            this.relationships.forEach(function(r) { kinds[r.source_cardinality] = true; kinds[r.target_cardinality] = true; });
            this.legendTypes = Object.keys(END_LABELS).filter(function(k) { return kinds[k]; })
                .map(function(k) { return { type: k, label: END_LABELS[k] }; });
            let picked = self.selected && self.selected.kind === 'entity'
                ? self.entities.find(function(e) { return e.id === self.selected.id; }) : null;
            self.selectedNode = picked
                ? { label: picked.name, url: picked.url }
                : (self.selected ? { label: 'a relationship' } : null);
        },

        relGlyph: function(kind) {
            let def = END_PATHS[kind] || END_PATHS.exactly_one;
            let c = colours();
            return '<svg width="44" height="16" viewBox="-2 -8 44 16" aria-hidden="true">'
                + '<path d="M 0 0 H 40" stroke="' + c.foreground + '" stroke-width="1.5" fill="none"/>'
                + '<path d="' + def.d + '" stroke="' + c.foreground + '" stroke-width="1.5" fill="' + (def.ring ? c.card : 'none') + '"/>'
                + '</svg>';
        },

        /* ── view controls ──────────────────────────────── */
        zoomBy: function(factor) {
            let s = Math.min(2, Math.max(0.25, this.paper.scale().sx * factor));
            this.paper.scale(s, s);
            this.zoomPercent = Math.round(s * 100);
        },
        zoomIn: function() { this.zoomBy(1.2); },
        zoomOut: function() { this.zoomBy(1 / 1.2); },
        fitCanvas: function() {
            if (!this.paper) return;
            if (!this.graph.getElements().length) { this.zoomPercent = 100; return; }
            let narrow = window.matchMedia('(max-width: 767px)').matches;
            this.paper.scaleContentToFit({ padding: 30, maxScale: 1, minScale: narrow ? 0.55 : 0.25 });
            this.paper.translate(0, 0);
            let bbox = this.paper.getContentBBox();
            let area = this.paper.getComputedSize();
            let dx = bbox.width > area.width ? 30 - bbox.x : (area.width - bbox.width) / 2 - bbox.x;
            let dy = bbox.height > area.height ? 30 - bbox.y : (area.height - bbox.height) / 2 - bbox.y;
            this.paper.translate(dx, dy);
            this.zoomPercent = Math.round(this.paper.scale().sx * 100);
        },
        shellSetTool: function(tool) {
            this.shellTool = tool;
            this.pendingSource = null;
            this.say(tool === 'connect' ? 'Click the first entity, then the entity it relates to.' : 'Ready');
        },
        shellSetConnector: function(style) {
            this.connectorStyle = style;
            this.draw();
        },
        shellFocus: function(id) {
            let cell = this.cellFor(id);
            if (!cell) return;
            this.select('entity', id);
            let box = cell.getBBox();
            let area = this.paper.getComputedSize();
            let s = this.paper.scale().sx;
            this.paper.translate(area.width / 2 - (box.x + box.width / 2) * s, area.height / 2 - (box.y + box.height / 2) * s);
            if (window.matchMedia('(max-width: 767px)').matches) this.shellRailOpen = false;
        },

        /* ── selection ──────────────────────────────────── */
        select: function(kind, id) {
            this.selected = { kind: kind, id: id };
            this.refreshState();
            this.paintSelection();
        },
        clearSelection: function() {
            this.selected = null;
            this.refreshState();
            this.paintSelection();
        },
        paintSelection: function() {
            let self = this;
            let c = colours();
            this.graph.getElements().forEach(function(cell) {
                let on = self.selected && self.selected.kind === 'entity' && self.selected.id === cell.get('entityId');
                let pending = self.pendingSource === cell.get('entityId');
                cell.attr('body/stroke', on || pending ? c.ring : c.border);
                cell.attr('body/strokeWidth', on || pending ? 2.5 : 1);
            });
            this.graph.getLinks().forEach(function(link) {
                let on = self.selected && self.selected.kind === 'relationship' && self.selected.id === link.get('relId');
                link.attr('line/stroke', on ? c.ring : c.foreground);
                link.attr('line/strokeWidth', on ? 2.5 : 1.5);
            });
        },
        onEntityClick: function(cell) {
            let id = cell.get('entityId');
            if (this.shellTool === 'connect') {
                if (this.pendingSource === null) {
                    this.pendingSource = id;
                    this.paintSelection();
                    this.say('Now click the entity it relates to.');
                } else {
                    let source = this.pendingSource;
                    this.pendingSource = null;
                    this.openRelationship(null, source, id);
                    this.shellSetTool('select');
                }
                return;
            }
            this.select('entity', id);
        },
        selectRelationship: function(id) {
            if (this.shellTool !== 'select') return;
            this.select('relationship', id);
        },
        entityById: function(id) { return this.entities.find(function(e) { return e.id === id; }); },

        /* ── editing an entity ──────────────────────────── */
        shellOpenProperties: function() {
            if (!this.selected) return;
            if (this.selected.kind === 'entity') this.openEdit(this.selected.id);
            else this.openRelationship(this.selected.id);
        },
        openEdit: function(id) {
            this.editError = '';
            if (id) {
                let e = this.entityById(id);
                this.edit = {
                    id: e.id, name: e.name, description: e.description || '',
                    attributes: e.attributes.map(function(a) { return Object.assign({}, a); }),
                };
            } else {
                this.edit = { id: null, name: '', description: '', attributes: [blankAttribute()] };
            }
            this.editOpen = true;
        },
        addAttributeRow: function() { this.edit.attributes.push(blankAttribute()); },
        removeAttributeRow: function(index) { this.edit.attributes.splice(index, 1); },
        keyChanged: function(attr) { if (attr.is_primary_key) attr.is_nullable = false; },
        async saveEntity() {
            let self = this;
            if (self.busy) return;
            self.editError = '';
            if (!self.edit.name.trim()) { self.editError = 'Give the entity a name.'; return; }
            self.busy = true;
            let body = { name: self.edit.name, description: self.edit.description, attributes: self.edit.attributes };
            try {
                let url = self.edit.id ? '/architecture/api/data-erd/entities/' + self.edit.id
                                       : '/architecture/api/data-erd/' + self.domainId + '/entities';
                let response = await window.fetch(url, { // raw-fetch-ok: the person needs the server's own message beside the form, which Platform.fetch would show only as a toast
                    method: self.edit.id ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                    body: JSON.stringify(body),
                });
                let data = await response.json();
                if (!response.ok) throw new Error(data.error || 'The entity could not be saved.');
                let at = self.entities.findIndex(function(e) { return e.id === data.id; });
                if (at === -1) self.entities.push(data); else self.entities.splice(at, 1, data);
                self.entities.sort(function(a, b) { return a.name.localeCompare(b.name); });
                self.editOpen = false;
                self.draw();
                if (at === -1) { self.fitCanvas(); }
                self.select('entity', data.id);
                self.say('Saved ' + data.name + '.');
            } catch (err) {
                self.editError = err.message;
            } finally {
                self.busy = false;
            }
        },

        /* ── relating two entities ──────────────────────── */
        presetFor: function(a, b) {
            let found = 'custom';
            Object.keys(PRESET_ENDS).forEach(function(k) { if (PRESET_ENDS[k][0] === a && PRESET_ENDS[k][1] === b) found = k; });
            return found;
        },
        presetChanged: function() {
            let ends = PRESET_ENDS[this.rel.preset];
            if (ends) { this.rel.source_cardinality = ends[0]; this.rel.target_cardinality = ends[1]; }
        },
        endChanged: function() { this.rel.preset = this.presetFor(this.rel.source_cardinality, this.rel.target_cardinality); },
        openRelationship: function(relId, sourceId, targetId) {
            this.relError = '';
            if (relId) {
                let r = this.relationships.find(function(x) { return x.id === relId; });
                this.rel = {
                    id: r.id, source_id: r.source_id, target_id: r.target_id,
                    sourceName: this.entityById(r.source_id).name, targetName: this.entityById(r.target_id).name,
                    preset: this.presetFor(r.source_cardinality, r.target_cardinality),
                    source_cardinality: r.source_cardinality, target_cardinality: r.target_cardinality, label: r.label || '',
                };
            } else {
                this.rel = {
                    id: null, source_id: sourceId, target_id: targetId,
                    sourceName: this.entityById(sourceId).name, targetName: this.entityById(targetId).name,
                    preset: 'one-to-many', source_cardinality: 'exactly_one', target_cardinality: 'zero_or_many', label: '',
                };
            }
            this.relOpen = true;
        },
        async saveRelationship() {
            let self = this;
            if (self.busy) return;
            self.busy = true;
            self.relError = '';
            let body = { source_cardinality: self.rel.source_cardinality, target_cardinality: self.rel.target_cardinality, label: self.rel.label };
            try {
                let url = '/architecture/api/data-erd/relationships';
                let method = 'POST';
                if (self.rel.id) { url += '/' + self.rel.id; method = 'PUT'; }
                else { body.source_id = self.rel.source_id; body.target_id = self.rel.target_id; }
                let response = await window.fetch(url, { // raw-fetch-ok: the person needs the server's own message beside the form, which Platform.fetch would show only as a toast
                    method: method,
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() },
                    body: JSON.stringify(body),
                });
                let data = await response.json();
                if (!response.ok) throw new Error(data.error || 'The relationship could not be saved.');
                let at = self.relationships.findIndex(function(r) { return r.id === data.id; });
                if (at === -1) self.relationships.push(data); else self.relationships.splice(at, 1, data);
                self.relOpen = false;
                self.draw();
                self.select('relationship', data.id);
                self.say('Saved the relationship between ' + self.rel.sourceName + ' and ' + self.rel.targetName + '.');
            } catch (err) {
                self.relError = err.message;
            } finally {
                self.busy = false;
            }
        },

        /* ── deleting ───────────────────────────────────── */
        deleteSelected: function() {
            let self = this;
            if (!self.selected) return;
            let sel = self.selected;
            window.dispatchEvent(new CustomEvent('open-confirm-erd-delete', { detail: { callback: function() { self.confirmDelete(sel); } } }));
        },
        async confirmDelete(sel) {
            let self = this;
            let url = sel.kind === 'entity' ? '/architecture/api/data-erd/entities/' + sel.id
                                            : '/architecture/api/data-erd/relationships/' + sel.id;
            try {
                let response = await window.fetch(url, { method: 'DELETE', headers: { 'X-CSRFToken': csrf() } }); // raw-fetch-ok: DELETE with the CSRF header; the answer is checked on the next line
                if (!response.ok) throw new Error('It could not be deleted.');
                if (sel.kind === 'entity') {
                    self.entities = self.entities.filter(function(e) { return e.id !== sel.id; });
                    self.relationships = self.relationships.filter(function(r) { return r.source_id !== sel.id && r.target_id !== sel.id; });
                } else {
                    self.relationships = self.relationships.filter(function(r) { return r.id !== sel.id; });
                }
                self.selected = null;
                self.draw();
                self.say('Deleted.');
            } catch (err) {
                self.say(err.message);
                if (window.Platform && Platform.toast) Platform.toast.error(err.message);
            }
        },

        /* ── export ─────────────────────────────────────── */
        exportSvg: function() {
            let svg = this.paper.svg.cloneNode(true);
            let box = this.paper.getContentBBox();
            svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
            svg.setAttribute('viewBox', [box.x - 20, box.y - 20, box.width + 40, box.height + 40].join(' '));
            svg.setAttribute('width', box.width + 40);
            svg.setAttribute('height', box.height + 40);
            let blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' });
            let a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = (CFG.domainName || 'data-model').replace(/[^a-z0-9]+/gi, '-').toLowerCase() + '-erd.svg';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(a.href);
        },
    };
}
