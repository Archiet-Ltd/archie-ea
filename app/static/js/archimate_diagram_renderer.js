/**
 * ArchiMate 3.2 Visual Diagram Renderer
 *
 * A read-only SVG renderer for ArchiMate diagrams using D3.js v7.
 * Renders elements with correct ArchiMate 3.2 notation (shapes, icons, colors)
 * and relationships with proper line styles and arrowheads.
 *
 * Requires: D3.js v7 loaded globally (window.d3)
 *
 * Supported viewpoints:
 *   - organization: Business layer elements
 *   - application_cooperation: App components, services, data
 *   - technology: Nodes, devices, system software
 *   - layered: Cross-layer, elements stacked by layer
 *   - motivation: Goals, principles, requirements
 *   - implementation_migration: Work packages, deliverables, plateaus
 *
 * @example
 *   const renderer = new ArchiMateDiagramRenderer('#diagram-container');
 *   renderer.render({
 *       viewpoint: 'application_cooperation',
 *       elements: [
 *           { id: '1', name: 'CRM', type: 'ApplicationComponent', layer: 'application', x: 100, y: 100 }
 *       ],
 *       relationships: [
 *           { source: '1', target: '2', type: 'flow', label: 'Data' }
 *       ]
 *   });
 */
class ArchiMateDiagramRenderer {

    // ── ArchiMate layer colors ────────
    static LAYER_COLORS = {
        business:       '#FFB74D',
        application:    '#4FC3F7',
        technology:     '#81C784',
        physical:       '#A1887F',
        motivation:     '#9575CD',
        strategy:       '#F06292',
        implementation: '#64B5F6'
    };

    // Darker border variants for each layer
    static LAYER_BORDER_COLORS = {
        business:       '#F57C00',
        application:    '#0288D1',
        technology:     '#388E3C',
        physical:       '#6D4C41',
        motivation:     '#5E35B1',
        strategy:       '#D81B60',
        implementation: '#1976D2'
    };

    // ── Element dimensions ────────────────────────────────────────────────
    static ELEM_W = 160;
    static ELEM_H = 60;
    static ICON_SIZE = 16;
    static CORNER_RADIUS = 8;

    // ── Viewpoint metadata ────────────────────────────────────────────────
    static VIEWPOINTS = {
        organization:            { title: 'Organization Viewpoint',              layers: ['business'] },
        application_cooperation: { title: 'Application Cooperation Viewpoint',   layers: ['application'] },
        technology:              { title: 'Technology Viewpoint',                 layers: ['technology', 'physical'] },
        layered:                 { title: 'Layered Viewpoint',                   layers: ['business', 'application', 'technology'] },
        motivation:              { title: 'Motivation Viewpoint',                layers: ['motivation'] },
        implementation_migration:{ title: 'Implementation & Migration Viewpoint',layers: ['implementation'] }
    };

    // ── Relationship style config ─────────────────────────────────────────
    static REL_STYLES = {
        composition:  { dash: null,    color: '#555', markerEnd: 'diamond-filled' },
        aggregation:  { dash: null,    color: '#555', markerEnd: 'diamond-open' },
        assignment:   { dash: null,    color: '#555', markerEnd: 'circle-arrow' },
        realization:  { dash: '6,4',   color: '#555', markerEnd: 'triangle-open' },
        serving:      { dash: null,    color: '#555', markerEnd: 'arrow-open' },
        flow:         { dash: '6,4',   color: '#555', markerEnd: 'arrow-filled' },
        triggering:   { dash: null,    color: '#555', markerEnd: 'arrow-filled' },
        association:  { dash: null,    color: '#888', markerEnd: 'none' },
        access:       { dash: '4,3',   color: '#555', markerEnd: 'arrow-open' },
        influence:    { dash: '6,4',   color: '#555', markerEnd: 'triangle-open' }
    };

