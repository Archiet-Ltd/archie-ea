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

    /* ── Banded layout (opt-in: create(el, {layout:'bands'})) ─────────────────
     *  Pure geometry, no JointJS: given the elements, the declared bands and the
     *  container width, return where every node and every band sits. Elements whose
     *  layer is not one of the declared bands go to a trailing "unplaced" band,
     *  which only exists when something lands in it. Declared bands with nothing in
     *  them collapse to their header. Within a band: depth, then name.
     */
    let BAND_HEADER = 28, BAND_PAD = 12, BAND_GAP = 12, NODE_H_FULL = 130, NODE_H_COMPACT = 48;
    let UNPLACED_BAND = { layer: 'unplaced', label: 'Layer not recorded' };

    function bandNodeWidth(width) {
        return Math.min(168, Math.max(96, width - 24));
    }

    /* Optional fifth argument, used only by the opt-in connection style and row
     * alignment: { colGap, rowGap, header, rowAlign: 'centre' }. Absent, the
     * placement is the left-aligned one every existing caller gets. */
    function computeBandLayout(elements, bands, width, compact, layoutOpts) {
        let lo = layoutOpts || {};
        let colGap = lo.colGap != null ? lo.colGap : BAND_GAP;
        let rowGap = lo.rowGap != null ? lo.rowGap : BAND_GAP;
        let header = lo.header != null ? lo.header : BAND_HEADER;
        let centreRows = lo.rowAlign === 'centre';
        let nodeW = bandNodeWidth(width);
        let nodeH = compact ? NODE_H_COMPACT : NODE_H_FULL;
        let perRow = Math.max(1, Math.floor((width - 2 * BAND_PAD + colGap) / (nodeW + colGap)));
        let declared = (bands || []).map(function(b) { return { layer: String(b.layer || '').toLowerCase(), label: b.label }; });
        let known = {};
        declared.forEach(function(b) { known[b.layer] = true; });

        let members = {};
        let unplaced = [];
        (elements || []).forEach(function(el) {
            let layer = String(el.layer || '').toLowerCase();
            if (known[layer]) { (members[layer] = members[layer] || []).push(el); }
            else { unplaced.push(el); }
        });
        let order = function(a, b) {
            let da = typeof a.depth === 'number' ? a.depth : 0;
            let db = typeof b.depth === 'number' ? b.depth : 0;
            if (da !== db) return da - db;
            return String(a.name || '').localeCompare(String(b.name || ''));
        };

        let all = declared.slice();
        if (unplaced.length) { all.push(UNPLACED_BAND); members[UNPLACED_BAND.layer] = unplaced; }

        let y = 0, placements = {}, zones = [];
        all.forEach(function(b) {
            let list = (members[b.layer] || []).slice().sort(order);
            let rows = Math.ceil(list.length / perRow);
            let h = list.length ? header + rows * (nodeH + rowGap) + (BAND_PAD - rowGap) : header;
            zones.push({ layer: b.layer, label: b.label, x: 0, y: y, width: width, height: h, count: list.length });
            list.forEach(function(el, i) {
                let startX = BAND_PAD;
                if (centreRows) {
                    let inRow = Math.min(perRow, list.length - Math.floor(i / perRow) * perRow);
                    startX = Math.floor((width - (inRow * nodeW + (inRow - 1) * colGap)) / 2);
                }
                placements[el.id] = {
                    band: b.layer,
                    x: startX + (i % perRow) * (nodeW + colGap),
                    y: y + header + Math.floor(i / perRow) * (nodeH + rowGap),
                    width: nodeW, height: nodeH,
                };
            });
            y += h + 4;
        });
        return { zones: zones, placements: placements, height: Math.max(y, BAND_HEADER), nodeWidth: nodeW, nodeHeight: nodeH, perRow: perRow };
    }

    /* ── Plain connection style (opt-in: create(el, {connectionStyle:'plain'})) ──
     *  Pure geometry for routing one connection around the nodes of a drawing.
     *  Rectangles are {id, x, y, w, h} in drawing pixels. Band zones are never
     *  passed in: only nodes are obstacles, so a route never hugs a band edge.
     *  Values: columns 24px apart and rows 16px apart (so every gap between nodes
     *  is wider than the 16px floor), a 36px band header (arcs sit below the band
     *  label), a worked-out partner offset 13px, routes kept 4px off nodes and 6px
     *  inside the drawing. */
    let PLAIN_COL_GAP = 24, PLAIN_ROW_GAP = 16, PLAIN_HEADER = 36;
    let PLAIN_PAIR_OFFSET = 13, PLAIN_CLEARANCE = 4, PLAIN_EDGE = 6, PLAIN_MIN_CORRIDOR = 8, PLAIN_CORNER = 12, PLAIN_STEP = 12;

    function plainSegmentHitsRect(a, b, r, m) {
        let x0 = r.x - m, y0 = r.y - m, x1 = r.x + r.w + m, y1 = r.y + r.h + m;
        let dx = b.x - a.x, dy = b.y - a.y, t0 = 0, t1 = 1;
        let p = [-dx, dx, -dy, dy], q = [a.x - x0, x1 - a.x, a.y - y0, y1 - a.y];
        for (let i = 0; i < 4; i++) {
            if (p[i] === 0) { if (q[i] < 0) return false; continue; }
            let t = q[i] / p[i];
            if (p[i] < 0) { if (t > t1) return false; if (t > t0) t0 = t; }
            else { if (t < t0) return false; if (t < t1) t1 = t; }
        }
        return true;
    }

    /* x positions of the vertical gaps in one row that stay clear of its nodes: the
       middle of each gap between nodes (nearest to midX first), then, only when the
       row has no such gap, the middle of a side margin. */
    function plainRowGaps(rowRects, midX, width) {
        let m = PLAIN_CLEARANCE;
        let spans = rowRects.map(function(r) { return [r.x - m, r.x + r.w + m]; })
            .sort(function(a, b) { return a[0] - b[0]; });
        let merged = [];
        spans.forEach(function(s) {
            let last = merged[merged.length - 1];
            if (last && s[0] <= last[1]) last[1] = Math.max(last[1], s[1]);
            else merged.push([s[0], s[1]]);
        });
        let lo = PLAIN_EDGE, hi = width - PLAIN_EDGE, cur = lo, inner = [], side = [];
        let gap = function(from, to) { return { x: (from + to) / 2, lo: from, hi: to }; };
        merged.forEach(function(g, i) {
            if (g[0] - cur >= PLAIN_MIN_CORRIDOR) { (i === 0 ? side : inner).push(gap(cur, g[0])); }
            cur = Math.max(cur, g[1]);
        });
        if (hi - cur >= PLAIN_MIN_CORRIDOR) { (merged.length ? side : inner).push(gap(cur, hi)); }
        let byDistance = function(a, b) { return Math.abs(a.x - midX) - Math.abs(b.x - midX) || a.x - b.x; };
        return inner.sort(byDistance).concat(side.sort(byDistance));
    }

    /* Rows of nodes: nodes whose vertical extents overlap share a row. Sorted top to bottom. */
    function plainRows(rects) {
        let sorted = rects.slice().sort(function(a, b) { return a.y - b.y; });
        let rows = [];
        sorted.forEach(function(r) {
            let last = rows[rows.length - 1];
            if (last && r.y < last.bottom - 1) { last.rects.push(r); last.bottom = Math.max(last.bottom, r.y + r.h); }
            else rows.push({ top: r.y, bottom: r.y + r.h, rects: [r] });
        });
        return rows;
    }

    /* y of the horizontal channel between row i and row i + 1: the middle of a narrow
       gap, or, below a band, just above the next row (clear of the band edges and of
       the next band label). */
    function plainChannel(rows, i) {
        let upper = rows[i], lower = rows[i + 1];
        return lower.top - upper.bottom <= 24 ? (upper.bottom + lower.top) / 2 : lower.top - 8;
    }

    /* The smooth connector curves through its vertices with a spline that treats them
       as evenly spaced, so a handful of far-apart corners makes it swing wide. The
       engine therefore hands it a route that is already rounded: the corners of the
       routed polyline are cut with a small curve and the whole path is sampled at an
       even step. Returns the interior points; the anchors are the two ends. */
    function plainSmoothVertices(line, radius, step) {
        let dense = [line[0]];
        for (let i = 1; i < line.length - 1; i++) {
            let p0 = line[i - 1], p1 = line[i], p2 = line[i + 1];
            let d1 = Math.hypot(p1.x - p0.x, p1.y - p0.y), d2 = Math.hypot(p2.x - p1.x, p2.y - p1.y);
            let r = Math.min(radius, d1 / 2, d2 / 2);
            if (r < 0.5) { dense.push(p1); continue; }
            let q1 = { x: p1.x + (p0.x - p1.x) / d1 * r, y: p1.y + (p0.y - p1.y) / d1 * r };
            let q2 = { x: p1.x + (p2.x - p1.x) / d2 * r, y: p1.y + (p2.y - p1.y) / d2 * r };
            for (let k = 0; k <= 4; k++) {
                let t = k / 4, u = 1 - t;
                dense.push({ x: u * u * q1.x + 2 * u * t * p1.x + t * t * q2.x, y: u * u * q1.y + 2 * u * t * p1.y + t * t * q2.y });
            }
        }
        dense.push(line[line.length - 1]);
        let lens = [0];
        for (let i = 1; i < dense.length; i++) lens.push(lens[i - 1] + Math.hypot(dense[i].x - dense[i - 1].x, dense[i].y - dense[i - 1].y));
        let total = lens[lens.length - 1];
        let n = Math.max(2, Math.round(total / step));
        let out = [], j = 1;
        for (let k = 1; k < n; k++) {
            let t = total * k / n;
            while (j < dense.length - 1 && lens[j] < t) j++;
            let seg = (lens[j] - lens[j - 1]) || 1, f = (t - lens[j - 1]) / seg;
            out.push({ x: dense[j - 1].x + (dense[j].x - dense[j - 1].x) * f, y: dense[j - 1].y + (dense[j].y - dense[j - 1].y) * f });
        }
        return out;
    }

    /* One connection. S and T are the end nodes; `rows` is plainRows() of every node;
       `slot` = {index, corridor}: index is the connection place among those joining
       the same two nodes (each later one sits PLAIN_PAIR_OFFSET further along),
       corridor is how many earlier ones already took a route round the nodes, so a
       later one takes the next-nearest gap. Returns anchors, vertices and the polyline. */
    function planPlainRoute(S, T, rows, width, height, slot, arcMax) {
        let tOff = slot.tOff || 0;
        let sc = { x: S.x + S.w / 2, y: S.y + S.h / 2 }, tc = { x: T.x + T.w / 2, y: T.y + T.h / 2 };
        let off = slot.index * PLAIN_PAIR_OFFSET;
        let clampV = function(v) {
            return { x: Math.min(width - PLAIN_EDGE, Math.max(PLAIN_EDGE, v.x)), y: Math.min(height, Math.max(0, v.y)) };
        };
        let rowOf = function(r) {
            for (let i = 0; i < rows.length; i++) { if (rows[i].rects.indexOf(r) >= 0) return i; }
            return -1;
        };
        let iS = rowOf(S), iT = rowOf(T);
        let A, B, line = null, vertices = [], srcSide, tgtSide, corridor = false;
        if (S === T || iS === iT) {
            /* Same row: both ends on the top edge, an arc over the row between them. */
            let sx = sc.x + (S === T ? -12 : off), tx = tc.x + (S === T ? 12 : off + tOff);
            A = { x: sx, y: S.y }; B = { x: tx, y: T.y };
            srcSide = tgtSide = 'top';
            let span = Math.abs(tx - sx);
            let h = Math.min(arcMax, 5 + 2 * Math.round(span / 100)) + 4 * slot.index;
            let top = Math.min(S.y, T.y);
            line = [A, clampV({ x: sx, y: top - h }), clampV({ x: tx, y: top - h }), B];
        } else {
            let down = iT > iS, step = down ? 1 : -1;
            srcSide = down ? 'bottom' : 'top'; tgtSide = down ? 'top' : 'bottom';
            A = { x: sc.x + off, y: down ? S.y + S.h : S.y };
            B = { x: tc.x + off + tOff, y: down ? T.y : T.y + T.h };
            let others = [];
            rows.forEach(function(row) { row.rects.forEach(function(r) { if (r !== S && r !== T) others.push(r); }); });
            /* A straight line is kept only when it is steep enough not to graze the
               band edges it crosses and clear of every other node. */
            let steep = Math.abs(B.y - A.y) >= 0.45 * Math.abs(B.x - A.x);
            let blocked = !steep || others.some(function(r) { return plainSegmentHitsRect(A, B, r, PLAIN_CLEARANCE + 2); });
            if (blocked) {
                corridor = true;
                let midX = (A.x + B.x) / 2;
                let pts = [A];
                let chan = function(j) { return plainChannel(rows, step > 0 ? iS + j : iS - j - 1); };
                pts.push({ x: A.x, y: chan(0) });
                let m = Math.abs(iT - iS) - 1;
                for (let j = 1; j <= m; j++) {
                    let gaps = plainRowGaps(rows[iS + step * j].rects, midX, width);
                    /* the next-nearest gap; when the row has no more, the same gap, shifted
                       along inside it so the two connections do not share a line */
                    let gx = midX;
                    if (gaps.length) {
                        let g = gaps[Math.min(slot.corridor, gaps.length - 1)];
                        let extra = Math.max(0, slot.corridor - gaps.length + 1);
                        gx = Math.min(g.hi - 2, Math.max(g.lo + 2, g.x + extra * PLAIN_PAIR_OFFSET));
                        if (extra && gx === g.hi - 2 && g.x + extra * PLAIN_PAIR_OFFSET > g.hi - 2) gx = Math.max(g.lo + 2, g.x - extra * PLAIN_PAIR_OFFSET);
                    }
                    pts.push({ x: gx, y: chan(j - 1) });
                    pts.push({ x: gx, y: chan(j) });
                }
                pts.push({ x: B.x, y: chan(m) });
                pts.push(B);
                /* drop repeated points and points that sit on a straight run */
                let clean = [];
                pts.forEach(function(p) {
                    let q = clean[clean.length - 1];
                    if (q && Math.abs(q.x - p.x) < 1 && Math.abs(q.y - p.y) < 1) return;
                    clean.push(p);
                });
                line = [clean[0]];
                for (let i = 1; i < clean.length - 1; i++) {
                    let p = clean[i - 1], q = clean[i], n = clean[i + 1];
                    let straight = (Math.abs(p.x - q.x) < 1 && Math.abs(q.x - n.x) < 1) || (Math.abs(p.y - q.y) < 1 && Math.abs(q.y - n.y) < 1);
                    if (!straight) line.push(clampV(q));
                }
                line.push(clean[clean.length - 1]);
            }
        }
        if (line && line.length > 2) vertices = plainSmoothVertices(line, PLAIN_CORNER, PLAIN_STEP);
        return { A: A, B: B, line: line || [A, B], srcSide: srcSide, tgtSide: tgtSide, off: off, tOff: tOff, vertices: vertices, corridor: corridor };
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

        /* ── Opt-in state: every branch below is dead unless its option was passed ── */
        let banded = opts.layout === 'bands';
        let compact = opts.nodeStyle === 'compact';
        let useTokens = opts.tokens === true;
        let plain = opts.connectionStyle === 'plain';
        let centreRows = opts.rowAlign === 'centre';
        let lastPaperHeight = 0;
        let selectedId = null, centreId = null, highlighted = {};
        let resizeObserver = null, resizeFrame = 0;

        /* --layer-* custom properties hold bare HSL triples ("263 70% 50%"). */
        function tokenColour(name, alpha) {
            let raw = '';
            try { raw = getComputedStyle(containerEl).getPropertyValue(name).trim(); } catch (e) { raw = ''; }
            if (!raw) return null;
            return 'hsl(' + raw + (alpha != null ? ' / ' + alpha : '') + ')';
        }

        function applyTokens(cell, layer, isZone) {
            let stroke = tokenColour('--layer-' + layer);
            if (!stroke) return;
            if (isZone) {
                cell.attr('body/fill', tokenColour('--layer-' + layer, 0.14));
                cell.attr('body/stroke', stroke);
                cell.attr('label/fill', tokenColour('--layer-' + layer + '-emphasis') || stroke);
                return;
            }
            cell.attr('body/fill', tokenColour('--layer-' + layer, 0.2));
            cell.attr('body/stroke', stroke);
            let text = tokenColour('--foreground');
            if (text) cell.attr('nameLabel/fill', text);
        }

        function compactify(node, width) {
            if (SPECIAL_TYPES[node.get('elType')]) return;
            node.resize(width, NODE_H_COMPACT);
            ['accentBar', 'iconBox', 'typeIcon', 'typeLabel', 'intelligenceBadge',
             'resizeHandle', 'resizeHandleTL', 'resizeHandleTR', 'resizeHandleBL',
             'resizeHandleT', 'resizeHandleR', 'resizeHandleB', 'resizeHandleL'].forEach(function(sel) {
                node.attr(sel + '/display', 'none');
            });
            node.removePorts();
            node.attr('nameLabel/x', Math.round(width / 2));
            node.attr('nameLabel/y', Math.round(NODE_H_COMPACT / 2));
            node.attr('nameLabel/textAnchor', 'middle');
            node.attr('nameLabel/textVerticalAnchor', 'middle');
            node.attr('nameLabel/textWrap', { width: -16, maxLineCount: 2, ellipsis: true });
        }

        /* Provenance styling for one edge. A no-op unless the relationship names a
           provenance (an existing payload that happens to carry a `key` is untouched).
           Derived edges are dashed AND carry a text badge; colour never carries the
           meaning on its own.
           Platform vocabulary (see derived_relationship.py and intelligence/services/
           query_service.py): a worked-out fact carries provenance 'derivation' and/or
           kind 'derived'; a genuinely recorded fact carries provenance 'explicit'. Any
           other value is unrecognised and must NOT render with the confident "recorded"
           styling -- it falls back to the safer, dashed "worked out" treatment. */
        function decorateEdge(link, rel) {
            if (!rel || !rel.provenance) return;
            let isDerived = rel.provenance === 'derivation' || rel.kind === 'derived';
            let isExplicit = rel.provenance === 'explicit';
            let kind = isDerived ? 'derived' : (isExplicit ? 'recorded' : 'derived');
            link.set('provenance', kind);
            link.set('stale', rel.stale === true);
            link.set('edgeKey', rel.key != null ? rel.key : (rel.id != null ? rel.id : null));
            link.attr('root/data-kind', kind);
            if (link.get('edgeKey') != null) link.attr('root/data-edge', String(link.get('edgeKey')));
            if (kind === 'derived') {
                link.attr('line/strokeDasharray', plain ? '5,5' : '4,4');
                let text = rel.stale === true ? '◷ Worked out, may be out of date' : 'Worked out';
                let ink = plain ? plainInk() : null;
                link.appendLabel({
                    attrs: {
                        text: Object.assign({ text: text, fontSize: plain ? 11 : 10, fontWeight: 600, fontFamily: 'Inter, system-ui, sans-serif', fill: plain ? ink.text : '#1a1a1a' }, plain ? { style: { textTransform: 'uppercase' } } : {}),
                        rect: Object.assign({ fill: plain ? ink.badgeFill : '#fff', stroke: plain ? ink.stroke : '#1a1a1a', strokeWidth: plain ? 1 : 0.5, strokeDasharray: plain ? '' : '3,2', rx: plain ? 6 : 3, ry: plain ? 6 : 3 }, plain ? { x: -(text.length * 7.6 + 16) / 2, y: -10, width: text.length * 7.6 + 16, height: 20 } : { ref: 'text', refWidth: 8, refHeight: 4, refX: -4, refY: -2 }),
                    },
                    position: { distance: 0.5, offset: plain ? 0 : 14 },
                });
            }
        }

        function makeLink(src, tgt, rel) {
            let link = createLink(src, tgt, rel.type || 'association', rel.id);
            if (plain) stylePlainLink(link);
            decorateEdge(link, rel);
            return link;
        }

        function styleNode(node, layer, width) {
            if (plain) plainNodeChrome(node);
            else if (useTokens) applyTokens(node, layer, false);
            if (compact) compactify(node, width || 200);
        }

        function bandWidth() {
            let w = containerEl.clientWidth || (containerEl.getBoundingClientRect && containerEl.getBoundingClientRect().width) || 0;
            return Math.max(w, 120);
        }

        /* A relationship's stable identity for change detection and matching. Most
           payloads carry `key` or `id`; a relationship that carries neither still gets
           a deterministic (if not guaranteed-unique across true duplicates) synthetic
           key from its endpoints and type, so it renders instead of being dropped. */
        function syntheticEdgeKey(rel) {
            return 'syn\u0001' + rel.source_id + '\u0001' + rel.target_id + '\u0001' + (rel.type || '');
        }
        function edgeKeyOf(rel) {
            if (rel.key != null) return rel.key;
            if (rel.id != null) return rel.id;
            return syntheticEdgeKey(rel);
        }
        function linkKey(l) { return l.get('edgeKey') != null ? l.get('edgeKey') : l.get('relId'); }

        function elementSignature(el) {
            return [el.name, el.type, String(el.layer || ''), el.depth].join('\u0001');
        }
        function relSignature(rel) {
            return [rel.source_id, rel.target_id, rel.type, rel.provenance || '', rel.stale === true].join('\u0001');
        }

        function nodeCells() {
            return graph.getElements().filter(function(c) { return c.get('elementId') != null && !c.get('isLayerZone'); });
        }

        function paintSelection() {
            nodeCells().forEach(function(c) {
                let id = c.get('elementId');
                let isSel = id === selectedId, isCentre = id === centreId;
                c.attr('root/data-selected', isSel ? 'true' : null);
                c.attr('root/data-centre', isCentre ? 'true' : null);
                /* Capture the node's own original stroke width the first time it is
                   painted (mirrors the link base-width capture below), so clearing
                   selection restores what the node actually had, not a flat guess. */
                let base = c.get('baseStrokeWidth');
                if (base == null) { base = c.attr('body/strokeWidth') || 1; c.set('baseStrokeWidth', base); }
                if (plain) paintPlainNode(c, isSel || isCentre);
                else c.attr('body/strokeWidth', isCentre ? 4 : (isSel ? 3 : base));
            });
            graph.getLinks().forEach(function(l) {
                let k = linkKey(l);
                let on = k != null && highlighted[String(k)] === true;
                l.attr('root/data-highlighted', on ? 'true' : null);
                let base = l.get('baseStrokeWidth');
                if (base == null) { base = l.attr('line/strokeWidth') || 2; l.set('baseStrokeWidth', base); }
                l.attr('line/strokeWidth', on ? base + 1.5 : base);
                if (plain) paintPlainLink(l, on);
            });
        }

        /* ── Plain connection style: colours, link restyle and routing ── */
        function plainInk() {
            return {
                stroke: tokenColour('--border') || 'currentColor',
                line: tokenColour('--muted-foreground') || 'currentColor',
                highlight: tokenColour('--ring') || 'currentColor',
                text: tokenColour('--muted-foreground') || 'currentColor',
                badgeFill: tokenColour('--card') || 'Canvas',
                foreground: tokenColour('--foreground') || 'currentColor',
                accent: tokenColour('--accent') || tokenColour('--muted') || 'none',
            };
        }

        /* Node cards, band zones and hover for the plain style: token colours only, a
           1px hairline, no tint and no shadow. The centre element and a selected one
           get a 2px primary ring held 2px off the card. */
        function scrubColourLiterals(cell) {
            let walk = function(obj, path) {
                Object.keys(obj).forEach(function(k) {
                    let v = obj[k];
                    if (v && typeof v === 'object') walk(v, path.concat(k));
                    else if (typeof v === 'string' && /^(#[0-9a-fA-F]{3,8}|rgba?\(.*)$/.test(v)) cell.attr(path.concat(k).join('/'), 'currentColor');
                });
            };
            walk(cell.attr(), []);
        }

        function plainNodeChrome(node) {
            let ink = plainInk();
            scrubColourLiterals(node);
            node.attr('accentBar/display', 'none');
            node.attr('iconBox/fill', 'none');
            node.attr('iconBox/stroke', 'none');
            node.attr('body/fill', tokenColour('--card') || 'none');
            node.attr('body/stroke', ink.stroke);
            node.attr('body/strokeWidth', 1);
            node.attr('body/strokeDasharray', '');
            node.attr('body/rx', 6);
            node.attr('body/ry', 6);
            node.attr('body/opacity', 1);
            node.attr('body/style', { transition: 'fill 150ms ease-out' });
            let markup = node.get('markup') || node.markup;
            if (Array.isArray(markup) && !markup.some(function(m) { return m.selector === 'ring'; })) {
                node.set('markup', markup.concat([{ tagName: 'rect', selector: 'ring' }]));
            }
            node.attr('ring', { x: -3, y: -3, refWidth: 6, refHeight: 6, rx: 9, ry: 9, fill: 'none', strokeWidth: 2, pointerEvents: 'none', display: 'none' });
            node.attr('nameLabel/fill', ink.foreground);
            node.attr('nameLabel/fontSize', 14);
            node.attr('nameLabel/fontWeight', 600);
            node.attr('typeLabel/fill', ink.text);
            node.attr('typeLabel/fontSize', 12);
        }

        function plainZoneChrome(zone) {
            let ink = plainInk();
            let muted = tokenColour('--muted', 0.25);
            zone.attr('body/fill', muted || 'none');
            zone.attr('body/stroke', ink.stroke);
            zone.attr('body/strokeWidth', 1);
            zone.attr('body/strokeDasharray', '');
            zone.attr('body/opacity', 1);
            zone.attr('body/rx', 6);
            zone.attr('body/ry', 6);
            zone.attr('label/fill', ink.text);
            zone.attr('label/fontSize', 10);
            zone.attr('label/fontWeight', 600);
            zone.attr('label/letterSpacing', '0.08em');
            zone.attr('label/style', { textTransform: 'uppercase' });
        }

        function paintPlainNode(node, ringed) {
            if (ringed) node.attr('ring/stroke', plainInk().highlight);
            let display = ringed ? 'block' : 'none';
            if (node.attr('ring/display') !== display) node.attr('ring/display', display);
        }

        function paintPlainLink(link, on) {
            let ink = plainInk();
            let c = on ? ink.highlight : ink.line;
            if (link.attr('line/stroke') !== c) link.attr('line/stroke', c);
            let m = link.attr('line/targetMarker');
            if (!m || m.fill !== c || m.stroke !== c) {
                link.attr('line/targetMarker', { type: 'path', d: markerPath('filled-arrow'), fill: c, stroke: c, strokeWidth: 1 });
            }
        }

        /* Restyle a freshly created link: no relationship word, one filled arrowhead,
           solid unless worked out, token colours, and the engine-routed pair
           normal router + smooth connector (the Composer editor's own vocabulary). */
        function stylePlainLink(link) {
            link.labels([]);
            link.removeAttr('line/sourceMarker');
            link.attr('line/strokeWidth', 1);
            link.attr('line/strokeDasharray', '');
            link.set('router', { name: 'normal' });
            link.set('connector', { name: 'smooth' });
            paintPlainLink(link, false);
        }

        function plainSize() {
            let w = bandWidth();
            let h = lastPaperHeight;
            if (!h) { try { h = paper.getComputedSize().height; } catch (e) { h = 0; } }
            return { width: w, height: h || opts.height || 400 };
        }

        function sameJson(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

        /* Put the "Worked out" badge where it sits clear of every node: the point
           along the drawn polyline nearest the middle whose badge-sized box touches
           no node and stays inside the drawing. */
        function placePlainBadge(link, plan, rects, width, taken, others) {
            let labels = link.labels();
            if (!labels.length) return;
            let text = labels[0].attrs && labels[0].attrs.text ? String(labels[0].attrs.text.text) : '';
            let bw = text.length * 7.6 + 16, bh = 20;
            let pts = plan.line;
            let lens = [0];
            for (let i = 1; i < pts.length; i++) lens.push(lens[i - 1] + Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y));
            let total = lens[lens.length - 1] || 1;
            let at = function(d) {
                let t = d * total;
                for (let i = 1; i < pts.length; i++) {
                    if (t <= lens[i] || i === pts.length - 1) {
                        let seg = (lens[i] - lens[i - 1]) || 1, f = Math.min(1, Math.max(0, (t - lens[i - 1]) / seg));
                        return { x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * f, y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * f };
                    }
                }
                return pts[pts.length - 1];
            };
            let best = 0.5, chosen = null;
            let tries = [0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.18, 0.82];
            for (let k = 0; k < tries.length; k++) {
                let p = at(tries[k]);
                let box = { x: p.x - bw / 2 - 3, y: p.y - bh / 2 - 3, w: bw + 6, h: bh + 6 };
                let hits = function(r) {
                    return box.x < r.x + r.w && box.x + box.w > r.x && box.y < r.y + r.h && box.y + box.h > r.y;
                };
                let onOther = others.some(function(o) {
                    if (o.link === link) return false;
                    for (let i = 1; i < o.line.length; i++) { if (plainSegmentHitsRect(o.line[i - 1], o.line[i], { x: box.x, y: box.y, w: box.w, h: box.h }, 0)) return true; }
                    return false;
                });
                if (box.x >= 0 && box.x + box.w <= width && !rects.some(hits) && !taken.some(hits) && !onOther) { best = tries[k]; chosen = box; break; }
            }
            if (chosen) taken.push({ x: chosen.x, y: chosen.y, w: chosen.w, h: chosen.h });
            let pos = { distance: best, offset: 0 };
            if (!sameJson(link.prop('labels/0/position'), pos)) link.prop('labels/0/position', pos);
        }

        /* Recompute every plain link's route. Called after every layout, update and
           resize: moving one node changes the obstacles for every link, not only its own. */
        function routePlain() {
            if (!plain) return;
            let size = plainSize();
            let rects = nodeCells().map(function(c) {
                let b = c.getBBox();
                return { id: c.id, x: b.x, y: b.y, w: b.width, h: b.height };
            });
            let rows = plainRows(rects);
            let taken = graph.getElements().filter(function(c) { return c.get('isLayerZone'); }).map(function(z) {
                let b = z.getBBox();
                return { x: b.x, y: b.y, w: b.width, h: PLAIN_HEADER };
            });
            let byId = {};
            rects.forEach(function(r) { byId[r.id] = r; });
            let groups = {};
            graph.getLinks().forEach(function(l) {
                let s = l.source().id, t = l.target().id;
                if (!byId[s] || !byId[t]) return;
                let g = [s, t].sort().join('|');
                (groups[g] = groups[g] || []).push(l);
            });
            /* Pass 1 plans every link; connections that arrive on the same side of the same
               node are then spread (recorded first, each later one 13px further along) and
               planned again, so no two arrivals share a stretch of line. */
            let items = [];
            Object.keys(groups).forEach(function(g) {
                let list = groups[g].slice().sort(function(a, b) {
                    let ra = a.get('provenance') === 'derived' ? 1 : 0, rb = b.get('provenance') === 'derived' ? 1 : 0;
                    return (ra - rb) || String(linkKey(a)).localeCompare(String(linkKey(b)));
                });
                let corridors = 0;
                list.forEach(function(link, index) {
                    let sId = link.source().id, tId = link.target().id;
                    let slot = { index: index, corridor: corridors, tOff: 0 };
                    let plan = planPlainRoute(byId[sId], byId[tId], rows, size.width, size.height, slot, banded ? 8 : 24);
                    if (plan.corridor) corridors++;
                    items.push({ link: link, sId: sId, tId: tId, slot: slot, plan: plan });
                });
            });
            let arrivals = {};
            items.slice().sort(function(a, b) {
                let ra = a.link.get('provenance') === 'derived' ? 1 : 0, rb = b.link.get('provenance') === 'derived' ? 1 : 0;
                return (ra - rb) || String(linkKey(a.link)).localeCompare(String(linkKey(b.link)));
            }).forEach(function(it) {
                if (it.sId === it.tId) return;
                let k = it.tId + '|' + it.plan.tgtSide;
                let n = arrivals[k] = (arrivals[k] || 0);
                arrivals[k]++;
                let want = n * PLAIN_PAIR_OFFSET - it.plan.off;
                if (want > 0) {
                    it.slot.tOff = want;
                    it.plan = planPlainRoute(byId[it.sId], byId[it.tId], rows, size.width, size.height, it.slot, banded ? 8 : 24);
                }
            });
            items.forEach(function(it) {
                let plan = it.plan, link = it.link;
                /* Measure from the node's own body when it has one, so a resize handle or port
                   sticking out of the box does not hold the connection off the visible border. */
                let src = { id: it.sId, anchor: { name: plan.srcSide, args: { dx: plan.off } }, connectionPoint: { name: 'boundary' } };
                let tgt = { id: it.tId, anchor: { name: plan.tgtSide, args: { dx: plan.off + plan.tOff } }, connectionPoint: { name: 'boundary' } };
                if (it.sId === it.tId) { src.anchor.args.dx = -12; tgt.anchor.args.dx = 12; }
                if (graph.getCell(it.sId).attr('body')) src.selector = 'body';
                if (graph.getCell(it.tId).attr('body')) tgt.selector = 'body';
                if (!sameJson(link.source(), src)) link.source(src);
                if (!sameJson(link.target(), tgt)) link.target(tgt);
                if (!sameJson(link.vertices(), plan.vertices)) link.vertices(plan.vertices);
            });
            let drawn = items.map(function(it) { return { link: it.link, line: it.plan.line }; });
            items.forEach(function(it) { placePlainBadge(it.link, it.plan, rects, size.width, taken, drawn); });
        }

        /* Banded layout: draws the zones and positions every node. Returns nothing. */
        function layoutBands() {
            let elements = nodeCells().map(function(c) { return c.get('modelEl'); });
            let width = bandWidth();
            let layoutOpts;
            if (plain || centreRows) {
                layoutOpts = {};
                if (centreRows) layoutOpts.rowAlign = 'centre';
                if (plain) { layoutOpts.colGap = PLAIN_COL_GAP; layoutOpts.rowGap = PLAIN_ROW_GAP; layoutOpts.header = PLAIN_HEADER; }
            }
            let plan = computeBandLayout(elements, opts.bands, width, compact, layoutOpts);
            /* Reuse a zone that is already drawn; only touch what moved or resized. */
            let drawn = {};
            graph.getCells().filter(function(c) { return c.get('isLayerZone'); }).forEach(function(z) { drawn[z.get('zoneLayer')] = z; });
            let wanted = {};
            plan.zones.forEach(function(z) {
                wanted[z.layer] = true;
                let zone = drawn[z.layer];
                if (zone) {
                    let pos = zone.position(), size = zone.size();
                    if (pos.x !== z.x || pos.y !== z.y) zone.position(z.x, z.y);
                    if (size.width !== z.width || size.height !== z.height) zone.resize(z.width, z.height);
                    if (zone.attr('label/text') !== z.label && z.label) zone.attr('label/text', z.label);
                } else {
                    zone = createLayerZone(z.layer, z.x, z.y, z.width, z.height, { label: z.label });
                    zone.attr('root/data-band', z.layer);
                    graph.addCell(zone);
                    zone.toBack();
                }
                if (plain) plainZoneChrome(zone);
                else if (useTokens) applyTokens(zone, z.layer, true);
            });
            Object.keys(drawn).forEach(function(l) { if (!wanted[l]) drawn[l].remove(); });
            nodeCells().forEach(function(c) {
                let pl = plan.placements[c.get('elementId')];
                if (!pl) return;
                let pos = c.position();
                if (pos.x !== pl.x || pos.y !== pl.y) c.position(pl.x, pl.y);
                c.set('band', pl.band);
                /* Junction/Grouping/Location/Note still get positioned into their band,
                   but never resized -- the same skip-list compactify uses, so a special
                   node's own notation size (e.g. the small diamond junction glyph) is
                   never stretched or squashed to fit the row. */
                if (SPECIAL_TYPES[c.get('elType')]) return;
                if (compact) {
                    if (c.size().width !== pl.width) {
                        c.resize(pl.width, pl.height);
                        c.attr('nameLabel/x', Math.round(pl.width / 2));
                    }
                } else if (c.size().width !== pl.width) {
                    c.resize(pl.width, pl.height);
                }
            });
            paper.setDimensions(opts.width || '100%', plan.height);
            lastPaperHeight = plan.height;
        }

        function relayout() {
            if (banded) layoutBands();
            if (plain) { routePlain(); paintSelection(); }
        }

        function updateModel(model) {
            model = model || {};
            let elements = model.elements || [];
            let relationships = model.relationships || [];
            let width = banded ? computeBandLayout([], opts.bands, bandWidth(), compact).nodeWidth : 200;
            let existing = {};
            nodeCells().forEach(function(c) { existing[c.get('elementId')] = c; });
            
            let wantEl = {};
            elements.forEach(function(el) { wantEl[el.id] = el; });
            let wantRel = {};
            relationships.forEach(function(r) { if (edgeKeyOf(r) != null) wantRel[String(edgeKeyOf(r))] = r; });

            /* Remove what is gone or changed. Removing a node removes its links too. */
            Object.keys(existing).forEach(function(id) {
                let el = wantEl[id];
                if (!el || elementSignature(el) !== existing[id].get('signature')) existing[id].remove();
            });
            graph.getLinks().forEach(function(l) {
                let k = linkKey(l);
                let rel = k != null ? wantRel[String(k)] : null;
                /* No signature guard here (unlike the old check): a link with no
                   recorded signature yet (e.g. one created before this engine tracked
                   signatures) must be treated as changed, not kept as-is, or a relationship
                   whose visual state changes is never actually replaced on update(). */
                if (!rel || relSignature(rel) !== l.get('signature')) l.remove();
            });

            /* Add what is new (or was just removed because it changed). */
            let cellMap = {};
            nodeCells().forEach(function(c) { cellMap[c.get('elementId')] = c; });
            let fresh = 0;
            elements.forEach(function(el, i) {
                if (cellMap[el.id]) return;
                let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                let elType = el.type || 'ApplicationComponent';
                /* One plain card for every type: the notation shapes (chamfered, folded,
                   layered) carry their own outlines and shadows. The element keeps its type. */
                let node = createNode(el.id, el.name, plain && !SPECIAL_TYPES[elType] ? 'ApplicationComponent' : elType, layer, 40 + (i % 4) * 240, 40 + Math.floor(i / 4) * 160);
                if (plain) node.set('elType', elType);
                node.set('modelEl', el);
                node.set('signature', elementSignature(el));
                styleNode(node, layer, width);
                node.set('baseStrokeWidth', node.attr('body/strokeWidth') || 1);
                graph.addCell(node);
                cellMap[el.id] = node;
                canvasElements[el.id] = el;
                fresh++;
            });
            Object.keys(canvasElements).forEach(function(id) { if (!wantEl[id]) delete canvasElements[id]; });

            let linked = {};
            graph.getLinks().forEach(function(l) { if (linkKey(l) != null) linked[String(linkKey(l))] = true; });
            relationships.forEach(function(rel) {
                let k = edgeKeyOf(rel);
                let src = cellMap[rel.source_id], tgt = cellMap[rel.target_id];
                if (k == null || !src || !tgt || linked[String(k)]) return;
                let link = makeLink(src, tgt, rel);
                if (link.get('edgeKey') == null) link.set('edgeKey', k);
                link.set('signature', relSignature(rel));
                graph.addCell(link);
                linked[String(k)] = true;
            });

            relayout();
            paintSelection();
            return fresh;
        }

        function scheduleRelayout() {
            if (resizeFrame) return;
            resizeFrame = requestAnimationFrame(function() { resizeFrame = 0; relayout(); });
        }

        if (banded && typeof ResizeObserver !== 'undefined') {
            resizeObserver = new ResizeObserver(scheduleRelayout);
            resizeObserver.observe(containerEl);
        }

        if (plain) {
            paper.on('element:mouseenter', function(view) {
                if (view.model.get('isLayerZone') || view.model.get('elementId') == null) return;
                let a = plainInk().accent;
                if (a !== 'none') view.model.attr('body/fill', a);
            });
            paper.on('element:mouseleave', function(view) {
                if (view.model.get('isLayerZone') || view.model.get('elementId') == null) return;
                view.model.attr('body/fill', tokenColour('--card') || 'none');
            });
        }

        if (typeof opts.onSelect === 'function') {
            paper.on('element:pointerclick', function(view) {
                let id = view.model.get('elementId');
                if (id == null) return;
                selectedId = id;
                paintSelection();
                opts.onSelect(id, view.model.get('modelEl') || canvasElements[id] || null);
            });
        }

        return {
            graph: graph,
            paper: paper,
            mode: mode,

            loadElements: function(elements, relationships) {
                if (banded) {
                    graph.clear();
                    canvasElements = {};
                    updateModel({ elements: elements || [], relationships: relationships || [] });
                    return;
                }
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
                    let elType = el.type || 'ApplicationComponent';
                    let node = createNode(el.id, el.name, plain && !SPECIAL_TYPES[elType] ? 'ApplicationComponent' : elType, layer, x, y);
                    if (plain) node.set('elType', elType);
                    if (useTokens || compact) { node.set('modelEl', el); styleNode(node, layer, 200); }
                    /* Always recorded (not just under useTokens/compact) so a later
                       update() call -- which is not itself gated on banded/useTokens/
                       compact -- has a real signature to diff against instead of treating
                       every node as freshly changed. */
                    node.set('signature', elementSignature(el));
                    graph.addCell(node);
                    cellMap[el.id] = node;
                    canvasElements[el.id] = el;
                });

                (relationships || []).forEach(function(rel) {
                    let src = cellMap[rel.source_id];
                    let tgt = cellMap[rel.target_id];
                    if (!src || !tgt) return;
                    let link = makeLink(src, tgt, rel);
                    /* Same reasoning as the node signature above: give every link a
                       stable edgeKey (synthetic when the relationship has neither key
                       nor id) and a signature, so update() can both find it again and
                       tell whether it changed. */
                    if (link.get('edgeKey') == null) link.set('edgeKey', edgeKeyOf(rel));
                    link.set('signature', relSignature(rel));
                    graph.addCell(link);
                });
                if (plain) relayout();
            },

            fitToContent: function() {
                paper.scaleContentToFit({ padding: 20, maxScale: 1.2, minScale: 0.3 });
            },

            getElementCount: function() { return Object.keys(canvasElements).length; },
            getRelCount: function() { return graph.getLinks().length; },

            update: function(model) { return updateModel(model); },

            setSelected: function(id) { selectedId = id == null ? null : id; paintSelection(); },

            setCentre: function(id) { centreId = id == null ? null : id; paintSelection(); },

            setHighlightedEdges: function(keys) {
                highlighted = {};
                (keys instanceof Set ? Array.from(keys) : (keys || [])).forEach(function(k) { highlighted[String(k)] = true; });
                paintSelection();
            },

            /* Node rectangles in paper (container-relative) pixels, top-to-bottom then
               left-to-right, for an overlay that lives outside this engine. */
            getNodeBounds: function() {
                return nodeCells().map(function(c) {
                    let r = paper.localToPaperRect(c.getBBox());
                    return { id: c.get('elementId'), x: r.x, y: r.y, width: r.width, height: r.height };
                }).sort(function(a, b) { return (a.y - b.y) || (a.x - b.x); });
            },

            resize: function() { relayout(); },

            destroy: function() {
                if (resizeObserver) { resizeObserver.disconnect(); resizeObserver = null; }
                if (resizeFrame) { cancelAnimationFrame(resizeFrame); resizeFrame = 0; }
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
        computeBandLayout: computeBandLayout,
        ZONE_COLORS: ZONE_COLORS,
        applyZoneStyle: applyZoneStyle,
    };
})();
