/**
 * ComposerRenderer — Reusable ArchiMate diagram rendering engine
 * Extracted from composer.js for use across Solution Detail, Architecture Assistant, AI Chat.
 *
 * Usage:
 *   var renderer = ComposerRenderer.create(containerEl, { mode: 'view', width: '100%', height: 400 });
 *   renderer.loadElements(elements, relationships);
 *   renderer.fitToContent();
 *   renderer.destroy();
 *
 * A read-only banded view (the Twin map draws with this):
 *   var renderer = ComposerRenderer.create(containerEl, { mode: 'view', theme: 'tokens' });
 *   renderer.drawBands({ width, bands, elements, relationships, classes, buttons });
 *   renderer.select(elementId, pathKeys);
 *   renderer.focus(elementId);
 * `theme: 'tokens'` draws with the design-token classes instead of literal
 * colours, for pages inside the platform shell (see drawBandedView).
 */
let ComposerRenderer = (function() {
    'use strict';

    /* ── ArchiMate layer colours (ArchiMate 3.2 standard-adjacent) ── */
    /* ArchiMate 3.2 standard colors (The Open Group specification) */
    let LAYER_COLORS = {
        business:       { fill: '#FFFFB5', stroke: '#1a1a1a', text: '#1a1a1a', port: '#1a1a1a', badge: '#e6e68a', accent: '#c8a200' },
        application:    { fill: '#B5FFFF', stroke: '#1a1a1a', text: '#1a1a1a', port: '#1a1a1a', badge: '#80e5e5', accent: '#0097a7' },
        technology:     { fill: '#C9E7B7', stroke: '#1a1a1a', text: '#1a1a1a', port: '#1a1a1a', badge: '#a0cf8a', accent: '#2e7d32' },
        motivation:     { fill: '#CCCCFF', stroke: '#1a1a1a', text: '#1a1a1a', port: '#1a1a1a', badge: '#a3a3e6', accent: '#5c5c9e' },
        strategy:       { fill: '#F5DEAA', stroke: '#1a1a1a', text: '#1a1a1a', port: '#1a1a1a', badge: '#dfc480', accent: '#a07000' },
        implementation: { fill: '#FFE0E0', stroke: '#1a1a1a', text: '#1a1a1a', port: '#1a1a1a', badge: '#e6b3b3', accent: '#c0392b' },
        physical:       { fill: '#C9E7B7', stroke: '#1a1a1a', text: '#1a1a1a', port: '#1a1a1a', badge: '#a0cf8a', accent: '#558b2f' },
        composite:      { fill: '#f1f5f9', stroke: '#1a1a1a', text: '#1a1a1a', port: '#64748b', badge: '#e2e8f0', accent: '#64748b' },
    };
    let DEFAULT_LAYER = { fill: '#f8fafc', stroke: '#1a1a1a', text: '#1a1a1a', port: '#64748b', badge: '#e2e8f0', accent: '#94a3b8' };

    function layerColor(layer) {
        return LAYER_COLORS[(layer || '').toLowerCase()] || DEFAULT_LAYER;
    }

    /* ── Element type palette ─────────────────────────────── */
    let PALETTE = {
        'Business': [
            { type: 'BusinessActor',         label: 'Actor' },
            { type: 'BusinessRole',          label: 'Role' },
            { type: 'BusinessCollaboration', label: 'Collaboration' },
            { type: 'BusinessInterface',     label: 'Interface' },
            { type: 'BusinessProcess',       label: 'Process' },
            { type: 'BusinessFunction',      label: 'Function' },
            { type: 'BusinessInteraction',   label: 'Interaction' },
            { type: 'BusinessService',       label: 'Service' },
            { type: 'BusinessEvent',         label: 'Event' },
            { type: 'BusinessObject',        label: 'Object' },
            { type: 'Contract',              label: 'Contract' },
            { type: 'Representation',        label: 'Representation' },
            { type: 'Product',               label: 'Product' },
        ],
        'Application': [
            { type: 'ApplicationComponent',     label: 'Application' },
            { type: 'ApplicationCollaboration', label: 'App Collaboration' },
            { type: 'ApplicationInterface',     label: 'App Interface' },
            { type: 'ApplicationInteraction',   label: 'App Interaction' },
            { type: 'ApplicationService',       label: 'App Service' },
            { type: 'ApplicationFunction',      label: 'App Function' },
            { type: 'ApplicationProcess',       label: 'App Process' },
            { type: 'ApplicationEvent',         label: 'App Event' },
            { type: 'DataObject',               label: 'Data Object' },
        ],
        'Technology': [
            { type: 'Node',                      label: 'Node' },
            { type: 'Device',                    label: 'Device' },
            { type: 'SystemSoftware',            label: 'System SW' },
            { type: 'TechnologyCollaboration',   label: 'Tech Collaboration' },
            { type: 'TechnologyInterface',       label: 'Tech Interface' },
            { type: 'TechnologyService',         label: 'Tech Service' },
            { type: 'TechnologyFunction',        label: 'Tech Function' },
            { type: 'TechnologyProcess',         label: 'Tech Process' },
            { type: 'TechnologyInteraction',     label: 'Tech Interaction' },
            { type: 'TechnologyEvent',           label: 'Tech Event' },
            { type: 'Path',                      label: 'Path' },
            { type: 'CommunicationNetwork',      label: 'Network' },
            { type: 'Artifact',                  label: 'Artifact' },
        ],
        'Physical': [
            { type: 'Equipment',             label: 'Equipment' },
            { type: 'Facility',              label: 'Facility' },
            { type: 'DistributionNetwork',   label: 'Distribution Network' },
            { type: 'Material',              label: 'Material' },
        ],
        'Motivation': [
            { type: 'Stakeholder',           label: 'Stakeholder' },
            { type: 'Driver',                label: 'Driver' },
            { type: 'Assessment',            label: 'Assessment' },
            { type: 'Goal',                  label: 'Goal' },
            { type: 'Outcome',               label: 'Outcome' },
            { type: 'Requirement',           label: 'Requirement' },
            { type: 'Constraint',            label: 'Constraint' },
            { type: 'Principle',             label: 'Principle' },
            { type: 'Meaning',               label: 'Meaning' },
            { type: 'Value',                 label: 'Value' },
        ],
        'Strategy': [
            { type: 'Capability',            label: 'Capability' },
            { type: 'Resource',              label: 'Resource' },
            { type: 'CourseOfAction',        label: 'Course of Action' },
            { type: 'ValueStream',           label: 'Value Stream' },
        ],
        'Implementation & Migration': [
            { type: 'WorkPackage',           label: 'Work Package' },
            { type: 'Deliverable',           label: 'Deliverable' },
            { type: 'ImplementationEvent',   label: 'Impl. Event' },
            { type: 'Plateau',               label: 'Plateau' },
            { type: 'Gap',                   label: 'Gap' },
        ],
        'Composite': [
            { type: 'Grouping',              label: 'Grouping' },
            { type: 'Location',              label: 'Location' },
            { type: 'AndJunction',           label: 'AND Junction' },
            { type: 'OrJunction',            label: 'OR Junction' },
        ],
        'Diagram Tools': [
            { type: 'Note',                  label: 'Note' },
        ],
    };

    /* Layer lookup from element type */
    /* Normalize long palette keys to short internal layer IDs */
    let PALETTE_KEY_TO_LAYER = {
        'implementation & migration': 'implementation',
        'diagram tools': 'other',
    };
    let TYPE_TO_LAYER = {};
    Object.keys(PALETTE).forEach(function(layerName) {
        const layerKey = PALETTE_KEY_TO_LAYER[layerName.toLowerCase()] || layerName.toLowerCase();
        PALETTE[layerName].forEach(function(item) {
            TYPE_TO_LAYER[item.type] = layerKey;
        });
    });

    function guessLayer(elType) {
        return TYPE_TO_LAYER[elType] || 'application';
    }

    /* ── Relationship styles ──────────────────────────────── */
    let REL_STYLES = {
        composition:    { stroke: '#475569', strokeWidth: 2.5, strokeDasharray: '',       targetMarker: 'diamond-filled' },
        aggregation:    { stroke: '#475569', strokeWidth: 2.5, strokeDasharray: '',       targetMarker: 'diamond' },
        assignment:     { stroke: '#475569', strokeWidth: 2.5, strokeDasharray: '',       targetMarker: 'filled-arrow',  sourceMarker: 'ball' },
        realization:    { stroke: '#64748b', strokeWidth: 2,   strokeDasharray: '8,4',    targetMarker: 'hollow-triangle' },
        serving:        { stroke: '#94a3b8', strokeWidth: 2,   strokeDasharray: '',       targetMarker: 'open-arrow' },
        access:         { stroke: '#94a3b8', strokeWidth: 2,   strokeDasharray: '2,3',    targetMarker: 'open-arrow' },
        influence:      { stroke: '#8b5cf6', strokeWidth: 2,   strokeDasharray: '6,3',    targetMarker: 'open-arrow' },
        triggering:     { stroke: '#7c3aed', strokeWidth: 2,   strokeDasharray: '',       targetMarker: 'filled-arrow' },
        flow:           { stroke: '#7c3aed', strokeWidth: 2,   strokeDasharray: '10,4',   targetMarker: 'filled-arrow' },
        specialization: { stroke: '#64748b', strokeWidth: 2,   strokeDasharray: '',       targetMarker: 'hollow-triangle' },
        association:    { stroke: '#cbd5e1', strokeWidth: 1.5, strokeDasharray: '',       targetMarker: '' },
    };

    function markerPath(markerType) {
        if (markerType === 'diamond-filled' || markerType === 'diamond') return 'M 0 -5 L 8 0 L 0 5 L -8 0 Z';
        if (markerType === 'filled-arrow')    return 'M 10 -5 L 0 0 L 10 5 Z';
        if (markerType === 'open-arrow')       return 'M 10 -5 L 0 0 L 10 5';
        if (markerType === 'hollow-triangle')  return 'M 12 -6 L 0 0 L 12 6 Z';
        if (markerType === 'ball')             return 'M 0 0 a 4 4 0 1 0 0.01 0 Z';
        return '';
    }

    /** Humanize a relationship type name for display on link labels.
     *  "AssociationRelationship" -> "Association", "serving" -> "Serving" */
    function humanizeRelType(raw) {
        if (!raw) return '';
        let s = raw.replace(/Relationship$/, '');
        s = s.replace(/([A-Z])/g, ' $1').trim();
        return s.charAt(0).toUpperCase() + s.slice(1);
    }

    function markerFill(markerType, stroke) {
        if (markerType === 'diamond-filled')  return stroke;
        if (markerType === 'diamond')         return '#fff';
        if (markerType === 'filled-arrow')    return stroke;
        if (markerType === 'open-arrow')      return 'none';
        if (markerType === 'hollow-triangle') return '#fff';
        if (markerType === 'ball')            return stroke;
        return 'none';
    }

    /* ── ArchiMate 3.2 type icons (SVG paths, 16x16 viewBox) ── */
    let TYPE_ICONS = {
        /* Business layer */
        BusinessActor:      'M8 2a2 2 0 1 1 0 4 2 2 0 0 1 0-4zm0 5c-2.5 0-5 1-5 2v1h10v-1c0-1-2.5-2-5-2zm-3 4v3h6v-3',
        BusinessRole:       'M3 6a5 5 0 0 1 10 0v1H3V6zm1 2h8v4a4 4 0 0 1-8 0V8z',
        BusinessCollaboration: 'M5 5a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm6 0a3 3 0 1 1 0 6 3 3 0 0 1 0-6z',
        BusinessInterface:  'M8 3a5 5 0 1 1 0 10M3 8h5',
        BusinessProcess:    'M2 4h8l4 4-4 4H2l4-4L2 4z',
        BusinessFunction:   'M2 3v10l6-5L2 3zm6 0v10l6-5L8 3z',
        BusinessInteraction:'M2 6h8l3 2-3 2H2l3-2-3-2z',
        BusinessService:    'M3 5h10a2 2 0 0 1 0 4H3a2 2 0 0 1 0-4z',
        BusinessObject:     'M2 3h12v10H2V3zm0 3h12',
        BusinessEvent:      'M2 3h9l3 5-3 5H2l3-5-3-5z',
        Contract:           'M3 2h10v12H3V2zm0 3h10m-5 0v9',
        Representation:     'M3 2h10v12H3V2zm2 3h6m-6 2h6m-6 2h4',
        Product:            'M2 4h12v8H2V4zm0 0l6-2 6 2',
        /* Application layer */
        ApplicationComponent:     'M5 3h8v10H5V3zM2 5h3v2H2V5zm0 4h3v2H2V9z',
        ApplicationCollaboration: 'M5 4a4 4 0 1 1 0 8 4 4 0 0 1 0-8zm6 0a4 4 0 1 1 0 8 4 4 0 0 1 0-8z',
        ApplicationInterface:     'M8 3a5 5 0 1 1 0 10M3 8h5',
        ApplicationInteraction:   'M2 6h8l3 2-3 2H2l3-2-3-2z',
        ApplicationService:       'M3 5h10a2 2 0 0 1 0 4H3a2 2 0 0 1 0-4z',
        ApplicationFunction:      'M2 3v10l6-5L2 3zm6 0v10l6-5L8 3z',
        ApplicationProcess:       'M2 4h8l4 4-4 4H2l4-4L2 4z',
        ApplicationEvent:         'M2 5h7l3 3-3 3H2l3-3-3-3z',
        DataObject:               'M2 2h9l3 3v9H2V2zm9 0v3h3',
        /* Technology layer */
        Node:                 'M2 5h9v7H2V5zm0 0L5 2h9v7h-3',
        Device:               'M2 3h12v7H2V3zm3 7h6v2H5v-2z',
        SystemSoftware:       'M8 2a6 6 0 1 1 0 12 6 6 0 0 1 0-12zm0 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6z',
        TechnologyCollaboration: 'M5 5a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm6 0a3 3 0 1 1 0 6 3 3 0 0 1 0-6z',
        TechnologyInterface:  'M8 3a5 5 0 1 1 0 10M3 8h5',
        TechnologyService:    'M3 5h10a2 2 0 0 1 0 4H3a2 2 0 0 1 0-4z',
        TechnologyFunction:   'M2 4v8l5-4-5-4zm5 0v8l5-4-5-4z',
        TechnologyProcess:    'M2 5h7l3 3-3 3H2l3-3-3-3z',
        TechnologyInteraction:'M2 6h7l3 2-3 2H2l3-2-3-2z',
        TechnologyEvent:      'M2 5h7l3 3-3 3H2l3-3-3-3z',
        Path:                 'M2 8h3l2-4 2 8 2-8 2 4h3',
        CommunicationNetwork: 'M2 8h3l2-4 2 8 2-8 2 4h3',
        Artifact:             'M3 1h7l3 3v10H3V1zm7 0v3h3',
        /* Physical layer */
        Equipment:            'M8 2a6 6 0 1 1 0 12 6 6 0 0 1 0-12zm0 4a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM6 2v1M10 2v1M14 6h-1M14 10h-1M10 14v-1M6 14v-1M2 10h1M2 6h1',
        Facility:             'M2 14V4h12v10H2zm3-3h2V9H5v2zm4 0h2V9H9v2zm-4-4h2V5H5v2zm4 0h2V5H9v2z',
        DistributionNetwork:  'M8 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM3 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm10 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM8 6v4M8 10l-3 2m3-2l3 2',
        Material:             'M8 2l5 3v6l-5 3-5-3V5l5-3zM8 2v9M3 5l5 3 5-3',
        /* Motivation layer */
        Stakeholder:          'M8 2a2 2 0 1 1 0 4 2 2 0 0 1 0-4zm0 5c-2.5 0-5 1-5 2v1h10v-1c0-1-2.5-2-5-2zm-3 4v3h6v-3',
        Driver:               'M8 2l6 6-6 6-6-6 6-6z',
        Assessment:           'M8 3a5 5 0 1 1 0 10 5 5 0 0 1 0-10zM7 7l2 2 3-3',
        Goal:                 'M8 2a6 6 0 1 1 0 12 6 6 0 0 1 0-12zm0 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm0 1.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3z',
        Outcome:              'M3 8a5 5 0 1 1 10 0A5 5 0 0 1 3 8zm3 0l2 2 3-3',
        Requirement:          'M3 2h10v12H3V2zm2 3h6m-6 2.5h6m-6 2.5h4',
        Constraint:           'M3 2h10v12H3V2zm2 3h6m-6 2.5h6m-6 2.5h4M13 2L3 14',
        Principle:            'M3 2h10v12H3V2zm2 3h6m-6 2.5h6m-6 2.5h4',
        Meaning:              'M3 4h10v7H9.5L8 13l-1.5-2H3V4z',
        Value:                'M2 6l6-4 6 4-6 6-6-6z',
        /* Strategy layer */
        Capability:           'M2 4h12v8H2V4zm3 2v4m3-4v4m3-4v4',
        Resource:             'M4 4h8l2 2v4l-2 2H4l-2-2V6l2-2z',
        CourseOfAction:       'M2 8h10m-3-3l3 3-3 3',
        ValueStream:          'M2 4h10l2 4-2 4H2l2-4-2-4z',
        /* Implementation layer */
        WorkPackage:          'M2 4h12v8H2V4zm0 0L8 2l6 2',
        Deliverable:          'M3 1h7l3 3v10H3V1zm7 0v3h3',
        ImplementationEvent:  'M2 5h7l3 3-3 3H2l3-3-3-3z',
        Plateau:              'M2 4h12v2H2V4zm1 3h10v2H3V7zm2 3h6v2H5v-2z',
        Gap:                  'M2 6h4l2 4h4l2-4h0M2 6l2 4m8-4l-2 4',
        /* Composite */
        Location:             'M8 1a5 5 0 0 1 5 5c0 3.5-5 8-5 8S3 9.5 3 6a5 5 0 0 1 5-5zm0 3a2 2 0 1 0 0 4 2 2 0 0 0 0-4z',
    };

    function typeIconPath(elType) {
        return TYPE_ICONS[elType] || '';
    }

    /* ── JointJS custom ArchiMate element shapes ─────────────────────────────
     *
     *  Shape categories follow ArchiMate 3.2 visual notation:
     *    archimate.Node               -> Active Structure    (rounded rect, rx:10)
     *    archimate.BehaviorNode       -> Behavior            (square rect, rx:0)
     *    archimate.PassiveNode        -> Passive Structure   (folded-corner polygon)
     *    archimate.MotivationNode     -> Motivation          (chamfered-octagon polygon)
     *    archimate.ImplementationNode -> Implementation      (layered shadow rect)
     *    archimate.Container          -> White-box composite (dashed border)
     *
     *  All card-shaped elements share a vertical layout (200x130 default):
     *    icon box     (12, 12) 40x40  -- top-left
     *    name label   (12, 60)        -- below icon, bold 12px, wraps 2 lines
     *    type label   (12, 79)        -- below name, 9px caption
     *    maturity badge  top-right    -- hidden unless maturity data present
     *    progress bar    bottom       -- hidden unless maturity data present
     */
    let _shapesRegistered = false;

    function defineArchiMateShape() {
        if (_shapesRegistered) return;
        _shapesRegistered = true;

        /* ── Shared port groups ─────────────────────────────── *
         * UX-CMP-001: All ports are bidirectional (magnet: true) so architects
         * can initiate connections from any side — matches Lucidchart muscle memory.
         * 3 ports per side (12 total) let parallel relationships spread visually.
         * JointJS built-in layout functions auto-space multiple items per group. */
        let _portCircle = function(fill) {
            return { r: 5, magnet: true, fill: fill, stroke: '#1a1a1a', strokeWidth: 1.5 };
        };
        let _portMarkup = [{ tagName: 'circle', selector: 'circle' }];
        let CARD_PORTS = {
            groups: {
                'left':   { position: 'left',   attrs: { circle: _portCircle('#fff')    }, markup: _portMarkup },
                'right':  { position: 'right',  attrs: { circle: _portCircle('#1a1a1a') }, markup: _portMarkup },
                'top':    { position: 'top',    attrs: { circle: _portCircle('#fff')    }, markup: _portMarkup },
                'bottom': { position: 'bottom', attrs: { circle: _portCircle('#fff')    }, markup: _portMarkup },
            },
            items: [
                { group: 'left' },  { group: 'left' },  { group: 'left' },
                { group: 'right' }, { group: 'right' }, { group: 'right' },
                { group: 'top' },   { group: 'top' },   { group: 'top' },
                { group: 'bottom' },{ group: 'bottom' },{ group: 'bottom' },
            ],
        };

        /* ── Shared card attrs (all node shapes) ── */
        let CARD_ATTRS_BASE = {
            accentBar:          { x: 0, y: 0, refWidth: '100%', height: 5, rx: 0, ry: 0, fill: '#94a3b8' },
            iconBox:            { x: 12, y: 12, width: 40, height: 40, rx: 8, ry: 8, fill: 'rgba(255,255,255,0.65)', stroke: 'rgba(0,0,0,0.04)', strokeWidth: 1 },
            typeIcon:           { transform: 'translate(22, 22) scale(1.2)', fill: 'none', stroke: '#1a1a1a', strokeWidth: 1.1, strokeLinecap: 'round', strokeLinejoin: 'round', d: '' },
            nameLabel:          { x: 12, y: 60, textAnchor: 'start', textVerticalAnchor: 'top', fontSize: 12, fontWeight: 700, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: '#1a1a1a', text: 'Element', textWrap: { width: -24, maxLineCount: 2, ellipsis: true } },
            typeLabel:          { x: 12, y: 79, textAnchor: 'start', textVerticalAnchor: 'top', fontSize: 9,  fontWeight: 500, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: 'rgba(26,26,26,0.45)', text: '', textWrap: { width: -24, maxLineCount: 1, ellipsis: true } },
            maturityBadgeBg:    { refX: '100%', x: -50, y: 10, width: 36, height: 18, rx: 9, ry: 9, fill: 'rgba(0,0,0,0.75)', display: 'none' },
            maturityBadgeLabel: { refX: '100%', x: -32, y: 19, textAnchor: 'middle', textVerticalAnchor: 'middle', fontSize: 8, fontWeight: 800, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: '#ffffff', text: '', display: 'none' },
            maturityLeftLabel:  { x: 12, refY: '100%', y: -28, textAnchor: 'start', textVerticalAnchor: 'middle', fontSize: 7.5, fontWeight: 700, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: 'rgba(26,26,26,0.45)', text: '', letterSpacing: '0.06em', display: 'none' },
            maturityRightLabel: { refX: '100%', x: -12, refY: '100%', y: -28, textAnchor: 'end', textVerticalAnchor: 'middle', fontSize: 7.5, fontWeight: 700, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: 'rgba(26,26,26,0.45)', text: '', display: 'none' },
            maturityTrack:      { x: 12, refY: '100%', y: -16, refWidth: -24, height: 4, rx: 9999, ry: 9999, fill: 'rgba(0,0,0,0.08)', display: 'none' },
            maturityFill:       { x: 12, refY: '100%', y: -16, width: 0, height: 4, rx: 9999, ry: 9999, fill: '#1a1a1a', display: 'none' },
            /* Corner resize handles — all 4 corners (opacity controlled by CSS, NOT SVG attr) */
            resizeHandle:       { refX: '100%', refY: '100%', x: -10, y: -10, width: 10, height: 10, cursor: 'nwse-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
            resizeHandleTL:     { x: 0, y: 0, width: 10, height: 10, cursor: 'nwse-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
            resizeHandleTR:     { refX: '100%', x: -10, y: 0, width: 10, height: 10, cursor: 'nesw-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
            resizeHandleBL:     { x: 0, refY: '100%', y: -10, width: 10, height: 10, cursor: 'nesw-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
            /* Edge midpoint handles for single-axis resize (ENT-105) — opacity controlled by CSS */
            resizeHandleT:      { refX: '50%', x: -6, y: -2, width: 12, height: 5, cursor: 'ns-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
            resizeHandleR:      { refX: '100%', x: -3, refY: '50%', y: -6, width: 5, height: 12, cursor: 'ew-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
            resizeHandleB:      { refX: '50%', x: -6, refY: '100%', y: -3, width: 12, height: 5, cursor: 'ns-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
            resizeHandleL:      { x: -2, refY: '50%', y: -6, width: 5, height: 12, cursor: 'ew-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
            intelligenceBadge:{ refX: '100%', refY: -4, textAnchor: 'end', textVerticalAnchor: 'bottom', fontSize: 9, fontWeight: 600, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: '#6366f1', text: '', display: 'none' },
            /* GAP-CMP-009: Data classification badge (circle + PII text) — hidden by default */
            classificationBadge: { cx: 188, cy: 12, r: 5, fill: '#94a3b8', display: 'none' },
            piiBadge: { x: 170, y: 30, fontSize: 8, fontWeight: 700, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: '#ef4444', text: 'PII', display: 'none' },
        };

        /* ── Shared card markup (rect body) ── */
        let CARD_MARKUP_RECT = [
            { tagName: 'rect', selector: 'body' },
            { tagName: 'rect', selector: 'accentBar' },
            { tagName: 'rect', selector: 'iconBox' },
            { tagName: 'path', selector: 'typeIcon' },
            { tagName: 'rect', selector: 'maturityBadgeBg' },
            { tagName: 'text', selector: 'maturityBadgeLabel' },
            { tagName: 'text', selector: 'nameLabel' },
            { tagName: 'text', selector: 'typeLabel' },
            { tagName: 'text', selector: 'maturityLeftLabel' },
            { tagName: 'text', selector: 'maturityRightLabel' },
            { tagName: 'rect', selector: 'maturityTrack' },
            { tagName: 'rect', selector: 'maturityFill' },
            { tagName: 'rect', selector: 'resizeHandle' },
            { tagName: 'rect', selector: 'resizeHandleTL' },
            { tagName: 'rect', selector: 'resizeHandleTR' },
            { tagName: 'rect', selector: 'resizeHandleBL' },
            { tagName: 'rect', selector: 'resizeHandleT' },
            { tagName: 'rect', selector: 'resizeHandleR' },
            { tagName: 'rect', selector: 'resizeHandleB' },
            { tagName: 'rect', selector: 'resizeHandleL' },
            { tagName: 'text', selector: 'intelligenceBadge' },
        ];

        /* ═══════════════════════════════════════════════════════════
         *  1. archimate.Node — Active Structure (rounded rectangle)
         * ═══════════════════════════════════════════════════════════ */
        joint.dia.Element.define('archimate.Node', {
            size: { width: 200, height: 130 },
            attrs: Object.assign({}, CARD_ATTRS_BASE, {
                /* rx:3 and a crisp dark border, not the 10px/near-invisible-border
                   "SaaS card" look — the official ArchiMate 3.2 notation for Active
                   Structure elements is a plain, lightly-rounded, sharply-bordered
                   rectangle (17 Aug 2026: composer skin request). */
                body: { refWidth: '100%', refHeight: '100%', rx: 3, ry: 3, fill: '#f8fafc', stroke: 'rgba(17,24,39,0.55)', strokeWidth: 1 },
            }),
            ports: CARD_PORTS,
        }, { markup: CARD_MARKUP_RECT });

        /* ═══════════════════════════════════════════════════════════
         *  2. archimate.BehaviorNode — Behavior (square rectangle)
         * ═══════════════════════════════════════════════════════════ */
        joint.dia.Element.define('archimate.BehaviorNode', {
            size: { width: 200, height: 130 },
            attrs: Object.assign({}, CARD_ATTRS_BASE, {
                body: { refWidth: '100%', refHeight: '100%', rx: 0, ry: 0, fill: '#f8fafc', stroke: 'rgba(17,24,39,0.55)', strokeWidth: 1 },
            }),
            ports: CARD_PORTS,
        }, { markup: CARD_MARKUP_RECT });

        /* ═══════════════════════════════════════════════════════════
         *  3. archimate.PassiveNode — Passive Structure (folded corner)
         *  Top-right fold: 12% wide x 15% tall via polygon refPoints
         * ═══════════════════════════════════════════════════════════ */
        joint.dia.Element.define('archimate.PassiveNode', {
            size: { width: 200, height: 130 },
            attrs: Object.assign({}, CARD_ATTRS_BASE, {
                body:         { refPoints: '0,0 0.88,0 1,0.15 1,1 0,1', fill: '#f8fafc', stroke: 'rgba(17,24,39,0.55)', strokeWidth: 1 },
                foldTriangle: { refPoints: '0.88,0 1,0 1,0.15', fill: 'rgba(255,255,255,0.6)', stroke: 'rgba(17,24,39,0.55)', strokeWidth: 1 },
                foldCrease:   { refPoints: '0.88,0 0.88,0.15 1,0.15', fill: 'none', stroke: 'rgba(0,0,0,0.18)', strokeWidth: 1 },
            }),
            ports: CARD_PORTS,
        }, {
            markup: [
                { tagName: 'polygon',  selector: 'body' },
                { tagName: 'polygon',  selector: 'foldTriangle' },
                { tagName: 'polyline', selector: 'foldCrease' },
                { tagName: 'rect',     selector: 'accentBar' },
                { tagName: 'rect',     selector: 'iconBox' },
                { tagName: 'path',     selector: 'typeIcon' },
                { tagName: 'rect',     selector: 'maturityBadgeBg' },
                { tagName: 'text',     selector: 'maturityBadgeLabel' },
                { tagName: 'text',     selector: 'nameLabel' },
                { tagName: 'text',     selector: 'typeLabel' },
                { tagName: 'text',     selector: 'maturityLeftLabel' },
                { tagName: 'text',     selector: 'maturityRightLabel' },
                { tagName: 'rect',     selector: 'maturityTrack' },
                { tagName: 'rect',     selector: 'maturityFill' },
                { tagName: 'rect',     selector: 'resizeHandle' },
                { tagName: 'rect',     selector: 'resizeHandleTL' },
                { tagName: 'rect',     selector: 'resizeHandleTR' },
                { tagName: 'rect',     selector: 'resizeHandleBL' },
                { tagName: 'rect',     selector: 'resizeHandleT' },
                { tagName: 'rect',     selector: 'resizeHandleR' },
                { tagName: 'rect',     selector: 'resizeHandleB' },
                { tagName: 'rect',     selector: 'resizeHandleL' },
                { tagName: 'text',     selector: 'intelligenceBadge' },
                /* GAP-CMP-009: Data classification badge elements */
                { tagName: 'circle',   selector: 'classificationBadge' },
                { tagName: 'text',     selector: 'piiBadge' },
            ],
        });

        /* ═══════════════════════════════════════════════════════════
         *  4. archimate.MotivationNode — Motivation (chamfered octagon)
         *  All four corners chamfered at 10% of each dimension
         * ═══════════════════════════════════════════════════════════ */
        joint.dia.Element.define('archimate.MotivationNode', {
            size: { width: 200, height: 130 },
            attrs: Object.assign({}, CARD_ATTRS_BASE, {
                body:      { refPoints: '0.1,0 0.9,0 1,0.1 1,0.9 0.9,1 0.1,1 0,0.9 0,0.1', fill: '#f8fafc', stroke: 'rgba(17,24,39,0.55)', strokeWidth: 1.5 },
                accentBar: { x: 20, y: 0, refWidth: -40, height: 5, rx: 0, ry: 0, fill: '#94a3b8' },
            }),
            ports: CARD_PORTS,
        }, {
            markup: [
                { tagName: 'polygon', selector: 'body' },
                { tagName: 'rect',    selector: 'accentBar' },
                { tagName: 'rect',    selector: 'iconBox' },
                { tagName: 'path',    selector: 'typeIcon' },
                { tagName: 'rect',    selector: 'maturityBadgeBg' },
                { tagName: 'text',    selector: 'maturityBadgeLabel' },
                { tagName: 'text',    selector: 'nameLabel' },
                { tagName: 'text',    selector: 'typeLabel' },
                { tagName: 'text',    selector: 'maturityLeftLabel' },
                { tagName: 'text',    selector: 'maturityRightLabel' },
                { tagName: 'rect',    selector: 'maturityTrack' },
                { tagName: 'rect',    selector: 'maturityFill' },
                { tagName: 'rect',    selector: 'resizeHandle' },
                { tagName: 'rect',    selector: 'resizeHandleTL' },
                { tagName: 'rect',    selector: 'resizeHandleTR' },
                { tagName: 'rect',    selector: 'resizeHandleBL' },
                { tagName: 'rect',    selector: 'resizeHandleT' },
                { tagName: 'rect',    selector: 'resizeHandleR' },
                { tagName: 'rect',    selector: 'resizeHandleB' },
                { tagName: 'rect',    selector: 'resizeHandleL' },
                { tagName: 'text',    selector: 'intelligenceBadge' },
            ],
        });

        /* ═══════════════════════════════════════════════════════════
         *  5. archimate.ImplementationNode — Implementation (layered)
         *  Offset shadow underlay signals stacked/phased items
         * ═══════════════════════════════════════════════════════════ */
        joint.dia.Element.define('archimate.ImplementationNode', {
            size: { width: 200, height: 130 },
            attrs: Object.assign({}, CARD_ATTRS_BASE, {
                underlay: { refWidth: '100%', refHeight: '100%', x: 4, y: 4, rx: 3, ry: 3, fill: '#94a3b8', opacity: 0.25, stroke: 'none' },
                body:     { refWidth: '100%', refHeight: '100%', rx: 3, ry: 3, fill: '#f8fafc', stroke: 'rgba(17,24,39,0.55)', strokeWidth: 1 },
            }),
            ports: CARD_PORTS,
        }, {
            markup: [
                { tagName: 'rect', selector: 'underlay' },
                { tagName: 'rect', selector: 'body' },
                { tagName: 'rect', selector: 'accentBar' },
                { tagName: 'rect', selector: 'iconBox' },
                { tagName: 'path', selector: 'typeIcon' },
                { tagName: 'rect', selector: 'maturityBadgeBg' },
                { tagName: 'text', selector: 'maturityBadgeLabel' },
                { tagName: 'text', selector: 'nameLabel' },
                { tagName: 'text', selector: 'typeLabel' },
                { tagName: 'text', selector: 'maturityLeftLabel' },
                { tagName: 'text', selector: 'maturityRightLabel' },
                { tagName: 'rect', selector: 'maturityTrack' },
                { tagName: 'rect', selector: 'maturityFill' },
                { tagName: 'rect', selector: 'resizeHandle' },
                { tagName: 'rect', selector: 'resizeHandleTL' },
                { tagName: 'rect', selector: 'resizeHandleTR' },
                { tagName: 'rect', selector: 'resizeHandleBL' },
                { tagName: 'rect', selector: 'resizeHandleT' },
                { tagName: 'rect', selector: 'resizeHandleR' },
                { tagName: 'rect', selector: 'resizeHandleB' },
                { tagName: 'rect', selector: 'resizeHandleL' },
                { tagName: 'text', selector: 'intelligenceBadge' },
            ],
        });

        /* ── Container shape for white-box rendering ── */
        joint.dia.Element.define('archimate.Container', {
            size: { width: 320, height: 220 },
            attrs: {
                /* Card body — dashed border per ArchiMate 3.2 white-box notation */
                body: {
                    refWidth: '100%', refHeight: '100%',
                    rx: 10, ry: 10,
                    fill: '#f8fafc', stroke: 'rgba(0,0,0,0.15)', strokeWidth: 1.5,
                    strokeDasharray: '6,3', fillOpacity: 0.55,
                },
                /* Left accent bar */
                accentBar: { x: 0, y: 8, width: 5, refHeight: -16, rx: 3, ry: 3, fill: '#94a3b8' },
                /* Separator between header and content area */
                headerSep: { x: 8, y: 58, refWidth: -16, height: 1, fill: 'rgba(0,0,0,0.07)', stroke: 'none', strokeWidth: 0 },
                /* Icon box in header */
                iconBox: { x: 12, y: 9, width: 40, height: 40, rx: 8, ry: 8, fill: 'rgba(255,255,255,0.65)', stroke: 'rgba(0,0,0,0.04)', strokeWidth: 1 },
                typeIcon: { transform: 'translate(22, 19) scale(1.2)', fill: 'none', stroke: '#1a1a1a', strokeWidth: 1.1, strokeLinecap: 'round', strokeLinejoin: 'round', d: '' },
                nameLabel: { x: 62, y: 16, textAnchor: 'start', textVerticalAnchor: 'top', fontSize: 12, fontWeight: 700, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: '#1a1a1a', text: 'Container', textWrap: { width: -74, maxLineCount: 1, ellipsis: true } },
                typeLabel: { x: 62, y: 33, textAnchor: 'start', textVerticalAnchor: 'top', fontSize: 9, fontWeight: 500, fontFamily: 'Public Sans, Inter, system-ui, sans-serif', fill: 'rgba(26,26,26,0.45)', text: '', textWrap: { width: -74, maxLineCount: 1, ellipsis: true } },
                resizeHandle: { refX: '100%', refY: '100%', x: -12, y: -12, width: 10, height: 10, cursor: 'nwse-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
                resizeHandleTL: { x: 2, y: 2, width: 10, height: 10, cursor: 'nwse-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
                resizeHandleTR: { refX: '100%', x: -12, y: 2, width: 10, height: 10, cursor: 'nesw-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
                resizeHandleBL: { x: 2, refY: '100%', y: -12, width: 10, height: 10, cursor: 'nesw-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
                resizeHandleT: { refX: '50%', x: -6, y: -2, width: 12, height: 5, cursor: 'ns-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
                resizeHandleR: { refX: '100%', x: -3, refY: '50%', y: -6, width: 5, height: 12, cursor: 'ew-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
                resizeHandleB: { refX: '50%', x: -6, refY: '100%', y: -3, width: 12, height: 5, cursor: 'ns-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
                resizeHandleL: { x: -2, refY: '50%', y: -6, width: 5, height: 12, cursor: 'ew-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
                intelligenceBadge: { refX: '100%', refY: -4, textAnchor: 'end', textVerticalAnchor: 'bottom', fontSize: 9, fontWeight: 600, fontFamily: 'Inter, system-ui, sans-serif', fill: '#6366f1', text: '', display: 'none' },
                /* Legacy no-ops for backward compatibility */
                headerBar:  { x: 0, y: 0, width: 0, height: 0, fill: 'none', stroke: 'none' },
                headerClip: { x: 0, y: 0, width: 0, height: 0, fill: 'none', stroke: 'none' },
                badgeBg:    { x: 0, y: 0, width: 0, height: 0, fill: 'none', stroke: 'none' },
                badgeLabel: { text: '', fill: 'none', x: 0, y: 0 },
            },
            ports: {
                groups: {
                    'in':     { position: 'left',   attrs: { circle: { r: 5, magnet: 'passive', fill: '#fff',    stroke: '#94a3b8', strokeWidth: 1.5 } }, markup: [{ tagName: 'circle', selector: 'circle' }] },
                    'out':    { position: 'right',  attrs: { circle: { r: 5, magnet: true,      fill: '#94a3b8', stroke: '#94a3b8', strokeWidth: 1.5 } }, markup: [{ tagName: 'circle', selector: 'circle' }] },
                    'top':    { position: 'top',    attrs: { circle: { r: 5, magnet: true,      fill: '#fff',    stroke: '#94a3b8', strokeWidth: 1.5 } }, markup: [{ tagName: 'circle', selector: 'circle' }] },
                    'bottom': { position: 'bottom', attrs: { circle: { r: 5, magnet: true,      fill: '#fff',    stroke: '#94a3b8', strokeWidth: 1.5 } }, markup: [{ tagName: 'circle', selector: 'circle' }] },
                },
            },
        }, {
            markup: [
                { tagName: 'rect', selector: 'body' },
                { tagName: 'rect', selector: 'accentBar' },
                { tagName: 'rect', selector: 'headerSep' },
                { tagName: 'rect', selector: 'iconBox' },
                { tagName: 'path', selector: 'typeIcon' },
                { tagName: 'text', selector: 'nameLabel' },
                { tagName: 'text', selector: 'typeLabel' },
                { tagName: 'rect', selector: 'resizeHandle' },
                { tagName: 'rect', selector: 'resizeHandleTL' },
                { tagName: 'rect', selector: 'resizeHandleTR' },
                { tagName: 'rect', selector: 'resizeHandleBL' },
                { tagName: 'rect', selector: 'resizeHandleT' },
                { tagName: 'rect', selector: 'resizeHandleR' },
                { tagName: 'rect', selector: 'resizeHandleB' },
                { tagName: 'rect', selector: 'resizeHandleL' },
                { tagName: 'text', selector: 'intelligenceBadge' },
            ],
        });

        /* ── Junction shape (AND=filled, OR=hollow) ──*/
        joint.dia.Element.define('archimate.Junction', {
            size: { width: 24, height: 24 },
            attrs: {
                body: { cx: 12, cy: 12, r: 10, fill: '#1e293b', stroke: '#1e293b', strokeWidth: 2 },
                label: { refX: '50%', refY: 30, textAnchor: 'middle', textVerticalAnchor: 'top', fontSize: 9, fontWeight: 600, fontFamily: 'Inter, system-ui, sans-serif', fill: '#64748b', text: '' },
            },
            ports: {
                groups: {
                    'in':     { position: 'left',   attrs: { circle: { r: 4, magnet: 'passive', fill: '#fff', stroke: '#64748b', strokeWidth: 1 } }, markup: [{ tagName: 'circle', selector: 'circle' }] },
                    'out':    { position: 'right',  attrs: { circle: { r: 4, magnet: true,      fill: '#64748b', stroke: '#64748b', strokeWidth: 1 } }, markup: [{ tagName: 'circle', selector: 'circle' }] },
                    'top':    { position: 'top',    attrs: { circle: { r: 4, magnet: true,      fill: '#fff', stroke: '#64748b', strokeWidth: 1 } }, markup: [{ tagName: 'circle', selector: 'circle' }] },
                    'bottom': { position: 'bottom', attrs: { circle: { r: 4, magnet: true,      fill: '#fff', stroke: '#64748b', strokeWidth: 1 } }, markup: [{ tagName: 'circle', selector: 'circle' }] },
                },
            },
        }, {
            markup: [{ tagName: 'circle', selector: 'body' }, { tagName: 'text', selector: 'label' }],
        });

        /* ── Grouping shape (dashed rectangle) ── */
        joint.dia.Element.define('archimate.Grouping', {
            size: { width: 260, height: 160 },
            ports: CARD_PORTS,
            attrs: {
                body: { refWidth: '100%', refHeight: '100%', rx: 6, ry: 6, fill: '#f1f5f9', stroke: '#94a3b8', strokeWidth: 1.5, strokeDasharray: '8,4', fillOpacity: 0.3 },
                headerBar: { width: 120, height: 22, rx: 6, ry: 6, fill: '#e2e8f0', stroke: '#94a3b8', strokeWidth: 1 },
                nameLabel: { x: 60, y: 11, textAnchor: 'middle', textVerticalAnchor: 'middle', fontSize: 11, fontWeight: 600, fontFamily: 'Inter, system-ui, sans-serif', fill: '#334155', text: 'Group' },
                resizeHandle: { refX: '100%', refY: '100%', x: -12, y: -12, width: 10, height: 10, cursor: 'nwse-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
                resizeHandleTL: { x: 2, y: 2, width: 10, height: 10, cursor: 'nwse-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
                resizeHandleTR: { refX: '100%', x: -12, y: 2, width: 10, height: 10, cursor: 'nesw-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
                resizeHandleBL: { x: 2, refY: '100%', y: -12, width: 10, height: 10, cursor: 'nesw-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1.5, rx: 2 },
                resizeHandleT: { refX: '50%', x: -6, y: -2, width: 12, height: 5, cursor: 'ns-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
                resizeHandleR: { refX: '100%', x: -3, refY: '50%', y: -6, width: 5, height: 12, cursor: 'ew-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
                resizeHandleB: { refX: '50%', x: -6, refY: '100%', y: -3, width: 12, height: 5, cursor: 'ns-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
                resizeHandleL: { x: -2, refY: '50%', y: -6, width: 5, height: 12, cursor: 'ew-resize', fill: '#ffffff', stroke: '#2563eb', strokeWidth: 1, rx: 2 },
            },
        }, {
            markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'rect', selector: 'headerBar' }, { tagName: 'text', selector: 'nameLabel' }, { tagName: 'rect', selector: 'resizeHandle' }, { tagName: 'rect', selector: 'resizeHandleTL' }, { tagName: 'rect', selector: 'resizeHandleTR' }, { tagName: 'rect', selector: 'resizeHandleBL' }, { tagName: 'rect', selector: 'resizeHandleT' }, { tagName: 'rect', selector: 'resizeHandleR' }, { tagName: 'rect', selector: 'resizeHandleB' }, { tagName: 'rect', selector: 'resizeHandleL' }],
        });

        /* ── Note shape (yellow sticky note) ── */
        joint.dia.Element.define('archimate.Note', {
            size: { width: 160, height: 80 },
            attrs: {
                body: { refWidth: '100%', refHeight: '100%', fill: '#fef9c3', stroke: '#ca8a04', strokeWidth: 1, rx: 2, ry: 2 },
                fold: { refX: '100%', x: -16, y: 0, d: 'M 0 0 L 0 16 L 16 0 Z', fill: '#fde68a', stroke: '#ca8a04', strokeWidth: 1 },
                noteText: { refX: 8, refY: 8, textAnchor: 'start', textVerticalAnchor: 'top', fontSize: 11, fontWeight: 400, fontFamily: 'Inter, system-ui, sans-serif', fill: '#713f12', text: 'Note', textWrap: { width: -20, maxLineCount: 4, ellipsis: true } },
            },
        }, {
            markup: [{ tagName: 'rect', selector: 'body' }, { tagName: 'path', selector: 'fold' }, { tagName: 'text', selector: 'noteText' }],
        });
    }

    /* ── GAP-INT-002: Deployment zone color mapping ────────────────────── */
    let ZONE_COLORS = {
        'saas':          { fill: '#eff6ff', stroke: '#dbeafe', label: 'SaaS' },
        'on_premises':   { fill: '#f9fafb', stroke: '#e5e7eb', label: 'On-Premises' },
        'azure':         { fill: '#eef2ff', stroke: '#e0e7ff', label: 'Azure' },
        'aws':           { fill: '#fff7ed', stroke: '#fed7aa', label: 'AWS' },
        'gcp':           { fill: '#f0fdf4', stroke: '#bbf7d0', label: 'GCP' },
        'cloud':         { fill: '#eff6ff', stroke: '#dbeafe', label: 'Cloud' },
        'private_cloud': { fill: '#faf5ff', stroke: '#e9d5ff', label: 'Private Cloud' },
        'hybrid':        { fill: '#fffbeb', stroke: '#fef3c7', label: 'Hybrid' },
        'middleware':    { fill: '#f0fdfa', stroke: '#99f6e4', label: 'Middleware' },
        'dmz':           { fill: '#fffbeb', stroke: '#fde68a', label: 'DMZ' },
        'internet':      { fill: '#f8fafc', stroke: '#cbd5e1', label: 'Internet' },
        'default':       { fill: '#f1f5f9', stroke: '#e2e8f0', label: '' },
    };

    /** Apply zone styling to a Grouping or Location node */
    function applyZoneStyle(node) {
        let zoneType = node.get('zoneType') || 'default';
        let zs = ZONE_COLORS[zoneType] || ZONE_COLORS['default'];
        node.attr('body/fill', zs.fill);
        node.attr('body/stroke', zs.stroke);
        node.attr('body/strokeWidth', 2);
        node.attr('body/strokeDasharray', '8,4');
        if (node.attr('headerBar')) {
            node.attr('headerBar/fill', zs.fill);
            node.attr('headerBar/stroke', zs.stroke);
        }
    }

    /* ── Special element type checks ── */
    let SPECIAL_TYPES = { AndJunction: 'junction', OrJunction: 'junction', Grouping: 'grouping', Location: 'location', Note: 'note' };

    /* ── Shape category map (ArchiMate 3.2 visual notation) ────────────────
     *  Maps element type -> shape class name used by createNode()
     *    'node'           -> archimate.Node              (Active Structure, rounded rect)
     *    'behavior'       -> archimate.BehaviorNode      (Behavior, square rect)
     *    'passive'        -> archimate.PassiveNode       (Passive Structure, folded corner)
     *    'motivation'     -> archimate.MotivationNode    (Motivation, chamfered octagon)
     *    'implementation' -> archimate.ImplementationNode (Implementation, layered shadow)
     *  Unmapped types fall back to 'node'.
     */
    let SHAPE_CATEGORY = {
        /* Active Structure — rounded rect */
        BusinessActor: 'node', BusinessRole: 'node', BusinessCollaboration: 'node', BusinessInterface: 'node',
        ApplicationComponent: 'node', ApplicationCollaboration: 'node', ApplicationInterface: 'node',
        Node: 'node', Device: 'node', SystemSoftware: 'node',
        TechnologyCollaboration: 'node', TechnologyInterface: 'node',
        Equipment: 'node', Facility: 'node', DistributionNetwork: 'node',
        Resource: 'node', Capability: 'node',
        /* Behavior — square rect */
        BusinessProcess: 'behavior', BusinessFunction: 'behavior', BusinessInteraction: 'behavior',
        BusinessEvent: 'behavior', BusinessService: 'behavior',
        ApplicationProcess: 'behavior', ApplicationFunction: 'behavior', ApplicationInteraction: 'behavior',
        ApplicationEvent: 'behavior', ApplicationService: 'behavior',
        TechnologyProcess: 'behavior', TechnologyFunction: 'behavior', TechnologyInteraction: 'behavior',
        TechnologyEvent: 'behavior', TechnologyService: 'behavior',
        CommunicationNetwork: 'behavior', Path: 'behavior',
        ValueStream: 'behavior', CourseOfAction: 'behavior',
        /* Passive Structure — folded corner */
        BusinessObject: 'passive', Contract: 'passive', Product: 'passive', Representation: 'passive',
        DataObject: 'passive', Artifact: 'passive', Deliverable: 'passive', Material: 'passive',
        /* Motivation — chamfered octagon */
        Stakeholder: 'motivation', Driver: 'motivation', Assessment: 'motivation',
        Goal: 'motivation', Outcome: 'motivation', Principle: 'motivation',
        Requirement: 'motivation', Constraint: 'motivation', Meaning: 'motivation', Value: 'motivation',
        /* Implementation — layered shadow */
        WorkPackage: 'implementation', ImplementationEvent: 'implementation', Plateau: 'implementation', Gap: 'implementation',
    };

    function shapeCategory(elType) {
        return SHAPE_CATEGORY[elType] || 'node';
    }

    /* ── Create special shapes (junction, grouping, location, note) ── */
    function createSpecialNode(elementId, name, elType, x, y) {
        let node;
        if (elType === 'AndJunction') {
            node = new joint.shapes.archimate.Junction({ position: { x: x, y: y }, attrs: { body: { fill: '#1e293b' }, label: { text: 'AND' } } });
        } else if (elType === 'OrJunction') {
            node = new joint.shapes.archimate.Junction({ position: { x: x, y: y }, attrs: { body: { fill: '#fff' }, label: { text: 'OR' } } });
        } else if (elType === 'Grouping' || elType === 'Location') {
            /* GAP-INT-002: Both Grouping and Location render as deployment zone containers */
            node = new joint.shapes.archimate.Grouping({
                position: { x: x, y: y },
                size: { width: 400, height: 300 },
                attrs: { nameLabel: { text: name || (elType === 'Location' ? 'Location' : 'Group') } },
            });
            node.set('renderingMode', 'white_box');
            /* Apply default zone styling and listen for zone type changes */
            applyZoneStyle(node);
            node.on('change:zoneType', function() { applyZoneStyle(node); });
        } else if (elType === 'Note') {
            node = new joint.shapes.archimate.Note({ position: { x: x, y: y }, attrs: { noteText: { text: name || 'Note' } } });
        }
        if (!node) return null;
        /* UX-CMP-001: Ports are declared in CARD_PORTS.items (12 total, 3 per side).
         * Grouping/Location shapes get them from the shape definition.
         * Only addPort if the shape didn't include items (backward compat). */
        if (!node.getPorts().length) {
            ['left','left','left','right','right','right','top','top','top','bottom','bottom','bottom'].forEach(function(g) {
                node.addPort({ group: g });
            });
        }
        node.set('elementId', elementId);
        node.set('elType', elType);
        node.set('elLayer', elType === 'Location' ? 'physical' : 'connectors');
        node.set('elName', name || elType);
        return node;
    }

    /* ── Create a sticky-note annotation cell (CMP-052) ──────────
     *  Annotations are free-floating notes not connected to an element.
     *  They are marked with isAnnotation=true so the composer can
     *  distinguish them from regular ArchiMate elements.
     */
    function createAnnotation(x, y, text, w, h) {
        let annotW = w || 160;
        let annotH = h || 80;
        let annotId = 'annot-' + Date.now() + '-' + Math.random().toString(36).substr(2, 6);
        let node = new joint.shapes.standard.Rectangle({
            position: { x: x || 100, y: y || 100 },
            size: { width: annotW, height: annotH },
            attrs: {
                body: {
                    fill: '#fef9c3',
                    stroke: '#ca8a04',
                    strokeWidth: 1,
                    rx: 4,
                    ry: 4,
                },
                label: {
                    text: text || '',
                    fontSize: 12,
                    fill: '#1e293b',
                    textWrap: { width: annotW - 10, height: null, ellipsis: true },
                },
            },
        });
        node.set('elementId', annotId);
        node.set('elType', 'Annotation');
        node.set('elLayer', 'annotation');
        node.set('isAnnotation', true);
        return node;
    }

    /* ── Create a layer-zone swimlane cell (CMP-039) ────────────
     *  Layer zones are non-interactive horizontal bands that visually
     *  organise the canvas into ArchiMate layers.  They are sent to the
     *  back of the graph so they never obscure elements.
     *
     *  Callers:  composer_graph.toggleLayerZones()
     *            composer_persistence.loadSavedViewpoint()   (restore)
     *
     *  An optional sixth argument, `zoneDef` ({box_key, label}), turns a
     *  plain layer band into a canvas box. When given, the rendered label is
     *  zoneDef.label (falling back to the usual "<Layer> Layer" text when
     *  zoneDef carries no label of its own — the restore path below only
     *  persists box_key, not label) and the cell carries 'boxKey' so
     *  _serializeCanvasExt and the restore path can round-trip which canvas
     *  box a swimlane is. Entries are not placed into it here — that is a
     *  later change's projection.
     */
    function createLayerZone(layer, x, y, w, h, zoneDef) {
        let c = layerColor(layer);
        let displayName = (zoneDef && zoneDef.label)
            || (layer.charAt(0).toUpperCase() + layer.slice(1) + ' Layer');

        let zone = new joint.shapes.standard.Rectangle({
            position: { x: x || 0, y: y || 0 },
            size: { width: w || 1400, height: h || 160 },
            attrs: {
                body: {
                    fill: c.fill,
                    stroke: c.accent || c.stroke,
                    strokeWidth: 1.5,
                    strokeDasharray: '8,4',
                    opacity: 0.45,
                    rx: 4,
                    ry: 4,
                },
                label: {
                    text: displayName,
                    fill: c.accent || c.text,
                    fontSize: 10,
                    fontWeight: 700,
                    fontFamily: 'Inter, system-ui, sans-serif',
                    textAnchor: 'start',
                    textVerticalAnchor: 'top',
                    refX: 16,
                    refY: 8,
                    letterSpacing: 0.08,
                    textTransform: 'uppercase',
                },
            },
        });

        zone.set('isLayerZone', true);
        zone.set('zoneLayer', layer);
        if (zoneDef && zoneDef.box_key) {
            zone.set('boxKey', zoneDef.box_key);
        }
        return zone;
    }

    /* ── Create a styled ArchiMate node ────────────────────────
     *  Selects the correct JointJS shape class based on SHAPE_CATEGORY
     *  so each element type renders with its proper ArchiMate 3.2 geometry.
     */
    function createNode(elementId, name, elType, layer, x, y) {
        /* Handle special types (junctions, groupings, notes) */
        if (SPECIAL_TYPES[elType]) {
            return createSpecialNode(elementId, name, elType, x, y);
        }
        let c = layerColor(layer);
        let iconPath = typeIconPath(elType);
        let typeName = (elType || '').replace(/([A-Z])/g, ' $1').trim();
        let cat = shapeCategory(elType);

        /* Select shape class from category */
        let ShapeClass;
        if      (cat === 'behavior')       { ShapeClass = joint.shapes.archimate.BehaviorNode; }
        else if (cat === 'passive')        { ShapeClass = joint.shapes.archimate.PassiveNode; }
        else if (cat === 'motivation')     { ShapeClass = joint.shapes.archimate.MotivationNode; }
        else if (cat === 'implementation') { ShapeClass = joint.shapes.archimate.ImplementationNode; }
        else                               { ShapeClass = joint.shapes.archimate.Node; }

        /* 17 Aug 2026 composer skin request: the accent stripe and translucent
           icon backing box are Entelim's own "SaaS card" polish, not part of the
           ArchiMate 3.2 visual notation — real ArchiMate editor renders
           are a single flat-coloured element with a crisp border and the type
           icon sitting directly on the fill. accentBar is hidden (opacity 0,
           not removed from markup, to avoid touching 5 shapes' selector lists);
           iconBox drops its background/border so the icon reads as part of the
           notation rather than a UI chrome affordance. */
        let shapeAttrs = {
            body:      { fill: c.fill, stroke: 'rgba(17,24,39,0.55)' },
            accentBar: { fill: c.fill, opacity: 0 },
            iconBox:   { fill: 'none', stroke: 'none' },
            typeIcon:  iconPath ? { d: iconPath, stroke: c.text, strokeWidth: 1.1 } : { d: '' },
            nameLabel: { text: name || '(unnamed)', fill: c.text },
            typeLabel: { text: typeName },
        };

        /* ImplementationNode: tint underlay with layer accent */
        if (cat === 'implementation') {
            shapeAttrs.underlay = { fill: c.accent || c.fill, opacity: 0.22 };
        }

        let node = new ShapeClass({
            position: { x: x, y: y },
            size: { width: 200, height: 130 },
            attrs: shapeAttrs,
        });

        /* UX-CMP-001: Override port colors with layer accent.
         * Ports are pre-declared in CARD_PORTS.items; here we just restyle them. */
        const _ac = c.accent || '#1a1a1a';
        node.getPorts().forEach(function(p, i) {
            let fill = (p.group === 'right' && i % 3 === 0) ? _ac : '#fff';
            node.portProp(p.id, 'attrs/circle/fill', fill);
            node.portProp(p.id, 'attrs/circle/stroke', _ac);
        });

        node.set('elementId', elementId);
        node.set('elType', elType);
        node.set('elLayer', layer);
        node.set('elName', name);

        /* GAP-CMP-009: Data classification badge for DataObject elements.
         * When dataClassification or containsPII are set on the cell,
         * add a small colored indicator on the node shape. */
        if (elType === 'DataObject') {
            node.on('change:dataClassification change:containsPII', function() {
                const cls = node.get('dataClassification') || '';
                const pii = node.get('containsPII') || false;
                const badgeColors = {
                    'public':       '#22c55e',
                    'internal':     '#3b82f6',
                    'confidential': '#f59e0b',
                    'restricted':   '#ef4444',
                };
                const badgeColor = badgeColors[cls] || '';
                if (badgeColor || pii) {
                    node.attr('classificationBadge/fill', badgeColor || '#94a3b8');
                    node.attr('classificationBadge/display', 'block');
                    node.attr('classificationBadge/r', 5);
                    node.attr('classificationBadge/cx', 188);
                    node.attr('classificationBadge/cy', 12);
                    if (pii) {
                        node.attr('piiBadge/text', 'PII');
                        node.attr('piiBadge/display', 'block');
                        node.attr('piiBadge/x', 170);
                        node.attr('piiBadge/y', 30);
                        node.attr('piiBadge/fontSize', 8);
                        node.attr('piiBadge/fontWeight', 700);
                        node.attr('piiBadge/fill', '#ef4444');
                    } else {
                        node.attr('piiBadge/display', 'none');
                    }
                } else {
                    node.attr('classificationBadge/display', 'none');
                    node.attr('piiBadge/display', 'none');
                }
            });
        }

        return node;
    }

    function applyImportedElementPresentation(node, options) {
        if (!node || !options) return;

        let renderMode = options.rendering_mode || options.lucid_rendering_mode || options.renderingMode || '';
        if (String(renderMode).indexOf('lucid_') !== 0) return;

        let stereotype = String(options.lucid_stereotype || '').trim();
        let attrs = node.get('attrs') || {};
        let isBlackBox = renderMode === 'lucid_black_box';
        let palette = isBlackBox ? {
            bodyFill: '#1f2937',
            bodyStroke: '#111827',
            nameText: '#ffffff',
            typeText: 'rgba(255,255,255,0.72)',
            foldFill: '#374151',
            foldStroke: 'rgba(255,255,255,0.30)',
            portStroke: '#ffffff',
            portFill: '#1f2937',
        } : {
            bodyFill: '#ffffff',
            bodyStroke: '#111827',
            nameText: '#111827',
            typeText: 'rgba(17,24,39,0.66)',
            foldFill: '#f8fafc',
            foldStroke: 'rgba(17,24,39,0.24)',
            portStroke: '#111827',
            portFill: '#ffffff',
        };

        if (attrs.body) {
            node.attr('body/fill', palette.bodyFill);
            node.attr('body/stroke', palette.bodyStroke);
            node.attr('body/strokeWidth', 1.5);
            node.attr('body/rx', 0);
            node.attr('body/ry', 0);
        }
        if (attrs.accentBar) {
            node.attr('accentBar/display', 'none');
        }
        if (attrs.iconBox) {
            node.attr('iconBox/display', 'none');
        }
        if (attrs.typeIcon) {
            node.attr('typeIcon/display', 'none');
        }
        if (attrs.nameLabel) {
            node.attr('nameLabel/fill', palette.nameText);
            node.attr('nameLabel/fontFamily', 'Inter, system-ui, sans-serif');
            node.attr('nameLabel/fontWeight', isBlackBox ? 700 : 600);
            node.attr('nameLabel/fontSize', isBlackBox ? 12 : 11);
            node.attr('nameLabel/textWrap', {
                width: -28,
                maxLineCount: isBlackBox ? 2 : 3,
                ellipsis: true,
            });
            if (isBlackBox) {
                node.attr('nameLabel/x', Math.round(node.size().width / 2));
                node.attr('nameLabel/y', Math.round(node.size().height / 2));
                node.attr('nameLabel/textAnchor', 'middle');
                node.attr('nameLabel/textVerticalAnchor', 'middle');
            } else {
                node.attr('nameLabel/x', 14);
                node.attr('nameLabel/y', stereotype ? 32 : 24);
                node.attr('nameLabel/textAnchor', 'start');
                node.attr('nameLabel/textVerticalAnchor', 'top');
            }
        }
        if (attrs.typeLabel) {
            node.attr('typeLabel/fill', palette.typeText);
            node.attr('typeLabel/fontFamily', 'Inter, system-ui, sans-serif');
            node.attr('typeLabel/fontWeight', 700);
            node.attr('typeLabel/fontSize', 9);
            node.attr('typeLabel/textTransform', 'uppercase');
            if (isBlackBox || !stereotype) {
                node.attr('typeLabel/display', 'none');
            } else {
                node.attr('typeLabel/display', '');
                node.attr('typeLabel/text', stereotype);
                node.attr('typeLabel/x', 14);
                node.attr('typeLabel/y', 12);
                node.attr('typeLabel/textAnchor', 'start');
                node.attr('typeLabel/textVerticalAnchor', 'top');
            }
        }
        if (attrs.headerBar) {
            node.attr('headerBar/display', 'none');
        }
        if (attrs.foldTriangle) {
            node.attr('foldTriangle/fill', palette.foldFill);
            node.attr('foldTriangle/stroke', palette.foldStroke);
        }
        if (attrs.foldCrease) {
            node.attr('foldCrease/stroke', palette.foldStroke);
        }
        if (attrs.headerSep) {
            node.attr('headerSep/display', 'none');
        }

        node.set('renderingMode', renderMode);
        node.set('lucidImported', true);
        if (stereotype) {
            node.set('lucidStereotype', stereotype);
        }

        if (node.getPorts && node.getPorts().length) {
            node.getPorts().forEach(function(port) {
                node.portProp(port.id, 'attrs/circle/fill', palette.portFill);
                node.portProp(port.id, 'attrs/circle/stroke', palette.portStroke);
                node.portProp(port.id, 'attrs/circle/r', 0);
            });
        }

        if (node.get('elType') === 'Location') {
            node.resize(240, 104);
        } else if (isBlackBox) {
            node.resize(196, 76);
        } else {
            node.resize(224, stereotype ? 94 : 82);
        }
    }

    /* ── Create a container node (white-box mode) ─────────── */
    function createContainerNode(elementId, name, elType, layer, x, y, w, h) {
        let c = layerColor(layer);
        let iconPath = typeIconPath(elType);
        let typeName = (elType || '').replace(/([A-Z])/g, ' $1').trim();

        let container = new joint.shapes.archimate.Container({
            position: { x: x, y: y },
            size: { width: w || 320, height: h || 220 },
            attrs: {
                body:      { fill: c.fill, stroke: c.accent || c.stroke, fillOpacity: 0.55, strokeDasharray: '6,3' },
                accentBar: { fill: c.accent || c.stroke },
                iconBox:   { fill: 'rgba(255,255,255,0.65)' },
                nameLabel: { text: name || '(unnamed)', fill: c.text },
                typeLabel: { text: typeName },
                typeIcon:  iconPath ? { d: iconPath, stroke: c.text } : { d: '' },
                resizeHandle: { stroke: c.accent || c.stroke },
            },
        });

        /* UX-CMP-001: Override port colors for containers.
         * Ports pre-declared in CARD_PORTS.items; restyle with layer accent. */
        const _cs = c.accent || c.stroke;
        container.getPorts().forEach(function(p, i) {
            let fill = (p.group === 'right' && i % 3 === 0) ? _cs : '#fff';
            container.portProp(p.id, 'attrs/circle/fill', fill);
            container.portProp(p.id, 'attrs/circle/stroke', _cs);
        });

        container.set('elementId', elementId);
        container.set('elType', elType);
        container.set('elLayer', layer);
        container.set('elName', name);
        container.set('renderingMode', 'white_box');

        return container;
    }

    /* ── Create a styled relationship link ────────────────── */
    function createLink(sourceCell, targetCell, relType, relId) {
        let style = REL_STYLES[relType] || REL_STYLES.association;
        let mp = markerPath(style.targetMarker);
        let mf = markerFill(style.targetMarker, style.stroke);

        let lineAttrs = {
            stroke: style.stroke, strokeWidth: style.strokeWidth,
            strokeDasharray: style.strokeDasharray || '',
            targetMarker: mp ? { type: 'path', d: mp, fill: mf, stroke: style.stroke, strokeWidth: 1 }
                             : { type: 'path', d: '' },
        };

        /* Source marker (e.g. ball for Assignment) */
        if (style.sourceMarker) {
            let smp = markerPath(style.sourceMarker);
            let smf = markerFill(style.sourceMarker, style.stroke);
            if (smp) {
                lineAttrs.sourceMarker = { type: 'path', d: smp, fill: smf, stroke: style.stroke, strokeWidth: 1 };
            }
        }

        let link = new joint.shapes.standard.Link({
            source: { id: sourceCell.id },
            target: { id: targetCell.id },
            attrs: {
                line: lineAttrs,
            },
            labels: [{
                attrs: {
                    text: { text: humanizeRelType(relType), fontSize: 10, fontWeight: 500, fontFamily: 'Inter, system-ui, sans-serif', fill: '#64748b' },
                    rect: { fill: '#fff', stroke: '#e2e8f0', strokeWidth: 0.5, rx: 3, ry: 3, ref: 'text', refWidth: 8, refHeight: 4, refX: -4, refY: -2 },
                },
                position: { distance: 0.5, offset: -12 },
            }],
            router: { name: 'manhattan', args: { step: 12, padding: 36 } },
            connector: { name: 'rounded', args: { radius: 8 } },
        });

        link.set('relType', relType);
        link.set('relId', relId || null);
        link.set('routingStyle', 'manhattan');
        return link;
    }

    /* ── Layer banding: the one layered layout ───────────────
     *  Places every element cell in a horizontal band for its layer, bands
     *  top to bottom in LAYER_Y_ORDER (or opts.order). Used by the Composer's
     *  auto-layout and by every read-only view drawn with this renderer, so
     *  there is one layered layout, not one per page.
     *
     *  applyLayerBanding(graph)                  — the Composer's defaults.
     *  applyLayerBanding(graph, null, opts)      — a view's own geometry:
     *    opts.order       [layer, ...] band order; with opts.showEmpty every
     *                     listed band is laid out even when it holds nothing
     *    opts.cols        elements per row
     *    opts.spacingX/Y  cell pitch; opts.bandGap gap between bands
     *    opts.top         y of the first band; opts.header space above a
     *                     band's first row for its title; opts.pad inner padding
     *    opts.width       rows are centred inside this width
     *  (The second argument is accepted and ignored: an existing Composer call
     *  passes its paper there.)
     *  Returns the bands it laid out: [{layer, y, height, count}].
     */
    let LAYER_Y_ORDER = {
        'strategy': 0, 'motivation': 1, 'business': 2,
        'application': 3, 'technology': 4, 'physical': 5, 'implementation': 6,
    };

    function applyLayerBanding(graph, _paper, opts) {
        let cells = graph.getElements().filter(function(c) {
            return !c.get('isLayerZone') && !c.get('isAnnotation');
        });
        if (cells.length === 0 && !(opts && opts.showEmpty)) return [];

        let layersPresent = {};
        cells.forEach(function(cell) {
            let layer = (cell.get('elLayer') || '').toLowerCase();
            if (!layersPresent[layer]) layersPresent[layer] = [];
            layersPresent[layer].push(cell);
        });

        if (!opts) {
            /* The Composer's own layout, unchanged. */
            let sortedLayers = Object.keys(layersPresent)
                .filter(function(l) { return LAYER_Y_ORDER[l] !== undefined; })
                .sort(function(a, b) { return LAYER_Y_ORDER[a] - LAYER_Y_ORDER[b]; });
            let unknownLayers = Object.keys(layersPresent).filter(function(l) {
                return LAYER_Y_ORDER[l] === undefined;
            });
            sortedLayers = sortedLayers.concat(unknownLayers);

            if (sortedLayers.length < 2) {
                let cols = Math.ceil(Math.sqrt(cells.length));
                cells.forEach(function(cell, i) {
                    cell.position(40 + (i % cols) * 240, 40 + Math.floor(i / cols) * 160);
                });
                return [];
            }

            let COLS_MAX = 10;
            let SPACING_X = 240;   /* element width 200 + 40px gap */
            let SPACING_Y = 160;   /* element height 130 + 30px gap */
            let BAND_GAP = 80;
            let yOffset = 40;
            let laid = [];

            sortedLayers.forEach(function(layer) {
                let nodes = layersPresent[layer];
                let cols = Math.min(nodes.length, COLS_MAX);
                let startX = Math.max(40, (cols <= 3 ? 200 : 40));
                nodes.forEach(function(n, i) {
                    n.position(startX + (i % cols) * SPACING_X, yOffset + Math.floor(i / cols) * SPACING_Y);
                });
                let rows = Math.ceil(nodes.length / cols);
                laid.push({ layer: layer, y: yOffset, height: rows * SPACING_Y, count: nodes.length });
                yOffset += rows * SPACING_Y + BAND_GAP;
            });
            return laid;
        }

        let order = (opts.order || Object.keys(LAYER_Y_ORDER)).slice();
        Object.keys(layersPresent).forEach(function(l) {
            if (order.indexOf(l) === -1) order.push(l);
        });
        let perRow = Math.max(1, opts.cols || 1);
        let spacingX = opts.spacingX || 240;
        let spacingY = opts.spacingY || 160;
        let bandGap = opts.bandGap || 0;
        let header = opts.header || 0;
        let pad = opts.pad || 0;
        let width = opts.width || 0;
        let y = opts.top || 0;
        let bands = [];

        order.forEach(function(layer) {
            let members = layersPresent[layer] || [];
            if (!members.length && !opts.showEmpty) return;
            let rows = Math.ceil(members.length / perRow);
            members.forEach(function(cell, i) {
                let r = Math.floor(i / perRow);
                let inRow = Math.min(perRow, members.length - r * perRow);
                let cellW = cell.size().width;
                let rowWidth = inRow * spacingX - (spacingX - cellW);
                let start = width ? (width - rowWidth) / 2 : pad;
                cell.position(start + (i % perRow) * spacingX, y + header + pad + r * spacingY);
            });
            let height = header + pad + (rows ? rows * spacingY - (spacingY - (members[0] ? members[0].size().height : 0)) + pad : 0);
            bands.push({ layer: layer, y: y, height: height, count: members.length });
            y += height + bandGap;
        });
        return bands;
    }

    /* ── Relationship type key ────────────────────────────────
     *  "Serving", "ServingRelationship", "serving_relationship" -> "serving",
     *  the key REL_STYLES is written in. Unknown types read as association. */
    function relTypeKey(raw) {
        let key = String(raw || '').replace(/[_\s-]*relationship$/i, '').replace(/[_\s-]/g, '').toLowerCase();
        return REL_STYLES[key] ? key : 'association';
    }

    /* ── Token theme ──────────────────────────────────────────
     *  A page inside the platform shell draws with the design tokens rather
     *  than the notation's literal colours, so the picture follows the theme
     *  (light, dark, high contrast) like the rest of the page. The shapes,
     *  icons and line styles are the same notation; only where a colour comes
     *  from changes. Full class names are written out here so the stylesheet
     *  build can see them. */
    let TOKEN_BAND_CLASS = {
        motivation:     'fill-layer-motivation/10 stroke-layer-motivation/40',
        strategy:       'fill-layer-strategy/10 stroke-layer-strategy/40',
        business:       'fill-layer-business/10 stroke-layer-business/40',
        application:    'fill-layer-application/10 stroke-layer-application/40',
        technology:     'fill-layer-technology/10 stroke-layer-technology/40',
        physical:       'fill-layer-technology/10 stroke-layer-technology/40',
        implementation: 'fill-layer-implementation/10 stroke-layer-implementation/40',
    };
    let TOKEN_BAND_DEFAULT = 'fill-muted/40 stroke-border';
    let TOKEN_ELEMENT_STROKE = {
        motivation:     'stroke-layer-motivation/40',
        strategy:       'stroke-layer-strategy/40',
        business:       'stroke-layer-business/40',
        application:    'stroke-layer-application/40',
        technology:     'stroke-layer-technology/40',
        physical:       'stroke-layer-technology/40',
        implementation: 'stroke-layer-implementation/40',
    };
    let TOKEN_ELEMENT_FILL = 'fill-background';
    let TOKEN_ELEMENT_SELECTED_STROKE = 'stroke-primary';
    let TOKEN_LINE = 'stroke-muted-foreground';
    let TOKEN_LINE_ON_PATH = 'stroke-primary';

    function _isLiteralColour(value) {
        if (!value) return false;
        let v = String(value).trim().toLowerCase();
        return v.charAt(0) === '#' || v.indexOf('rgb') === 0 || v === 'white' || v === 'black';
    }

    function _isLight(value) {
        let v = String(value).trim().toLowerCase();
        return v === '#fff' || v === '#ffffff' || v === 'white' || v.indexOf('rgba(255,255,255') === 0 ||
            v.indexOf('rgba(255, 255, 255') === 0 || v === '#fafbfc' || v === '#f8fafc';
    }

    function _swap(node, attr, cls) {
        node.removeAttribute(attr);
        if (cls) cls.split(' ').forEach(function(c) { if (c) node.classList.add(c); });
    }

    /* Replace every literal fill and stroke under `root` with a token class.
       `roleOf(node)` names what the node is, when the caller knows better
       than its colour does. */
    function tokeniseTree(root, roleOf) {
        let nodes = [root].concat(Array.prototype.slice.call(root.querySelectorAll('*')));
        nodes.forEach(function(node) {
            if (!node.getAttribute) return;
            let fill = node.getAttribute('fill');
            let stroke = node.getAttribute('stroke');
            if (!_isLiteralColour(fill) && !_isLiteralColour(stroke)) return;
            let role = roleOf ? roleOf(node) : null;
            if (role && role.fill !== undefined && _isLiteralColour(fill)) _swap(node, 'fill', role.fill);
            else if (_isLiteralColour(fill)) _swap(node, 'fill', _isLight(fill) ? 'fill-background' : (node.tagName.toLowerCase() === 'text' ? 'fill-foreground' : 'fill-muted-foreground'));
            if (role && role.stroke !== undefined && _isLiteralColour(stroke)) _swap(node, 'stroke', role.stroke);
            else if (_isLiteralColour(stroke)) _swap(node, 'stroke', _isLight(stroke) ? 'stroke-border' : 'stroke-muted-foreground');
        });
    }

    function _tokeniseCellView(view) {
        let cell = view.model;
        let layer = String(cell.get('zoneLayer') || cell.get('elLayer') || '').toLowerCase();
        tokeniseTree(view.el, function(node) {
            let selector = node.getAttribute('joint-selector');
            if (cell.get('isLayerZone')) {
                if (selector === 'body') return { fill: TOKEN_BAND_CLASS[layer] || TOKEN_BAND_DEFAULT, stroke: '' };
                if (selector === 'label') return { fill: 'fill-foreground' };
                return null;
            }
            if (cell.isLink()) {
                if (selector === 'line') return { stroke: TOKEN_LINE };
                if (node.tagName.toLowerCase() === 'text') return { fill: 'fill-muted-foreground' };
                if (node.tagName.toLowerCase() === 'rect') return { fill: 'fill-background', stroke: 'stroke-border' };
                return null;
            }
            if (selector === 'body') return { fill: TOKEN_ELEMENT_FILL, stroke: TOKEN_ELEMENT_STROKE[layer] || 'stroke-border' };
            if (selector === 'nameLabel') return { fill: 'fill-foreground' };
            if (selector === 'typeLabel') return { fill: 'fill-muted-foreground' };
            if (selector === 'typeIcon') return { stroke: 'stroke-muted-foreground' };
            return null;
        });
    }

    /* Markers live in the paper's <defs>, outside any cell view. */
    function _tokeniseDefs(paper) {
        let defs = paper.svg ? paper.svg.querySelectorAll('defs') : [];
        Array.prototype.forEach.call(defs, function(d) { tokeniseTree(d, null); });
    }

    /* ── Badge on a link: words plus a mark, never colour alone ── */
    let GLYPH_WORKED_OUT = [
        ['circle', { cx: 12, cy: 4.5, r: 2.5 }], ['path', { d: 'm10.2 6.3-3.9 3.9' }],
        ['circle', { cx: 4.5, cy: 12, r: 2.5 }], ['path', { d: 'M7 12h10' }],
        ['circle', { cx: 19.5, cy: 12, r: 2.5 }], ['path', { d: 'm13.8 17.7 3.9-3.9' }],
        ['circle', { cx: 12, cy: 19.5, r: 2.5 }]
    ];
    let GLYPH_CLOCK = [['circle', { cx: 12, cy: 12, r: 10 }], ['polyline', { points: '12 6 12 12 16 14' }]];

    function _glyphMarkup(parts, x, label) {
        let g = {
            tagName: 'g',
            attributes: {
                transform: 'translate(' + x + ',-6) scale(0.5)', fill: 'none', 'class': 'stroke-muted-foreground',
                'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round'
            },
            children: []
        };
        if (label) {
            g.attributes.role = 'img';
            g.attributes['aria-label'] = label;
            g.children.push({ tagName: 'title', textContent: label });
        }
        parts.forEach(function(part) {
            g.children.push({ tagName: part[0], attributes: part[1] });
        });
        return g;
    }

    /* badge: {text, title, clockLabel?, className?} */
    function badgeLabel(badge) {
        let glyphs = badge.clockLabel ? 2 : 1;
        let width = 8 + glyphs * 16 + Math.ceil(String(badge.text).length * 6.2) + 8;
        let x = -width / 2 + 8;
        let children = [
            { tagName: 'title', textContent: badge.title || badge.text },
            { tagName: 'rect', attributes: { x: -width / 2, y: -10, width: width, height: 20, rx: 4, 'class': 'fill-background stroke-muted-foreground' } },
            _glyphMarkup(GLYPH_WORKED_OUT, x)
        ];
        x += 16;
        if (badge.clockLabel) {
            children.push(_glyphMarkup(GLYPH_CLOCK, x, badge.clockLabel));
            x += 16;
        }
        children.push({ tagName: 'text', attributes: { x: x, y: 4, 'class': 'fill-foreground text-xs' }, textContent: badge.text });
        return {
            markup: [{ tagName: 'g', className: badge.className || 'cr-badge', children: children }],
            attrs: {},
            position: { distance: 0.5 }
        };
    }

    /* ── A read-only banded view: bands, elements, typed links, and a real
     *  button over every element for keyboard and pointer ───────────────
     *  spec = {
     *    width,                                   drawing width in CSS pixels
     *    bands: [{layer, label}],                 top to bottom
     *    elements: [{id, name, type, layer, band, subtitle, emphasis}],
     *    relationships: [{id, source_id, target_id, type, kind, badge}],
     *        kind 'derived' draws dashed (5,5) with its badge in place of the
     *        type label; any other kind draws the ArchiMate line of `type`
     *    classes: {edge, badge},                  class names a page tests by
     *    buttons: {host, onSelect(id), suffix(id)} optional keyboard layer
     *  }
     *  Returns {height}.
     */
    let VIEW_NODE_W = 180;
    let VIEW_NODE_H = 112;
    let VIEW_GAP_X = 24;
    let VIEW_GAP_Y = 20;
    let VIEW_PAD = 12;
    let VIEW_HEADER = 26;
    let VIEW_BUTTON_CLASS = 'absolute rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background';
    let VIEW_HIDDEN_SELECTORS = ['accentBar', 'iconBox', 'resizeHandle', 'resizeHandleTL', 'resizeHandleTR', 'resizeHandleBL',
        'resizeHandleT', 'resizeHandleR', 'resizeHandleB', 'resizeHandleL', 'maturityBadgeBg', 'maturityBadgeLabel',
        'maturityLeftLabel', 'maturityRightLabel', 'maturityTrack', 'maturityFill', 'intelligenceBadge'];

    function drawBandedView(state, spec) {
        let graph = state.graph;
        let paper = state.paper;
        let width = Math.max(VIEW_NODE_W + 2 * VIEW_PAD, Math.floor(spec.width || 0));
        let classes = spec.classes || {};
        let perRow = Math.max(1, Math.floor((width - 2 * VIEW_PAD + VIEW_GAP_X) / (VIEW_NODE_W + VIEW_GAP_X)));
        let bandOf = {};
        (spec.bands || []).forEach(function(b) { bandOf[b.layer] = b; });

        graph.clear();
        state.cells = {};
        state.links = {};

        /* Elements first so the layout can place them. */
        let elementCells = [];
        (spec.elements || []).forEach(function(el) {
            let layer = el.band || (el.layer || '').toLowerCase() || guessLayer(el.type);
            let node = createNode(el.id, el.name || '', el.type || 'ApplicationComponent', layer, 0, 0);
            if (!node) return;
            if (node.getPorts && node.getPorts().length) node.removePorts();
            node.resize(VIEW_NODE_W, VIEW_NODE_H);
            node.set('elLayer', layer);
            let attrs = {
                nameLabel: { text: el.name || 'Not recorded', y: 38, textWrap: { width: -24, maxLineCount: 2, ellipsis: true } },
                typeLabel: { text: el.subtitle || '', y: 76, fontSize: 10, textWrap: { width: -24, maxLineCount: 2, ellipsis: true } },
                typeIcon: { transform: 'translate(12, 12) scale(1.2)' },
            };
            VIEW_HIDDEN_SELECTORS.forEach(function(sel) { attrs[sel] = { display: 'none' }; });
            if (el.emphasis) attrs.body = { strokeWidth: 2.5 };
            node.attr(attrs);
            elementCells.push(node);
            state.cells[el.id] = node;
        });
        graph.addCells(elementCells);

        let laid = applyLayerBanding(graph, null, {
            order: (spec.bands || []).map(function(b) { return b.layer; }),
            showEmpty: true,
            cols: perRow,
            spacingX: VIEW_NODE_W + VIEW_GAP_X,
            spacingY: VIEW_NODE_H + VIEW_GAP_Y,
            header: VIEW_HEADER,
            pad: VIEW_PAD,
            width: width,
        });
        let height = 0;
        let zones = laid.map(function(b) {
            let def = bandOf[b.layer] || { label: '' };
            let bandHeight = b.count ? b.height : VIEW_HEADER + 12;
            let zone = createLayerZone(b.layer, 1, b.y + 1, width - 2, bandHeight - 2, { label: def.label });
            zone.attr({
                body: { strokeDasharray: null, opacity: null, rx: 8, ry: 8, strokeWidth: 1 },
                label: { textTransform: null, letterSpacing: null, fontSize: 12, fontWeight: 500, refX: VIEW_PAD, refY: 8 },
            });
            /* Behind everything else on the paper. */
            zone.set('z', -1);
            height = Math.max(height, b.y + bandHeight);
            return zone;
        });
        graph.addCells(zones);

        (spec.relationships || []).forEach(function(rel) {
            let src = state.cells[rel.source_id];
            let tgt = state.cells[rel.target_id];
            if (!src || !tgt) return;
            let derived = rel.kind === 'derived';
            let link = createLink(src, tgt, relTypeKey(rel.type), rel.id);
            let style = REL_STYLES[relTypeKey(rel.type)];
            link.attr('line/strokeDasharray', derived ? '5,5' : (style.strokeDasharray || null));
            link.attr('line/data-kind', derived ? 'derived' : 'explicit');
            link.router({ name: 'manhattan', args: { step: 12, padding: 16, excludeTypes: ['standard.Rectangle'] } });
            if (derived) {
                link.source({ id: src.id, anchor: { name: 'center', args: { dx: 14 } } });
                link.target({ id: tgt.id, anchor: { name: 'center', args: { dx: 14 } } });
                link.labels(rel.badge ? [badgeLabel(Object.assign({ className: classes.badge }, rel.badge))] : []);
            }
            link.set('relKey', rel.id);
            graph.addCell(link);
            state.links[rel.id] = link;
        });

        paper.setDimensions(width, height);
        state.height = height;
        state.width = width;

        graph.getCells().forEach(function(cell) {
            let view = paper.findViewByModel(cell);
            if (!view) return;
            if (state.theme === 'tokens') _tokeniseCellView(view);
            if (cell.isLink()) {
                if (classes.edge) view.el.classList.add(classes.edge);
                view.el.setAttribute('data-edge', String(cell.get('relKey')));
                view.el.setAttribute('data-kind', cell.attr('line/data-kind'));
            } else if (cell.get('isLayerZone')) {
                view.el.setAttribute('data-layer', cell.get('zoneLayer'));
            } else {
                view.el.setAttribute('data-element', String(cell.get('elementId')));
                view.el.setAttribute('data-layer', cell.get('elLayer'));
            }
        });
        if (state.theme === 'tokens') _tokeniseDefs(paper);

        if (spec.buttons && spec.buttons.host) _drawButtons(state, spec, elementCells);
        return { height: height };
    }

    /* Real buttons over the drawing, in reading order (top band first, left
       to right). They are what a keyboard or a pointer reaches; the drawing
       itself sits inside the element that describes it to a screen reader. */
    function _drawButtons(state, spec, elementCells) {
        let host = spec.buttons.host;
        let onSelect = spec.buttons.onSelect;
        let suffix = spec.buttons.suffix;
        /* A redraw (a resize, the side panel closing) keeps the buttons that
           are still drawn, so keyboard focus stays where the person left it. */
        let previous = state.buttons || {};
        let focused = document.activeElement && host.contains(document.activeElement)
            ? document.activeElement.getAttribute('data-node') : null;
        state.buttons = {};
        let ordered = elementCells.slice().sort(function(a, b) {
            let pa = a.position();
            let pb = b.position();
            return (pa.y - pb.y) || (pa.x - pb.x);
        });
        ordered.forEach(function(cell, index) {
            let id = cell.get('elementId');
            let pos = cell.position();
            let size = cell.size();
            let button = previous[id];
            if (!button) {
                button = document.createElement('button');
                button.type = 'button';
                button.className = VIEW_BUTTON_CLASS;
                button.setAttribute('data-node', String(id));
                button.setAttribute('aria-pressed', 'false');
                button.appendChild(document.createElement('span'));
                button.firstChild.className = 'sr-only';
                button.addEventListener('click', function() { if (onSelect) onSelect(id); });
            }
            delete previous[id];
            button.title = cell.get('elName') || '';
            button.style.left = pos.x + 'px';
            button.style.top = pos.y + 'px';
            button.style.width = size.width + 'px';
            button.style.height = size.height + 'px';
            button.firstChild.textContent = (cell.get('elName') || 'Not recorded') + (suffix ? suffix(id) : '');
            /* Tab order follows the picture; only move a button that is out of place. */
            if (host.children[index] !== button) host.insertBefore(button, host.children[index] || null);
            state.buttons[id] = button;
        });
        Object.keys(previous).forEach(function(id) {
            if (previous[id].parentNode === host) host.removeChild(previous[id]);
        });
        if (focused && state.buttons[focused] && document.activeElement !== state.buttons[focused]) {
            state.buttons[focused].focus({ preventScroll: true });
        }
    }

    /* Selection and the highlighted path change without a new layout. */
    function paintSelection(state, selectedId, pathKeys) {
        pathKeys = pathKeys || {};
        Object.keys(state.cells || {}).forEach(function(id) {
            let view = state.paper.findViewByModel(state.cells[id]);
            if (!view) return;
            let body = view.el.querySelector('[joint-selector="body"]');
            let selected = String(id) === String(selectedId);
            if (body && state.theme === 'tokens') {
                /* Selected: a primary outline, thicker than the centre's. */
                let layerStroke = TOKEN_ELEMENT_STROKE[state.cells[id].get('elLayer')] || 'stroke-border';
                layerStroke.split(' ').forEach(function(c) { body.classList.toggle(c, !selected); });
                body.classList.toggle(TOKEN_ELEMENT_SELECTED_STROKE, selected);
                if (!body.hasAttribute('data-stroke-width')) body.setAttribute('data-stroke-width', body.getAttribute('stroke-width') || '1');
                body.setAttribute('stroke-width', selected ? '3' : body.getAttribute('data-stroke-width'));
            }
            let button = state.buttons ? state.buttons[id] : null;
            if (button) button.setAttribute('aria-pressed', selected ? 'true' : 'false');
        });
        Object.keys(state.links || {}).forEach(function(key) {
            let view = state.paper.findViewByModel(state.links[key]);
            if (!view) return;
            let line = view.el.querySelector('[joint-selector="line"]');
            if (!line) return;
            let on = !!pathKeys[key];
            line.setAttribute('stroke-width', on ? 3 : 2);
            if (state.theme === 'tokens') {
                line.classList.toggle(TOKEN_LINE, !on);
                line.classList.toggle(TOKEN_LINE_ON_PATH, on);
            }
        });
    }

    /* ── Public factory: create a renderer bound to a container element ── */
    function create(containerEl, opts) {
        opts = opts || {};
        let mode = opts.mode || 'view';

        defineArchiMateShape();

        let graph = new joint.dia.Graph();
        let paper = new joint.dia.Paper({
            el: containerEl,
            model: graph,
            width: opts.width || '100%',
            height: opts.height || 400,
            gridSize: opts.gridSize || 12,
            drawGrid: mode === 'edit' ? [
                { name: 'dot', args: { color: '#dde1e6', thickness: 1 } },
                { name: 'dot', args: { color: '#c8cdd3', thickness: 1, scaleFactor: 5 } },
            ] : false,
            background: { color: opts.background || (opts.theme === 'tokens' ? 'transparent' : '#fafbfc') },
            interactive: mode === 'edit'
                ? { linkMove: true, elementMove: true, addLinkFromMagnet: true }
                : { elementMove: false, addLinkFromMagnet: false },
            linkPinning: false,
        });

        let canvasElements = {};
        /* The read-only banded view keeps what it drew here: cells by element
           id, links by relationship id, and the buttons over the elements. */
        let viewState = { graph: graph, paper: paper, theme: opts.theme || null, cells: {}, links: {}, buttons: {} };

        return {
            graph: graph,
            paper: paper,
            mode: mode,

            /* Draw a read-only banded view (see drawBandedView). */
            drawBands: function(spec) {
                return drawBandedView(viewState, spec);
            },

            /* Mark one element as selected and a set of links as its path. */
            select: function(selectedId, pathKeys) {
                paintSelection(viewState, selectedId, pathKeys);
            },

            /* Move keyboard focus to the button over one element. */
            focus: function(elementId) {
                let button = viewState.buttons[elementId];
                if (button) button.focus();
            },

            loadElements: function(elements, relationships) {
                graph.clear();
                canvasElements = {};
                let cellMap = {};
                let cols = Math.max(1, Math.ceil(Math.sqrt(elements.length)));

                elements.forEach(function(el, i) {
                    let col = i % cols;
                    let row = Math.floor(i / cols);
                    let x = 40 + col * 240;
                    let y = 40 + row * 160;
                    let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                    let node = createNode(el.id, el.name, el.type || 'ApplicationComponent', layer, x, y);
                    graph.addCell(node);
                    cellMap[el.id] = node;
                    canvasElements[el.id] = el;
                });

                (relationships || []).forEach(function(rel) {
                    let src = cellMap[rel.source_id];
                    let tgt = cellMap[rel.target_id];
                    if (!src || !tgt) return;
                    graph.addCell(createLink(src, tgt, rel.type || 'association', rel.id));
                });
            },

            fitToContent: function() {
                paper.scaleContentToFit({ padding: 20, maxScale: 1.2, minScale: 0.3 });
            },

            getElementCount: function() { return Object.keys(canvasElements).length; },
            getRelCount: function() { return graph.getLinks().length; },

            destroy: function() {
                graph.clear();
                paper.remove();
            },
        };
    }

    return {
        create: create,
        LAYER_Y_ORDER: LAYER_Y_ORDER,
        applyLayerBanding: applyLayerBanding,
        relTypeKey: relTypeKey,
        tokeniseTree: tokeniseTree,
        LAYER_COLORS: LAYER_COLORS,
        DEFAULT_LAYER: DEFAULT_LAYER,
        REL_STYLES: REL_STYLES,
        PALETTE: PALETTE,
        TYPE_TO_LAYER: TYPE_TO_LAYER,
        SPECIAL_TYPES: SPECIAL_TYPES,
        SHAPE_CATEGORY: SHAPE_CATEGORY,
        TYPE_ICONS: TYPE_ICONS,
        layerColor: layerColor,
        guessLayer: guessLayer,
        shapeCategory: shapeCategory,
        typeIconPath: typeIconPath,
        markerPath: markerPath,
        markerFill: markerFill,
        humanizeRelType: humanizeRelType,
        createNode: createNode,
        createLink: createLink,
        createContainerNode: createContainerNode,
        applyImportedElementPresentation: applyImportedElementPresentation,
        createSpecialNode: createSpecialNode,
        defineArchiMateShape: defineArchiMateShape,
        createAnnotation: createAnnotation,
        createLayerZone: createLayerZone,
        ZONE_COLORS: ZONE_COLORS,
        applyZoneStyle: applyZoneStyle,
    };
})();