    // ── Shape classification ──────────────────────────────────────────────
    // Maps ArchiMate element type to a shape category used for rendering
    static SHAPE_MAP = {
        // Business layer
        BusinessProcess:    'rounded-arrow',
        BusinessFunction:   'rounded-arrow',
        BusinessService:    'rounded-arrow',
        BusinessInteraction:'rounded-arrow',
        BusinessEvent:      'rounded-arrow',
        BusinessActor:      'person-rect',
        BusinessRole:       'person-rect',
        BusinessCollaboration: 'person-rect',
        BusinessInterface:  'rounded',
        BusinessObject:     'folded-corner',
        Contract:           'folded-corner',
        Representation:     'folded-corner',
        Product:            'rounded',
        // Application layer
        ApplicationComponent: 'component-rect',
        ApplicationService:   'component-rect',
        ApplicationFunction:  'component-rect',
        ApplicationProcess:   'component-rect',
        ApplicationInteraction:'component-rect',
        ApplicationInterface: 'rounded',
        ApplicationEvent:     'rounded',
        ApplicationCollaboration: 'rounded',
        DataObject:           'folded-corner',
        Artifact:             'folded-corner',
        // Technology layer
        Node:                 'box3d',
        Device:               'box3d',
        SystemSoftware:       'box3d',
        TechnologyService:    'rounded',
        TechnologyFunction:   'rounded',
        TechnologyProcess:    'rounded',
        TechnologyInterface:  'rounded',
        TechnologyInteraction:'rounded',
        TechnologyCollaboration:'rounded',
        TechnologyEvent:      'rounded',
        Path:                 'rounded',
        CommunicationNetwork: 'rounded',
        // Physical
        Equipment:            'box3d',
        Facility:             'box3d',
        DistributionNetwork:  'rounded',
        Material:             'folded-corner',
        // Motivation
        Goal:                 'ellipse',
        Principle:            'ellipse',
        Requirement:          'ellipse',
        Constraint:           'ellipse',
        Driver:               'ellipse',
        Assessment:           'ellipse',
        Stakeholder:          'person-rect',
        Value:                'ellipse',
        Meaning:              'ellipse',
        Outcome:              'ellipse',
        // Strategy
        Resource:             'rounded',
        Capability:           'rounded',
        ValueStream:          'rounded',
        CourseOfAction:       'rounded',
        // Implementation
        WorkPackage:          'rounded',
        Deliverable:          'folded-corner',
        Plateau:              'box3d',
        Gap:                  'ellipse',
        ImplementationEvent:  'rounded'
    };

    /* ====================================================================
     *  Constructor
     * ==================================================================== */

    /**
     * Create a new ArchiMate diagram renderer.
     * @param {string} containerSelector - CSS selector for the target container element
     * @param {Object} [options] - Optional configuration overrides
     * @param {number} [options.width]  - Fixed width (defaults to container width)
     * @param {number} [options.height] - Fixed height (defaults to container height)
     * @param {boolean} [options.legend] - Show legend (default true)
     * @param {boolean} [options.autoLayout] - Use force-directed auto-layout (default false)
     */
    constructor(containerSelector, options = {}) {
        this._containerSel = containerSelector;
        this._container = document.querySelector(containerSelector);
        if (!this._container) {
            throw new Error(`ArchiMateDiagramRenderer: container "${containerSelector}" not found`);
        }
        this._options = Object.assign({
            legend: true,
            autoLayout: false
        }, options);
        this._svg = null;
        this._rootGroup = null;
        this._zoomBehavior = null;
        this._data = null;
        this._elemMap = new Map();
        this._simulation = null;
    }

    /* ====================================================================
     *  Public API
     * ==================================================================== */

    /**
     * Render an ArchiMate diagram from a data object.
     * @param {Object} data
     * @param {string}  data.viewpoint - One of the six supported viewpoint codes
     * @param {Array}   data.elements  - Array of element objects
     * @param {string}  data.elements[].id
     * @param {string}  data.elements[].name
     * @param {string}  data.elements[].type   - ArchiMate element type (e.g. ApplicationComponent)
     * @param {string}  data.elements[].layer  - ArchiMate layer key
     * @param {number}  [data.elements[].x]    - X position (optional if autoLayout)
     * @param {number}  [data.elements[].y]    - Y position (optional if autoLayout)
     * @param {number}  [data.elements[].w]    - Custom width
     * @param {number}  [data.elements[].h]    - Custom height
     * @param {Array}   data.relationships - Array of relationship objects
     * @param {string}  data.relationships[].source - Source element ID
     * @param {string}  data.relationships[].target - Target element ID
     * @param {string}  data.relationships[].type   - Relationship type key
     * @param {string}  [data.relationships[].label] - Optional label
     */
    render(data) {
        this._data = data;
        this._elemMap.clear();
        data.elements.forEach(el => this._elemMap.set(el.id, el));

        // Tear down previous render
        if (this._simulation) { this._simulation.stop(); this._simulation = null; }
        d3.select(this._containerSel).select('svg').remove();

        // Measure container
        const rect = this._container.getBoundingClientRect();
        this._width  = this._options.width  || rect.width  || 960;
        this._height = this._options.height || rect.height || 600;

        this._initSVG();
        this._defineMarkers();

        if (this._options.autoLayout || this._needsAutoLayout(data.elements)) {
            this._runAutoLayout(data);
        }

        this._renderRelationships(data.relationships || []);
        this._renderElements(data.elements);
        this._renderRelationshipLabels(data.relationships || []);

        if (this._options.legend) {
            this._renderLegend(data);
        }

        this._renderTitle(data.viewpoint);
        this.fitToContent();
    }

