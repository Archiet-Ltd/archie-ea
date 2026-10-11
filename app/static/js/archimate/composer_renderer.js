/**
 * ComposerRenderer — Reusable ArchiMate diagram rendering engine
 * Extracted from composer.js for use across Solution Detail, Architecture Assistant, AI Chat.
 *
 * Usage:
 *   var renderer = ComposerRenderer.create(containerEl, { mode: 'view', width: '100%', height: 400 });
 *   renderer.loadElements(elements, relationships);
 *   renderer.fitToContent();
 *   renderer.destroy();
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
            background: { color: opts.background || '#fafbfc' },
            interactive: mode === 'edit'
                ? { linkMove: true, elementMove: true, addLinkFromMagnet: true }
                : { elementMove: false, addLinkFromMagnet: false },
            linkPinning: false,
        });

        let canvasElements = {};

        return {
            graph: graph,
            paper: paper,
            mode: mode,

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