    /**
     * Export the current SVG as a serialized string (suitable for PDF conversion).
     * @returns {string} The SVG markup
     */
    exportSVG() {
        if (!this._svg) return '';
        const svgNode = this._svg.node();
        // Inline styles for export
        const clone = svgNode.cloneNode(true);
        clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
        return new XMLSerializer().serializeToString(clone);
    }

    /** Zoom in by 30% */
    zoomIn() {
        if (this._svg && this._zoomBehavior) {
            this._svg.transition().duration(300)
                .call(this._zoomBehavior.scaleBy, 1.3);
        }
    }

    /** Zoom out by 30% */
    zoomOut() {
        if (this._svg && this._zoomBehavior) {
            this._svg.transition().duration(300)
                .call(this._zoomBehavior.scaleBy, 0.7);
        }
    }

    /** Fit diagram content inside the viewport */
    fitToContent() {
        if (!this._svg || !this._rootGroup) return;
        const bounds = this._rootGroup.node().getBBox();
        if (bounds.width === 0 || bounds.height === 0) return;

        const padding = 40;
        const fullW = bounds.width  + padding * 2;
        const fullH = bounds.height + padding * 2;
        const scale = Math.min(this._width / fullW, this._height / fullH, 1.5);
        const tx = (this._width  - bounds.width  * scale) / 2 - bounds.x * scale;
        const ty = (this._height - bounds.height * scale) / 2 - bounds.y * scale;

        this._svg.transition().duration(400)
            .call(this._zoomBehavior.transform,
                  d3.zoomIdentity.translate(tx, ty).scale(scale));
    }

    /** Remove the renderer and clean up */
    destroy() {
        if (this._simulation) { this._simulation.stop(); }
        d3.select(this._containerSel).select('svg').remove();
        this._svg = null;
        this._rootGroup = null;
    }

    /* ====================================================================
     *  SVG Bootstrap
     * ==================================================================== */

    /** @private */
    _initSVG() {
        this._svg = d3.select(this._containerSel)
            .append('svg')
            .attr('width', this._width)
            .attr('height', this._height)
            .attr('class', 'archimate-diagram')
            .style('background', '#fafafa')
            .style('font-family', "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif");

        this._zoomBehavior = d3.zoom()
            .scaleExtent([0.1, 5])
            .on('zoom', (event) => {
                this._rootGroup.attr('transform', event.transform);
            });

        this._svg.call(this._zoomBehavior);
        this._rootGroup = this._svg.append('g').attr('class', 'diagram-root');

        // Tooltip container
        this._tooltip = d3.select(this._containerSel)
            .append('div')
            .attr('class', 'archimate-tooltip')
            .style('position', 'absolute')
            .style('pointer-events', 'none')
            .style('background', 'rgba(15,23,42,0.92)')
            .style('color', '#f8fafc')
            .style('padding', '8px 12px')
            .style('border-radius', '6px')
            .style('font-size', '12px')
            .style('line-height', '1.4')
            .style('max-width', '260px')
            .style('box-shadow', '0 4px 12px rgba(0,0,0,0.25)')
            .style('opacity', 0)
            .style('z-index', 1000)
            .style('transition', 'opacity 0.15s');
    }

    /* ====================================================================
     *  Marker definitions (arrowheads / decorators)
     * ==================================================================== */

    /** @private */
    _defineMarkers() {
        const defs = this._svg.append('defs');

        // ── Filled arrow (flow, triggering) ──────────────────────────
        defs.append('marker')
            .attr('id', 'marker-arrow-filled')
            .attr('viewBox', '0 -5 10 10')
            .attr('refX', 10).attr('refY', 0)
            .attr('markerWidth', 8).attr('markerHeight', 8)
            .attr('orient', 'auto')
            .append('path').attr('d', 'M0,-4L10,0L0,4Z').attr('fill', '#555');

        // ── Open arrow (serving, access) ─────────────────────────────
        defs.append('marker')
            .attr('id', 'marker-arrow-open')
            .attr('viewBox', '0 -5 10 10')
            .attr('refX', 10).attr('refY', 0)
            .attr('markerWidth', 8).attr('markerHeight', 8)
            .attr('orient', 'auto')
            .append('path').attr('d', 'M0,-4L10,0L0,4')
                .attr('fill', 'none').attr('stroke', '#555').attr('stroke-width', 1.5);

        // ── Open triangle (realization, influence) ───────────────────
        defs.append('marker')
            .attr('id', 'marker-triangle-open')
            .attr('viewBox', '0 -6 12 12')
            .attr('refX', 12).attr('refY', 0)
            .attr('markerWidth', 10).attr('markerHeight', 10)
            .attr('orient', 'auto')
            .append('path').attr('d', 'M0,-5L12,0L0,5Z')
                .attr('fill', '#fff').attr('stroke', '#555').attr('stroke-width', 1.2);

        // ── Filled diamond (composition) ─────────────────────────────
        defs.append('marker')
            .attr('id', 'marker-diamond-filled')
            .attr('viewBox', '0 -6 12 12')
            .attr('refX', 0).attr('refY', 0)
            .attr('markerWidth', 10).attr('markerHeight', 10)
            .attr('orient', 'auto')
            .append('path').attr('d', 'M0,0L6,-5L12,0L6,5Z').attr('fill', '#555');

        // ── Open diamond (aggregation) ───────────────────────────────
        defs.append('marker')
            .attr('id', 'marker-diamond-open')
            .attr('viewBox', '0 -6 12 12')
            .attr('refX', 0).attr('refY', 0)
            .attr('markerWidth', 10).attr('markerHeight', 10)
            .attr('orient', 'auto')
            .append('path').attr('d', 'M0,0L6,-5L12,0L6,5Z')
                .attr('fill', '#fff').attr('stroke', '#555').attr('stroke-width', 1.2);

        // ── Circle + arrow (assignment) ──────────────────────────────
        const assignG = defs.append('marker')
            .attr('id', 'marker-circle-arrow')
            .attr('viewBox', '-2 -7 18 14')
            .attr('refX', 16).attr('refY', 0)
            .attr('markerWidth', 14).attr('markerHeight', 14)
            .attr('orient', 'auto');
        assignG.append('circle')
            .attr('cx', 3).attr('cy', 0).attr('r', 3).attr('fill', '#555');
        assignG.append('path')
            .attr('d', 'M7,-4L15,0L7,4Z').attr('fill', '#555');

        // ── Drop shadow filter ───────────────────────────────────────
        const filter = defs.append('filter')
            .attr('id', 'shadow-sm')
            .attr('x', '-10%').attr('y', '-10%')
            .attr('width', '130%').attr('height', '140%');
        filter.append('feDropShadow')
            .attr('dx', 0).attr('dy', 1)
            .attr('stdDeviation', 2)
            .attr('flood-color', 'rgba(0,0,0,0.12)');
    }

    /* ====================================================================
     *  Element rendering
     * ==================================================================== */

    /** @private */
    _renderElements(elements) {
        const group = this._rootGroup.append('g').attr('class', 'elements');
        const self = this;

        const elGroups = group.selectAll('g.element')
            .data(elements, d => d.id)
            .enter()
            .append('g')
            .attr('class', 'element')
            .attr('transform', d => `translate(${d.x || 0},${d.y || 0})`);

        elGroups.each(function (d) {
            const g = d3.select(this);
            const w = d.w || ArchiMateDiagramRenderer.ELEM_W;
            const h = d.h || ArchiMateDiagramRenderer.ELEM_H;
            const shape = ArchiMateDiagramRenderer.SHAPE_MAP[d.type] || 'rounded';
            const fill = ArchiMateDiagramRenderer.LAYER_COLORS[d.layer] || '#E0E0E0';
            const stroke = ArchiMateDiagramRenderer.LAYER_BORDER_COLORS[d.layer] || '#999';

            // Draw shape
            self._drawShape(g, shape, w, h, fill, stroke);

            // Draw icon
            self._drawIcon(g, shape, d.type, w, stroke);

            // Label
            g.append('text')
                .attr('x', w / 2)
                .attr('y', h / 2 + 4)
                .attr('text-anchor', 'middle')
                .attr('dominant-baseline', 'central')
                .attr('font-size', '11px')
                .attr('font-weight', 500)
                .attr('fill', '#1e293b')
                .text(d.name)
                .each(function () { self._wrapText(this, w - 24); });
        });

        // Tooltip interaction
        elGroups
            .on('mouseenter', (event, d) => {
                const vp = ArchiMateDiagramRenderer.VIEWPOINTS[self._data.viewpoint];
                self._tooltip
                    .html(
                        `<strong>${self._escapeHtml(d.name)}</strong><br/>` +
                        `<span style="opacity:0.7">Type:</span> ${d.type}<br/>` +
                        `<span style="opacity:0.7">Layer:</span> ${d.layer}<br/>` +
                        (vp ? `<span style="opacity:0.7">Viewpoint:</span> ${vp.title}` : '')
                    )
                    .style('opacity', 1);
            })
            .on('mousemove', (event) => {
                const containerRect = self._container.getBoundingClientRect();
                self._tooltip
                    .style('left', (event.clientX - containerRect.left + 14) + 'px')
                    .style('top',  (event.clientY - containerRect.top  - 10) + 'px');
            })
            .on('mouseleave', () => {
                self._tooltip.style('opacity', 0);
            });
    }

    /**
     * Draw the correct ArchiMate shape for an element.
     * @private
     */
    _drawShape(g, shape, w, h, fill, stroke) {
        const r = ArchiMateDiagramRenderer.CORNER_RADIUS;

        switch (shape) {
            case 'rounded':
            case 'rounded-arrow':
            case 'component-rect':
            case 'person-rect':
                g.append('rect')
                    .attr('width', w).attr('height', h)
                    .attr('rx', r).attr('ry', r)
                    .attr('fill', fill).attr('stroke', stroke)
                    .attr('stroke-width', 1.5)
                    .style('filter', 'url(#shadow-sm)');
                break;

            case 'folded-corner': {
                const fold = 12;
                g.append('path')
                    .attr('d', `M0,0 L${w - fold},0 L${w},${fold} L${w},${h} L0,${h} Z`)
                    .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', 1.5)
                    .style('filter', 'url(#shadow-sm)');
                // Folded corner triangle
                g.append('path')
                    .attr('d', `M${w - fold},0 L${w - fold},${fold} L${w},${fold}`)
                    .attr('fill', 'none').attr('stroke', stroke).attr('stroke-width', 1);
                break;
            }

            case 'box3d': {
                const depth = 10;
                // Front face
                g.append('rect')
                    .attr('x', 0).attr('y', depth)
                    .attr('width', w).attr('height', h - depth)
                    .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', 1.5)
                    .style('filter', 'url(#shadow-sm)');
                // Top face (parallelogram)
                g.append('path')
                    .attr('d', `M0,${depth} L${depth},0 L${w + depth},0 L${w},${depth} Z`)
                    .attr('fill', d3.color(fill).brighter(0.3))
                    .attr('stroke', stroke).attr('stroke-width', 1.2);
                // Right face
                g.append('path')
                    .attr('d', `M${w},${depth} L${w + depth},0 L${w + depth},${h - depth} L${w},${h} Z`)
                    .attr('fill', d3.color(fill).darker(0.2))
                    .attr('stroke', stroke).attr('stroke-width', 1.2);
                break;
            }

            case 'ellipse':
                g.append('ellipse')
                    .attr('cx', w / 2).attr('cy', h / 2)
                    .attr('rx', w / 2).attr('ry', h / 2)
                    .attr('fill', fill).attr('stroke', stroke).attr('stroke-width', 1.5)
                    .style('filter', 'url(#shadow-sm)');
                break;

            default:
                g.append('rect')
                    .attr('width', w).attr('height', h)
                    .attr('rx', r).attr('ry', r)
                    .attr('fill', fill).attr('stroke', stroke)
                    .attr('stroke-width', 1.5)
                    .style('filter', 'url(#shadow-sm)');
        }
    }

    /**
     * Draw the small ArchiMate notation icon in the top-right corner.
     * @private
     */
    _drawIcon(g, shape, type, w, stroke) {
        const s = ArchiMateDiagramRenderer.ICON_SIZE;
        const ix = w - s - 6;
        const iy = 5;

        switch (shape) {
            case 'rounded-arrow':
                // Small arrow icon (business process/function)
                g.append('path')
                    .attr('d', `M${ix},${iy + s * 0.3} L${ix + s * 0.65},${iy + s * 0.3} L${ix + s * 0.65},${iy} L${ix + s},${iy + s / 2} L${ix + s * 0.65},${iy + s} L${ix + s * 0.65},${iy + s * 0.7} L${ix},${iy + s * 0.7} Z`)
                    .attr('fill', 'none').attr('stroke', stroke).attr('stroke-width', 1.2);
                break;

            case 'component-rect': {
                // Component icon: small square with two horizontal bars
                const cx = ix + 2;
                const cy = iy + 2;
                const cw = s - 4;
                const ch = s - 4;
                g.append('rect')
                    .attr('x', cx + 3).attr('y', cy)
                    .attr('width', cw - 3).attr('height', ch)
                    .attr('fill', 'none').attr('stroke', stroke).attr('stroke-width', 1.1);
                // Two small protruding bars
                g.append('rect')
                    .attr('x', cx).attr('y', cy + 2)
                    .attr('width', 6).attr('height', 3)
                    .attr('fill', 'none').attr('stroke', stroke).attr('stroke-width', 1);
                g.append('rect')
                    .attr('x', cx).attr('y', cy + ch - 5)
                    .attr('width', 6).attr('height', 3)
                    .attr('fill', 'none').attr('stroke', stroke).attr('stroke-width', 1);
                break;
            }

            case 'person-rect': {
                // Stick figure / person icon
                const px = ix + s / 2;
                const py = iy;
                // Head
                g.append('circle')
                    .attr('cx', px).attr('cy', py + 3)
                    .attr('r', 3)
                    .attr('fill', 'none').attr('stroke', stroke).attr('stroke-width', 1.1);
                // Body
                g.append('line')
                    .attr('x1', px).attr('y1', py + 6)
                    .attr('x2', px).attr('y2', py + 12)
                    .attr('stroke', stroke).attr('stroke-width', 1.1);
                // Arms
                g.append('line')
                    .attr('x1', px - 4).attr('y1', py + 8)
                    .attr('x2', px + 4).attr('y2', py + 8)
                    .attr('stroke', stroke).attr('stroke-width', 1.1);
                // Legs
                g.append('line')
                    .attr('x1', px).attr('y1', py + 12)
                    .attr('x2', px - 3).attr('y2', py + s)
                    .attr('stroke', stroke).attr('stroke-width', 1.1);
                g.append('line')
                    .attr('x1', px).attr('y1', py + 12)
                    .attr('x2', px + 3).attr('y2', py + s)
                    .attr('stroke', stroke).attr('stroke-width', 1.1);
                break;
            }

            case 'folded-corner':
                // No additional icon needed (the fold IS the notation)
                break;

            case 'box3d':
                // No additional icon needed (3D shape IS the notation)
                break;

            case 'ellipse':
                // For motivation elements, draw a small distinguishing mark
                if (type === 'Goal' || type === 'Outcome') {
                    // Target/bullseye icon
                    const ecx = ix + s / 2;
                    const ecy = iy + s / 2;
                    g.append('circle')
                        .attr('cx', ecx).attr('cy', ecy).attr('r', s / 2 - 1)
                        .attr('fill', 'none').attr('stroke', stroke).attr('stroke-width', 1);
                    g.append('circle')
                        .attr('cx', ecx).attr('cy', ecy).attr('r', 2)
                        .attr('fill', stroke);
                } else if (type === 'Principle') {
                    // Small "P" text
                    g.append('text')
                        .attr('x', ix + s / 2).attr('y', iy + s / 2 + 1)
                        .attr('text-anchor', 'middle')
                        .attr('dominant-baseline', 'central')
                        .attr('font-size', '10px')
                        .attr('font-weight', 700)
                        .attr('fill', stroke)
                        .text('P');
                } else if (type === 'Requirement' || type === 'Constraint') {
                    // Exclamation mark
                    g.append('text')
                        .attr('x', ix + s / 2).attr('y', iy + s / 2 + 1)
                        .attr('text-anchor', 'middle')
                        .attr('dominant-baseline', 'central')
                        .attr('font-size', '11px')
                        .attr('font-weight', 700)
                        .attr('fill', stroke)
                        .text('!');
                }
                break;
        }
    }

    /* ====================================================================
     *  Relationship rendering
     * ==================================================================== */

    /** @private */
    _renderRelationships(relationships) {
        const group = this._rootGroup.append('g').attr('class', 'relationships');
        const self = this;

        group.selectAll('path.rel-line')
            .data(relationships)
            .enter()
            .append('path')
            .attr('class', 'rel-line')
            .attr('d', d => self._calcRelPath(d))
            .attr('fill', 'none')
            .attr('stroke', d => (ArchiMateDiagramRenderer.REL_STYLES[d.type] || {}).color || '#888')
            .attr('stroke-width', 1.5)
            .attr('stroke-dasharray', d => (ArchiMateDiagramRenderer.REL_STYLES[d.type] || {}).dash || null)
            .attr('marker-end', d => {
                const style = ArchiMateDiagramRenderer.REL_STYLES[d.type];
                if (!style || style.markerEnd === 'none') return null;
                // Composition/aggregation: diamond is at the source, no marker at the target
                if (style.markerEnd === 'diamond-filled' || style.markerEnd === 'diamond-open') return null;
                return `url(#marker-${style.markerEnd})`;
            })
            .attr('marker-start', d => {
                const style = ArchiMateDiagramRenderer.REL_STYLES[d.type];
                if (!style) return null;
                // Composition and aggregation have the diamond at the SOURCE end
                if (style.markerEnd === 'diamond-filled') return 'url(#marker-diamond-filled)';
                if (style.markerEnd === 'diamond-open') return 'url(#marker-diamond-open)';
                return null;
            });
    }

    /** @private */
    _renderRelationshipLabels(relationships) {
        const group = this._rootGroup.append('g').attr('class', 'rel-labels');
        const self = this;

        const labeled = relationships.filter(r => r.label);
        group.selectAll('text.rel-label')
            .data(labeled)
            .enter()
            .append('text')
            .attr('class', 'rel-label')
            .attr('font-size', '10px')
            .attr('fill', '#64748b')
            .attr('text-anchor', 'middle')
            .attr('dominant-baseline', 'central')
            .each(function (d) {
                const mid = self._calcRelMidpoint(d);
                d3.select(this)
                    .attr('x', mid.x)
                    .attr('y', mid.y - 8);
            })
            .text(d => d.label);

        // White background behind labels for readability
        group.selectAll('text.rel-label').each(function () {
            const bbox = this.getBBox();
            const parent = this.parentNode;
            const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            bg.setAttribute('x', bbox.x - 3);
            bg.setAttribute('y', bbox.y - 1);
            bg.setAttribute('width', bbox.width + 6);
            bg.setAttribute('height', bbox.height + 2);
            bg.setAttribute('fill', '#fafafa');
            bg.setAttribute('rx', '3');
            parent.insertBefore(bg, this);
        });
    }

    /**
     * Calculate path between two connected elements.
     * Routes from the edge of the source element to the edge of the target.
     * @private
     */
    _calcRelPath(rel) {
        const src = this._elemMap.get(rel.source);
        const tgt = this._elemMap.get(rel.target);
        if (!src || !tgt) return 'M0,0';

        const sw = src.w || ArchiMateDiagramRenderer.ELEM_W;
        const sh = src.h || ArchiMateDiagramRenderer.ELEM_H;
        const tw = tgt.w || ArchiMateDiagramRenderer.ELEM_W;
        const th = tgt.h || ArchiMateDiagramRenderer.ELEM_H;

        const srcCx = (src.x || 0) + sw / 2;
        const srcCy = (src.y || 0) + sh / 2;
        const tgtCx = (tgt.x || 0) + tw / 2;
        const tgtCy = (tgt.y || 0) + th / 2;

        // Calculate intersection with source element boundary
        const srcPt = this._rectEdgePoint(srcCx, srcCy, sw, sh, tgtCx, tgtCy);
        const tgtPt = this._rectEdgePoint(tgtCx, tgtCy, tw, th, srcCx, srcCy);

        return `M${srcPt.x},${srcPt.y} L${tgtPt.x},${tgtPt.y}`;
    }

    /** @private */
    _calcRelMidpoint(rel) {
        const src = this._elemMap.get(rel.source);
        const tgt = this._elemMap.get(rel.target);
        if (!src || !tgt) return { x: 0, y: 0 };

        const sw = src.w || ArchiMateDiagramRenderer.ELEM_W;
        const sh = src.h || ArchiMateDiagramRenderer.ELEM_H;
        const tw = tgt.w || ArchiMateDiagramRenderer.ELEM_W;
        const th = tgt.h || ArchiMateDiagramRenderer.ELEM_H;

        return {
            x: ((src.x || 0) + sw / 2 + (tgt.x || 0) + tw / 2) / 2,
            y: ((src.y || 0) + sh / 2 + (tgt.y || 0) + th / 2) / 2
        };
    }

    /**
     * Find the point on the edge of a rectangle (cx,cy,w,h) in the direction of (px,py).
     * @private
     */
    _rectEdgePoint(cx, cy, w, h, px, py) {
        const dx = px - cx;
        const dy = py - cy;
        if (dx === 0 && dy === 0) return { x: cx, y: cy };

        const absDx = Math.abs(dx);
        const absDy = Math.abs(dy);
        const hw = w / 2;
        const hh = h / 2;

        let t;
        if (absDx * hh > absDy * hw) {
            // Intersects left or right edge
            t = hw / absDx;
        } else {
            // Intersects top or bottom edge
            t = hh / absDy;
        }
        return { x: cx + dx * t, y: cy + dy * t };
    }

    /* ====================================================================
     *  Auto-layout (D3 force simulation)
     * ==================================================================== */

    /** @private */
    _needsAutoLayout(elements) {
        return elements.some(el => el.x == null || el.y == null);
    }

    /** @private */
    _runAutoLayout(data) {
        const w = this._width;
        const h = this._height;

        // Assign starting positions for elements lacking coordinates
        data.elements.forEach((el, i) => {
            if (el.x == null) el.x = (w / 2) + (Math.cos(i * 0.7) * 200);
            if (el.y == null) el.y = (h / 2) + (Math.sin(i * 0.7) * 150);
        });

        // Build link array with references
        const links = (data.relationships || []).map(r => ({
            source: r.source,
            target: r.target
        }));

        // Run synchronous force ticks
        const sim = d3.forceSimulation(data.elements)
            .force('link', d3.forceLink(links).id(d => d.id).distance(220))
            .force('charge', d3.forceManyBody().strength(-400))
            .force('center', d3.forceCenter(w / 2, h / 2))
            .force('collision', d3.forceCollide()
                .radius(d => Math.max(d.w || ArchiMateDiagramRenderer.ELEM_W, d.h || ArchiMateDiagramRenderer.ELEM_H) * 0.7))
            .stop();

        // Layer separation for layered viewpoint
        if (data.viewpoint === 'layered') {
            const layerOrder = { business: 0, application: 1, technology: 2, physical: 3 };
            sim.force('layerY', d3.forceY(d => {
                const rank = layerOrder[d.layer] != null ? layerOrder[d.layer] : 2;
                return 100 + rank * 180;
            }).strength(0.6));
        }

        // Run 200 ticks
        for (let i = 0; i < 200; i++) sim.tick();

        // Copy simulation positions back to element data
        data.elements.forEach(el => {
            el.x = Math.round(el.x);
            el.y = Math.round(el.y);
        });
    }

    /* ====================================================================
     *  Legend
     * ==================================================================== */

    /** @private */
    _renderLegend(data) {
        // Determine which layers are present
        const layers = [...new Set(data.elements.map(el => el.layer))];
        if (layers.length === 0) return;

        const legendG = this._svg.append('g')
            .attr('class', 'legend')
            .attr('transform', `translate(12, ${this._height - layers.length * 22 - 30})`);

        legendG.append('rect')
            .attr('x', -6).attr('y', -6)
            .attr('width', 140)
            .attr('height', layers.length * 22 + 20)
            .attr('rx', 6).attr('fill', 'rgba(255,255,255,0.92)')
            .attr('stroke', '#e2e8f0').attr('stroke-width', 1);

        legendG.append('text')
            .attr('x', 0).attr('y', 8)
            .attr('font-size', '10px').attr('font-weight', 600).attr('fill', '#475569')
            .text('Layers');

        layers.forEach((layer, i) => {
            const row = legendG.append('g')
                .attr('transform', `translate(0, ${20 + i * 22})`);

            row.append('rect')
                .attr('width', 14).attr('height', 14).attr('rx', 3)
                .attr('fill', ArchiMateDiagramRenderer.LAYER_COLORS[layer] || '#E0E0E0')
                .attr('stroke', ArchiMateDiagramRenderer.LAYER_BORDER_COLORS[layer] || '#999')
                .attr('stroke-width', 1);

            row.append('text')
                .attr('x', 20).attr('y', 11)
                .attr('font-size', '11px').attr('fill', '#334155')
                .text(layer.charAt(0).toUpperCase() + layer.slice(1));
        });
    }

    /* ====================================================================
     *  Title bar
     * ==================================================================== */

    /** @private */
    _renderTitle(viewpoint) {
        const vp = ArchiMateDiagramRenderer.VIEWPOINTS[viewpoint];
        if (!vp) return;

        this._svg.append('text')
            .attr('x', this._width / 2)
            .attr('y', 22)
            .attr('text-anchor', 'middle')
            .attr('font-size', '14px')
            .attr('font-weight', 600)
            .attr('fill', '#1e293b')
            .text(vp.title);
    }

    /* ====================================================================
     *  Text wrapping helper
     * ==================================================================== */

    /** @private */
    _wrapText(textNode, maxWidth) {
        const text = d3.select(textNode);
        const words = text.text().split(/\s+/);
        if (words.length <= 1) return;

        const x = text.attr('x');
        const y = parseFloat(text.attr('y'));
        const lineHeight = 13;
        let line = [];
        let lineNum = 0;

        text.text(null);

        // Temporary span for measuring
        let tspan = text.append('tspan')
            .attr('x', x)
            .attr('dy', 0)
            .attr('text-anchor', 'middle');

        for (const word of words) {
            line.push(word);
            tspan.text(line.join(' '));
            if (tspan.node().getComputedTextLength() > maxWidth && line.length > 1) {
                line.pop();
                tspan.text(line.join(' '));
                line = [word];
                lineNum++;
                tspan = text.append('tspan')
                    .attr('x', x)
                    .attr('dy', lineHeight)
                    .attr('text-anchor', 'middle')
                    .text(word);
            }
        }

        // Vertically center the wrapped text
        const totalLines = lineNum + 1;
        if (totalLines > 1) {
            const offset = -((totalLines - 1) * lineHeight) / 2;
            text.selectAll('tspan').each(function (_, i) {
                if (i === 0) {
                    d3.select(this).attr('dy', offset);
                }
            });
        }
    }

    /* ====================================================================
     *  Utility
     * ==================================================================== */

    /** @private */
    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}
