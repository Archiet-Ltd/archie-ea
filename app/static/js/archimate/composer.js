/**
 * ArchiMate Architecture Composer — JointJS-based unified diagram tool
 *
 * Combines editing (palette, drag-drop, relationship creation) with
 * viewpoint viewing (layer banding, neighbor focus, detail panel).
 *
 * Mode: "edit" = full composer, "view" = read-only viewpoint viewer.
 */
function composerApp() {
    'use strict';

    /* ── Delegate rendering constants/functions to ComposerRenderer ── */
    let LAYER_COLORS = ComposerRenderer.LAYER_COLORS;
    let DEFAULT_LAYER = ComposerRenderer.DEFAULT_LAYER;
    let layerColor = ComposerRenderer.layerColor;

    let PALETTE = ComposerRenderer.PALETTE;

    /* ── Viewpoint → allowed palette layers ────────────── */
    let VIEWPOINT_PALETTE_MAP = {
        // ── Existing viewpoints ──────────────────────────────────────────────
        'application_cooperation':   ['Application'],
        'technology':                ['Technology', 'Physical'],
        'motivation':                ['Motivation', 'Strategy'],
        'strategy':                  ['Strategy', 'Motivation'],
        'implementation_migration':  ['Implementation & Migration'],
        'migration':                 ['Implementation & Migration'],
        'implementation_deployment': ['Technology', 'Implementation & Migration', 'Physical'],
        'layered':       ['Business', 'Application', 'Technology', 'Physical', 'Motivation', 'Strategy', 'Implementation & Migration', 'Composite', 'Diagram Tools'],
        'information':               ['Application', 'Technology'],
        'information_structure':     ['Application', 'Technology'],
        'basic':         ['Business', 'Application', 'Technology', 'Physical', 'Motivation', 'Strategy', 'Implementation & Migration', 'Composite', 'Diagram Tools'],
        'custom':        ['Business', 'Application', 'Technology', 'Physical', 'Motivation', 'Strategy', 'Implementation & Migration', 'Composite', 'Diagram Tools'],
        '':              ['Business', 'Application', 'Technology', 'Physical', 'Motivation', 'Strategy', 'Implementation & Migration', 'Composite', 'Diagram Tools'],
        // ── CMP-063: 7 missing ArchiMate 3.2 viewpoints ─────────────────────
        'organization':                  ['Business', 'Composite'],
        'business_process':              ['Business', 'Composite'],
        'business_process_cooperation':  ['Business', 'Composite'],
        'product':                       ['Business', 'Application', 'Composite'],
        'service_realization':           ['Business', 'Application', 'Technology', 'Composite'],
        'capability':                    ['Strategy', 'Motivation', 'Composite'],
        'value_chain':                   ['Business', 'Strategy', 'Composite'],
        // CMP-062: Physical layer viewpoint
        'physical':                      ['Technology', 'Physical', 'Composite'],
        // CMP-063: additional missing viewpoints
        'stakeholder':                   ['Motivation', 'Strategy'],
        'actor_cooperation':             ['Business', 'Composite'],
        'application_usage':             ['Business', 'Application', 'Composite'],
        'technology_usage':              ['Application', 'Technology', 'Composite'],
    };

    let TYPE_TO_LAYER = ComposerRenderer.TYPE_TO_LAYER;
    let guessLayer = ComposerRenderer.guessLayer;
    let REL_STYLES = ComposerRenderer.REL_STYLES;
    let markerPath = ComposerRenderer.markerPath;
    let markerFill = ComposerRenderer.markerFill;

    /* ── Custom Undo/Redo stack (replaces Rappid CommandManager) ─ */
    let UndoStack = {
        _undo: [],
        _redo: [],
        _MAX: 50,
        _recording: true,

        pause: function() { this._recording = false; },
        resume: function() { this._recording = true; },

        push: function(action) {
            if (!this._recording) return;
            this._undo.push(action);
            if (this._undo.length > this._MAX) this._undo.shift();
            this._redo = [];
        },

        canUndo: function() { return this._undo.length > 0; },
        canRedo: function() { return this._redo.length > 0; },

        undo: function() {
            let a = this._undo.pop();
            if (!a) return;
            this._recording = false;
            try { a.undo(); } finally { this._recording = true; }
            this._redo.push(a);
        },

        redo: function() {
            let a = this._redo.pop();
            if (!a) return;
            this._recording = false;
            try { a.redo(); } finally { this._recording = true; }
            this._undo.push(a);
        },

        clear: function() { this._undo = []; this._redo = []; },
    };

    /* ── Layer Y-band ordering ────────────────────────────── */
    let LAYER_Y_ORDER = {
        'strategy': 0, 'motivation': 1, 'business': 2,
        'application': 3, 'technology': 4, 'physical': 5, 'implementation': 6,
    };

    /* ── Element types that support white-box nesting ─────── */
    let CONTAINER_TYPES = {
        ApplicationComponent: true, ApplicationCollaboration: true,
        Node: true, Device: true,
        BusinessProcess: true, BusinessFunction: true,
        Plateau: true, WorkPackage: true,
        Capability: true, ValueStream: true,
        SystemSoftware: true, CommunicationNetwork: true,
        Grouping: true, Location: true,
    };

    function isContainerType(elType) {
        return CONTAINER_TYPES[elType] === true;
    }

    /* ── Built-in starter templates ────────────────────────────── */
    let BUILT_IN_TEMPLATES = [
        {
            id: 'builtin-app-arch',
            name: 'Application Architecture',
            description: '3-layer view: business actors use application services realized by technology infrastructure',
            is_builtin: true,
            elements: [
                { id: 'ba1', name: 'Customer Service Rep', type: 'BusinessActor', layer: 'business', x: 40, y: 40 },
                { id: 'bs1', name: 'Customer Management', type: 'BusinessService', layer: 'business', x: 320, y: 40 },
                { id: 'ac1', name: 'CRM Application', type: 'ApplicationComponent', layer: 'application', x: 40, y: 240 },
                { id: 'as1', name: 'Customer API', type: 'ApplicationService', layer: 'application', x: 320, y: 240 },
                { id: 'do1', name: 'Customer Data', type: 'DataObject', layer: 'application', x: 600, y: 240 },
                { id: 'nd1', name: 'Application Server', type: 'Node', layer: 'technology', x: 40, y: 440 },
                { id: 'ss1', name: 'PostgreSQL', type: 'SystemSoftware', layer: 'technology', x: 320, y: 440 },
                { id: 'dv1', name: 'Database Server', type: 'Device', layer: 'technology', x: 600, y: 440 },
            ],
            relationships: [
                { source: 'ba1', target: 'bs1', type: 'serving' },
                { source: 'bs1', target: 'as1', type: 'realization' },
                { source: 'ac1', target: 'as1', type: 'serving' },
                { source: 'as1', target: 'do1', type: 'access' },
                { source: 'ac1', target: 'nd1', type: 'realization' },
                { source: 'ss1', target: 'do1', type: 'serving' },
                { source: 'ss1', target: 'dv1', type: 'assignment' },
            ],
        },
        {
            id: 'builtin-capability-map',
            name: 'Capability Map',
            description: 'Strategic capabilities driving business outcomes via courses of action',
            is_builtin: true,
            elements: [
                { id: 'g1', name: 'Revenue Growth', type: 'Goal', layer: 'motivation', x: 280, y: 20 },
                { id: 'ca1', name: 'Customer Engagement', type: 'Capability', layer: 'strategy', x: 40, y: 200 },
                { id: 'ca2', name: 'Sales Management', type: 'Capability', layer: 'strategy', x: 280, y: 200 },
                { id: 'ca3', name: 'Service Delivery', type: 'Capability', layer: 'strategy', x: 520, y: 200 },
                { id: 'co1', name: 'Digital Transformation', type: 'CourseOfAction', layer: 'strategy', x: 160, y: 400 },
                { id: 'co2', name: 'Process Automation', type: 'CourseOfAction', layer: 'strategy', x: 440, y: 400 },
                { id: 'r1', name: 'CRM Platform', type: 'Resource', layer: 'strategy', x: 280, y: 580 },
            ],
            relationships: [
                { source: 'ca1', target: 'g1', type: 'realization' },
                { source: 'ca2', target: 'g1', type: 'realization' },
                { source: 'ca3', target: 'g1', type: 'realization' },
                { source: 'co1', target: 'ca1', type: 'realization' },
                { source: 'co1', target: 'ca2', type: 'realization' },
                { source: 'co2', target: 'ca3', type: 'realization' },
                { source: 'r1', target: 'co1', type: 'assignment' },
                { source: 'r1', target: 'co2', type: 'assignment' },
            ],
        },
        {
            id: 'builtin-motivation',
            name: 'Motivation View',
            description: 'Stakeholder concerns drive goals, constrained by principles and requirements',
            is_builtin: true,
            elements: [
                { id: 'sh1', name: 'CIO', type: 'Stakeholder', layer: 'motivation', x: 280, y: 20 },
                { id: 'dr1', name: 'Cost Reduction', type: 'Driver', layer: 'motivation', x: 80, y: 180 },
                { id: 'dr2', name: 'Digital Growth', type: 'Driver', layer: 'motivation', x: 480, y: 180 },
                { id: 'gl1', name: '30% OpEx Savings', type: 'Goal', layer: 'motivation', x: 80, y: 360 },
                { id: 'gl2', name: 'Cloud-First by 2027', type: 'Goal', layer: 'motivation', x: 480, y: 360 },
                { id: 'rq1', name: 'Retire Legacy Apps', type: 'Requirement', layer: 'motivation', x: 80, y: 540 },
                { id: 'pr1', name: 'Buy Before Build', type: 'Principle', layer: 'motivation', x: 480, y: 540 },
            ],
            relationships: [
                { source: 'sh1', target: 'dr1', type: 'association' },
                { source: 'sh1', target: 'dr2', type: 'association' },
                { source: 'dr1', target: 'gl1', type: 'influence' },
                { source: 'dr2', target: 'gl2', type: 'influence' },
                { source: 'gl1', target: 'rq1', type: 'realization' },
                { source: 'gl2', target: 'pr1', type: 'realization' },
                { source: 'pr1', target: 'rq1', type: 'influence' },
            ],
        },
        {
            id: 'builtin-migration',
            name: 'Migration Roadmap',
            description: 'Three-phase plateau plan: Foundation, Migration, Optimisation with deliverables',
            is_builtin: true,
            elements: [
                { id: 'p1', name: 'Phase 1: Foundation', type: 'Plateau', layer: 'implementation', x: 40, y: 40 },
                { id: 'w1', name: 'Infrastructure Setup', type: 'WorkPackage', layer: 'implementation', x: 280, y: 40 },
                { id: 'p2', name: 'Phase 2: Migration', type: 'Plateau', layer: 'implementation', x: 40, y: 220 },
                { id: 'w2', name: 'Data Migration', type: 'WorkPackage', layer: 'implementation', x: 280, y: 220 },
                { id: 'w3', name: 'User Training', type: 'WorkPackage', layer: 'implementation', x: 520, y: 220 },
                { id: 'p3', name: 'Phase 3: Optimise', type: 'Plateau', layer: 'implementation', x: 40, y: 400 },
                { id: 'd1', name: 'Production System', type: 'Deliverable', layer: 'implementation', x: 280, y: 400 },
                { id: 'gp1', name: 'Legacy Decommission', type: 'Gap', layer: 'implementation', x: 520, y: 400 },
            ],
            relationships: [
                { source: 'w1', target: 'p1', type: 'realization' },
                { source: 'p1', target: 'p2', type: 'triggering' },
                { source: 'w2', target: 'p2', type: 'realization' },
                { source: 'w3', target: 'p2', type: 'realization' },
                { source: 'p2', target: 'p3', type: 'triggering' },
                { source: 'd1', target: 'p3', type: 'realization' },
                { source: 'gp1', target: 'p3', type: 'association' },
            ],
        },
        {
            id: 'builtin-tech-deployment',
            name: 'Technology Deployment',
            description: 'Infrastructure layers: load balancer, web tier, app tier, database — fully connected',
            is_builtin: true,
            elements: [
                { id: 'lb', name: 'Load Balancer', type: 'Device', layer: 'technology', x: 280, y: 20 },
                { id: 'ws', name: 'Web Server', type: 'Node', layer: 'technology', x: 80, y: 200 },
                { id: 'ng', name: 'Nginx', type: 'SystemSoftware', layer: 'technology', x: 80, y: 380 },
                { id: 'as', name: 'App Server', type: 'Node', layer: 'technology', x: 480, y: 200 },
                { id: 'sb', name: 'Spring Boot', type: 'SystemSoftware', layer: 'technology', x: 480, y: 380 },
                { id: 'db', name: 'Database Cluster', type: 'Node', layer: 'technology', x: 280, y: 540 },
                { id: 'pg', name: 'PostgreSQL', type: 'SystemSoftware', layer: 'technology', x: 280, y: 700 },
                { id: 'nw', name: 'Corporate LAN', type: 'CommunicationNetwork', layer: 'technology', x: 280, y: 880 },
            ],
            relationships: [
                { source: 'lb', target: 'ws', type: 'serving' },
                { source: 'lb', target: 'as', type: 'serving' },
                { source: 'ng', target: 'ws', type: 'assignment' },
                { source: 'sb', target: 'as', type: 'assignment' },
                { source: 'as', target: 'db', type: 'serving' },
                { source: 'pg', target: 'db', type: 'assignment' },
                { source: 'nw', target: 'lb', type: 'serving' },
            ],
        },
        {
            id: 'builtin-p2p-integration',
            name: 'Point-to-Point Integration',
            description: 'Source app exposes a service consumed by target app via flow relationship',
            is_builtin: true,
            elements: [
                { id: 'sa', name: 'Source Application', type: 'ApplicationComponent', layer: 'application', x: 40, y: 40 },
                { id: 'ds', name: 'Data Export Service', type: 'ApplicationService', layer: 'application', x: 40, y: 220 },
                { id: 'ai', name: 'REST API Interface', type: 'ApplicationInterface', layer: 'application', x: 280, y: 220 },
                { id: 'ta', name: 'Target Application', type: 'ApplicationComponent', layer: 'application', x: 520, y: 40 },
                { id: 'is', name: 'Import Service', type: 'ApplicationService', layer: 'application', x: 520, y: 220 },
                { id: 'do', name: 'Shared Data Object', type: 'DataObject', layer: 'application', x: 280, y: 400 },
            ],
            relationships: [
                { source: 'sa', target: 'ds', type: 'serving' },
                { source: 'ds', target: 'ai', type: 'realization' },
                { source: 'ai', target: 'is', type: 'flow' },
                { source: 'is', target: 'ta', type: 'serving' },
                { source: 'ds', target: 'do', type: 'access' },
                { source: 'is', target: 'do', type: 'access' },
            ],
        },
        {
            id: 'builtin-hub-spoke',
            name: 'Hub-and-Spoke Integration',
            description: 'Central integration hub mediates data flow between source and three target systems',
            is_builtin: true,
            elements: [
                { id: 'src', name: 'Source System', type: 'ApplicationComponent', layer: 'application', x: 40, y: 200 },
                { id: 'hub', name: 'Integration Hub', type: 'ApplicationComponent', layer: 'application', x: 320, y: 200 },
                { id: 'tA', name: 'Target A (CRM)', type: 'ApplicationComponent', layer: 'application', x: 600, y: 40 },
                { id: 'tB', name: 'Target B (ERP)', type: 'ApplicationComponent', layer: 'application', x: 600, y: 200 },
                { id: 'tC', name: 'Target C (DWH)', type: 'ApplicationComponent', layer: 'application', x: 600, y: 360 },
                { id: 'mw', name: 'Message Broker', type: 'SystemSoftware', layer: 'technology', x: 320, y: 420 },
            ],
            relationships: [
                { source: 'src', target: 'hub', type: 'flow' },
                { source: 'hub', target: 'tA', type: 'flow' },
                { source: 'hub', target: 'tB', type: 'flow' },
                { source: 'hub', target: 'tC', type: 'flow' },
                { source: 'mw', target: 'hub', type: 'serving' },
            ],
        },
        // ── Phase B: Business Process Cooperation ──────────────────
        {
            id: 'builtin-business-process',
            name: 'Business Process Cooperation',
            description: 'Phase B — business roles perform processes that realize services and produce objects',
            is_builtin: true,
            elements: [
                { id: 'role1', name: 'Account Manager', type: 'BusinessRole', layer: 'business', x: 40, y: 40 },
                { id: 'role2', name: 'Operations Team', type: 'BusinessRole', layer: 'business', x: 40, y: 200 },
                { id: 'proc1', name: 'Order Processing', type: 'BusinessProcess', layer: 'business', x: 280, y: 40 },
                { id: 'proc2', name: 'Fulfilment', type: 'BusinessProcess', layer: 'business', x: 280, y: 200 },
                { id: 'proc3', name: 'Invoicing', type: 'BusinessProcess', layer: 'business', x: 280, y: 360 },
                { id: 'svc', name: 'Order Management Service', type: 'BusinessService', layer: 'business', x: 560, y: 120 },
                { id: 'obj', name: 'Sales Order', type: 'BusinessObject', layer: 'business', x: 560, y: 300 },
                { id: 'evt', name: 'Order Received', type: 'BusinessEvent', layer: 'business', x: 40, y: 360 },
            ],
            relationships: [
                { source: 'role1', target: 'proc1', type: 'assignment' },
                { source: 'role2', target: 'proc2', type: 'assignment' },
                { source: 'proc1', target: 'proc2', type: 'triggering' },
                { source: 'proc2', target: 'proc3', type: 'triggering' },
                { source: 'proc1', target: 'svc', type: 'realization' },
                { source: 'proc1', target: 'obj', type: 'access' },
                { source: 'evt', target: 'proc1', type: 'triggering' },
            ],
        },
        // ── Phase C: Data / Information Structure ──────────────────
        {
            id: 'builtin-data-architecture',
            name: 'Data Architecture',
            description: 'Phase C — data objects accessed by applications, stored in databases, governed by contracts',
            is_builtin: true,
            elements: [
                { id: 'do1', name: 'Customer Record', type: 'DataObject', layer: 'application', x: 280, y: 40 },
                { id: 'do2', name: 'Order Record', type: 'DataObject', layer: 'application', x: 280, y: 180 },
                { id: 'do3', name: 'Product Catalog', type: 'DataObject', layer: 'application', x: 280, y: 320 },
                { id: 'app1', name: 'CRM System', type: 'ApplicationComponent', layer: 'application', x: 40, y: 40 },
                { id: 'app2', name: 'ERP System', type: 'ApplicationComponent', layer: 'application', x: 40, y: 180 },
                { id: 'app3', name: 'E-Commerce Portal', type: 'ApplicationComponent', layer: 'application', x: 40, y: 320 },
                { id: 'db', name: 'Master Data Store', type: 'Node', layer: 'technology', x: 560, y: 180 },
                { id: 'ct', name: 'Data Sharing Agreement', type: 'Contract', layer: 'business', x: 560, y: 40 },
            ],
            relationships: [
                { source: 'app1', target: 'do1', type: 'access' },
                { source: 'app2', target: 'do2', type: 'access' },
                { source: 'app3', target: 'do3', type: 'access' },
                { source: 'do1', target: 'do2', type: 'association' },
                { source: 'do2', target: 'do3', type: 'association' },
                { source: 'db', target: 'do1', type: 'realization' },
                { source: 'db', target: 'do2', type: 'realization' },
                { source: 'ct', target: 'do1', type: 'association' },
            ],
        },
        // ── Cross-layer: Service Realization ───────────────────────
        {
            id: 'builtin-service-realization',
            name: 'Service Realization',
            description: 'Cross-layer traceability — business service realized by app service realized by technology',
            is_builtin: true,
            elements: [
                { id: 'bsvc', name: 'Customer Onboarding', type: 'BusinessService', layer: 'business', x: 280, y: 40 },
                { id: 'bproc', name: 'KYC Verification', type: 'BusinessProcess', layer: 'business', x: 40, y: 40 },
                { id: 'asvc', name: 'Identity Check API', type: 'ApplicationService', layer: 'application', x: 280, y: 180 },
                { id: 'acomp', name: 'Onboarding Application', type: 'ApplicationComponent', layer: 'application', x: 40, y: 180 },
                { id: 'tsvc', name: 'Container Runtime', type: 'TechnologyService', layer: 'technology', x: 280, y: 320 },
                { id: 'node', name: 'Kubernetes Cluster', type: 'Node', layer: 'technology', x: 40, y: 320 },
                { id: 'art', name: 'Docker Image', type: 'Artifact', layer: 'technology', x: 540, y: 320 },
            ],
            relationships: [
                { source: 'bproc', target: 'bsvc', type: 'realization' },
                { source: 'acomp', target: 'asvc', type: 'realization' },
                { source: 'asvc', target: 'bsvc', type: 'realization' },
                { source: 'node', target: 'tsvc', type: 'realization' },
                { source: 'tsvc', target: 'acomp', type: 'serving' },
                { source: 'art', target: 'acomp', type: 'realization' },
                { source: 'node', target: 'art', type: 'assignment' },
            ],
        },
        // ── Governance: Security Architecture ──────────────────────
        {
            id: 'builtin-security-arch',
            name: 'Security Architecture',
            description: 'Constraints and policies governing application access, authentication, and data protection',
            is_builtin: true,
            elements: [
                { id: 'cst1', name: 'Data Privacy (GDPR)', type: 'Constraint', layer: 'motivation', x: 40, y: 40 },
                { id: 'cst2', name: 'Encryption at Rest', type: 'Constraint', layer: 'motivation', x: 40, y: 180 },
                { id: 'req', name: 'Multi-Factor Authentication', type: 'Requirement', layer: 'motivation', x: 40, y: 320 },
                { id: 'app', name: 'Customer Portal', type: 'ApplicationComponent', layer: 'application', x: 320, y: 100 },
                { id: 'asvc', name: 'Auth Service', type: 'ApplicationService', layer: 'application', x: 320, y: 260 },
                { id: 'db', name: 'Encrypted Data Store', type: 'Node', layer: 'technology', x: 580, y: 100 },
                { id: 'fw', name: 'Web Application Firewall', type: 'Node', layer: 'technology', x: 580, y: 260 },
            ],
            relationships: [
                { source: 'cst1', target: 'app', type: 'realization' },
                { source: 'cst2', target: 'db', type: 'realization' },
                { source: 'req', target: 'asvc', type: 'realization' },
                { source: 'asvc', target: 'app', type: 'serving' },
                { source: 'fw', target: 'app', type: 'serving' },
                { source: 'db', target: 'app', type: 'serving' },
            ],
        },
        // ── Phase A: Stakeholder Concerns ──────────────────────────
        {
            id: 'builtin-stakeholder-concerns',
            name: 'Stakeholder Concerns',
            description: 'Phase A — stakeholders drive assessments that influence goals and shape architectural principles',
            is_builtin: true,
            elements: [
                { id: 'sh1', name: 'CTO', type: 'Stakeholder', layer: 'motivation', x: 40, y: 40 },
                { id: 'sh2', name: 'CISO', type: 'Stakeholder', layer: 'motivation', x: 40, y: 200 },
                { id: 'sh3', name: 'Business Owner', type: 'Stakeholder', layer: 'motivation', x: 40, y: 360 },
                { id: 'dr1', name: 'Cost Reduction', type: 'Driver', layer: 'motivation', x: 280, y: 40 },
                { id: 'dr2', name: 'Regulatory Compliance', type: 'Driver', layer: 'motivation', x: 280, y: 200 },
                { id: 'dr3', name: 'Time to Market', type: 'Driver', layer: 'motivation', x: 280, y: 360 },
                { id: 'as1', name: 'Legacy Complexity High', type: 'Assessment', layer: 'motivation', x: 520, y: 120 },
                { id: 'gl1', name: 'Simplify Application Landscape', type: 'Goal', layer: 'motivation', x: 520, y: 300 },
            ],
            relationships: [
                { source: 'sh1', target: 'dr1', type: 'association' },
                { source: 'sh2', target: 'dr2', type: 'association' },
                { source: 'sh3', target: 'dr3', type: 'association' },
                { source: 'dr1', target: 'as1', type: 'association' },
                { source: 'dr2', target: 'as1', type: 'association' },
                { source: 'as1', target: 'gl1', type: 'influence' },
                { source: 'dr3', target: 'gl1', type: 'influence' },
            ],
        },
    ];

    /* ── Nesting depth check ────────────────────────────────── */
    function getNestingDepth(cell) {
        let depth = 0;
        let parent = cell.getParentCell ? cell.getParentCell() : null;
        while (parent) {
            depth++;
            parent = parent.getParentCell ? parent.getParentCell() : null;
        }
        return depth;
    }

    let TYPE_ICONS = ComposerRenderer.TYPE_ICONS;
    let typeIconPath = ComposerRenderer.typeIconPath;

    /* ── Shape definitions delegated to ComposerRenderer ── */
    let defineArchiMateShape = ComposerRenderer.defineArchiMateShape;
    let SPECIAL_TYPES = ComposerRenderer.SPECIAL_TYPES;
    let SHAPE_CATEGORY = ComposerRenderer.SHAPE_CATEGORY;
    let shapeCategory = ComposerRenderer.shapeCategory;
    let createSpecialNode = ComposerRenderer.createSpecialNode;
    let createNode = ComposerRenderer.createNode;
    let createContainerNode = ComposerRenderer.createContainerNode;
    let createLink = ComposerRenderer.createLink;
    let humanizeRelTypeLabel = ComposerRenderer.humanizeRelType;

    /* ── Helper: get CSRF token ───────────────────────────── */
    function csrfToken() {
        return (document.querySelector('meta[name=csrf-token]') || {}).content || '';
    }

    /* ── Layer banding algorithm ──────────────────────────── */
    function applyLayerBanding(graph) {
        /* Exclude non-element cells: layer zone swimlanes and annotations */
        let cells = graph.getElements().filter(function(c) {
            return !c.get('isLayerZone') && !c.get('isAnnotation');
        });
        if (cells.length === 0) return;

        let layersPresent = {};
        cells.forEach(function(cell) {
            let layer = (cell.get('elLayer') || '').toLowerCase();
            if (!layersPresent[layer]) layersPresent[layer] = [];
            layersPresent[layer].push(cell);
        });

        /* Only use known ArchiMate layers for banding */
        let sortedLayers = Object.keys(layersPresent)
            .filter(function(l) { return LAYER_Y_ORDER[l] !== undefined; })
            .sort(function(a, b) { return LAYER_Y_ORDER[a] - LAYER_Y_ORDER[b]; });

        /* Unknown layers (connectors, empty string, etc.) get appended last */
        let unknownLayers = Object.keys(layersPresent).filter(function(l) {
            return LAYER_Y_ORDER[l] === undefined;
        });
        sortedLayers = sortedLayers.concat(unknownLayers);

        if (sortedLayers.length < 2) {
            /* Single layer: simple grid */
            let cols = Math.ceil(Math.sqrt(cells.length));
            cells.forEach(function(cell, i) {
                cell.position(40 + (i % cols) * 240, 40 + Math.floor(i / cols) * 160);
            });
            return;
        }

        let COLS_MAX = 10;
        let SPACING_X = 240;   /* element width 200 + 40px gap */
        let SPACING_Y = 160;   /* element height 130 + 30px gap */
        let BAND_GAP = 80;
        let yOffset = 40;

        sortedLayers.forEach(function(layer) {
            let nodes = layersPresent[layer];
            let cols = Math.min(nodes.length, COLS_MAX);
            let totalW = cols * SPACING_X;
            let startX = Math.max(40, (cols <= 3 ? 200 : 40));

            nodes.forEach(function(n, i) {
                let col = i % cols;
                let row = Math.floor(i / cols);
                n.position(startX + col * SPACING_X, yOffset + row * SPACING_Y);
            });

            let rows = Math.ceil(nodes.length / cols);
            yOffset += rows * SPACING_Y + BAND_GAP;
        });
    }

    /* ── CMP-027: Toast helper ──────────────────────────── */
    function _toast(type, msg) {
        if (window.Platform && window.Platform.toast && window.Platform.toast[type]) {
            window.Platform.toast[type](msg);
        } else if (window.showToast) {
            window.showToast(msg, type);
        }
    }

    /* ── ENT-106: Apply / remove a custom annotation label on a JointJS link ── */
    function _applyCustomLabel(link, text) {
        /* JointJS links start with one label (the relType badge at index 0).
           Custom annotation lives at index 1. */
        let labels = link.labels();
        /* Strip any existing annotation labels (keep only index 0 = relType) */
        if (labels.length > 1) {
            link.labels([labels[0]]);
        }
        if (text) {
            link.appendLabel({
                attrs: {
                    text: {
                        text: text,
                        fill: '#334155',
                        fontSize: 10,
                        fontFamily: 'Inter, system-ui, sans-serif',
                        fontWeight: 500,
                    },
                    rect: {
                        fill: '#fffbeb', stroke: '#fbbf24', strokeWidth: 0.5,
                        rx: 3, ry: 3,
                        ref: 'text', refWidth: 8, refHeight: 4, refX: -4, refY: -2,
                    },
                },
                position: { distance: 0.5, offset: 10 },
            });
        }
    }

    /* ================================================================== */
    /*  Alpine.js component                                                */
    /* ================================================================== */
    /* CMP-032: Merge module methods into the Alpine data object */
    let _helpers = {
        guessLayer: guessLayer,
        csrfToken: csrfToken,
        createNode: createNode,
        createLink: createLink,
        createContainerNode: createContainerNode,
        applyImportedElementPresentation: ComposerRenderer.applyImportedElementPresentation,
        createAnnotation: ComposerRenderer.createAnnotation,
        createLayerZone: ComposerRenderer.createLayerZone,
        layerColor: layerColor,
        REL_STYLES: REL_STYLES,
        markerPath: markerPath,
        markerFill: markerFill,
        _toast: _toast,
        UndoStack: UndoStack,
        isContainerType: isContainerType,
        getNestingDepth: getNestingDepth,
        applyLayerBanding: applyLayerBanding,
        VIEWPOINT_PALETTE_MAP: VIEWPOINT_PALETTE_MAP,
        applyCustomLabel: _applyCustomLabel,
    };

    let _base = {

        /* ── State ────────────────────────────────────────── */
        graph: null,
        paper: null,
        solutionId: (window.__COMPOSER_CONFIG__ || {}).solutionId || 0,
        solutionLabel: (window.__COMPOSER_CONFIG__ || {}).solutionName || 'Enterprise',
        paletteFilter: '',
        statusText: 'Ready',
        zoomPercent: 100,
        canvasCursorX: 0,
        canvasCursorY: 0,

        /* Mode: 'edit' or 'view' */
        mode: 'edit',

        /* Connect mode — click source then target to draw relationships without port drag */
        connectModeActive: false,
        connectModeSource: null,
        _portHintShown: false,

        /* Grid visibility toggle */
        showGrid: true,

        /* Locked cells — keyed by cell.id; locked elements cannot be moved or resized */
        _lockedCells: {},
        lockedCount: 0,

        /* Floating panels */
        componentsPanelOpen: true,
        capabilitiesPanelOpen: false,
        sidebarCapabilities: [],
        capabilitiesLoading: false,
        capabilitiesError: '',
        capabilityFilter: '',

        /* Mini-map state */
        miniMapExpanded: (function() {
            try {
                let stored = window.localStorage.getItem('archie.composer.miniMapExpanded');
                return stored === null ? true : stored === 'true';
            } catch (e) { return true; }
        })(),
        _miniMapDragging: false,
        _miniMapRAF: null,

        /* CMP-028: Auto-persist state */
        _showAutosavePrompt: false,
        _pendingAutosaveRestore: null,

        /* Toolbar dropdown states */
        saveDropdownOpen: false,
        diagramsDropdownOpen: false,
        aiDropdownOpen: false,
        layoutDropdownOpen: false,
        analyzeDropdownOpen: false,
        exportDropdownOpen: false,
        moreDropdownOpen: false, /* CMP-063: overflow dropdown for narrow viewports */
        showShortcutsModal: false, /* CMP-070: keyboard shortcut help modal */

        /* Viewpoint state */
        vpDropdownOpen: false,
        activeViewpoint: null,
        activeViewpointName: '',
        viewpointLoading: false,
        scopeFallback: false,

        /* Save-name modal */
        saveNameOpen: false,
        saveNameValue: '',
        _saveNameCallback: null,
        _saveNamePromptHint: '',

        /* Autosave timestamp */
        lastSavedAt: null,
        _autosaveLabel: '',
        _saveFailed: false,
        _saving: false,

        /* Sequence View: a lifeline/message render of the elements + relationships
           already on the canvas (no separate sequence-diagram store — see
           layoutSequence()). sequenceMessages holds the ordered list shown in the
           reorder panel; sequencePanelOpen toggles that panel. */
        sequenceViewActive: false,
        sequencePanelOpen: false,
        sequenceMessages: [],

        /* Detail panel */
        selectedNode: null,
        selectedEdge: null,
        selectedLink: null,
        /* GAP-INT-003: Annotation card visibility toggle */
        showAnnotations: true,

        /* Relationship context menu */
        relCtxMenuOpen: false,
        relCtxMenuX: 0,
        relCtxMenuY: 0,
        relCtxMenuLink: null,

        /* Drag state */
        dragType: null,
        dragOver: false,

        /* CMP2-002: Bulk import from portfolio */
        bulkImportOpen: false,
        bulkImportQuery: '',
        bulkImportResults: [],
        bulkImportSelected: {},
        bulkImportLoading: false,
        bulkImportLayerFilter: '',

        /* Search modal */
        searchOpen: false,
        searchQuery: '',
        searchType: '',
        searchTypeFilter: '',
        searchResults: [],
        searchLoading: false,
        searchLayerFilter: '',
        newElementName: '',
        similarElements: [],
        checkingReuse: false,
        _reuseDebounceTimer: null,
        dropX: 400,
        dropY: 300,

        /* Relationship picker */
        relPickerOpen: false,
        relPickerX: 0,
        relPickerY: 0,
        relPickerTypes: [],
        relPickerSourceId: null,
        relPickerTargetId: null,
        relPickerInvalidTypes: [],
        /* Set when the server refuses the type just picked: {message, validTypes}. */
        relPickerRejection: null,
        relPickerSourceCell: null,
        relPickerTargetCell: null,
        accessMode: 'readwrite',
        flowLabel: '',
        associationWarning: false,
        defaultRouting: 'manhattan',
        _isChangeType: false,

        /* Context menu */
        ctxMenuOpen: false,
        ctxMenuX: 0,
        ctxMenuY: 0,
        ctxMenuCell: null,

        /* ENT-104: Element colour override picker */
        colourPickerOpen: false,
        colourPickerX: 0,
        colourPickerY: 0,

        /* ENT-107: Viewpoint tab strip */
        viewpointTabs: [],
        activeTabId: null,

        /* ENT-111: Relationship staleness review after Abacus sync */
        showStalenessReview: false,
        staleRelationships: [],

        /* Canvas (blank) context menu */
        canvasCtxMenuOpen: false,
        canvasCtxMenuX: 0,
        canvasCtxMenuY: 0,
        _canvasCtxPaperCoords: null,

        /* Canvas search (Ctrl+F) */
        canvasSearchOpen: false,
        canvasSearchQuery: '',
        canvasSearchMatches: [],
        canvasSearchIdx: 0,

        /* CMP-057: Search and Replace (Ctrl+H) */
        searchReplaceOpen: false,
        srFindText: '',
        srReplaceText: '',
        srCaseSensitive: false,
        srUseRegex: false,
        srMatches: [],
        srCurrentIndex: -1,
        /* Rendered under the Find box. An unparseable regex used to abort the
           search silently, which read as "0 matches" — the same answer a valid
           pattern with no hits gives. Inline rather than a toast because the
           search runs debounced on every keystroke. */
        srRegexError: null,

        /* Zoom preset dropdown */
        zoomDropdownOpen: false,

        /* Layer preset dropdown */
        layerPresetOpen: false,

        /* Quick-add command palette */
        quickAddOpen: false,
        quickAddQuery: '',
        quickAddResults: [],
        quickAddLoading: false,
        _quickAddNewType: 'ApplicationComponent',

        /* Canvas tracking */
        canvasElements: {},
        elementCount: 0,
        relCount: 0,

        /* Wave 10: Quality score + stale tracking */
        qualityScore: null,
        staleCount: 0,
        _staleElementIds: [],

        /* Neighbor focus tracking */
        _focusedNodeId: null,

        /* Pan state */
        _isPanning: false,
        _panStart: null,
        _spaceDown: false,

        /* Selection tracking */
        _selectedCells: [],
        _rubberBand: null,

        /* Smart alignment guides */
        _guideLines: [],

        /* Selected cell reference for inline editing */
        _currentSelectedCell: null,

        /* Tab cycle index for cycling through connected elements */
        _tabCycleIndex: -1,
        _tabCycleNeighbors: [],

        /* Bulk delete confirmation */
        bulkDeleteConfirmOpen: false,
        bulkDeleteCount: 0,

        /* Generic confirm dialog (used by deleteRelationship / deleteElement) */
        confirmDialogOpen: false,
        confirmDialogMsg: '',
        _confirmDialogCallback: null,

        /* Nesting prompt state */
        nestingPromptOpen: false,
        nestingPromptChild: null,
        nestingPromptParent: null,

        /* Clipboard for copy/paste */
        _clipboard: [],
        _clipboardLinks: [],

        /* Saved viewpoints */
        savedVpOpen: false,
        savedViewpoints: [],
        currentSavedVpId: null,
        viewpointDirty: false,
        /* GAP-CMP-007: ARB review status */
        viewpointReviewStatus: '',

        /* Version snapshots (CMP-015) */
        snapshotListOpen: false,
        snapshots: [],

        /* Diagram templates (CMP-015) */
        templateListOpen: false,
        templates: [],
        _builtinTemplates: BUILT_IN_TEMPLATES,

        /* Portfolio-generated templates (CMP2-006) */
        portfolioTemplates: [],
        portfolioTemplatesLoading: false,
        portfolioSectionOpen: false,

        /* Style templates (CMP-055) */
        customStyleTemplates: [],
        styleDropdownOpen: false,
        styleTemplateSaveOpen: false,
        newStyleTemplateName: '',

        /* Layer visibility toggles */
        layerVisibility: { business: true, application: true, technology: true, motivation: true, strategy: true, implementation: true, composite: true, other: true },

        /* Solution-only filter in search modal */
        solutionOnlyFilter: false,

        /* AI suggestion mode (CMP-016) */
        suggestionsEnabled: false,
        suggestions: [],
        _suggestionTimer: null,

        /* AI generate modal (CMP-017) */
        generateModalOpen: false,
        generateDescription: '',
        generatePhase: '',
        generateLoading: false,
        generatedElements: [],
        generatedRelationships: [],

        /* Context-aware generation */
        generateDomain: '',
        generateContextPreview: null,
        generateContextLoading: false,
        generateUseContext: true,
        generateIncludeGaps: true,
        generateGaps: [],
        generateRationale: '',
        generateContextExpanded: false,

        /* AI extract modal (CMP-022) */
        extractModalOpen: false,
        extractText: '',
        extractPhase: '',
        extractLoading: false,
        extractedElements: [],
        extractedRelationships: [],

        /* Validation panel (CMP-018) */
        validationPanelOpen: false,
        validationReport: { passed: [], warnings: [], errors: [] },
        validationLoading: false,
        validationPhase: '',

        /* GAP-CMP-002: Quick validation badge counts */
        validationErrors: 0,
        validationWarnings: 0,
        _quickValidationTimer: null,

        /* CMP-043: Custom properties state */
        customProperties: {},
        customPropsOpen: false,
        _customPropNewKey: '',
        _customPropNewVal: '',

        /* CMP-051 / CMP2-010: Snapshot diff overlay state */
        snapshotDiffActive: false,
        snapshotDiffStats: null,
        /* CMP2-010: Version comparison diff view */
        versionCompareOpen: false,
        versionCompareA: null,
        versionCompareB: null,
        diffSummaryOpen: false,
        diffDetails: null,
        _diffOverlayCells: [],

        /* CMP-052: Annotation state */
        annotEditOpen: false,
        annotEditText: '',
        annotationCells: [],
        _annotEditCell: null,

        /* CMP-054: Presentation mode state */
        presentationActive: false,
        presentationSlides: [],
        presentationIndex: 0,
        _presentationKeyHandler: null,

        /* CMP-047: Dependency propagation state */
        depPropagationActive: false,
        depPropagationRoot: null,

        /* CMP-053: Landscape map view */
        landscapeViewOpen: false,
        landscapeRowType: 'Capability',
        landscapeColType: 'ApplicationComponent',
        landscapeData: null,
        landscapeLoading: false,

        /* CMP-041: Matrix cross-reference view state */
        matrixViewOpen: false,
        matrixRowType: 'BusinessProcess',
        matrixColType: 'ApplicationComponent',
        matrixRows: [],
        matrixCols: [],
        matrixLoading: false,
        _matrixIntersections: {},

        /* CMP2-007: Relationship matrix view (diagram vs matrix toggle) */
        relMatrixOpen: false,
        relMatrixData: null,       /* { layers: [...], elements: [...], cells: {}, gaps: [] } */
        _relMatrixHighlightedLink: null,

        /* CMP-040: Drill-down navigation state */
        breadcrumbStack: [],
        linkViewpointModalOpen: false,
        linkViewpointTargetCell: null,
        linkViewpointList: [],

        /* CMP-049: Custom filtered views */
        activeFilter: '',
        filterBarVisible: false,
        gapOverlayActive: false,

        /* CMP-056: Diagram metadata */
        diagramDescription: '',
        diagramVersion: '1.0',
        diagramAudience: 'Technical',
        diagramLastModified: '',
        metadataPanelOpen: false,

        /* CMP-020: Enterprise Intelligence overlay state */
        intelligenceEnabled: false,
        intelligenceLoading: false,
        intelligenceData: {},

        /* ── Layer zones / lifecycle / delta / heatmap / derived state ── */
        layerZonesActive: false,
        layerZoneCells: [],
        lifecycleVizEnabled: false,
        deltaMode: false,
        heatmapEnabled: false,
        heatmapMetric: '',
        heatmapLoading: false,
        derivedEnabled: false,
        derivedLoading: false,
        explanationLoading: false,
        /* CMP2-004: Live metrics overlay */
        metricsOverlayType: 'off',
        metricsData: {},
        metricsLoading: false,

        /* CMP2-001: Current/Target/Gap state toggle */
        stateViewMode: 'all',  /* 'all' | 'current' | 'target' | 'gap' */

        /* editMode alias — kept in sync with mode in toggleMode() */
        editMode: true,

        /* ── Computed palette (viewpoint-scoped + text filter) ── */
        /* REQ-CMP-003: Save state for toolbar indicator */
        get _saveState() {
            if (this._saveFailed) return 'failed';
            if (this._saving) return 'saving';
            if (this.viewpointDirty) return 'dirty';
            if (this.lastSavedAt) return 'saved';
            return '';
        },

        /* CMP-07: ONE authoritative save indicator. The status bar previously
           showed up to five competing strings ("Unsaved changes", "Autosave:
           just now", "Save paused", "Autosave: 1 min ago", plus the toolbar
           "Save*") and the user could not tell whether their work was safe. This
           getter maps _saveState to a single {icon, text, tone}; the footer and
           toolbar both render it, so there is exactly one source of truth. */
        get saveIndicator() {
            switch (this._saveState) {
                case 'failed':
                    return { icon: 'alert-circle', text: 'Not saved — retrying', tone: 'text-destructive' };
                case 'saving':
                    return { icon: 'loader', text: 'Saving…', tone: 'text-muted-foreground' };
                case 'dirty':
                    return { icon: 'circle', text: 'Unsaved changes', tone: 'text-warning' };
                case 'saved':
                    return { icon: 'check', text: 'Saved' + (this._autosaveLabel ? ' · ' + this._autosaveLabel : ''), tone: 'text-success' };
                default:
                    return { icon: 'circle', text: 'Ready', tone: 'text-muted-foreground' };
            }
        },

        get filteredPalette() {
            let self = this;
            let vpKey = self.activeViewpoint || '';
            let allowedLayers = VIEWPOINT_PALETTE_MAP[vpKey] || VIEWPOINT_PALETTE_MAP[''];
            let f = (self.paletteFilter || '').toLowerCase();
            let result = {};
            Object.keys(PALETTE).forEach(function(layer) {
                if (allowedLayers.indexOf(layer) === -1) return;
                let items = PALETTE[layer];
                if (f) {
                    items = items.filter(function(t) {
                        return t.label.toLowerCase().indexOf(f) !== -1 || t.type.toLowerCase().indexOf(f) !== -1;
                    });
                }
                if (items.length) result[layer] = items;
            });
            return result;
        },

        /* ── Computed: filtered capabilities list (CAP-022) ── */
        get filteredCapabilities() {
            let f = (this.capabilityFilter || '').toLowerCase();
            if (!f) return this.sidebarCapabilities;
            return this.sidebarCapabilities.filter(function(cap) {
                return (cap.name || '').toLowerCase().indexOf(f) !== -1;
            });
        },

        /* ── Init ─────────────────────────────────────────── */
        init: function() {
            let self = this;

            defineArchiMateShape();

            this.graph = new joint.dia.Graph();

            this.paper = new joint.dia.Paper({
                el: document.getElementById('composer-canvas'),
                model: this.graph,
                width: '100%',
                height: '100%',
                gridSize: 12,
                snapGrid: { width: 12, height: 12 },
                drawGrid: [
                    { name: 'dot', args: { color: '#dde1e6', thickness: 1 } },
                    { name: 'dot', args: { color: '#c8cdd3', thickness: 1, scaleFactor: 5 } },
                ],
                background: { color: '#fafbfc' },

                interactive: function(cellView) {
                    if (self.mode === 'view') return { elementMove: false, addLinkFromMagnet: false };
                    /* Locked cells cannot be moved or have links added from them */
                    if (cellView && cellView.model && self._lockedCells[cellView.model.id]) {
                        return { elementMove: false, addLinkFromMagnet: false, vertexMove: false };
                    }
                    return { linkMove: true, elementMove: true, addLinkFromMagnet: true, vertexMove: true, vertexAdd: true, vertexRemove: true };
                },
                magnetThreshold: 'onleave',
                linkPinning: false,
                snapLinks: { radius: 30 },
                markAvailable: true,

                /* MM-04: Green glow on valid connection targets during magnet drag */
                highlighting: {
                    magnetAvailability: {
                        name: 'addClass',
                        options: { className: 'joint-highlighted-target' }
                    },
                },

                defaultLink: function() {
                    return new joint.shapes.standard.Link({
                        attrs: {
                            line: {
                                stroke: '#6366f1', strokeWidth: 2, strokeDasharray: '6,3',
                                targetMarker: { type: 'path', d: 'M 10 -5 L 0 0 L 10 5 Z', fill: '#6366f1' },
                            },
                        },
                        router: { name: 'manhattan', args: { step: 12, padding: 36 } },
                        connector: { name: 'rounded', args: { radius: 8 } },
                    });
                },

                validateConnection: function(cellViewS, magnetS, cellViewT, magnetT) {
                    if (self.mode === 'view') return false;
                    if (cellViewS === cellViewT) return false;
                    if (!magnetT) return false;
                    return true;
                },

                defaultConnectionPoint: { name: 'boundary', args: { offset: 2 } },
            });

            /* ── Pan / rubber-band select: drag on blank canvas ── */
            this.paper.on('blank:pointerdown', function(evt) {
                /* CMP-06: middle-mouse drag always pans, in any mode. Plain
                   left-drag in edit mode is rubber-band select (below), so
                   off-screen content was only reachable via Space+drag — an
                   undiscoverable gesture. Middle-mouse pan is the universal
                   diagram-tool convention and conflicts with nothing. */
                if (evt.button === 1 || self._spaceDown || self.mode === 'view') {
                    if (evt.button === 1 && evt.preventDefault) evt.preventDefault();
                    self._isPanning = true;
                    self._panStart = { x: evt.clientX, y: evt.clientY };
                    self.paper.el.style.cursor = 'grabbing';
                    self._clearSelection();
                    return;
                }
                /* Edit mode: plain drag = rubber-band selection (industry standard) */
                if (self.mode === 'edit') {
                    let svgPoint = self.paper.clientToLocalPoint(evt.clientX, evt.clientY);
                    self._rubberBand = { startX: svgPoint.x, startY: svgPoint.y, rect: null };
                    let rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                    rect.setAttribute('fill', 'rgba(59, 130, 246, 0.08)');
                    rect.setAttribute('stroke', '#3b82f6');
                    rect.setAttribute('stroke-width', '1.5');
                    rect.setAttribute('stroke-dasharray', '6,3');
                    rect.setAttribute('x', svgPoint.x);
                    rect.setAttribute('y', svgPoint.y);
                    rect.setAttribute('width', '0');
                    rect.setAttribute('height', '0');
                    self.paper.el.querySelector('svg').appendChild(rect);
                    self._rubberBand.rect = rect;
                    return; /* Don't pan */
                }
            });

            document.addEventListener('mousemove', function(e) {
                if (!self._isPanning || !self._panStart) return;
                let dx = e.clientX - self._panStart.x;
                let dy = e.clientY - self._panStart.y;
                self._panStart = { x: e.clientX, y: e.clientY };
                let t = self.paper.translate();
                self.paper.translate(t.tx + dx, t.ty + dy);
                self._scheduleMiniMapUpdate();
            });

            document.addEventListener('mouseup', function() {
                if (self._isPanning) {
                    self._isPanning = false;
                    self._panStart = null;
                    if (self.paper) self.paper.el.style.cursor = '';
                }
            });

            /* ── Rubber-band marquee selection: mousemove + mouseup ── */
            document.addEventListener('mousemove', function(e) {
                if (!self._rubberBand) return;
                let rb = self._rubberBand;
                let svgPoint = self.paper.clientToLocalPoint(e.clientX, e.clientY);
                let x = Math.min(rb.startX, svgPoint.x);
                let y = Math.min(rb.startY, svgPoint.y);
                let w = Math.abs(svgPoint.x - rb.startX);
                let h = Math.abs(svgPoint.y - rb.startY);
                rb.rect.setAttribute('x', x);
                rb.rect.setAttribute('y', y);
                rb.rect.setAttribute('width', w);
                rb.rect.setAttribute('height', h);
            });

            document.addEventListener('mouseup', function() {
                if (!self._rubberBand) return;
                let rb = self._rubberBand;
                /* Compute selection rectangle in local coordinates */
                let selX = parseFloat(rb.rect.getAttribute('x'));
                let selY = parseFloat(rb.rect.getAttribute('y'));
                let selW = parseFloat(rb.rect.getAttribute('width'));
                let selH = parseFloat(rb.rect.getAttribute('height'));
                /* Remove the SVG overlay rect */
                if (rb.rect.parentNode) rb.rect.parentNode.removeChild(rb.rect);
                self._rubberBand = null;
                /* Only select if dragged a meaningful area */
                if (selW < 5 && selH < 5) return;
                self._clearSelection();
                /* Find elements whose bounding boxes intersect the selection rect */
                self.graph.getElements().forEach(function(cell) {
                    let pos = cell.position();
                    let size = cell.size();
                    let cellRight = pos.x + size.width;
                    let cellBottom = pos.y + size.height;
                    let selRight = selX + selW;
                    let selBottom = selY + selH;
                    /* Check AABB intersection */
                    if (pos.x < selRight && cellRight > selX &&
                        pos.y < selBottom && cellBottom > selY) {
                        self._selectedCells.push(cell);
                        let view = self.paper.findViewByModel(cell);
                        if (view) self._highlightCell(view);
                    }
                });
                if (self._selectedCells.length > 0) {
                    self.statusText = 'Selected ' + self._selectedCells.length + ' element(s)';
                }
            });

            /* ── Resize handles: custom drag-to-resize ── */
            this._resizing = null;
            this.paper.on('element:pointerdown', function(cellView, evt) {
                if (self.mode === 'view') return;
                let target = evt.target;
                let selector = target && target.getAttribute('joint-selector');
                let resizeSelectors = ['resizeHandle', 'resizeHandleTL', 'resizeHandleTR', 'resizeHandleBL', 'resizeHandleT', 'resizeHandleR', 'resizeHandleB', 'resizeHandleL'];
                if (!selector || resizeSelectors.indexOf(selector) === -1) return;
                /* Locked cells cannot be resized */
                if (self._lockedCells[cellView.model.id]) return;

                evt.stopPropagation();
                /* Prevent JointJS from also starting an element drag */
                if (typeof cellView.preventDefaultInteraction === 'function') {
                    cellView.preventDefaultInteraction(evt);
                }
                let cell = cellView.model;
                let scale = self.paper.scale().sx;
                let pos = cell.position();
                self._resizing = {
                    cell: cell,
                    handle: selector,
                    startX: evt.clientX,
                    startY: evt.clientY,
                    startW: cell.size().width,
                    startH: cell.size().height,
                    startPosX: pos.x,
                    startPosY: pos.y,
                    scale: scale,
                };
                let cursorMap = {
                    resizeHandle: 'nwse-resize',
                    resizeHandleTL: 'nwse-resize',
                    resizeHandleTR: 'nesw-resize',
                    resizeHandleBL: 'nesw-resize',
                    resizeHandleT: 'ns-resize',
                    resizeHandleR: 'ew-resize',
                    resizeHandleB: 'ns-resize',
                    resizeHandleL: 'ew-resize',
                };
                self.paper.el.style.cursor = cursorMap[selector] || 'nwse-resize';
            });

            document.addEventListener('mousemove', function(e) {
                if (!self._resizing) return;
                let r = self._resizing;
                let dx = (e.clientX - r.startX) / r.scale;
                let dy = (e.clientY - r.startY) / r.scale;
                let newW = r.startW, newH = r.startH, newX = r.startPosX, newY = r.startPosY;
                if (r.handle === 'resizeHandle') {
                    /* Bottom-right corner: both axes grow right/down */
                    newW = Math.max(48, r.startW + dx);
                    newH = Math.max(36, r.startH + dy);
                } else if (r.handle === 'resizeHandleTL') {
                    /* Top-left corner: grow left + up, position shifts */
                    newW = Math.max(48, r.startW - dx);
                    newH = Math.max(36, r.startH - dy);
                    newX = r.startPosX + (r.startW - newW);
                    newY = r.startPosY + (r.startH - newH);
                } else if (r.handle === 'resizeHandleTR') {
                    /* Top-right corner: grow right + up, Y position shifts */
                    newW = Math.max(48, r.startW + dx);
                    newH = Math.max(36, r.startH - dy);
                    newY = r.startPosY + (r.startH - newH);
                } else if (r.handle === 'resizeHandleBL') {
                    /* Bottom-left corner: grow left + down, X position shifts */
                    newW = Math.max(48, r.startW - dx);
                    newH = Math.max(36, r.startH + dy);
                    newX = r.startPosX + (r.startW - newW);
                } else if (r.handle === 'resizeHandleR') {
                    newW = Math.max(48, r.startW + dx);
                } else if (r.handle === 'resizeHandleL') {
                    /* Left edge: width grows leftward, position shifts */
                    newW = Math.max(48, r.startW - dx);
                    newX = r.startPosX + (r.startW - newW);
                } else if (r.handle === 'resizeHandleB') {
                    newH = Math.max(36, r.startH + dy);
                } else if (r.handle === 'resizeHandleT') {
                    /* Top edge: height grows upward, position shifts */
                    newH = Math.max(36, r.startH - dy);
                    newY = r.startPosY + (r.startH - newH);
                }
                /* Snap to grid */
                newW = Math.round(newW / 12) * 12;
                newH = Math.round(newH / 12) * 12;
                newX = Math.round(newX / 12) * 12;
                newY = Math.round(newY / 12) * 12;
                /* Always set position to counteract any JointJS drag drift */
                r.cell.position(newX, newY);
                r.cell.resize(newW, newH);
            });

            document.addEventListener('mouseup', function() {
                if (self._resizing) {
                    self._resizing = null;
                    if (self.paper) self.paper.el.style.cursor = '';
                }
            });

            /* ── Block native browser context menu on the canvas ── */
            this.paper.el.addEventListener('contextmenu', function(e) {
                e.preventDefault();
            });

            /* ── Scroll wheel zoom (zooms toward cursor position) ── */
            this.paper.el.addEventListener('wheel', function(e) {
                e.preventDefault();
                let delta = e.deltaY > 0 ? -0.08 : 0.08;
                let oldScale = self.paper.scale().sx;
                let newScale = Math.min(3, Math.max(0.15, oldScale + delta));
                if (newScale === oldScale) return;
                /* Compute cursor position in paper coordinates before zoom */
                let paperRect = self.paper.el.getBoundingClientRect();
                let t = self.paper.translate();
                let mouseXPaper = (e.clientX - paperRect.left - t.tx) / oldScale;
                let mouseYPaper = (e.clientY - paperRect.top  - t.ty) / oldScale;
                /* Apply new scale */
                self.paper.scale(newScale, newScale);
                /* Adjust translate so the point under the cursor stays fixed */
                let newTx = e.clientX - paperRect.left - mouseXPaper * newScale;
                let newTy = e.clientY - paperRect.top  - mouseYPaper * newScale;
                self.paper.translate(newTx, newTy);
                self.zoomPercent = Math.round(newScale * 100);
            }, { passive: false });

            /* ── Undo/Redo: record graph mutations ── */
            this.graph.on('change:position', function(cell, pos, opt) {
                if (!UndoStack._recording) return;
                /* Bulk-move co-drags are synthetic; only record the primary drag */
                if (opt && opt._bulkMoving) return;
                let prev = cell.previous('position');
                if (!prev || (prev.x === pos.x && prev.y === pos.y)) return;
                UndoStack.push({
                    undo: function() { cell.position(prev.x, prev.y); },
                    redo: function() { cell.position(pos.x, pos.y); },
                });
            });

            /* ── Bulk move: drag one selected element moves all selected ── */
            this.graph.on('change:position', function(cell, newPos, opt) {
                if (opt._bulkMoving) return;
                if (self._selectedCells.length < 2) return;
                if (self._selectedCells.indexOf(cell) === -1) return;
                let oldPos = cell.previous('position');
                if (!oldPos) return;
                let dx = newPos.x - oldPos.x;
                let dy = newPos.y - oldPos.y;
                self._selectedCells.forEach(function(other) {
                    if (other === cell) return;
                    let pos = other.position();
                    other.position(pos.x + dx, pos.y + dy, { _bulkMoving: true });
                });
            });

            /* Zone membership: re-colour element when dragged into a different layer zone */
            this.graph.on('change:position', function(cell, pos, opt) {
                if (opt && opt._bulkMoving) return;
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                if (typeof self._detectZoneMembership === 'function') {
                    self._detectZoneMembership(cell);
                }
            });

            this.graph.on('add', function(cell) {
                if (!UndoStack._recording) return;
                let g = self.graph;
                let json = cell.toJSON();
                let isElem = !cell.isLink();
                UndoStack.push({
                    undo: function() {
                        cell.remove();
                        if (isElem) self.elementCount = Math.max(0, self.elementCount - 1);
                    },
                    redo: function() {
                        g.addCell(cell.clone().set(json));
                        if (isElem) self.elementCount++;
                    },
                });
            });

            this.graph.on('remove', function(cell) {
                if (!UndoStack._recording) return;
                let g = self.graph;
                let json = cell.toJSON();
                let isElem = !cell.isLink();
                UndoStack.push({
                    undo: function() {
                        let c = cell.clone();
                        c.set(json);
                        g.addCell(c);
                        if (isElem) self.elementCount++;
                    },
                    redo: function() {
                        cell.remove();
                        if (isElem) self.elementCount = Math.max(0, self.elementCount - 1);
                    },
                });
            });

            /* CMP-033: Track element rename for undo */
            this.graph.on('change:elName', function(cell, newName) {
                if (!UndoStack._recording) return;
                let oldName = cell.previous('elName');
                if (oldName === newName) return;
                UndoStack.push({
                    undo: function() { cell.set('elName', oldName); cell.attr('label/text', oldName); },
                    redo: function() { cell.set('elName', newName); cell.attr('label/text', newName); },
                });
            });

            /* CMP-033: Track element size changes for undo */
            this.graph.on('change:size', function(cell, newSize, options) {
                if (!UndoStack._recording) return;
                if (options && options._undo) return;
                let oldSize = cell.previous('size');
                if (!oldSize || (oldSize.width === newSize.width && oldSize.height === newSize.height)) return;
                UndoStack.push({
                    undo: function() { cell.resize(oldSize.width, oldSize.height, { _undo: true }); },
                    redo: function() { cell.resize(newSize.width, newSize.height, { _undo: true }); },
                });
            });

            /* ── Responsive element layout: adapt icon/label positions when resized ──
             *
             * All label and icon positions are hardcoded absolute pixel values in the
             * shape definitions (nameLabel y:60, iconBox y:12 h:40).  This listener
             * re-applies the layout whenever the element is resized so that content
             * stays inside the bounding box at any size:
             *
             *   h < 52 px  →  "chip" mode: only the coloured body is shown
             *   52 ≤ h < 75 →  "compact" mode: small icon in top-left, name alongside
             *   h ≥ 75 px  →  full card layout (default/restored)
             *
             * Container types (ApplicationComponent, Node, etc.) have a different
             * header-bar layout and are excluded — they handle their own sizing.
             */
            this.graph.on('change:size', function(cell, newSize) {
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                if (cell.get('lucidImported')) return;
                let elType = cell.get('elType');
                if (!elType || isContainerType(elType)) return;
                /* Only apply to regular card-style nodes that have an iconBox */
                let cellAttrs = cell.attributes && cell.attributes.attrs;
                if (!cellAttrs || cellAttrs.iconBox === undefined) return;

                let h = newSize.height;
                let attrs;

                if (h < 52) {
                    /* Chip mode — hide everything, show only the coloured body */
                    attrs = {
                        iconBox:   { display: 'none' },
                        typeIcon:  { display: 'none' },
                        accentBar: { display: 'none' },
                        nameLabel: { display: 'none' },
                        typeLabel: { display: 'none' },
                    };
                } else if (h < 75) {
                    /* Compact mode — 24px icon in top-left corner, name to its right */
                    let compactIconSize = 24;
                    attrs = {
                        iconBox:   { display: '', x: 8, y: Math.round((h - compactIconSize) / 2), width: compactIconSize, height: compactIconSize, rx: 4, ry: 4 },
                        typeIcon:  { display: '', transform: 'translate(14, ' + Math.round((h - compactIconSize) / 2 + 6) + ') scale(0.7)' },
                        accentBar: { display: '' },
                        nameLabel: { display: '', x: 40, y: Math.max(4, Math.round(h / 2) - 10), fontSize: 11, textWrap: { width: -48, maxLineCount: 2, ellipsis: true } },
                        typeLabel: { display: 'none' },
                    };
                } else {
                    /* Full card layout — restore default positions */
                    attrs = {
                        iconBox:   { display: '', x: 12, y: 12, width: 40, height: 40, rx: 8, ry: 8 },
                        typeIcon:  { display: '', transform: 'translate(22, 22) scale(1.2)' },
                        accentBar: { display: '' },
                        nameLabel: { display: '', x: 12, y: 60, fontSize: 12, textWrap: { width: -24, maxLineCount: 2, ellipsis: true } },
                        typeLabel: { display: '', x: 12, y: 79 },
                    };
                }

                /* _undo:true suppresses the change:attrs undo handler so layout
                 * adaptations don't appear as undoable user actions */
                cell.attr(attrs, { _undo: true });
            });


            /* Restore lock highlights when cells are added from saved graph JSON */
            this.graph.on('add', function(cell) {
                if (!cell.get('locked')) return;
                self._lockedCells[cell.id] = true;
                self.lockedCount++;
                /* Apply highlight after paper has rendered the view */
                setTimeout(function() {
                    let view = self.paper && self.paper.findViewByModel(cell);
                    if (!view) return;
                    try {
                        view.highlight(null, {
                            highlighter: { name: 'stroke', options: {
                                padding: 3, rx: 4,
                                attrs: { stroke: '#f59e0b', 'stroke-width': 2, 'stroke-dasharray': '4,3' },
                            }},
                        });
                    } catch(e) { /* swallow-ok: cosmetic lock highlight replayed 50ms after render; the lock itself is already recorded on the cell, so a view that is not up yet is nothing to act on */ }
                }, 50);
            });

            /* CMP-033: Track relationship type changes for undo */
            this.graph.on('change:relType', function(link, newType) {
                if (!UndoStack._recording) return;
                let oldType = link.previous('relType');
                if (oldType === newType) return;
                UndoStack.push({
                    undo: function() { link.set('relType', oldType, { _undo: true }); },
                    redo: function() { link.set('relType', newType, { _undo: true }); },
                });
            });

            /* CMP-033: Track attrs changes for undo (property/label changes) */
            this.graph.on('change:attrs', function(cell, attrs, options) {
                if (!UndoStack._recording) return;
                if (options && options._undo) return;
                let oldAttrs = cell.previous('attrs');
                if (!oldAttrs) return;
                let prevAttrs = JSON.parse(JSON.stringify(oldAttrs));
                let nextAttrs = JSON.parse(JSON.stringify(attrs));
                UndoStack.push({
                    undo: function() { cell.attr(prevAttrs, { _undo: true }); },
                    redo: function() { cell.attr(nextAttrs, { _undo: true }); },
                });
            });

            /* ── Event: link connected → show relationship type picker ── */
            /* F-05(c), Capgemini dry-run: a template-loaded element's id is a
               client-only string ("__builtin__sh1"), never written to the
               database. Connecting two of these sent that fake id straight to
               valid-relationship-types (400) and then to POST /api/relationships
               (500 — a string into an Integer FK). Materialize on demand here,
               before either lookup — the common case (both ids already real
               integers) makes zero network calls and behaves exactly as before. */
            function isMaterializedId(id) {
                return /^\d+$/.test(String(id));
            }

            this._materializeConnectEnds = function(srcCell, tgtCell, srcElementId, tgtElementId) {
                if (isMaterializedId(srcElementId) && isMaterializedId(tgtElementId)) {
                    return Promise.resolve({ srcElementId: srcElementId, tgtElementId: tgtElementId });
                }
                let toMaterialize = [];
                if (!isMaterializedId(srcElementId)) {
                    toMaterialize.push({
                        element_id: srcElementId,
                        name: srcCell.get('elName') || srcCell.get('name') || '',
                        el_type: srcCell.get('elType') || 'ApplicationComponent',
                        layer: srcCell.get('layer') || 'application',
                    });
                }
                if (!isMaterializedId(tgtElementId)) {
                    toMaterialize.push({
                        element_id: tgtElementId,
                        name: tgtCell.get('elName') || tgtCell.get('name') || '',
                        el_type: tgtCell.get('elType') || 'ApplicationComponent',
                        layer: tgtCell.get('layer') || 'application',
                    });
                }
                return Platform.fetch.post('/archimate/api/elements/materialize', { elements: toMaterialize }, { silent: true })
                    .then(function(data) {
                        let map = data.element_id_map || {};
                        let realSrc = map[String(srcElementId)] || srcElementId;
                        let realTgt = map[String(tgtElementId)] || tgtElementId;
                        // Adopt the new DB ids on the canvas cells — same pattern the
                        // full-diagram save flow already uses (composer_persistence.js) —
                        // so a later save or a second connect from the same cell also
                        // sees a real id, not a repeat materialize.
                        if (map[String(srcElementId)]) srcCell.set('elementId', realSrc);
                        if (map[String(tgtElementId)]) tgtCell.set('elementId', realTgt);
                        return { srcElementId: realSrc, tgtElementId: realTgt };
                    });
            };

            this.paper.on('link:connect', function(linkView) {
                if (self.mode === 'view') return;
                let link = linkView.model;
                let sourceId = link.get('source').id;
                let targetId = link.get('target').id;
                if (!sourceId || !targetId) return;

                let srcCell = self.graph.getCell(sourceId);
                let tgtCell = self.graph.getCell(targetId);
                if (!srcCell || !tgtCell) return;

                let rawSrcElementId = srcCell.get('elementId');
                let rawTgtElementId = tgtCell.get('elementId');
                if (!rawSrcElementId || !rawTgtElementId) return;

                self._materializeConnectEnds(srcCell, tgtCell, rawSrcElementId, rawTgtElementId)
                .catch(function() {
                    // Materialization failed — proceed with the raw ids so the existing
                    // error handling below (400/500 -> fallback association + toast)
                    // still fires, rather than silently dropping the connect gesture.
                    return { srcElementId: rawSrcElementId, tgtElementId: rawTgtElementId };
                })
                .then(function(resolved) {
                let srcElementId = resolved.srcElementId;
                let tgtElementId = resolved.tgtElementId;

                self._pendingLink = link;
                self.relPickerSourceCell = srcCell;
                self.relPickerTargetCell = tgtCell;
                self.relPickerSourceId = srcElementId;
                self.relPickerTargetId = tgtElementId;

                let srcPos = srcCell.position();
                let tgtPos = tgtCell.position();
                let midX = (srcPos.x + tgtPos.x) / 2 + 90;
                let midY = (srcPos.y + tgtPos.y) / 2 + 32;
                let paperRect = self.paper.el.getBoundingClientRect();
                let s = self.paper.scale().sx;
                let t = self.paper.translate();
                let pickerX = paperRect.left + midX * s + t.tx;
                let pickerY = paperRect.top + midY * s + t.ty;

                pickerX = Math.max(10, Math.min(pickerX, window.innerWidth - 240));
                pickerY = Math.max(10, Math.min(pickerY, window.innerHeight - 300));

                self.relPickerX = pickerX;
                self.relPickerY = pickerY;

                self.relPickerTypes = [];
                self.relPickerInvalidTypes = [];
                self.relPickerRejection = null;
                self.associationWarning = false;
                self.relPickerOpen = true;

                /* Junction constraint: only triggering and flow are valid */
                let srcElType = srcCell.get('elType');
                let tgtElType = tgtCell.get('elType');
                let isJunctionInvolved = (srcElType === 'AndJunction' || srcElType === 'OrJunction' ||
                                          tgtElType === 'AndJunction' || tgtElType === 'OrJunction');

                if (isJunctionInvolved) {
                    let JUNCTION_ALLOWED = ['triggering', 'flow'];
                    let ALL_REL_TYPES = [
                        'composition', 'aggregation', 'assignment', 'realization',
                        'serving', 'access', 'influence', 'triggering', 'flow',
                        'specialization', 'association',
                    ];
                    self.relPickerTypes = JUNCTION_ALLOWED.map(function(t) {
                        return { type: t, tier: 'standard', description: 'Valid for junctions' };
                    });
                    self.relPickerInvalidTypes = ALL_REL_TYPES.filter(function(t) {
                        return JUNCTION_ALLOWED.indexOf(t) === -1;
                    });
                    return;
                }

                Platform.fetch('/archimate/api/valid-relationship-types?source_id=' + srcElementId + '&target_id=' + tgtElementId, { silent: true })
                .then(function(data) {
                    let validDetailed = data.valid_types_detailed || [];
                    self.relPickerTypes = validDetailed.length > 0
                        ? validDetailed
                        : (data.valid_types || ['association']).map(function(t) {
                            return { type: t, tier: 'standard', description: '' };
                        });

                    /* Build invalid list — all ArchiMate relationship types not in the valid set */
                    let ALL_REL_TYPES = [
                        'composition', 'aggregation', 'assignment', 'realization',
                        'serving', 'access', 'influence', 'triggering', 'flow',
                        'specialization', 'association',
                    ];
                    let validSet = {};
                    self.relPickerTypes.forEach(function(v) { validSet[v.type || v] = true; });
                    self.relPickerInvalidTypes = ALL_REL_TYPES.filter(function(t) {
                        return !validSet[t];
                    });
                })
                // fabricated-ok: falls back to a single type tagged tier:'fallback' and raises an error toast
                .catch(function() {
                    self.relPickerTypes = [{ type: 'association', tier: 'fallback', description: '' }];
                    self.relPickerInvalidTypes = [];
                    _toast('error', 'Failed to load relationship types');
                });
                });
            });

            /* ── Right-click context menu ── */
            this.paper.on('element:contextmenu', function(cellView, evt) {
                evt.preventDefault();
                self.ctxMenuCell = cellView.model;
                self.ctxMenuX = evt.clientX;
                self.ctxMenuY = evt.clientY;
                self.ctxMenuOpen = true;

                self.$nextTick(function() {
                    if (window.lucide) lucide.createIcons();
                    let menu = document.querySelector('[aria-label="Element actions"].context-menu');
                    if (menu) {
                        let pos = self._fitCtxMenu(menu, evt.clientX, evt.clientY);
                        self.ctxMenuX = pos.x;
                        self.ctxMenuY = pos.y;
                    }
                });
            });

            /* ── Single click → select / neighbor focus ── */
            this.paper.on('element:pointerclick', function(cellView, evt) {
                let cell = cellView.model;
                let elId = cell.get('elementId');

                /* Connect mode: first click picks source; second click picks target + opens picker */
                if (self.connectModeActive && !cell.get('isLayerZone') && !cell.get('isAnnotation') && elId) {
                    if (!self.connectModeSource) {
                        self.connectModeSource = cell;
                        self._clearSelection();
                        self._selectedCells = [cell];
                        self._highlightCell(cellView);
                        self.statusText = 'Connect: now click the target element (Escape to cancel)';
                        return;
                    }
                    let sourceCell = self.connectModeSource;
                    let targetCell = cell;
                    self.connectModeSource = null;
                    self._clearSelection();
                    if (sourceCell === targetCell) {
                        self.statusText = 'Connect: click a DIFFERENT element as the target';
                        return;
                    }
                    let srcElementId = sourceCell.get('elementId');
                    let tgtElementId = targetCell.get('elementId');

                    let tempLink = createLink(sourceCell, targetCell, 'association');
                    self.graph.addCell(tempLink);

                    self._pendingLink = tempLink;
                    self.relPickerSourceCell = sourceCell;
                    self.relPickerTargetCell = targetCell;
                    self.relPickerSourceId = srcElementId;
                    self.relPickerTargetId = tgtElementId;

                    let srcPos = sourceCell.position();
                    let tgtPos = targetCell.position();
                    let midX = (srcPos.x + tgtPos.x) / 2 + 90;
                    let midY = (srcPos.y + tgtPos.y) / 2 + 32;
                    let paperRect = self.paper.el.getBoundingClientRect();
                    let s = self.paper.scale().sx;
                    let t = self.paper.translate();
                    self.relPickerX = Math.max(10, Math.min(paperRect.left + midX * s + t.tx, window.innerWidth - 240));
                    self.relPickerY = Math.max(10, Math.min(paperRect.top + midY * s + t.ty, window.innerHeight - 300));

                    self.relPickerTypes = [];
                    self.relPickerInvalidTypes = [];
                    self.relPickerRejection = null;
                    self.associationWarning = false;
                    self.relPickerOpen = true;
                    self.statusText = 'Pick relationship type…';

                    let ALL_REL = ['composition','aggregation','assignment','realization','serving','access','influence','triggering','flow','specialization','association'];
                    /* DEF-006, Capgemini dry-run (browser-verified live): this is
                       the keyboard 'C' connect-mode gesture (click source, click
                       target) — a SEPARATE code path from the drag-a-link
                       'link:connect' handler above, which F-05(c) already fixed.
                       This one still sent template placeholder ids
                       ("__builtin__ba1") straight to valid-relationship-types,
                       confirmed live: 400, "Failed to load relationship types".
                       Reuse the same _materializeConnectEnds helper before the
                       lookup, updating srcElementId/tgtElementId and the picker's
                       stored ids to the real, materialized ones so the
                       subsequent POST /api/relationships (on picking a type)
                       also gets real integer ids. */
                    self._materializeConnectEnds(sourceCell, targetCell, srcElementId, tgtElementId)
                    .catch(function() {
                        return { srcElementId: srcElementId, tgtElementId: tgtElementId };
                    })
                    .then(function(resolved) {
                        srcElementId = resolved.srcElementId;
                        tgtElementId = resolved.tgtElementId;
                        self.relPickerSourceId = srcElementId;
                        self.relPickerTargetId = tgtElementId;
                        return Platform.fetch('/archimate/api/valid-relationship-types?source_id=' + srcElementId + '&target_id=' + tgtElementId, { silent: true });
                    })
                    .then(function(data) {
                        let validDetailed = data.valid_types_detailed || [];
                        self.relPickerTypes = validDetailed.length > 0 ? validDetailed
                            : (data.valid_types || ['association']).map(function(t) { return { type: t, tier: 'standard', description: '' }; });
                        let validSet = {};
                        self.relPickerTypes.forEach(function(v) { validSet[v.type || v] = true; });
                        self.relPickerInvalidTypes = ALL_REL.filter(function(t) { return !validSet[t]; });
                    })
                    // fabricated-ok: falls back to a single type tagged tier:'fallback' and raises an error toast
                    .catch(function() {
                        self.relPickerTypes = [{ type: 'association', tier: 'fallback', description: '' }];
                        self.relPickerInvalidTypes = [];
                        _toast('error', 'Failed to load relationship types');
                    });
                    return;
                }

                /* CMP-047: Alt+click triggers dependency propagation */
                if (evt.altKey && cellView.model.get('elementId')) {
                    self.toggleDependencyPropagation(cellView.model);
                    return;
                }

                /* Multi-select with Shift key */
                if (evt.shiftKey && self.mode === 'edit') {
                    let idx = self._selectedCells.indexOf(cell);
                    if (idx >= 0) {
                        self._selectedCells.splice(idx, 1);
                        self._unhighlightCell(cellView);
                    } else {
                        self._selectedCells.push(cell);
                        self._highlightCell(cellView);
                    }
                    return;
                }

                /* Single select — clear others */
                self._clearSelection();
                self._selectedCells = [cell];
                self._highlightCell(cellView);

                /* Detail panel */
                self.selectedEdge = null;
                self.componentsPanelOpen = false;
                self.selectedNode = {
                    elementId: elId,
                    label: cell.get('elName') || '(unnamed)',
                    elType: cell.get('elType') || '',
                    layer: cell.get('elLayer') || '',
                    description: cell.get('localDescription') || '',
                    status: cell.get('localStatus') || '',
                    relationshipCount: 0,
                    viewpointCount: 0,
                    solutionCount: 0,
                    /* GAP-INT-002: Deployment zone type for Grouping/Location */
                    _zoneType: cell.get('zoneType') || 'default',
                };
                self._currentSelectedCell = cell;

                /* Fetch rich detail from API (skip __builtin__ template elements) */
                if (elId && parseInt(elId, 10) > 0) {
                    Platform.fetch('/archimate/api/elements/' + elId + '/detail', { silent: true })
                    .then(function(data) {
                        if (self.selectedNode && self.selectedNode.elementId === elId) {
                            self.selectedNode.description = data.description || '';
                            if (data.description) cell.set('localDescription', data.description);
                            self.selectedNode.relationshipCount = data.relationship_count || 0;
                            self.selectedNode.viewpointCount = data.viewpoint_count || 0;
                            self.selectedNode.solutionCount = data.solution_count || 0;
                            self.selectedNode.scope = data.scope || '';
                            self.selectedNode.buildingBlock = data.building_block_type || '';
                            /* REQ-CMP-001: enriched context */
                            self.selectedNode._linkedSolutions = data.linked_solutions || [];
                            self.selectedNode._linkedCapabilities = data.linked_capabilities || [];
                            self.selectedNode._connectedElements = data.connected_elements || [];
                            /* GAP-CMP-008: Requirements traceability */
                            self.selectedNode._linkedRequirements = data.linked_requirements || [];
                            /* GAP-CMP-009: Data classification & PII from custom_properties */
                            const cp = data.custom_properties || {};
                            self.selectedNode._dataClassification = cp.data_classification || '';
                            self.selectedNode._containsPII = cp.contains_pii || false;
                            /* GAP-CMP-004: Lifecycle transition history */
                            self.selectedNode._lifecycleHistory = cp.lifecycle_history || [];
                            /* GAP-INT-002: Restore zone type from custom_properties */
                            if (cp.zone_type && cell.get('elType') && (cell.get('elType') === 'Grouping' || cell.get('elType') === 'Location')) {
                                self.selectedNode._zoneType = cp.zone_type;
                                cell.set('zoneType', cp.zone_type);
                            }
                            /* GAP-INT-004: Restore event badge from custom_properties */
                            if (cp.event_schedule && cell) {
                                cell.set('eventSchedule', cp.event_schedule);
                                cell.attr('intelligenceBadge/text', cp.event_schedule);
                                cell.attr('intelligenceBadge/display', 'block');
                                cell.attr('intelligenceBadge/fill', '#ef4444');
                            }
                            /* GAP-INT-007: Interface metadata for ApplicationInterface */
                            self.selectedNode._interfaceMetadata = data.interface_metadata || null;
                        }
                    })
                    .catch(function() { /* detail enrichment is best-effort */ _toast('error', 'Failed to load element details'); });
                    /* CMP-043: Load custom properties from server */
                    self.loadCustomPropsFromServer(elId);
                }

                self.$nextTick(function() {
                    if (window.lucide) lucide.createIcons();
                });

                /* Neighbor focus in view mode */
                if (self.mode === 'view') {
                    self._applyNeighborFocus(cell);
                }
            });

            /* ── Link click → select with highlight + detail panel ── */
            this.paper.on('link:pointerclick', function(linkView) {
                let link = linkView.model;
                let srcCell = self.graph.getCell(link.get('source').id);
                let tgtCell = self.graph.getCell(link.get('target').id);

                if (self.selectedLink && self.selectedLink !== link) {
                    self._unhighlightLink(self.selectedLink);
                }
                self.selectedLink = link;
                self._highlightLink(link);

                self.selectedNode = null;
                self.componentsPanelOpen = false;
                self.selectedEdge = {
                    relType: link.get('relType') || 'association',
                    relId: link.get('relId') || null,
                    source: srcCell ? (srcCell.get('elName') || '?') : '?',
                    target: tgtCell ? (tgtCell.get('elName') || '?') : '?',
                    sourceId: srcCell ? srcCell.get('elementId') : null,
                    targetId: tgtCell ? tgtCell.get('elementId') : null,
                    routingStyle: (link.get('router') || {}).name || 'manhattan',
                    accessMode: link.get('accessMode') || '',
                    flowLabel: link.get('flowLabel') || '',
                    description: link.get('description') || '',
                    customLabel: link.get('customLabel') || '',
                    /* GAP-CMP-010: Cardinality labels */
                    sourceCardinality: link.get('sourceCardinality') || '',
                    targetCardinality: link.get('targetCardinality') || '',
                    /* GAP-INT-001: Connection specification */
                    _connSpec: (function() {
                        const cs = link.get('connectionSpec') || {};
                        return {
                            data_name: cs.data_name || '',
                            transfer_strategy: cs.transfer_strategy || '',
                            interface_type: cs.interface_type || '',
                            iam_method: cs.iam_method || '',
                            file_format: cs.file_format || '',
                            file_name_pattern: cs.file_name_pattern || '',
                            protocol: cs.protocol || '',
                        };
                    })(),
                };
                self.$nextTick(function() {
                    if (window.lucide) lucide.createIcons();
                });
            });

            /* ── Link right-click → relationship context menu ── */
            this.paper.on('link:contextmenu', function(linkView, evt) {
                if (self.mode === 'view') return;
                evt.preventDefault();
                self.relCtxMenuLink = linkView.model;
                self.relCtxMenuX = evt.clientX;
                self.relCtxMenuY = evt.clientY;
                self.relCtxMenuOpen = true;
                self.$nextTick(function() {
                    if (window.lucide) lucide.createIcons();
                    let menu = document.querySelector('[aria-label="Relationship actions"].context-menu');
                    if (menu) {
                        let pos = self._fitCtxMenu(menu, evt.clientX, evt.clientY);
                        self.relCtxMenuX = pos.x;
                        self.relCtxMenuY = pos.y;
                    }
                });
            });

            /* ── Link double-click → insert waypoint ── */
            this.paper.on('link:pointerdblclick', function(linkView, evt) {
                /* ENT-106: Inline relationship label editing.
                   Double-click opens a floating text input at the link midpoint.
                   Empty text removes the custom annotation. Undo/redo-tracked. */
                if (self.mode === 'view') return;
                evt.stopPropagation();
                let link = linkView.model;
                let paperEl = self.paper.el;
                let paperRect = paperEl.getBoundingClientRect();
                let scale = self.paper.scale();
                let translate = self.paper.translate();

                /* Mid-point of link in client coordinates */
                let midPt = linkView.getPointAtRatio(0.5);
                let clientX = midPt.x * scale.sx + translate.tx + paperRect.left;
                let clientY = midPt.y * scale.sy + translate.ty + paperRect.top;

                let prevLabel = link.get('customLabel') || '';

                let input = document.createElement('input');
                input.type = 'text';
                input.value = prevLabel;
                input.placeholder = 'Add label…';
                input.style.cssText = [
                    'position:fixed',
                    'z-index:9999',
                    'left:' + (clientX - 90) + 'px',
                    'top:' + (clientY - 13) + 'px',
                    'width:180px',
                    'height:26px',
                    'font-size:11px',
                    'font-family:Inter,system-ui,sans-serif',
                    'padding:2px 6px',
                    'border:1.5px solid #6366f1',
                    'border-radius:4px',
                    'background:#fff',
                    'color:#0f172a',
                    'outline:none',
                    'box-shadow:0 2px 8px rgba(0,0,0,.15)',
                    'text-align:center',
                ].join(';');

                document.body.appendChild(input);
                input.focus();
                input.select();

                let committed = false;
                function applyLabel() {
                    if (committed) return;
                    committed = true;
                    if (document.body.contains(input)) { document.body.removeChild(input); }
                    let newLabel = input.value.trim();
                    if (newLabel === prevLabel) return;
                    let prevL = prevLabel;
                    let newL = newLabel;
                    UndoStack.push({
                        undo: function() {
                            link.set('customLabel', prevL || null);
                            _applyCustomLabel(link, prevL);
                        },
                        redo: function() {
                            link.set('customLabel', newL || null);
                            _applyCustomLabel(link, newL);
                        },
                    });
                    link.set('customLabel', newLabel || null);
                    _applyCustomLabel(link, newLabel);
                    self.statusText = newLabel ? 'Label set: "' + newLabel + '"' : 'Label removed';
                }

                input.addEventListener('keydown', function(e) {
                    if (e.key === 'Enter') { applyLabel(); }
                    if (e.key === 'Escape') {
                        committed = true;
                        if (document.body.contains(input)) { document.body.removeChild(input); }
                    }
                });
                input.addEventListener('blur', applyLabel, { once: true });
            });

            /* ── Auto-embed: detect drop onto a white-box container ── */
            this.paper.on('element:pointerup', function(cellView) {
                /* MM-02: Restore opacity after drag */
                if (cellView._dragDimmed) {
                    cellView.el.style.opacity = '';
                    cellView.el.style.filter = '';
                    cellView._dragDimmed = false;
                }
                if (self.mode === 'view') return;
                let child = cellView.model;
                if (child.getParentCell()) return;

                let childBBox = child.getBBox();
                let childCenter = childBBox.center();
                let candidates = self.graph.getElements().filter(function(el) {
                    if (el.id === child.id) return false;
                    if (el.getParentCell() === child) return false;
                    let elBBox = el.getBBox();
                    /* Only trigger for elements that are meaningfully larger than the child
                     * (parent must be ≥20% wider AND ≥20% taller) to prevent same-size
                     * elements from accidentally triggering the nesting prompt when they
                     * overlap slightly. Also accept explicit white_box containers. */
                    let isWhiteBox = el.get('renderingMode') === 'white_box';
                    let isSignificantlyLarger = elBBox.width > childBBox.width * 1.2
                                             && elBBox.height > childBBox.height * 1.2;
                    return elBBox.containsPoint(childCenter)
                        && (isWhiteBox || isSignificantlyLarger);
                });

                if (candidates.length === 0) return;

                let parent = candidates[candidates.length - 1];
                let parentType = parent.get('elType') || '';
                if (!isContainerType(parentType)) return;

                let depth = getNestingDepth(parent);
                if (depth >= 3) {
                    self.statusText = 'Nesting depth limit: max 3 levels';
                    return;
                }

                self.nestingPromptChild = child;
                self.nestingPromptParent = parent;
                self.nestingPromptOpen = true;
                self._clearAlignGuides();
            });

            /* ── Smart alignment guides while dragging ── */
            this.paper.on('element:pointermove', function(cellView) {
                if (self.mode !== 'edit') return;
                self._showAlignGuides(cellView.model);
                /* MM-02: Visual feedback while dragging (opacity + lifted feel) */
                if (!cellView._dragDimmed) {
                    cellView.el.style.opacity = '0.75';
                    cellView.el.style.filter = 'drop-shadow(0 4px 8px rgba(0,0,0,0.15))';
                    cellView._dragDimmed = true;
                }
            });

            /* ── Double-click → inline rename (edit mode) or inspect (view mode) ── */
            this.paper.on('element:pointerdblclick', function(cellView, evt) {
                /* CMP-040: Drill-down on double-click if element has linked viewpoint */
                if (cellView.model.get('linkedViewpointId')) {
                    self.drillDown(cellView.model);
                    return;
                }
                /* CMP-052: Annotations open their own text editor */
                if (cellView.model.get('isAnnotation')) {
                    self._annotEditCell = cellView.model;
                    self.annotEditText = cellView.model.get('annotText') || '';
                    self.annotEditOpen = true;
                    return;
                }
                if (self.mode === 'edit') {
                    self._startInlineRename(cellView, evt);
                } else {
                    self.ctxMenuCell = cellView.model;
                    self.inspectElement();
                }
            });

            /* ── Blank click → deselect + clear focus ── */
            this.paper.on('blank:pointerclick', function() {
                self.relPickerOpen = false;
                self.ctxMenuOpen = false;
                self.relCtxMenuOpen = false;
                self.canvasCtxMenuOpen = false;
                self.selectedNode = null;
                self.selectedEdge = null;
                self.componentsPanelOpen = true;
                if (self.selectedLink) {
                    self._unhighlightLink(self.selectedLink);
                    self.selectedLink = null;
                }
                self._clearNeighborFocus();
            });

            /* ── Port affordance discovery hint (ENT-120) ── */
            this.paper.on('element:mouseenter', function(cellView) {
                if (self.mode === 'view') return;
                /* CMP-14 (revised): only raise the hovered element when it
                   ACTUALLY overlaps a neighbour — the case where its ports would
                   otherwise sit under another box. Raising on every hover (the
                   first version) reshuffled z-order across the whole diagram as
                   the mouse moved and pushed elements above the relationship
                   links, so arrows blinked out under whatever box was hovered.
                   We also keep links on top afterward so relationships never hide
                   behind a raised element, and use a shallow toFront (no deep) to
                   avoid dragging embedded children around. */
                if (cellView && cellView.model && !self._lockedCells[cellView.model.id]
                        && self._overlapsAnyElement(cellView.model)) {
                    cellView.model.toFront();
                    self._raiseLinks();
                }
                let el = cellView.el;
                if (el) {
                    el.classList.add('port-discover');
                    setTimeout(function() { el.classList.remove('port-discover'); }, 400);
                }
                /* One-time "drag from a port" hint */
                if (!self._portHintShown && !self.connectModeActive) {
                    let hasElements = Object.keys(self.canvasElements || {}).length;
                    if (hasElements >= 2) {
                        self._portHintShown = true;
                        _toast('info', '💡 Drag from a blue port dot to draw a connection — or press C to enter connect mode');
                    }
                }
            });

            /* ── Right-click on blank canvas ── */
            this.paper.on('blank:contextmenu', function(evt) {
                evt.preventDefault();
                self.canvasCtxMenuX = evt.clientX;
                self.canvasCtxMenuY = evt.clientY;
                self._canvasCtxPaperCoords = self.paper.clientToLocalPoint({ x: evt.clientX, y: evt.clientY });
                self.canvasCtxMenuOpen = true;
                self.ctxMenuOpen = false;
                self.relCtxMenuOpen = false;
                self.$nextTick(function() {
                    if (window.lucide) lucide.createIcons();
                    let menu = document.querySelector('[aria-label="Canvas actions"].context-menu');
                    if (menu) {
                        let pos = self._fitCtxMenu(menu, evt.clientX, evt.clientY);
                        self.canvasCtxMenuX = pos.x;
                        self.canvasCtxMenuY = pos.y;
                    }
                });
            });

            /* ── Keyboard shortcuts ── */
            document.addEventListener('keydown', function(e) {
                /* Allow typing in text inputs, EXCEPT for Ctrl+A which should select all canvas elements.
                   Also allow Escape to close modals from any context. */
                let inTextInput = (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA');
                if (inTextInput) {
                    let isSelectAll = (e.ctrlKey || e.metaKey) && e.key === 'a';
                    let isEscape = e.key === 'Escape';
                    if (!isSelectAll && !isEscape) return;
                    /* Blur the input so selectAll targets canvas, not text */
                    if (isSelectAll) { e.target.blur(); }
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
                    e.preventDefault();
                    if (e.shiftKey) { self.redo(); } else { self.undo(); }
                }
                if ((e.ctrlKey || e.metaKey) && (e.key === '=' || e.key === '+')) {
                    e.preventDefault(); self.zoomIn();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === '-') {
                    e.preventDefault(); self.zoomOut();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === '0') {
                    e.preventDefault(); self.fitCanvas();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'y') {
                    e.preventDefault(); self.redo();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                    e.preventDefault(); self.saveViewpoint();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'a' && self.mode === 'edit') {
                    e.preventDefault(); self.selectAll();
                }
                if (self.mode === 'edit' && (e.key === 'Delete' || e.key === 'Backspace')) {
                    self.deleteSelected();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                    e.preventDefault();
                    self.openQuickAdd();
                }
                if (e.key === '/' && self.mode === 'edit') {
                    e.preventDefault();
                    self.openQuickAdd();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
                    e.preventDefault();
                    self.openCanvasSearch();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'h') {
                    e.preventDefault();
                    self.openSearchReplace();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'c' && self.mode === 'edit') {
                    self._copySelected();
                }
                /* MM-10: Ctrl+X = Cut (copy + delete) */
                if ((e.ctrlKey || e.metaKey) && e.key === 'x' && self.mode === 'edit') {
                    e.preventDefault();
                    if (self._selectedCells.length === 0) return;
                    self._copySelected();
                    self._pushUndo();
                    self.confirmBulkDelete();
                    self.statusText = 'Cut ' + self._clipboard.length + ' element(s) to clipboard';
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'v' && self.mode === 'edit') {
                    e.preventDefault();
                    self._pasteClipboard();
                }
                if ((e.ctrlKey || e.metaKey) && e.key === 'd' && self.mode === 'edit') {
                    e.preventDefault();
                    self._duplicateSelected();
                }
                if (e.key === '?' && !e.ctrlKey && !e.metaKey && !e.altKey) {
                    e.preventDefault();
                    self.showShortcutsModal = true;
                }
                /* Cancel connect mode on Escape */
                if (e.key === 'Escape' && self.connectModeActive) {
                    self.connectModeActive = false;
                    self.connectModeSource = null;
                    self._clearSelection();
                    self.statusText = 'Edit mode';
                    if (self.paper) self.paper.el.style.cursor = '';
                }
                /* C — toggle Connect Mode */
                if (e.key === 'c' && !e.ctrlKey && !e.metaKey && !e.altKey && self.mode === 'edit') {
                    e.preventDefault();
                    self.toggleConnectMode();
                }
                /* Shift+K — open colour override picker for selected element(s) */
                if (e.key === 'K' && !e.ctrlKey && !e.metaKey && e.shiftKey && self.mode === 'edit') {
                    let hasSelection = (self._selectedCells && self._selectedCells.length) || self._currentSelectedCell;
                    if (hasSelection) {
                        e.preventDefault();
                        /* Position picker at centre-top of viewport */
                        let canvasEl = document.getElementById('composer-canvas');
                        if (canvasEl) {
                            let rect = canvasEl.getBoundingClientRect();
                            self.colourPickerX = rect.left + rect.width / 2 - 120;
                            self.colourPickerY = rect.top + 60;
                        }
                        self.colourPickerOpen = true;
                    }
                }
                /* G — toggle Grid visibility */
                if (e.key === 'g' && !e.ctrlKey && !e.metaKey && !e.altKey) {
                    e.preventDefault();
                    self.toggleGrid();
                }
                /* L — toggle Lock on selected element(s) */
                if (e.key === 'l' && !e.ctrlKey && !e.metaKey && !e.altKey && self.mode === 'edit') {
                    e.preventDefault();
                    self.toggleLock();
                }
                /* N — new element (open Quick Add) */
                if (e.key === 'n' && !e.ctrlKey && !e.metaKey && !e.altKey && self.mode === 'edit') {
                    e.preventDefault();
                    self.openQuickAdd();
                }
                /* R — relate selected pair (exactly 2 elements selected) */
                if (e.key === 'r' && !e.ctrlKey && !e.metaKey && !e.altKey && self.mode === 'edit') {
                    if (self._selectedCells.length === 2) {
                        e.preventDefault();
                        self._openRelPickerForPair(self._selectedCells[0], self._selectedCells[1]);
                    } else if (self._selectedCells.length !== 2) {
                        self.statusText = 'Select exactly 2 elements to relate (Shift+click)';
                    }
                }
                /* Tab — cycle through connected elements */
                if (e.key === 'Tab' && !e.ctrlKey && !e.metaKey && !e.altKey) {
                    let anchor = (self._selectedCells.length === 1) ? self._selectedCells[0] : self._currentSelectedCell;
                    if (anchor && self.graph) {
                        e.preventDefault();
                        let neighbors = self.graph.getNeighbors(anchor);
                        if (neighbors.length > 0) {
                            /* Reset cycle if anchor changed */
                            if (self._tabCycleNeighbors.length === 0 ||
                                self._tabCycleNeighbors !== neighbors ||
                                self._tabCycleIndex >= neighbors.length) {
                                self._tabCycleNeighbors = neighbors;
                                self._tabCycleIndex = -1;
                            }
                            self._tabCycleIndex = (self._tabCycleIndex + 1) % neighbors.length;
                            let nextCell = neighbors[self._tabCycleIndex];
                            self._clearSelection();
                            self._selectedCells = [nextCell];
                            self._currentSelectedCell = nextCell;
                            let view = self.paper.findViewByModel(nextCell);
                            if (view) self._highlightCell(view);
                            /* Scroll into view */
                            let pos = nextCell.position();
                            let size = nextCell.size();
                            self.paper.scrollToPoint(pos.x + size.width / 2, pos.y + size.height / 2);
                            self.statusText = 'Tab: ' + (nextCell.get('elName') || 'element') + ' (' + (self._tabCycleIndex + 1) + '/' + neighbors.length + ')';
                        } else {
                            self.statusText = 'No connected elements';
                        }
                    }
                }
                /* 1-6 — toggle layer visibility */
                let layerKeys = { '1': 'business', '2': 'application', '3': 'technology', '4': 'motivation', '5': 'strategy', '6': 'implementation' };
                if (layerKeys[e.key] && !e.ctrlKey && !e.metaKey && !e.altKey) {
                    e.preventDefault();
                    self.toggleLayerVisibility(layerKeys[e.key]);
                    let layerName = layerKeys[e.key];
                    self.statusText = layerName.charAt(0).toUpperCase() + layerName.slice(1) + ' layer ' + (self.layerVisibility[layerName] ? 'visible' : 'hidden');
                }
            });

            /* ── Arrow keys: nudge selected elements ── */
            document.addEventListener('keydown', function(e) {
                if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
                if (self.mode !== 'edit') return;
                let arrows = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] };
                let dir = arrows[e.key];
                if (!dir) return;
                let cells = self._selectedCells && self._selectedCells.length > 0
                    ? self._selectedCells
                    : (self._currentSelectedCell ? [self._currentSelectedCell] : []);
                if (cells.length === 0) return;
                e.preventDefault();
                /* Shift = 1px fine nudge, default = 1 grid unit (12px) */
                let step = e.shiftKey ? 1 : 12;
                let dx = dir[0] * step;
                let dy = dir[1] * step;
                cells.forEach(function(cell) {
                    if (cell.isLink && cell.isLink()) return;
                    if (self._lockedCells[cell.id]) return;
                    let pos = cell.position();
                    cell.position(pos.x + dx, pos.y + dy);
                });
            });

            /* ── Space key: hold to pan in edit mode ── */
            document.addEventListener('keydown', function(e) {
                if (e.code === 'Space' && !e.target.matches('input, textarea, [contenteditable]')) {
                    e.preventDefault();
                    self._spaceDown = true;
                    if (self.mode === 'edit' && self.paper) self.paper.el.style.cursor = 'grab';
                }
            });
            document.addEventListener('keyup', function(e) {
                if (e.code === 'Space') {
                    self._spaceDown = false;
                    if (self.paper && !self._isPanning) self.paper.el.style.cursor = '';
                }
            });

            /* ── Dirty state tracking ── */
            this.graph.on('change:position', function() { self.viewpointDirty = true; });
            this.graph.on('change:size', function() { self.viewpointDirty = true; });
            this.graph.on('add', function() { self.viewpointDirty = true; });
            this.graph.on('remove', function() { self.viewpointDirty = true; });

            /* ── Mini-map: update on graph/paper changes ── */
            let mmUpdate = function() { self._scheduleMiniMapUpdate(); };
            this.graph.on('change:position', mmUpdate);
            this.graph.on('change:size', mmUpdate);
            this.graph.on('add', mmUpdate);
            this.graph.on('remove', mmUpdate);
            /* Update mini-map on pan (translate changes) via mousemove debounce */
            this.paper.el.addEventListener('mouseup', mmUpdate);
            this.paper.el.addEventListener('wheel', mmUpdate, { passive: true });

            /* ── CMP-016: Debounced suggestion fetching on graph changes ── */
            let suggestionTrigger = function() {
                if (!self.suggestionsEnabled) return;
                clearTimeout(self._suggestionTimer);
                self._suggestionTimer = setTimeout(function() { self.fetchSuggestions(); }, 10000);
            };
            this.graph.on('add', suggestionTrigger);
            this.graph.on('remove', suggestionTrigger);

            /* ── CMP2-009: Debounced compliance score on graph changes ── */
            let complianceTrigger = function() {
                clearTimeout(self._complianceTimer);
                self._complianceTimer = setTimeout(function() {
                    if (typeof self.computeComplianceScore === 'function') {
                        self.computeComplianceScore();
                    }
                }, 1000);
            };
            this.graph.on('add', complianceTrigger);
            this.graph.on('remove', complianceTrigger);
            this.graph.on('change:source', complianceTrigger);
            this.graph.on('change:target', complianceTrigger);

            /* CMP2-007: Auto-regenerate relationship matrix when graph changes */
            let relMatrixRefresh = _.debounce(function() {
                if (self.relMatrixOpen) self.generateRelMatrix();
            }, 300);
            this.graph.on('add', relMatrixRefresh);
            this.graph.on('remove', relMatrixRefresh);
            this.graph.on('change:source', relMatrixRefresh);
            this.graph.on('change:target', relMatrixRefresh);
            this.graph.on('change:relType', relMatrixRefresh);
            this.graph.on('change:elName', relMatrixRefresh);

            window.addEventListener('beforeunload', function(e) {
                if (self.viewpointDirty && self.currentSavedVpId) {
                    e.preventDefault();
                    e.returnValue = '';
                }
            });

            /* ── Auto-save every 30s if dirty (C-03: also creates the
             * SavedDiagram server-side on first tick if the canvas has
             * never been manually saved — see _autoSave / _autoCreateSavedDiagram) ── */
            this._autoSaveTimer = setInterval(function() {
                if (self.viewpointDirty) {
                    self._autoSave();
                }
            }, 30000);

            /* ── Update autosave age label every 60s ── */
            setInterval(function() {
                if (!self.lastSavedAt) return;
                let diff = Math.floor((Date.now() - self.lastSavedAt) / 60000);
                self._autosaveLabel = diff === 0 ? 'just now' : diff === 1 ? '1 min ago' : diff + ' mins ago';
            }, 60000);

            /* ── CMP-028: Auto-persist to localStorage every 10s ── */
            let AUTOSAVE_KEY = 'composer_autosave_' + (self.solutionId || 'scratch');  // secrets-safety-ok
            try {
                let savedData = localStorage.getItem(AUTOSAVE_KEY);
                if (savedData && self.graph.getElements().length === 0) {
                    self._pendingAutosaveRestore = savedData;
                    self._showAutosavePrompt = true;
                }
            } catch(_) { /* swallow-ok: reading the localStorage crash-recovery snapshot throws in private mode; there is then simply no restore prompt, which promised the user nothing */ }
            setInterval(function() {
                if (!self.graph || !self.viewpointDirty) return;
                if (self.graph.getElements().length === 0) return;
                try {
                    localStorage.setItem(AUTOSAVE_KEY, JSON.stringify({
                        graph: self.graph.toJSON(),
                        timestamp: Date.now(),
                        elementCount: self.elementCount,
                        solutionId: self.solutionId,
                        // Preserve the diagram's identity so a restore updates the
                        // existing row rather than POSTing a duplicate on the next save.
                        currentSavedVpId: self.currentSavedVpId || null
                    }));
                } catch (e) {
                    /* C-03: this used to warn only to the console — invisible
                     * to the user. The primary autosave is server-side (see
                     * _autoSave above); this localStorage snapshot is only a
                     * secondary crash-recovery fallback, so a single quota
                     * failure is not an emergency, but the user must be told
                     * once rather than never — surfaced once per session,
                     * not every 10s. */
                    if (!self._localAutosaveWarned) {
                        self._localAutosaveWarned = true;
                        _toast('warning', 'Local browser backup is full or unavailable — your work is still being saved to the server.');
                    }
                }
            }, 10000);

            /* ── Wave 10: Load quality score for solution ── */
            if (self.solutionId) {
                Platform.fetch('/api/solutions/' + self.solutionId + '/quality-score', { silent: true })
                    .then(function(data) { if (data) self.qualityScore = data; })
                    .catch(function() { /* swallow-ok: unrequested enrichment fetched on canvas open; qualityScore stays null so the toolbar badge (x-if="qualityScore") is simply absent — it never shows a made-up percentage — and a network failure or non-ok response while opening the canvas must not interrupt the modelling task with a toast about a badge */ });
            }

            /* ── Check for saved viewpoint_id in URL ── */
            let urlParams = new URLSearchParams(window.location.search);
            let savedVpId = urlParams.get('viewpoint_id');
            if (savedVpId) {
                this.loadViewpointTabs();
                this.loadSavedViewpoint(parseInt(savedVpId, 10), '');
                this.$nextTick(function() { self.viewpointDirty = false; UndoStack.clear(); });
                return;
            }

            /* ── Check for initial viewpoint from URL ── */
            let initialVp = (window.__COMPOSER_CONFIG__ || {}).initialViewpoint;
            let initialLayer = (window.__COMPOSER_CONFIG__ || {}).initialLayer;
            /* A composer link can legitimately pass `layer` with no `viewpoint`
             * (e.g. traceability_chain.html's "+ Add" buttons, which only know
             * which layer to seed). Without a fallback here that layer was
             * silently dropped and the composer opened generically — default
             * to the 'layered' viewpoint so a layer-only link still does
             * something sensible, matching the `?viewpoint=layered&layer=X`
             * convention used everywhere else in this codebase. */
            if (!initialVp && initialLayer) {
                initialVp = 'layered';
            }
            if (initialVp) {
                this.selectViewpoint(initialVp, initialVp, initialLayer);
                return;
            }

            /* ── ENT-121: Prefill generate modal from AI Chat sessionStorage ── */
            let prefillParam = urlParams.get('prefill');
            if (prefillParam === '1') {
                try {
                    let prefillRaw = sessionStorage.getItem('composer_prefill');
                    let prefillData = prefillRaw ? JSON.parse(prefillRaw) : null;
                    if (prefillData && Array.isArray(prefillData.elements) && prefillData.elements.length > 0) {
                        sessionStorage.removeItem('composer_prefill');
                        let layerMap = {
                            ApplicationComponent: 'application', ApplicationService: 'application',
                            ApplicationInterface: 'application', ApplicationProcess: 'application',
                            ApplicationFunction: 'application', ApplicationEvent: 'application',
                            ApplicationInteraction: 'application', ApplicationCollaboration: 'application',
                            DataObject: 'application',
                            BusinessActor: 'business', BusinessRole: 'business', BusinessProcess: 'business',
                            BusinessService: 'business', BusinessFunction: 'business',
                            BusinessEvent: 'business', BusinessInteraction: 'business',
                            BusinessCollaboration: 'business', BusinessInterface: 'business',
                            BusinessObject: 'business', Contract: 'business', Product: 'business',
                            TechnologyService: 'technology', TechnologyComponent: 'technology',
                            TechnologyInterface: 'technology', TechnologyProcess: 'technology',
                            TechnologyFunction: 'technology', TechnologyEvent: 'technology',
                            TechnologyInteraction: 'technology', TechnologyCollaboration: 'technology',
                            Node: 'technology', SystemSoftware: 'technology', Device: 'technology',
                            Network: 'technology', CommunicationNetwork: 'technology', Path: 'technology',
                            Artifact: 'technology',
                            Capability: 'strategy', Resource: 'strategy', CourseOfAction: 'strategy',
                            ValueStream: 'strategy',
                            Goal: 'motivation', Driver: 'motivation', Assessment: 'motivation',
                            Requirement: 'motivation', Constraint: 'motivation', Principle: 'motivation',
                            Meaning: 'motivation', Value: 'motivation', Stakeholder: 'motivation',
                            WorkPackage: 'implementation', Deliverable: 'implementation',
                            ImplementationEvent: 'implementation', Gap: 'implementation',
                            Plateau: 'implementation',
                        };
                        this.generatedElements = prefillData.elements.map(function(e) {
                            return {
                                name: e.name || '',
                                type: e.type || 'ApplicationComponent',
                                layer: layerMap[e.type] || 'application',
                                description: e.description || e.reasoning || '',
                                category: 'new',
                                _accepted: false,
                            };
                        });
                        this.generatedRelationships = [];
                        this.generateModalOpen = true;
                        let appName = prefillData.app_name ? ' for ' + prefillData.app_name : '';
                        _toast('info', 'ArchiMate elements' + appName + ' loaded from AI Chat — review and accept to place on canvas');
                        return;
                    }
                } catch (_) { /* swallow-ok: defensive parse of our own sessionStorage prefill; malformed or absent simply means no prefill and the composer opens as normal */ }
            }

            /* ── Load existing data ── */
            let startBlank = (window.__COMPOSER_CONFIG__ || {}).startBlank;
            if (this.solutionId && !startBlank) {
                this.loadSolutionData();
            } else {
                this.statusText = 'Drag elements from the palette to start';
            }
            /* ENT-107: Load tab strip after canvas is ready */
            this.loadViewpointTabs();
            /* ENT-111: Check for stale relationships after Abacus sync */
            if (this.currentSavedVpId) { this.checkRelationshipHealth(); }

            /* ENT-121: Check for AI Chat prefill payload in sessionStorage */
            this._checkComposerPrefill();
        },

        /* ── Mode toggle ──────────────────────────────────── */
        toggleMode: function() {
            this.mode = this.mode === 'view' ? 'edit' : 'view';
            this.editMode = this.mode === 'edit';
            /* Exiting edit mode always clears connect mode */
            if (this.mode === 'view') {
                this.connectModeActive = false;
                this.connectModeSource = null;
            }
            this._clearNeighborFocus();
            this.statusText = this.mode === 'view' ? 'View mode' : 'Edit mode';
            this.$nextTick(function() {
                if (window.lucide) lucide.createIcons();
            });
        },

        /* ── ENT-121: Read AI Chat prefill payload from sessionStorage ── */
        _checkComposerPrefill: function() {
            let self = this;
            let params = new URLSearchParams(window.location.search);
            if (!params.has('prefill')) return;
            let raw = null;
            /* sessionStorage throws in private/incognito mode — best-effort, no prefill if unavailable */
            try { raw = sessionStorage.getItem('composer_prefill'); } catch (_) { /* swallow-ok: sessionStorage throws in private mode; raw stays null and the prefill is skipped, which is the no-prefill path the user already expects */ }
            if (!raw) return;
            let payload = null;
            try { payload = JSON.parse(raw); } catch (_) { return; }
            /* Only use if < 5 min old */
            if (!payload || !payload.elements || !Array.isArray(payload.elements)) return;
            if (payload.timestamp && (Date.now() - payload.timestamp) > 300000) return;

            /* Clear so refresh doesn't re-trigger — best-effort, same private-mode caveat as above */
            try { sessionStorage.removeItem('composer_prefill'); } catch (_) { /* swallow-ok: best-effort cleanup so a refresh does not re-trigger the prefill; same private-mode caveat as the read above */ }

            /* Normalise element shape to match composer_ai.js expectations */
            let elements = payload.elements.map(function(e) {
                return {
                    name: e.name || e.element_name || 'Unnamed',
                    type: e.type || e.element_type || 'ApplicationComponent',
                    layer: e.layer || '',
                    category: 'new',
                };
            });
            let relationships = (payload.relationships || []).map(function(r) {
                return {
                    source_name: r.source_name || r.source || '',
                    target_name: r.target_name || r.target || '',
                    type: r.type || r.relationship_type || 'association',
                };
            });

            self.$nextTick(function() {
                /* Open the AI Generate modal pre-populated */
                self.generateModalOpen = true;
                self.generateDescription = payload.app_name
                    ? 'Imported from AI Chat: ' + payload.app_name
                    : 'Imported from AI Chat';
                self.generatedElements = elements;
                self.generatedRelationships = relationships;
                self.generateGaps = [];
                self.generateRationale = 'Pre-filled from AI Chat /generate-archimate command';
                self._acceptedNameToId = {};
                self.generateLoading = false;
                _toast('info', 'AI Chat elements loaded — review and accept to place on canvas');
            });
        },

        toggleConnectMode: function() {
            if (this.mode === 'view') return;
            this.connectModeActive = !this.connectModeActive;
            this.connectModeSource = null;
            this._clearSelection();
            this.statusText = this.connectModeActive
                ? 'Connect mode: click a source element, then a target element'
                : 'Edit mode';
            if (this.paper) {
                this.paper.el.style.cursor = this.connectModeActive ? 'crosshair' : '';
            }
        },

        /* ── Grid show/hide toggle ────────────────────────── */
        toggleGrid: function() {
            this.showGrid = !this.showGrid;
            if (!this.paper) return;
            /* JointJS renders the grid as an SVG <rect> with a pattern fill.
             * Toggling drawGrid option and calling drawGrid() re-renders it. */
            this.paper.options.drawGrid = this.showGrid ? [
                { name: 'dot', args: { color: '#dde1e6', thickness: 1 } },
                { name: 'dot', args: { color: '#c8cdd3', thickness: 1, scaleFactor: 5 } },
            ] : false;
            try { this.paper.drawGrid(); } catch(e) { /* swallow-ok: cosmetic grid redraw; statusText below still reports whether the grid is on or off */ }
            this.statusText = this.showGrid ? 'Grid visible' : 'Grid hidden';
        },

        /* ── Element locking ─────────────────────────────────
         *
         * Locked elements cannot be moved or resized. The lock state is stored
         * in _lockedCells (keyed by cell id) and also on the cell itself via
         * cell.set('locked', true) so it is preserved in save/load. A dashed
         * amber outline is added as a visual indicator.
         */
        toggleLock: function() {
            let self = this;
            if (self.mode !== 'edit') return;
            let targets = self._selectedCells.length > 0
                ? self._selectedCells.slice()
                : (self._currentSelectedCell ? [self._currentSelectedCell] : []);
            if (targets.length === 0) return;

            targets.forEach(function(cell) {
                if (cell.isLink && cell.isLink()) return; /* Only lock elements */
                let id = cell.id;
                let nowLocked = !self._lockedCells[id];
                if (nowLocked) {
                    self._lockedCells[id] = true;
                    cell.set('locked', true);
                    self.lockedCount++;
                } else {
                    delete self._lockedCells[id];
                    cell.set('locked', false);
                    self.lockedCount = Math.max(0, self.lockedCount - 1);
                }
                /* Visual: amber dashed border when locked, removed when unlocked */
                let view = self.paper && self.paper.findViewByModel(cell);
                if (view) {
                    try {
                        if (nowLocked) {
                            view.highlight(null, {
                                highlighter: { name: 'stroke', options: {
                                    padding: 3, rx: 4,
                                    attrs: { stroke: '#f59e0b', 'stroke-width': 2, 'stroke-dasharray': '4,3' },
                                }},
                            });
                        } else {
                            view.unhighlight(null, {
                                highlighter: { name: 'stroke', options: {
                                    padding: 3, rx: 4,
                                    attrs: { stroke: '#f59e0b', 'stroke-width': 2, 'stroke-dasharray': '4,3' },
                                }},
                            });
                        }
                    } catch(e) { /* swallow-ok: cosmetic lock border; the lock state itself is already applied to the cell and counted in lockedCount */ }
                }
            });

            let lockCount = Object.keys(self._lockedCells).length;
            self.statusText = lockCount > 0
                ? lockCount + ' element(s) locked — press L or use context menu to unlock'
                : 'All elements unlocked';
        },

        /* ── Visual Group Box ────────────────────────────────
         *
         * Wraps the current selection in a labelled dashed-border rectangle
         * that acts as a visual container. The grouped elements are embedded
         * in the group box so they move with it. This is a diagram-only
         * visual grouping (isGroupBox flag) with no model semantics — distinct
         * from parent-child nesting via embedInParent().
         *
         * Keyboard: no default shortcut (group is available from context menu
         * and the toolbar "More" panel).
         */
        /* ── MM-17: Alignment + distribution for multi-select ── */
        _getMultiSelectBounds: function() {
            /* Returns bounding rect of all selected cells (for floating toolbar positioning) */
            if (this._selectedCells.length < 2) return null;
            let paper = this.paper;
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            this._selectedCells.forEach(function(cell) {
                let view = paper.findViewByModel(cell);
                if (!view) return;
                let bbox = view.el.getBoundingClientRect();
                if (bbox.left < minX) minX = bbox.left;
                if (bbox.top < minY) minY = bbox.top;
                if (bbox.right > maxX) maxX = bbox.right;
                if (bbox.bottom > maxY) maxY = bbox.bottom;
            });
            return { left: minX, top: minY, right: maxX, bottom: maxY,
                     cx: (minX + maxX) / 2, cy: (minY + maxY) / 2 };
        },

        alignSelectedLeft: function() {
            if (this._selectedCells.length < 2) return;
            let minX = Infinity;
            this._selectedCells.forEach(function(c) { minX = Math.min(minX, c.position().x); });
            let self = this;
            this._selectedCells.forEach(function(c) {
                let pos = c.position();
                c.position(minX, pos.y);
                let v = self.paper.findViewByModel(c);
                if (v) { self._unhighlightCell(v); self._highlightCell(v); }
            });
            this._pushUndo();
        },
        alignSelectedCenter: function() {
            if (this._selectedCells.length < 2) return;
            let minX = Infinity, maxX = -Infinity;
            this._selectedCells.forEach(function(c) {
                let b = c.getBBox();
                if (b.x < minX) minX = b.x;
                if (b.x + b.width > maxX) maxX = b.x + b.width;
            });
            let cx = (minX + maxX) / 2;
            let self = this;
            this._selectedCells.forEach(function(c) {
                let b = c.getBBox();
                c.position(cx - b.width / 2, c.position().y);
                let v = self.paper.findViewByModel(c);
                if (v) { self._unhighlightCell(v); self._highlightCell(v); }
            });
            this._pushUndo();
        },
        alignSelectedRight: function() {
            if (this._selectedCells.length < 2) return;
            let maxX = -Infinity;
            this._selectedCells.forEach(function(c) { let b = c.getBBox(); maxX = Math.max(maxX, b.x + b.width); });
            let self = this;
            this._selectedCells.forEach(function(c) {
                let b = c.getBBox();
                c.position(maxX - b.width, c.position().y);
                let v = self.paper.findViewByModel(c);
                if (v) { self._unhighlightCell(v); self._highlightCell(v); }
            });
            this._pushUndo();
        },
        alignSelectedTop: function() {
            if (this._selectedCells.length < 2) return;
            let minY = Infinity;
            this._selectedCells.forEach(function(c) { minY = Math.min(minY, c.position().y); });
            let self = this;
            this._selectedCells.forEach(function(c) {
                let pos = c.position();
                c.position(pos.x, minY);
                let v = self.paper.findViewByModel(c);
                if (v) { self._unhighlightCell(v); self._highlightCell(v); }
            });
            this._pushUndo();
        },
        alignSelectedMiddle: function() {
            if (this._selectedCells.length < 2) return;
            let minY = Infinity, maxY = -Infinity;
            this._selectedCells.forEach(function(c) {
                let b = c.getBBox();
                if (b.y < minY) minY = b.y;
                if (b.y + b.height > maxY) maxY = b.y + b.height;
            });
            let cy = (minY + maxY) / 2;
            let self = this;
            this._selectedCells.forEach(function(c) {
                let b = c.getBBox();
                c.position(c.position().x, cy - b.height / 2);
                let v = self.paper.findViewByModel(c);
                if (v) { self._unhighlightCell(v); self._highlightCell(v); }
            });
            this._pushUndo();
        },
        alignSelectedBottom: function() {
            if (this._selectedCells.length < 2) return;
            let maxY = -Infinity;
            this._selectedCells.forEach(function(c) { let b = c.getBBox(); maxY = Math.max(maxY, b.y + b.height); });
            let self = this;
            this._selectedCells.forEach(function(c) {
                let b = c.getBBox();
                c.position(c.position().x, maxY - b.height);
                let v = self.paper.findViewByModel(c);
                if (v) { self._unhighlightCell(v); self._highlightCell(v); }
            });
            this._pushUndo();
        },
        distributeSelectedH: function() {
            if (this._selectedCells.length < 3) return;
            let sorted = this._selectedCells.slice().sort(function(a, b) { return a.position().x - b.position().x; });
            let first = sorted[0].position().x;
            let lastB = sorted[sorted.length - 1].getBBox();
            let last = lastB.x + lastB.width;
            let totalW = 0;
            sorted.forEach(function(c) { totalW += c.getBBox().width; });
            let gap = (last - first - totalW) / (sorted.length - 1);
            let x = first;
            let self = this;
            sorted.forEach(function(c) {
                c.position(x, c.position().y);
                x += c.getBBox().width + gap;
                let v = self.paper.findViewByModel(c);
                if (v) { self._unhighlightCell(v); self._highlightCell(v); }
            });
            this._pushUndo();
        },
        distributeSelectedV: function() {
            if (this._selectedCells.length < 3) return;
            let sorted = this._selectedCells.slice().sort(function(a, b) { return a.position().y - b.position().y; });
            let first = sorted[0].position().y;
            let lastB = sorted[sorted.length - 1].getBBox();
            let last = lastB.y + lastB.height;
            let totalH = 0;
            sorted.forEach(function(c) { totalH += c.getBBox().height; });
            let gap = (last - first - totalH) / (sorted.length - 1);
            let y = first;
            let self = this;
            sorted.forEach(function(c) {
                c.position(c.position().x, y);
                y += c.getBBox().height + gap;
                let v = self.paper.findViewByModel(c);
                if (v) { self._unhighlightCell(v); self._highlightCell(v); }
            });
            this._pushUndo();
        },

        groupSelected: function() {
            let self = this;
            if (self.mode !== 'edit') return;
            let cells = self._selectedCells.filter(function(c) { return !c.isLink(); });
            if (cells.length < 2) {
                self._toast('Select two or more elements to group', 'warn');
                return;
            }

            /* Compute bounding box with padding */
            let PAD = 20;
            let bbox = cells[0].getBBox();
            cells.forEach(function(c) { bbox = bbox.union(c.getBBox()); });

            let groupCell = new joint.shapes.standard.Rectangle({
                position: { x: bbox.x - PAD, y: bbox.y - PAD },
                size:     { width: bbox.width + PAD * 2, height: bbox.height + PAD * 2 },
                attrs: {
                    body: {
                        fill: 'rgba(99,102,241,0.04)',
                        stroke: '#6366f1',
                        strokeWidth: 1.5,
                        strokeDasharray: '8,4',
                        rx: 8, ry: 8,
                    },
                    label: {
                        text: 'Group',
                        fontSize: 11,
                        fontFamily: 'Inter, sans-serif',
                        fill: '#6366f1',
                        refX: 8, refY: 8,
                        textAnchor: 'start',
                        textVerticalAnchor: 'top',
                    },
                },
                isGroupBox: true,
                groupLabel: 'Group',
                z: Math.min.apply(null, cells.map(function(c) { return c.get('z') || 0; })) - 1,
            });

            self.graph.addCell(groupCell);

            /* Embed selected elements so they travel with the group on drag */
            cells.forEach(function(c) { groupCell.embed(c); });

            /* Push group creation to UndoStack */
            UndoStack.push({
                undo: function() {
                    cells.forEach(function(c) { groupCell.unembed(c); });
                    groupCell.remove();
                },
                redo: function() {
                    self.graph.addCell(groupCell);
                    cells.forEach(function(c) { groupCell.embed(c); });
                },
            });

            /* Select the group box and clear element selection */
            self._clearSelection();
            let gv = self.paper.findViewByModel(groupCell);
            if (gv) self._highlightCell(gv);
            self._selectedCells = [groupCell];
            self.statusText = 'Grouped ' + cells.length + ' elements — drag the dashed box to move the group';
        },

        ungroupSelected: function() {
            let self = this;
            let groupCells = self._selectedCells.filter(function(c) { return c.get('isGroupBox'); });
            if (groupCells.length === 0 && self._currentSelectedCell && self._currentSelectedCell.get('isGroupBox')) {
                groupCells = [self._currentSelectedCell];
            }
            if (groupCells.length === 0) {
                self._toast('Select a group box first', 'warn');
                return;
            }
            groupCells.forEach(function(g) {
                let children = g.getEmbeddedCells().slice();
                children.forEach(function(c) { g.unembed(c); });
                g.remove();
                UndoStack.push({
                    undo: function() {
                        self.graph.addCell(g);
                        children.forEach(function(c) { g.embed(c); });
                    },
                    redo: function() {
                        children.forEach(function(c) { g.unembed(c); });
                        g.remove();
                    },
                });
            });
            self._clearSelection();
            self.statusText = 'Group removed';
        },

        /* ── Layer visibility toggle ──────────────────────── */
        toggleLayerVisibility: function(layer) {
            let self = this;
            let key = layer.toLowerCase();
            self.layerVisibility[key] = !self.layerVisibility[key];
            let visible = self.layerVisibility[key];

            self.graph.getElements().forEach(function(cell) {
                let elLayer = (cell.get('elLayer') || '').toLowerCase();
                if (elLayer === key) {
                    cell.attr('./display', visible ? '' : 'none');
                }
            });
            /* Also hide/show links connected to hidden elements */
            self.graph.getLinks().forEach(function(link) {
                let src = link.getSourceCell();
                let tgt = link.getTargetCell();
                let srcHidden = src && !self.layerVisibility[(src.get('elLayer') || '').toLowerCase()];
                let tgtHidden = tgt && !self.layerVisibility[(tgt.get('elLayer') || '').toLowerCase()];
                link.attr('./display', (srcHidden || tgtHidden) ? 'none' : '');
            });
        },


        /* ── Save viewpoint ────────────────────────────────── */


        /* ── Neighbor focus (view mode) ───────────────────── */
        _applyNeighborFocus: function(targetCell) {
            let self = this;
            let targetId = targetCell.id;

            /* Get connected links and neighbor elements */
            let connectedLinks = self.graph.getConnectedLinks(targetCell);
            let neighborIds = {};
            neighborIds[targetId] = true;
            connectedLinks.forEach(function(link) {
                let srcId = link.get('source').id;
                let tgtId = link.get('target').id;
                if (srcId) neighborIds[srcId] = true;
                if (tgtId) neighborIds[tgtId] = true;
            });

            let connectedLinkIds = {};
            connectedLinks.forEach(function(link) { connectedLinkIds[link.id] = true; });

            /* Dim non-neighbors */
            self.graph.getElements().forEach(function(cell) {
                let el = self.paper.findViewByModel(cell);
                if (!el) return;
                if (neighborIds[cell.id]) {
                    el.vel.removeClass('dimmed').addClass('highlighted');
                } else {
                    el.vel.removeClass('highlighted').addClass('dimmed');
                }
            });

            self.graph.getLinks().forEach(function(link) {
                let lv = self.paper.findViewByModel(link);
                if (!lv) return;
                if (connectedLinkIds[link.id]) {
                    lv.vel.removeClass('dimmed').addClass('highlighted');
                } else {
                    lv.vel.removeClass('highlighted').addClass('dimmed');
                }
            });

            self._focusedNodeId = targetId;
        },

        _clearNeighborFocus: function() {
            if (!this._focusedNodeId || !this.paper) return;
            this.graph.getElements().forEach(function(cell) {
                let el = this.paper.findViewByModel(cell);
                if (el) el.vel.removeClass('dimmed highlighted');
            }.bind(this));
            this.graph.getLinks().forEach(function(link) {
                let lv = this.paper.findViewByModel(link);
                if (lv) lv.vel.removeClass('dimmed highlighted');
            }.bind(this));
            this._focusedNodeId = null;
        },

        /* ── Load existing solution data ──────────────────── */
        loadSolutionData: function() {
            let self = this;
            self.statusText = 'Loading...';

            Platform.fetch('/archimate/viewpoints-api/basic/data?solution_id=' + this.solutionId, { silent: true })
            .then(function(data) {
                let elements = data.elements || [];
                let relationships = data.relationships || [];

                if (elements.length === 0) {
                    self.statusText = 'No elements yet \u2014 drag from palette to start';
                    return;
                }

                let cols = Math.ceil(Math.sqrt(elements.length));
                let cellMap = {};

                elements.forEach(function(el, i) {
                    let col = i % cols;
                    let row = Math.floor(i / cols);
                    let x = 40 + col * 240;
                    let y = 40 + row * 160;
                    let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                    let node = createNode(el.id, el.name, el.type || 'ApplicationComponent', layer, x, y);
                    self.graph.addCell(node);
                    cellMap[el.id] = node;
                    self.canvasElements[el.id] = el;
                });

                relationships.forEach(function(rel) {
                    let srcCell = cellMap[rel.source_id];
                    let tgtCell = cellMap[rel.target_id];
                    if (!srcCell || !tgtCell) return;
                    let link = createLink(srcCell, tgtCell, rel.type || 'association', rel.id);
                    /* BUG-CMP-002: Populate relationship metadata from API */
                    if (rel.description) link.set('description', rel.description);
                    if (rel.access_mode) link.set('accessMode', rel.access_mode);
                    if (rel.flow_label) link.set('flowLabel', rel.flow_label);
                    if (rel.custom_label) link.set('customLabel', rel.custom_label);
                    /* GAP-INT-001: Populate connection_spec from API */
                    if (rel.connection_spec) link.set('connectionSpec', rel.connection_spec);
                    self.graph.addCell(link);
                });

                self.elementCount = elements.length;
                self.relCount = relationships.length;

                /* Apply ArchiMate 3.2 layered layout when loading solution data */
                if (elements.length > 0 && typeof applyLayerBanding === 'function') {
                    applyLayerBanding(self.graph);
                    self.layerZonesActive = true;
                }

                const solName = (window.__COMPOSER_CONFIG__ || {}).solutionName || '';
                self.statusText = 'Loaded ' + elements.length + ' elements, ' + relationships.length + ' relationships' + (solName ? ' from ' + solName : '');
                self.fitCanvas();
                self.refreshMaturityOverlay();
                /* GAP-INT-003: Render annotation cards for loaded relationships */
                self.graph.getLinks().forEach(function(link) {
                    self._updateAnnotationCard(link);
                });
            })
            .catch(function(err) {
                _toast('error', 'Failed to load diagram: ' + ((err && err.message) || err));
                self.statusText = 'Error loading data';
            });
        },

        /* ── Palette drag ─────────────────────────────────── */
        onPaletteDragStart: function(event, typeObj) {
            if (this.mode === 'view') return;
            this.dragType = typeObj;
            event.dataTransfer.effectAllowed = 'copy';
            event.dataTransfer.setData('text/plain', typeObj.type);

            let ghost = document.createElement('div');
            ghost.className = 'drag-ghost';
            ghost.textContent = typeObj.label;
            ghost.style.left = '-9999px';
            document.body.appendChild(ghost);
            event.dataTransfer.setDragImage(ghost, ghost.offsetWidth / 2, ghost.offsetHeight / 2);
            setTimeout(function() { ghost.remove(); }, 0);

            this.dragOver = true;
        },

        onPaletteDragEnd: function() {
            this.dragOver = false;
            this.dragType = null;
        },

        /* ── Canvas drop → search modal ───────────────────── */
        onCanvasDrop: function(event) {
            event.preventDefault();
            this.dragOver = false;
            if (!this.dragType || this.mode === 'view') return;

            let rect = document.getElementById('composer-canvas').getBoundingClientRect();
            let scale = this.paper.scale();
            let translate = this.paper.translate();
            this.dropX = (event.clientX - rect.left - translate.tx) / scale.sx;
            this.dropY = (event.clientY - rect.top - translate.ty) / scale.sy;

            /* Centre element on cursor (element is 200×130) and snap to 12px grid */
            let GRID = 12;
            this.dropX = Math.round((this.dropX - 100) / GRID) * GRID;
            this.dropY = Math.round((this.dropY - 65) / GRID) * GRID;

            this.searchType = this.dragType.label;
            this.searchTypeFilter = this.dragType.type;
            this.searchQuery = '';
            this.searchResults = [];
            this.searchLayerFilter = '';
            this.newElementName = '';
            this.searchOpen = true;

            let self = this;
            this.$nextTick(function() {
                /* Palette drop: focus the create field so architect can type a name immediately */
                if (self.$refs.createInput) {
                    self.$refs.createInput.focus();
                } else if (self.$refs.searchInput) {
                    self.$refs.searchInput.focus();
                }
                self.doSearch();
            });
        },

        /* ── Palette drag-start / drag-end ────────────────── */
        onPaletteDragStart: function(event, t) {
            this.dragType = t;
            event.dataTransfer.effectAllowed = 'copy';
            event.dataTransfer.setData('text/plain', t.type);
        },

        onPaletteDragEnd: function(event) {
            if (event.dataTransfer.dropEffect === 'none') {
                this.dragType = null;
                this.dragOver = false;
            }
        },

        /* ── Pick element → place on canvas ───────────────── */
        /* CMP-08: keyboard quick-add (Ctrl+K) has no drag, so dropX/dropY stay at
           their last value and consecutive adds stack on the exact same spot.
           Cascade off any element already occupying the target so each new
           element lands somewhere visible. Deliberate drag-drops keep their
           cursor coordinates unless they'd land exactly on an existing node. */
        _placementFor: function(x, y) {
            /* CMP-08 (revised): nudge a new element off an ALREADY-OCCUPIED spot
               so consecutive keyboard quick-adds don't stack. Kept deliberately
               conservative after the first version could walk an element up to
               ~1900px off-screen: only treat a near-exact coincidence (12px) as
               occupied, cascade at most a few steps, and if it still can't find a
               clear spot, return the ORIGINAL point rather than flinging the
               element far away. */
            // STEP/NEAR must be sized against the actual node footprint (180x64,
            // see createNode) or the cascade "succeeds" while every element still
            // visually overlaps. QA (10 Sep 2026) found 3 quick-added elements
            // landing almost fully stacked with the previous STEP=28/NEAR=12:
            // occupied() only ever caught an exact-ish coincidence, so a 28px
            // nudge counted as "clear" while still covering ~85% of the previous
            // node. STEP now clears a node's width; NEAR is set to catch any
            // point that would still visually overlap one, not just a near-exact
            // hit.
            const STEP = 200;     // diagonal cascade step — clears node width (180)
            const NEAR = 60;      // treat anything within ~half a node's footprint as occupied
            const MAX_STEPS = 6;  // cap the cascade (~1200px)
            const els = (this.graph && this.graph.getElements) ? this.graph.getElements() : [];
            const occupied = function(cx, cy) {
                return els.some(function(el) {
                    const p = el.position();
                    return Math.abs(p.x - cx) < NEAR && Math.abs(p.y - cy) < NEAR;
                });
            };
            let px = x, py = y, guard = 0;
            while (occupied(px, py) && guard < MAX_STEPS) { px += STEP; py += STEP; guard++; }
            if (occupied(px, py)) return { x: x, y: y };  // gave up — keep the intended point
            return { x: px, y: py };
        },

        /* CMP-14: does this element's bbox intersect any other element's bbox? */
        _overlapsAnyElement: function(model) {
            if (!this.graph) return false;
            let b = model.getBBox();
            return this.graph.getElements().some(function(other) {
                if (other.id === model.id) return false;
                let o = other.getBBox();
                return b.x < o.x + o.width && b.x + b.width > o.x &&
                       b.y < o.y + o.height && b.y + b.height > o.y;
            });
        },

        /* CMP-14: keep relationship links painted above elements so a raised
           element never hides the arrows terminating at or crossing it. */
        _raiseLinks: function() {
            if (!this.graph) return;
            this.graph.getLinks().forEach(function(link) { link.toFront(); });
        },

        pickElement: function(item) {
            this.closeSearch();
            let layer = (item.layer || '').toLowerCase() || guessLayer(item.type);
            let pos = this._placementFor(this.dropX, this.dropY);
            let node = createNode(item.id, item.name, item.type, layer, pos.x, pos.y);
            this.graph.addCell(node);
            this.canvasElements[item.id] = item;
            this.elementCount++;
            this.statusText = 'Added: ' + item.name;
            this.logAuditEvent('element_added', 'element', item.id, item.name, null, item.type);

            if (this.solutionId) {
                this.linkElementToSolution(item.id);
            }

            /* CMP2-003: Auto-detect catalog relationships for the newly-placed element */
            this._autoDetectForElementDebounced(node);
            /* BUG-CMP-004: Check if dropped onto an existing element → prompt for relationship */
            this._checkDropOverlap(node);
            /* GAP-CMP-002/003: Update validation badge + orphan highlights */
            this._diagramChanged();
        },

        /* ── BUG-CMP-004: Drag-drop overlap → relationship prompt ── */
        _checkDropOverlap: function(newNode) {
            if (this.mode === 'view') return;
            let self = this;
            let newBBox = newNode.getBBox();
            let newId = newNode.get('elementId');
            let newType = newNode.get('elType');

            /* Find the first existing element whose bounding box overlaps the new node */
            let overlappingCell = null;
            this.graph.getElements().forEach(function(cell) {
                if (cell === newNode) return;
                if (cell.get('elementId') === newId) return;
                let cellBBox = cell.getBBox();
                /* Check if centres are within 80px — close enough for intentional drop */
                let dx = Math.abs((newBBox.x + newBBox.width / 2) - (cellBBox.x + cellBBox.width / 2));
                let dy = Math.abs((newBBox.y + newBBox.height / 2) - (cellBBox.y + cellBBox.height / 2));
                if (dx < 80 && dy < 60 && !overlappingCell) {
                    overlappingCell = cell;
                }
            });

            if (!overlappingCell) return;

            let targetType = overlappingCell.get('elType') || '';
            let targetId = overlappingCell.get('elementId');
            let targetName = overlappingCell.get('elName') || '';

            /* Fetch valid relationship types for this pair */
            Platform.fetch('/api/archimate/valid-relationships/' + encodeURIComponent(newType) + '/' + encodeURIComponent(targetType), { silent: true })
            .then(function(data) {
                let validTypes = (data.data || {}).valid_relationship_types || [];
                if (validTypes.length === 0) {
                    /* No valid relationships — offset the element to avoid overlap */
                    newNode.position(newBBox.x + 220, newBBox.y);
                    return;
                }
                /* Show the relationship picker for this pair.
                   Set up _pendingLink as a temporary JointJS link so createRelationship()
                   can process it using its existing code path. */
                let tmpLink = createLink(newNode, overlappingCell, 'association');
                self.graph.addCell(tmpLink);
                self._pendingLink = tmpLink;
                self._isChangeType = false;
                self.relPickerSourceId = newNode.get('elementId');
                self.relPickerTargetId = overlappingCell.get('elementId');
                self.relPickerSourceCell = newNode;
                self.relPickerTargetCell = overlappingCell;
                self.relPickerTypes = validTypes.map(function(t) {
                    return { type: t, tier: 'standard' };
                });
                self.relPickerOpen = true;
                self.statusText = 'Select relationship: ' + (newNode.get('elName') || '') + ' → ' + targetName;
            })
            .catch(function() {
                /* On error, just offset to avoid overlap */
                newNode.position(newBBox.x + 220, newBBox.y);
                _toast('error', 'Could not check valid relationships for this drop');
            });
        },

        /* ── CAP-022: Load solution capabilities for sidebar panel ── */
        loadSolutionCapabilities: function() {
            let self = this;
            if (!self.solutionId) {
                self.capabilitiesError = 'No solution context available';
                return;
            }
            self.capabilitiesLoading = true;
            self.capabilitiesError = '';
            Platform.fetch('/solutions/' + self.solutionId + '/all-capabilities', {
                headers: { 'Accept': 'application/json' },
                silent: true,
            })
            .then(function(data) {
                let caps = data.capabilities || data.data || [];
                self.sidebarCapabilities = caps;
                self.capabilitiesLoading = false;
                if (!caps.length) {
                    self.capabilitiesError = 'No capabilities linked to this solution';
                }
            })
            .catch(function(err) {
                self.capabilitiesLoading = false;
                self.capabilitiesError = 'Failed to load capabilities';
                _toast('error', 'Failed to load capabilities: ' + ((err && err.message) || 'Unknown error'));
            });
        },

        /* ── CAP-022: Generate ArchiMate elements from a capability ── */
        generateFromCapability: function(capId) {
            let self = this;
            if (!self.solutionId) return;
            self.statusText = 'Generating elements from capability...';

            Platform.fetch.post('/solutions/' + self.solutionId + '/generate-from-capabilities', { capability_ids: [capId] }, { silent: true })
            .then(function(data) {
                if (!data.success) {
                    self.statusText = 'Generation failed: ' + (data.error || 'unknown');
                    _toast('error', data.error || 'Generation failed');
                    return;
                }
                let elements = (data.data && data.data.elements_created) || [];
                let rect = self.paper.el.getBoundingClientRect();
                let vp = self.paper.translate();
                let s = self.paper.scale().sx || 1;
                let baseX = (rect.width / 2 - vp.tx) / s - 100;
                let baseY = (rect.height / 2 - vp.ty) / s - 65;

                elements.forEach(function(el, idx) {
                    if (!el.id) return;
                    let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                    let x = baseX + (idx % 4) * 200;
                    let y = baseY + Math.floor(idx / 4) * 160;
                    let node = createNode(el.id, el.name, el.type, layer, x, y);
                    self.graph.addCell(node);
                    self.canvasElements[el.id] = el;
                    self.elementCount++;
                    self.logAuditEvent('element_added', 'element', el.id, el.name, null, el.type);
                });

                self.statusText = 'Generated ' + elements.length + ' element(s) from capability';
                _toast('success', 'Generated ' + elements.length + ' ArchiMate element(s)');
                self.refreshMaturityOverlay();

                /* CMP2-003: Auto-detect catalog relationships for newly-generated elements */
                if (elements.length > 0) {
                    self._autoDetectBulkDebounced();
                }
            })
            .catch(function(err) {
                self.statusText = 'Generation failed';
                _toast('error', 'Generation failed: ' + ((err && err.message) || 'Unknown error'));
            });
        },

        /* ── Reuse detection (CMP-009) ─────────────────────── */
        checkReuseDebounced: function() {
            let self = this;
            if (self._reuseDebounceTimer) clearTimeout(self._reuseDebounceTimer);
            let name = (self.newElementName || '').trim();
            if (name.length < 2) {
                self.similarElements = [];
                self.checkingReuse = false;
                return;
            }
            self._reuseDebounceTimer = setTimeout(function() {
                self.checkingReuse = true;
                let url = '/archimate/api/elements/search?q=' + encodeURIComponent(name) + '&limit=5';
                Platform.fetch(url, { silent: true })
                    .then(function(resp) {
                        let data = resp.data || resp || [];
                        self.similarElements = Array.isArray(data) ? data.filter(function(el) {
                            return !self.canvasElements[el.id];
                        }) : [];
                        self.checkingReuse = false;
                    })
                    .catch(function() {
                        self.similarElements = [];
                        self.checkingReuse = false;
                        _toast('error', 'Failed to check for similar elements');
                    });
            }, 300);
        },

        pickSimilarElement: function(item) {
            this.similarElements = [];
            this.newElementName = '';
            this.pickElement(item);
        },

        /* ── Maturity overlay — derived from existing domain data ─── */
        refreshMaturityOverlay: function() {
            let self = this;
            let elements = self.graph.getElements();
            let ids = [];
            elements.forEach(function(cell) {
                let eid = cell.get('elementId');
                if (eid) ids.push(eid);
            });
            if (ids.length === 0) return;

            Platform.fetch.post('/archimate/api/element-maturity', { element_ids: ids }, { silent: true })
            .then(function(data) {
                let maturity = data.maturity || {};
                elements.forEach(function(cell) {
                    let eid = String(cell.get('elementId') || '');
                    let m = maturity[eid];
                    if (m) {
                        self._applyMaturityToCell(cell, m);
                    }
                });
            })
            .catch(function() { /* silent — maturity is optional overlay */ _toast('error', 'Failed to load maturity data'); });
        },

        _applyMaturityToCell: function(cell, m) {
            let c = layerColor((cell.get('elLayer') || '').toLowerCase());
            /* Track width = card width (200) minus padding (14+14) = 172 */
            let trackWidth = (cell.get('size') || {}).width || 200;
            trackWidth = trackWidth - 28;
            let barWidth = Math.max(1, Math.round(trackWidth * m.pct / 100));
            /* Maturity badge (top-right dark pill) */
            cell.attr('maturityBadgeBg/display', 'block');
            cell.attr('maturityBadgeLabel/display', 'block');
            cell.attr('maturityBadgeLabel/text', m.label);
            /* Progress bar labels */
            cell.attr('maturityLeftLabel/text', 'MATURITY');
            cell.attr('maturityLeftLabel/display', 'block');
            cell.attr('maturityRightLabel/text', m.pct + '%');
            cell.attr('maturityRightLabel/display', 'block');
            /* Progress bar */
            cell.attr('maturityTrack/display', 'block');
            cell.attr('maturityFill/display', 'block');
            cell.attr('maturityFill/width', barWidth);
            cell.attr('maturityFill/fill', c.accent || '#1a1a1a');
            cell.set('maturityData', m);
        },

        /* ── GAP-CMP-002: Quick validation badge ─────────── */
        _runQuickValidation: function() {
            let self = this;
            if (self._quickValidationTimer) clearTimeout(self._quickValidationTimer);
            self._quickValidationTimer = setTimeout(function() {
                let errors = 0;
                let warnings = 0;
                let elements = self.graph.getElements().filter(function(c) {
                    return !c.get('isLayerZone') && !c.get('isAnnotation');
                });
                let links = self.graph.getLinks();
                let nameMap = {};

                /* Error: self-relationships */
                links.forEach(function(link) {
                    let src = link.get('source');
                    let tgt = link.get('target');
                    if (src && tgt && src.id && tgt.id && src.id === tgt.id) {
                        errors++;
                    }
                });

                /* Warning: orphan elements (0 connections) — only when 2+ elements exist */
                if (elements.length >= 2) {
                    elements.forEach(function(cell) {
                        let connected = self.graph.getConnectedLinks(cell);
                        if (connected.length === 0) {
                            warnings++;
                        }
                    });
                }

                /* Warning: duplicate names */
                elements.forEach(function(cell) {
                    let name = (cell.get('elName') || '').trim().toLowerCase();
                    if (name) {
                        if (nameMap[name]) {
                            /* Count only once per duplicate group */
                            if (nameMap[name] === 1) warnings++;
                            nameMap[name]++;
                        } else {
                            nameMap[name] = 1;
                        }
                    }
                });

                self.validationErrors = errors;
                self.validationWarnings = warnings;
            }, 300);
        },

        /* ── GAP-CMP-003: Orphan detection nudge ──────────── */
        _highlightOrphans: function() {
            let self = this;
            let elements = self.graph.getElements().filter(function(c) {
                return !c.get('isLayerZone') && !c.get('isAnnotation');
            });
            if (elements.length < 5) {
                /* Below threshold — clear any existing orphan highlights */
                elements.forEach(function(cell) {
                    if (cell.get('_orphanHighlight')) {
                        cell.attr('body/strokeDasharray', '');
                        /* Restore layer color stroke */
                        let c = layerColor((cell.get('elLayer') || '').toLowerCase());
                        cell.attr('body/stroke', c.stroke || '#1a1a1a');
                        cell.set('_orphanHighlight', false);
                    }
                });
                return;
            }
            elements.forEach(function(cell) {
                let links = self.graph.getConnectedLinks(cell);
                let isOrphan = links.length === 0;
                if (isOrphan) {
                    cell.attr('body/strokeDasharray', '4,2');
                    cell.attr('body/stroke', '#f59e0b');
                } else if (cell.get('_orphanHighlight')) {
                    cell.attr('body/strokeDasharray', '');
                    let c = layerColor((cell.get('elLayer') || '').toLowerCase());
                    cell.attr('body/stroke', c.stroke || '#1a1a1a');
                }
                cell.set('_orphanHighlight', isOrphan);
            });
        },

        /* GAP-CMP-002/003: Trigger both quick-validation and orphan highlight */
        _diagramChanged: function() {
            this._runQuickValidation();
            this._highlightOrphans();
        },

        confirmAssociation: function() {
            this._associationConfirmed = true;
            this.associationWarning = false;
            this.createRelationship('association');
        },

        cancelAssociationWarning: function() {
            this.associationWarning = false;
        },

        cancelRelPicker: function() {
            this.relPickerOpen = false;
            this.associationWarning = false;
            this.relPickerRejection = null;
            if (this._pendingLink) {
                this._pendingLink.remove();
                this._pendingLink = null;
            }
        },


        /* ── Context menu actions ─────────────────────────── */
        closeContextMenu: function() {
            this.ctxMenuOpen = false;
        },

        /* ENT-104: Element colour override ─────────────────────────────── */
        openColourPicker: function() {
            let cell = this.ctxMenuCell || this._currentSelectedCell;
            if (!cell) return;
            this.ctxMenuOpen = false;
            /* Position near context menu or centre of screen */
            this.colourPickerX = this.ctxMenuX || 300;
            this.colourPickerY = this.ctxMenuY || 200;
            this.colourPickerOpen = true;
        },

        setElementColour: function(colour) {
            let self = this;
            let cells = self._selectedCells && self._selectedCells.length
                ? self._selectedCells.slice()
                : (self._currentSelectedCell ? [self._currentSelectedCell] : []);
            if (!cells.length) return;
            let snapshots = cells.map(function(c) {
                return { id: c.id, prevFill: c.attr('body/fill'), prevCf: c.get('customFill') };
            });
            UndoStack.push({
                undo: function() {
                    snapshots.forEach(function(s) {
                        let c = self.graph.getCell(s.id);
                        if (!c) return;
                        if (s.prevFill) { c.attr('body/fill', s.prevFill); } else { c.removeAttr('body/fill'); }
                        c.set('customFill', s.prevCf || null);
                    });
                },
                redo: function() {
                    snapshots.forEach(function(s) {
                        let c = self.graph.getCell(s.id);
                        if (!c) return;
                        c.attr('body/fill', colour);
                        c.set('customFill', colour);
                    });
                }
            });
            cells.forEach(function(c) {
                c.attr('body/fill', colour);
                c.set('customFill', colour);
            });
            self.colourPickerOpen = false;
            self.viewpointDirty = true;
            self._autoSave();
        },

        resetElementColour: function() {
            let self = this;
            let cells = self._selectedCells && self._selectedCells.length
                ? self._selectedCells
                : (self._currentSelectedCell ? [self._currentSelectedCell] : []);
            if (!cells.length) return;
            cells.forEach(function(c) {
                c.removeAttr('body/fill');
                c.set('customFill', null);
            });
            self.colourPickerOpen = false;
            self.viewpointDirty = true;
            self._autoSave();
        },

        /* ENT-111: Relationship staleness review ─────────────────────────── */
        checkRelationshipHealth: function() {
            let self = this;
            if (!self.currentSavedVpId) return;
            Platform.fetch('/archimate/api/saved-viewpoints/' + self.currentSavedVpId + '/relationship-health', { silent: true })
            .then(function(data) {
                if (!data || !data.stale_relationships || !data.stale_relationships.length) return;
                let dismissed = sessionStorage.getItem('ent111_dismissed_vp' + self.currentSavedVpId);
                if (dismissed) return;
                self.staleRelationships = data.stale_relationships;
                self.showStalenessReview = true;
            })
            .catch(function() { /* swallow-ok: unsolicited background staleness advisory; on failure the review panel just does not open, the viewpoint itself is unaffected, and the user was never told the check would run */ });
        },

        keepAsIntent: function(relId) {
            let self = this;
            /* The row used to be struck from the review list — and the whole panel
               closed — the instant the PATCH was FIRED, before any response came
               back. A 403/500, or no network at all, therefore looked exactly like
               "marked as architectural intent"; the flag was never persisted and
               the relationship reappeared as stale on the next check. Only remove
               the row once the server has confirmed the write. */
            Platform.fetch.patch('/archimate/api/saved-viewpoints/' + self.currentSavedVpId, {
                relationship_intent: { rel_id: relId, is_architectural_intent: true },
            }, { silent: true })
            .then(function() {
                self.staleRelationships = self.staleRelationships.filter(function(x) { return x.rel_id !== relId; });
                if (!self.staleRelationships.length) { self.showStalenessReview = false; }
            })
            .catch(function(err) {
                _toast('error', 'Could not mark the relationship as architectural intent — ' + ((err && err.message) || 'the change was not saved'));
            });
        },

        removeStalenessRel: function(relId) {
            let self = this;
            // Find the JointJS link cell whose 'relId' attribute matches the DB integer
            let cell = self.graph && self.graph.getCells().find(function(c) {
                return c.get('relId') == relId; // eslint-disable-line eqeqeq
            });
            if (cell) { cell.remove(); }
            self.staleRelationships = self.staleRelationships.filter(function(r) { return r.rel_id !== relId; });
            if (!self.staleRelationships.length) {
                self.showStalenessReview = false;
                self._autoSave();
            }
        },

        dismissStalenessReview: function() {
            sessionStorage.setItem('ent111_dismissed_vp' + this.currentSavedVpId, '1');
            this.showStalenessReview = false;
        },


        sendToFront: function() {
            this.ctxMenuOpen = false;
            let cell = this.ctxMenuCell;
            if (!cell) return;
            cell.toFront({ deep: true });
            this.statusText = '"' + (cell.get('elName') || '') + '" sent to front';
        },

        sendToBack: function() {
            this.ctxMenuOpen = false;
            let cell = this.ctxMenuCell;
            if (!cell) return;
            cell.toBack({ deep: true });
            this.statusText = '"' + (cell.get('elName') || '') + '" sent to back';
        },


        /* ── Zoom controls ────────────────────────────────── */
        zoomIn: function() {
            let s = Math.min(3, this.paper.scale().sx + 0.15);
            this.paper.scale(s, s);
            this.zoomPercent = Math.round(s * 100);
            this._scheduleMiniMapUpdate();
        },

        zoomOut: function() {
            let s = Math.max(0.15, this.paper.scale().sx - 0.15);
            this.paper.scale(s, s);
            this.zoomPercent = Math.round(s * 100);
            this._scheduleMiniMapUpdate();
        },

        /* ── Mini-map rendering & interaction ────────────── */
        _scheduleMiniMapUpdate: function() {
            let self = this;
            if (this._miniMapRAF) return;
            this._miniMapRAF = requestAnimationFrame(function() {
                self._miniMapRAF = null;
                self._renderMiniMap();
            });
        },

        _renderMiniMap: function() {
            if (!this.paper || !this.graph || !this.miniMapExpanded) return;
            let canvas = document.getElementById('minimap-canvas');
            if (!canvas) return;
            let ctx = canvas.getContext('2d');
            let W = canvas.width, H = canvas.height;
            ctx.clearRect(0, 0, W, H);

            let elements = this.graph.getElements();
            if (elements.length === 0) return;

            /* Compute content bounding box in local (paper) coordinates */
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            elements.forEach(function(el) {
                let pos = el.position();
                let size = el.size();
                if (pos.x < minX) minX = pos.x;
                if (pos.y < minY) minY = pos.y;
                if (pos.x + size.width > maxX) maxX = pos.x + size.width;
                if (pos.y + size.height > maxY) maxY = pos.y + size.height;
            });

            /* Add padding around content bounds */
            let pad = 80;
            minX -= pad; minY -= pad; maxX += pad; maxY += pad;
            let contentW = maxX - minX;
            let contentH = maxY - minY;
            if (contentW <= 0 || contentH <= 0) return;

            /* Scale factor to fit content in canvas */
            let scale = Math.min(W / contentW, H / contentH);

            /* Offset to center content in canvas */
            let offX = (W - contentW * scale) / 2;
            let offY = (H - contentH * scale) / 2;

            /* Store transform for pointer interaction */
            this._mmTransform = { minX: minX, minY: minY, scale: scale, offX: offX, offY: offY };

            /* Draw element rectangles */
            elements.forEach(function(el) {
                let pos = el.position();
                let size = el.size();
                let x = (pos.x - minX) * scale + offX;
                let y = (pos.y - minY) * scale + offY;
                let w = Math.max(2, size.width * scale);
                let h = Math.max(2, size.height * scale);

                /* Get layer color */
                let layer = (el.get('elLayer') || '').toLowerCase();
                let lc = layerColor(layer);
                ctx.fillStyle = lc.accent || '#94a3b8';
                ctx.globalAlpha = 0.7;
                ctx.fillRect(x, y, w, h);
                ctx.globalAlpha = 1;
            });

            /* Draw viewport rectangle */
            let paperEl = this.paper.el;
            let t = this.paper.translate();
            let s = this.paper.scale().sx;
            let vpLeft = (-t.tx / s);
            let vpTop = (-t.ty / s);
            let vpW = paperEl.clientWidth / s;
            let vpH = paperEl.clientHeight / s;

            let vx = (vpLeft - minX) * scale + offX;
            let vy = (vpTop - minY) * scale + offY;
            let vw = vpW * scale;
            let vh = vpH * scale;

            ctx.strokeStyle = 'hsl(221, 83%, 53%)';  /* primary blue */
            ctx.lineWidth = 1.5;
            ctx.strokeRect(vx, vy, vw, vh);
            ctx.fillStyle = 'hsla(221, 83%, 53%, 0.06)';
            ctx.fillRect(vx, vy, vw, vh);
        },

        miniMapPointerDown: function(e) {
            if (!this._mmTransform || !this.paper) return;
            this._miniMapDragging = true;
            this._miniMapPanTo(e);
        },

        miniMapPointerMove: function(e) {
            if (!this._miniMapDragging) return;
            this._miniMapPanTo(e);
        },

        miniMapPointerUp: function() {
            this._miniMapDragging = false;
        },

        _miniMapPanTo: function(e) {
            let canvas = document.getElementById('minimap-canvas');
            if (!canvas || !this._mmTransform) return;
            let rect = canvas.getBoundingClientRect();
            let mx = e.clientX - rect.left;
            let my = e.clientY - rect.top;

            /* Convert minimap pixel to paper local coordinate */
            let mm = this._mmTransform;
            let localX = (mx - mm.offX) / mm.scale + mm.minX;
            let localY = (my - mm.offY) / mm.scale + mm.minY;

            /* Center the viewport on that point */
            let s = this.paper.scale().sx;
            let paperEl = this.paper.el;
            let tx = -(localX * s) + paperEl.clientWidth / 2;
            let ty = -(localY * s) + paperEl.clientHeight / 2;
            this.paper.translate(tx, ty);
            this._scheduleMiniMapUpdate();
        },

        /* ── Toolbar actions ──────────────────────────────── */
        fitCanvas: function() {
            if (!this.paper) return;
            /* CMP: fit must not fit content behind a floating right-hand slide-out panel
               (validation / plateau / comments / audit) — those are absolutely positioned
               over the canvas, not flex siblings, so scaleContentToFit's own geometry has
               no idea they cover part of the paper. Widen the right padding to match
               whichever one is currently open. */
            let rightPanelOpen = this.validationPanelOpen
                || (this.deltaMode && this.plateauSuggestions && this.plateauSuggestions.length > 0)
                || this.commentPanelOpen
                || this.auditPanelOpen;
            let rightPadding = rightPanelOpen ? 40 + 320 : 40;
            /* CMP-05: fit was unreliable in the narrow side-panel — it zoomed to
               150% with the element half off the left edge. Two causes fixed:
               (1) maxScale 1.5 let a small selection zoom IN past the viewport;
               cap at 1 like every other fit call site. (2) scaleContentToFit read
               rendered geometry, which is stale while the panel is mid-resize;
               useModelGeometry reads the model bboxes instead, which are always
               current. */
            this.paper.scaleContentToFit({
                padding: { top: 40, bottom: 40, left: 40, right: rightPadding },
                maxScale: 1,
                minScale: 0.1,
                useModelGeometry: true
            });
            this.zoomPercent = Math.round(this.paper.scale().sx * 100);
            this._scheduleMiniMapUpdate();
        },

        /* CMP-026: Sanitize HTML for x-html bindings */
        sanitizeHtml: function(text) { return _sanitizeForHtml(text); },

        /* CMP-028: Auto-persist restore/discard */


        clearCanvas: async function() {
            if (!this.graph || this.mode === 'view') return;
            if (Object.keys(this.canvasElements).length > 0) {
                if (!(await Platform.modal.confirm('Clear canvas? (Elements stay in catalog)'))) return;
            }
            this.graph.clear();
            this.canvasElements = {};
            this.elementCount = 0;
            this.relCount = 0;
            this.statusText = 'Canvas cleared';
            /* GAP-CMP-002/003: Reset validation badge + orphan highlights */
            this.validationErrors = 0;
            this.validationWarnings = 0;
        },

        undo: function() {
            if (this.mode === 'view') return;
            if (UndoStack.canUndo()) {
                UndoStack.undo();
                this.statusText = 'Undo';
            }
        },

        redo: function() {
            if (this.mode === 'view') return;
            if (UndoStack.canRedo()) {
                UndoStack.redo();
                this.statusText = 'Redo';
            }
        },

        deleteSelected: function() {
            if (this.mode === 'view') return;
            if (this._selectedCells.length === 0) return;
            this.bulkDeleteCount = this._selectedCells.length;
            this.bulkDeleteConfirmOpen = true;
        },


        cancelBulkDelete: function() {
            this.bulkDeleteConfirmOpen = false;
        },

        /* Generic confirm dialog helpers */
        confirmDialog: function() {
            this.confirmDialogOpen = false;
            if (typeof this._confirmDialogCallback === 'function') {
                this._confirmDialogCallback();
                this._confirmDialogCallback = null;
            }
        },
        cancelConfirmDialog: function() {
            this.confirmDialogOpen = false;
            this._confirmDialogCallback = null;
        },

        /* ── Selection helpers ─────────────────────────────── */
        _highlightCell: function(cellView) {
            if (!cellView || !cellView.el) return;
            cellView.el.classList.add('selected');
            let body = cellView.el.querySelector('[joint-selector="body"]');
            if (body) {
                body.setAttribute('data-orig-stroke', body.getAttribute('stroke') || '');
                body.setAttribute('data-orig-stroke-width', body.getAttribute('stroke-width') || '1.5');
                body.setAttribute('stroke', '#3b82f6');
                body.setAttribute('stroke-width', '2.5');
                body.setAttribute('data-selected', '1');
            }
        },

        _unhighlightCell: function(cellView) {
            if (!cellView || !cellView.el) return;
            cellView.el.classList.remove('selected');
            let body = cellView.el.querySelector('[joint-selector="body"]');
            if (body) {
                let origStroke = body.getAttribute('data-orig-stroke') || 'rgba(0,0,0,0.08)';
                let origWidth = body.getAttribute('data-orig-stroke-width') || '1.5';
                body.setAttribute('stroke', origStroke);
                body.setAttribute('stroke-width', origWidth);
                body.removeAttribute('data-selected');
                body.removeAttribute('data-orig-stroke');
                body.removeAttribute('data-orig-stroke-width');
            }
        },

        _clearSelection: function() {
            let self = this;
            this._selectedCells.forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (view) self._unhighlightCell(view);
            });
            this._selectedCells = [];
        },

        /* ── Open relationship picker for two cells (used by R shortcut and connect mode) ── */
        _openRelPickerForPair: function(sourceCell, targetCell) {
            let self = this;
            let srcElementId = sourceCell.get('elementId');
            let tgtElementId = targetCell.get('elementId');
            if (!srcElementId || !tgtElementId) {
                _toast('warning', 'Both elements must be saved to the repository before relating');
                return;
            }

            let tempLink = createLink(sourceCell, targetCell, 'association');
            self.graph.addCell(tempLink);

            self._pendingLink = tempLink;
            self.relPickerSourceCell = sourceCell;
            self.relPickerTargetCell = targetCell;
            self.relPickerSourceId = srcElementId;
            self.relPickerTargetId = tgtElementId;

            let srcPos = sourceCell.position();
            let tgtPos = targetCell.position();
            let midX = (srcPos.x + tgtPos.x) / 2 + 90;
            let midY = (srcPos.y + tgtPos.y) / 2 + 32;
            let paperRect = self.paper.el.getBoundingClientRect();
            let s = self.paper.scale().sx;
            let t = self.paper.translate();
            self.relPickerX = Math.max(10, Math.min(paperRect.left + midX * s + t.tx, window.innerWidth - 240));
            self.relPickerY = Math.max(10, Math.min(paperRect.top + midY * s + t.ty, window.innerHeight - 300));

            self.relPickerTypes = [];
            self.relPickerInvalidTypes = [];
            self.relPickerRejection = null;
            self.associationWarning = false;
            self.relPickerOpen = true;
            self.statusText = 'Pick relationship type\u2026';

            let ALL_REL = ['composition','aggregation','assignment','realization','serving','access','influence','triggering','flow','specialization','association'];
            /* DEF-006, Capgemini dry-run (browser-verified live): third
               unfixed call site sending template placeholder ids straight to
               valid-relationship-types — this one backs both the R-shortcut
               and connect-mode via _openRelPickerForPair. Same materialize-
               first fix as the other two call sites. */
            self._materializeConnectEnds(sourceCell, targetCell, srcElementId, tgtElementId)
            .catch(function() {
                return { srcElementId: srcElementId, tgtElementId: tgtElementId };
            })
            .then(function(resolved) {
                srcElementId = resolved.srcElementId;
                tgtElementId = resolved.tgtElementId;
                self.relPickerSourceId = srcElementId;
                self.relPickerTargetId = tgtElementId;
                return Platform.fetch('/archimate/api/valid-relationship-types?source_id=' + srcElementId + '&target_id=' + tgtElementId, { silent: true });
            })
            .then(function(data) {
                let validDetailed = data.valid_types_detailed || [];
                self.relPickerTypes = validDetailed.length > 0 ? validDetailed
                    : (data.valid_types || ['association']).map(function(t) { return { type: t, tier: 'standard', description: '' }; });
                let validSet = {};
                self.relPickerTypes.forEach(function(v) { validSet[v.type || v] = true; });
                self.relPickerInvalidTypes = ALL_REL.filter(function(t) { return !validSet[t]; });
            })
            // fabricated-ok: falls back to a single type tagged tier:'fallback' and raises an error toast
            .catch(function() {
                self.relPickerTypes = [{ type: 'association', tier: 'fallback', description: '' }];
                self.relPickerInvalidTypes = [];
                _toast('error', 'Failed to load relationship types');
            });
        },

        /* ── Inline rename (double-click on element) ────────── */
        _startInlineRename: function(cellView) {
            let self = this;
            let cell = cellView.model;
            let oldName = cell.get('elName') || '';

            let nameEl = cellView.el.querySelector('[joint-selector="nameLabel"]');
            if (!nameEl) return;

            let bbox = nameEl.getBoundingClientRect();
            let paperRect = self.paper.el.getBoundingClientRect();

            let input = document.createElement('input');
            input.type = 'text';
            input.value = oldName;
            input.style.cssText = 'position:absolute;z-index:1000;'
                + 'left:' + (bbox.left - paperRect.left - 4) + 'px;'
                + 'top:' + (bbox.top - paperRect.top - 4) + 'px;'
                + 'width:' + Math.max(bbox.width + 24, 120) + 'px;'
                + 'height:' + (bbox.height + 8) + 'px;'
                + 'font-size:12px;font-weight:600;font-family:Inter,system-ui,sans-serif;'
                + 'text-align:center;border:2px solid #6366f1;border-radius:4px;'
                + 'outline:none;padding:2px 6px;background:#fff;color:#1e293b;';

            self.paper.el.style.position = 'relative';
            self.paper.el.appendChild(input);
            input.focus();
            input.select();

            function commit() {
                let newName = input.value.trim();
                if (input.parentNode) input.parentNode.removeChild(input);
                if (!newName || newName === oldName) return;
                cell.attr('nameLabel/text', newName);
                cell.set('elName', newName);
                self.statusText = 'Renamed: ' + newName;
            }

            input.addEventListener('blur', commit);
            input.addEventListener('keydown', function(e) {
                if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
                if (e.key === 'Escape') { input.value = oldName; input.blur(); }
            });
        },

        /* ── Copy / Paste / Duplicate ────────────────────────── */
        _copySelected: function() {
            if (this._selectedCells.length === 0) return;
            let self = this;
            let selectedIds = {};
            self._selectedCells.forEach(function(c) { selectedIds[c.id] = true; });

            self._clipboard = self._selectedCells.map(function(cell) {
                return {
                    elementId: cell.get('elementId'),
                    elName: cell.get('elName') || '',
                    elType: cell.get('elType') || '',
                    elLayer: cell.get('elLayer') || '',
                    position: cell.position(),
                    size: cell.size(),
                    renderingMode: cell.get('renderingMode') || 'black_box',
                    _localId: cell.id,  /* used to reconstruct links between copied elements */
                };
            });

            /* Also copy links whose BOTH endpoints are in the selection */
            self._clipboardLinks = self.graph.getLinks().filter(function(link) {
                let src = link.get('source');
                let tgt = link.get('target');
                let srcId = src && (src.id || src.cell);
                let tgtId = tgt && (tgt.id || tgt.cell);
                return srcId && tgtId && selectedIds[srcId] && selectedIds[tgtId];
            }).map(function(link) {
                let src = link.get('source');
                let tgt = link.get('target');
                return {
                    srcLocalId: src.id || src.cell,
                    tgtLocalId: tgt.id || tgt.cell,
                    relType: link.get('relType') || 'association',
                    relId: link.get('relId') || null,
                };
            });

            this.statusText = 'Copied ' + this._clipboard.length + ' element(s)'
                + (self._clipboardLinks.length ? ' and ' + self._clipboardLinks.length + ' relationship(s)' : '');
            /* Cross-tab clipboard persistence is a bonus — in-memory this._clipboard is
               already set above, so a private-mode/quota failure here is harmless. */
            try { localStorage.setItem('archimate_clipboard', JSON.stringify(this._clipboard)); } catch(e) { /* swallow-ok: cross-tab clipboard mirror; the in-memory _clipboard is already set above, so paste works in this tab either way */ }
        },

        _pasteClipboard: function(atPoint) {
            if (this._clipboard.length === 0) {
                /* Best-effort restore from a previous tab/session — if this throws
                   or the stored value is malformed, _clipboard just stays empty
                   and the paste below is a no-op. */
                try {
                    let stored = localStorage.getItem('archimate_clipboard');
                    if (stored) this._clipboard = JSON.parse(stored);
                } catch(e) { /* swallow-ok: optional restore of a clipboard from another tab; on failure _clipboard stays empty and the paste below is a no-op */ }
            }
            if (this._clipboard.length === 0) return;
            let self = this;
            let OFFSET = 30;

            /* When pasting at a specific canvas point (right-click → Paste),
             * compute the top-left corner of the clipboard bounding box and
             * translate all items so their origin lands at atPoint. */
            let translateX = null, translateY = null;
            if (atPoint && self._clipboard.length > 0) {
                let minX = self._clipboard[0].position.x;
                let minY = self._clipboard[0].position.y;
                self._clipboard.forEach(function(item) {
                    minX = Math.min(minX, item.position.x);
                    minY = Math.min(minY, item.position.y);
                });
                translateX = atPoint.x - minX;
                translateY = atPoint.y - minY;
            }

            self._clearSelection();

            let addedCells = [];  /* track for undo */
            /* Map from original cell id → new cell (for link reconstruction) */
            let idMap = {};

            self._clipboard.forEach(function(item) {
                let x = translateX !== null ? item.position.x + translateX : item.position.x + OFFSET;
                let y = translateY !== null ? item.position.y + translateY : item.position.y + OFFSET;
                let layer = item.elLayer || guessLayer(item.elType);
                let node;

                if (item.renderingMode === 'white_box' && isContainerType(item.elType)) {
                    node = createContainerNode(item.elementId, item.elName, item.elType, layer,
                                               x, y, item.size.width, item.size.height);
                } else {
                    node = createNode(item.elementId, item.elName, item.elType, layer, x, y);
                }
                self.graph.addCell(node);
                addedCells.push(node);

                /* Map old local id → new node so we can recreate links */
                if (item._localId) idMap[item._localId] = node;

                if (item.elementId && !self.canvasElements[item.elementId]) {
                    self.canvasElements[item.elementId] = {
                        id: item.elementId, name: item.elName, type: item.elType, layer: layer,
                    };
                    self.elementCount++;
                }

                self._selectedCells.push(node);
                let view = self.paper.findViewByModel(node);
                if (view) self._highlightCell(view);
            });

            /* Recreate relationships whose both endpoints were in the copied set */
            let clipLinks = self._clipboardLinks || [];
            clipLinks.forEach(function(linkData) {
                let srcNode = idMap[linkData.srcLocalId];
                let tgtNode = idMap[linkData.tgtLocalId];
                if (!srcNode || !tgtNode) return;
                let link = createLink(srcNode, tgtNode, linkData.relType, linkData.relId);
                self.graph.addCell(link);
                addedCells.push(link);
            });

            /* Push paste to UndoStack so it can be undone with Ctrl+Z */
            UndoStack.push({
                undo: function() {
                    addedCells.forEach(function(c) { c.remove(); });
                },
                redo: function() {
                    addedCells.forEach(function(c) { self.graph.addCell(c); });
                },
            });

            /* Shift clipboard positions so repeated Ctrl+V pastes cascade.
             * Skip when pasting at a specific point — position is already correct. */
            if (translateX === null) {
                self._clipboard = self._clipboard.map(function(item) {
                    return Object.assign({}, item, {
                        position: { x: item.position.x + OFFSET, y: item.position.y + OFFSET },
                    });
                });
            }

            let relCount = clipLinks.length;
            let elCount = addedCells.length - relCount;
            self.statusText = 'Pasted ' + elCount + ' element(s)'
                + (relCount ? ' and ' + relCount + ' relationship(s)' : '');
        },

        _duplicateSelected: function() {
            this._copySelected();
            this._pasteClipboard();
        },

        /* ── Inline property editing ───────────────────────── */
        updateSelectedNodeName: function() {
            if (!this.selectedNode || !this._currentSelectedCell) return;
            let cell = this._currentSelectedCell;
            let newName = this.selectedNode.label;
            cell.set('elName', newName);
            cell.attr('nameLabel/text', newName);
            this.statusText = 'Name updated';
            /* GAP-CMP-005: Sync name to catalog so other diagrams see the change */
            let elId = cell.get('elementId');
            if (elId && parseInt(elId, 10) > 0) {
                Platform.fetch.patch('/archimate/api/elements/' + elId, { name: newName }, { silent: true })
                .then(function(data) {
                    if (data.error) _toast('error', data.error);
                })
                .catch(function() { _toast('error', 'Failed to sync name to catalog'); });
            }
        },

        updateSelectedNodeDescription: function() {
            if (!this.selectedNode || !this._currentSelectedCell) return;
            this._currentSelectedCell.set('localDescription', this.selectedNode.description);
            /* GAP-CMP-005: Sync description to catalog so other diagrams see the change */
            let elId = this._currentSelectedCell.get('elementId');
            if (elId && parseInt(elId, 10) > 0) {
                let desc = this.selectedNode.description || '';
                Platform.fetch.patch('/archimate/api/elements/' + elId, { description: desc }, { silent: true })
                .then(function(data) {
                    if (data.error) _toast('error', data.error);
                })
                .catch(function() { _toast('error', 'Failed to sync description to catalog'); });
            }
        },

        updateSelectedNodeStatus: function() {
            if (!this.selectedNode || !this._currentSelectedCell) return;
            this._currentSelectedCell.set('localStatus', this.selectedNode.status);
            this.statusText = 'Status: ' + (this.selectedNode.status || 'cleared');
        },

        /* GAP-CMP-009: Persist data classification & PII to custom_properties */
        updateDataClassification: function() {
            if (!this.selectedNode || !this._currentSelectedCell) return;
            let elId = this.selectedNode.elementId;
            const classification = this.selectedNode._dataClassification || '';
            const pii = this.selectedNode._containsPII || false;

            /* Update custom_properties on the server via PATCH */
            Platform.fetch.patch('/archimate/api/elements/' + elId, {
                custom_properties: {
                    data_classification: classification,
                    contains_pii: pii,
                }
            }, { silent: true })
            .catch(function() { _toast('error', 'Failed to save classification'); });

            /* Update visual badge on the JointJS node */
            this._currentSelectedCell.set('dataClassification', classification);
            this._currentSelectedCell.set('containsPII', pii);
        },

        /* GAP-INT-002: Persist deployment zone type to custom_properties */
        updateZoneType: function() {
            if (!this.selectedNode || !this._currentSelectedCell) return;
            const zoneType = this.selectedNode._zoneType || 'default';
            this._currentSelectedCell.set('zoneType', zoneType);
            /* Persist via custom_properties */
            let elId = this.selectedNode.elementId;
            if (elId && parseInt(elId, 10) > 0) {
                Platform.fetch.patch('/archimate/api/elements/' + elId, { custom_properties: { zone_type: zoneType } }, { silent: true })
                .catch(function() { _toast('error', 'Failed to save zone type'); });
            }
            this.statusText = 'Zone type: ' + zoneType;
        },

        /* ── Connection label editing ──────────────────────── */
        updateSelectedLinkLabel: function() {
            if (!this.selectedLink || !this.selectedEdge) return;
            let labelText = this.selectedEdge.customLabel || '';
            this.selectedLink.set('customLabel', labelText);
            if (labelText) {
                this.selectedLink.label(0, {
                    attrs: {
                        text: { text: labelText, fontSize: 10, fontWeight: 500, fill: '#64748b' },
                        rect: { fill: '#ffffff', stroke: '#e2e8f0', rx: 3, ry: 3 },
                    },
                    position: { distance: 0.5, offset: -12 },
                });
            } else {
                let relType = this.selectedEdge.relType || '';
                this.selectedLink.label(0, {
                    attrs: {
                        text: { text: relType, fontSize: 10, fontWeight: 500, fill: '#64748b' },
                        rect: { fill: '#ffffff', stroke: '#e2e8f0', rx: 3, ry: 3 },
                    },
                    position: { distance: 0.5, offset: -12 },
                });
            }
            /* BUG-CMP-002: Persist custom_label to backend */
            if (typeof this._persistRelMetadata === 'function') {
                let relId = this.selectedLink.get('relId');
                this._persistRelMetadata(relId, { custom_label: labelText || null });
            }
            this.statusText = 'Label updated';
        },

        /* GAP-CMP-010: Cardinality label editing */
        updateSelectedLinkCardinality: function() {
            if (!this.selectedLink || !this.selectedEdge) return;
            let srcCard = this.selectedEdge.sourceCardinality || '';
            let tgtCard = this.selectedEdge.targetCardinality || '';
            this.selectedLink.set('sourceCardinality', srcCard);
            this.selectedLink.set('targetCardinality', tgtCard);
            /* Update visual labels at source and target ends */
            let labels = this.selectedLink.labels() || [];
            /* Remove existing cardinality labels (indices > 0) */
            while (labels.length > 1) { this.selectedLink.removeLabel(labels.length - 1); labels = this.selectedLink.labels(); }
            if (srcCard) {
                this.selectedLink.appendLabel({
                    attrs: { text: { text: srcCard, fontSize: 9, fill: '#94a3b8' }, rect: { fill: 'transparent' } },
                    position: { distance: 0.1, offset: -14 },
                });
            }
            if (tgtCard) {
                this.selectedLink.appendLabel({
                    attrs: { text: { text: tgtCard, fontSize: 9, fill: '#94a3b8' }, rect: { fill: 'transparent' } },
                    position: { distance: 0.9, offset: -14 },
                });
            }
            this.statusText = 'Cardinality updated';
        },


        /* ── Nesting prompt actions ────────────────────────── */
        confirmNesting: function(relType) {
            this.nestingPromptOpen = false;
            let child = this.nestingPromptChild;
            let parent = this.nestingPromptParent;
            if (!child || !parent) return;

            let self = this;

            /* Switch parent to container (white-box) if not already */
            if (parent.get('renderingMode') !== 'white_box' && isContainerType(parent.get('elType'))) {
                self._switchToContainer(parent);
                parent = self._getReplacementCell(parent);
                if (!parent) return;
            }

            parent.embed(child);

            /* Auto-resize parent to fit child with padding */
            self._autoResizeContainer(parent);

            /* Create ArchiMate relationship if composition or aggregation */
            if (relType !== 'grouping') {
                let srcId = parent.get('elementId');
                let tgtId = child.get('elementId');
                if (srcId && tgtId) {
                    Platform.fetch.post('/archimate/api/relationships', {
                        source_element_id: srcId,
                        target_element_id: tgtId,
                        relationship_type: relType,
                        solution_id: self.solutionId || null,
                    }, { silent: true })
                    .then(function(data) {
                        if (data.id) {
                            self.relCount++;
                        }
                    })
                    .catch(function() { _toast('error', 'Operation failed. Please try again.'); });
                }
            }

            self.statusText = 'Nested "' + (child.get('elName') || '') + '" inside "' + (parent.get('elName') || '') + '" (' + relType + ')';
            self.nestingPromptChild = null;
            self.nestingPromptParent = null;
        },

        cancelNesting: function() {
            this.nestingPromptOpen = false;
            this.nestingPromptChild = null;
            this.nestingPromptParent = null;
        },

        /* ── Collapse/expand container (hide/show children) ── */
        toggleCollapse: function() {
            this.ctxMenuOpen = false;
            let cell = this.ctxMenuCell;
            if (!cell) return;
            if (cell.get('renderingMode') !== 'white_box') {
                this.statusText = 'Only white-box containers can be collapsed';
                return;
            }

            let children = cell.getEmbeddedCells();
            if (children.length === 0) {
                this.statusText = 'No nested elements to collapse';
                return;
            }

            let isCollapsed = cell.get('collapsed');
            let self = this;

            if (isCollapsed) {
                /* Expand: restore children visibility and original size */
                let savedSize = cell.get('_expandedSize');
                children.forEach(function(child) {
                    let view = self.paper.findViewByModel(child);
                    if (view) view.el.style.display = '';
                    /* Also unhide links connected to child */
                    self.graph.getConnectedLinks(child).forEach(function(link) {
                        let lView = self.paper.findViewByModel(link);
                        if (lView) lView.el.style.display = '';
                    });
                });
                if (savedSize) cell.resize(savedSize.width, savedSize.height);
                cell.set('collapsed', false);
                this.statusText = 'Expanded: ' + (cell.get('elName') || '');
            } else {
                /* Collapse: hide children and shrink container */
                cell.set('_expandedSize', cell.size());
                children.forEach(function(child) {
                    let view = self.paper.findViewByModel(child);
                    if (view) view.el.style.display = 'none';
                    self.graph.getConnectedLinks(child).forEach(function(link) {
                        let lView = self.paper.findViewByModel(link);
                        if (lView) lView.el.style.display = 'none';
                    });
                });
                cell.resize(180, 64);
                cell.set('collapsed', true);
                this.statusText = 'Collapsed: ' + (cell.get('elName') || '') + ' (' + children.length + ' hidden)';
            }
        },

        _switchToContainer: function(cell) {
            let self = this;
            let elId = cell.get('elementId');
            let name = cell.get('elName') || '';
            let elType = cell.get('elType') || '';
            let layer = cell.get('elLayer') || '';
            let pos = cell.position();
            let embedded = cell.getEmbeddedCells().slice();

            /* Preserve connected links */
            let connectedLinks = self.graph.getConnectedLinks(cell).map(function(l) {
                return {
                    json: l.toJSON(),
                    isSource: (l.get('source') || {}).id === cell.id,
                };
            });

            UndoStack.pause();
            cell.remove();

            let container = createContainerNode(elId, name, elType, layer, pos.x, pos.y, 280, 180);
            self.graph.addCell(container);

            /* Re-embed children */
            embedded.forEach(function(ch) {
                container.embed(ch);
            });

            /* Reconnect links */
            connectedLinks.forEach(function(info) {
                let link = self.graph.getCell(info.json.id);
                if (!link) return;
                if (info.isSource) {
                    link.set('source', { id: container.id });
                } else {
                    link.set('target', { id: container.id });
                }
            });

            self._lastReplacedCellId = container.id;
            UndoStack.resume();
        },

        _switchToNode: function(cell) {
            let self = this;
            let elId = cell.get('elementId');
            let name = cell.get('elName') || '';
            let elType = cell.get('elType') || '';
            let layer = cell.get('elLayer') || '';
            let pos = cell.position();
            let embedded = cell.getEmbeddedCells().slice();

            /* Preserve connected links */
            let connectedLinks = self.graph.getConnectedLinks(cell).map(function(l) {
                return {
                    json: l.toJSON(),
                    isSource: (l.get('source') || {}).id === cell.id,
                };
            });

            UndoStack.pause();

            /* Unembed children before removing */
            embedded.forEach(function(ch) {
                cell.unembed(ch);
            });
            cell.remove();

            let node = createNode(elId, name, elType, layer, pos.x, pos.y);
            self.graph.addCell(node);

            /* Reconnect links */
            connectedLinks.forEach(function(info) {
                let link = self.graph.getCell(info.json.id);
                if (!link) return;
                if (info.isSource) {
                    link.set('source', { id: node.id });
                } else {
                    link.set('target', { id: node.id });
                }
            });

            self._lastReplacedCellId = node.id;
            UndoStack.resume();
        },

        _getReplacementCell: function() {
            if (!this._lastReplacedCellId) return null;
            return this.graph.getCell(this._lastReplacedCellId) || null;
        },

        _autoResizeContainer: function(container) {
            let embedded = container.getEmbeddedCells();
            if (embedded.length === 0) return;

            let PADDING = 20;
            let HEADER = 32;
            let containerPos = container.position();
            let minX = containerPos.x;
            let minY = containerPos.y;
            let maxX = containerPos.x + container.size().width;
            let maxY = containerPos.y + container.size().height;

            embedded.forEach(function(child) {
                let cPos = child.position();
                let cSize = child.size();
                minX = Math.min(minX, cPos.x - PADDING);
                minY = Math.min(minY, cPos.y - PADDING - HEADER);
                maxX = Math.max(maxX, cPos.x + cSize.width + PADDING);
                maxY = Math.max(maxY, cPos.y + cSize.height + PADDING);
            });

            container.position(minX, minY);
            container.resize(maxX - minX, maxY - minY);
        },


        layoutDagre: function(direction) {
            if (typeof dagre === 'undefined') { this.statusText = 'Dagre layout library not loaded'; return; }
            let elements = this.graph.getElements();
            if (elements.length === 0) { this.statusText = 'Nothing to layout'; return; }
            direction = direction || 'TB';

            let g = new dagre.graphlib.Graph({ compound: true });
            g.setGraph({ rankdir: direction, nodesep: 40, ranksep: 80, marginx: 40, marginy: 40 });
            g.setDefaultEdgeLabel(function() { return {}; });

            elements.forEach(function(el) {
                let size = el.size();
                g.setNode(el.id, { width: size.width, height: size.height, label: el.id });
            });

            elements.forEach(function(el) {
                let parent = el.getParentCell ? el.getParentCell() : null;
                if (parent && g.hasNode(parent.id)) g.setParent(el.id, parent.id);
            });

            this.graph.getLinks().forEach(function(link) {
                let src = link.get('source') && link.get('source').id;
                let tgt = link.get('target') && link.get('target').id;
                if (src && tgt && g.hasNode(src) && g.hasNode(tgt)) g.setEdge(src, tgt);
            });

            dagre.layout(g);

            UndoStack.pause();
            elements.forEach(function(el) {
                let node = g.node(el.id);
                if (node) el.position(Math.round((node.x - node.width / 2) / 12) * 12,
                                       Math.round((node.y - node.height / 2) / 12) * 12);
            });
            UndoStack.resume();

            this.paper.scaleContentToFit({ padding: 40, maxScale: 1 });
            this.zoomPercent = Math.round(this.paper.scale().sx * 100);
            this._scheduleMiniMapUpdate();
            this.statusText = 'Hierarchical layout applied (' + direction + ')';
        },

        /* ── Sequence View ──────────────────────────────────────────────
           A lifeline/message render of the elements + relationships already
           on the canvas. Deliberately not a new element/table: per ADR 0008
           (one system of record per concept) the messages ARE the existing
           ArchiMate relationships, ordered by relationship.sequence_order
           (a nullable step number persisted on archimate_relationships — see
           app/models/archimate_core.py). No sequence_order yet falls back to
           the order the relationships were created in, so every relationship
           on the canvas renders instead of being dropped for lacking a step.
           Only ArchiMate-typed elements can ever be lifelines here — there is
           no free-form UML actor/object, on purpose: it keeps every message
           traceable back to a real element in the architecture repository,
           which a Lucidchart sequence diagram cannot offer. */
        layoutSequence: function() {
            let self = this;
            let allLinks = self.graph.getLinks().filter(function(l) { return !l.get('isAnnotation'); });
            if (!allLinks.length) {
                self.statusText = 'Sequence View needs at least one relationship between elements';
                _toast('info', 'Add relationships between elements first, then apply Sequence View.');
                return;
            }

            let messages = allLinks.map(function(link, idx) {
                let src = link.get('source'), tgt = link.get('target');
                return {
                    link: link,
                    srcId: src && src.id,
                    tgtId: tgt && tgt.id,
                    relId: link.get('relId'),
                    relType: link.get('relType'),
                    seq: (typeof link.get('sequenceOrder') === 'number') ? link.get('sequenceOrder') : null,
                    createdAt: link.get('createdAt') || 0,
                    _idx: idx,
                };
            }).filter(function(m) { return m.srcId && m.tgtId && self.graph.getCell(m.srcId) && self.graph.getCell(m.tgtId); });

            if (!messages.length) {
                self.statusText = 'Sequence View needs relationships between two placed elements';
                return;
            }

            messages.sort(function(a, b) {
                if (a.seq != null && b.seq != null) return a.seq - b.seq;
                if (a.seq != null) return -1;
                if (b.seq != null) return 1;
                if (a.createdAt !== b.createdAt) return a.createdAt - b.createdAt;
                return a._idx - b._idx;
            });

            let lifelineOrder = [];
            let seen = {};
            messages.forEach(function(m) {
                if (!seen[m.srcId]) { seen[m.srcId] = true; lifelineOrder.push(m.srcId); }
                if (!seen[m.tgtId]) { seen[m.tgtId] = true; lifelineOrder.push(m.tgtId); }
            });

            let colGap = 220, rowGap = 70, rowStart = 220, headerY = 90;
            let xById = {};

            UndoStack.pause();

            /* Drop any guide lines left from a previous Sequence View pass. */
            self.graph.getCells().filter(function(c) { return c.get('isSequenceGuide'); })
                .forEach(function(c) { c.remove(); });

            lifelineOrder.forEach(function(id, i) {
                let cell = self.graph.getCell(id);
                if (!cell) return;
                let w = cell.size().width;
                let x = 60 + i * colGap;
                xById[id] = x + w / 2;
                cell.position(x, headerY);
            });

            let lastRowY = rowStart;
            messages.forEach(function(m, i) {
                let rowY = rowStart + i * rowGap;
                lastRowY = rowY;
                let sx = xById[m.srcId], tx = xById[m.tgtId];
                if (sx == null || tx == null) return;
                m.link.router(null);
                m.link.connector({ name: 'normal' });
                m.link.vertices([{ x: sx, y: rowY }, { x: tx, y: rowY }]);
                let existing = m.link.label(0);
                let baseText = (existing && existing.attrs && existing.attrs.text && existing.attrs.text.text) || humanizeRelTypeLabel(m.relType);
                baseText = baseText.replace(/^\d+\.\s*/, '');
                m.link.label(0, { attrs: { text: { text: (i + 1) + '. ' + baseText } } });
            });

            lifelineOrder.forEach(function(id) {
                let cell = self.graph.getCell(id);
                if (!cell) return;
                let pos = cell.position(), size = cell.size();
                let x = pos.x + size.width / 2;
                let guide = new joint.shapes.standard.Link({
                    source: { x: x, y: pos.y + size.height },
                    target: { x: x, y: lastRowY + 40 },
                    attrs: {
                        line: { stroke: '#94a3b8', strokeWidth: 1, strokeDasharray: '4,4', targetMarker: { d: '' } },
                    },
                    router: null,
                    connector: { name: 'normal' },
                });
                guide.set('isSequenceGuide', true);
                guide.set('isAnnotation', true);
                self.graph.addCell(guide);
                guide.toBack();
            });

            UndoStack.resume();

            self.sequenceViewActive = true;
            self.sequencePanelOpen = true;
            self.sequenceMessages = messages.map(function(m, i) {
                let srcCell = self.graph.getCell(m.srcId);
                let tgtCell = self.graph.getCell(m.tgtId);
                return {
                    relId: m.relId,
                    order: i,
                    label: (i + 1) + '. ' + humanizeRelTypeLabel(m.relType),
                    srcName: srcCell ? srcCell.get('elName') : '',
                    tgtName: tgtCell ? tgtCell.get('elName') : '',
                };
            });

            self.paper.scaleContentToFit({ padding: 40, maxScale: 1 });
            self.zoomPercent = Math.round(self.paper.scale().sx * 100);
            self._scheduleMiniMapUpdate();
            self.statusText = 'Sequence View — ' + lifelineOrder.length + ' lifelines, ' + messages.length + ' messages';
        },

        /* Reorder one message in the sequence panel and persist the new step
           numbers to the owning relationships, then re-run the layout so the
           canvas reflects the saved order — never a client-only reorder that
           a reload would silently discard. */
        moveMessage: function(index, direction) {
            let self = this;
            let arr = self.sequenceMessages.slice();
            let j = index + direction;
            if (j < 0 || j >= arr.length) return;
            let tmp = arr[index];
            arr[index] = arr[j];
            arr[j] = tmp;
            self.sequenceMessages = arr;

            let updates = arr.map(function(m, i) { return { relId: m.relId, order: i }; });
            Promise.all(updates.map(function(u) {
                if (!u.relId) return Promise.resolve();
                return Platform.fetch.put('/archimate/api/relationships/' + u.relId,
                    { sequence_order: u.order }, { silent: true });
            })).then(function() {
                updates.forEach(function(u) {
                    let link = self.graph.getLinks().find(function(l) { return l.get('relId') === u.relId; });
                    if (link) link.set('sequenceOrder', u.order);
                });
                self.layoutSequence();
            }).catch(function() {
                _toast('error', 'Failed to save message order — reload and try again');
            });
        },

        exitSequenceView: function() {
            this.sequenceViewActive = false;
            this.sequencePanelOpen = false;
            this.graph.getCells().filter(function(c) { return c.get('isSequenceGuide'); })
                .forEach(function(c) { c.remove(); });
            this.layoutDagre('TB');
        },

        exportXml: function() {
            if (!this.currentSavedVpId) {
                this.statusText = 'Save viewpoint first to export XML';
                return;
            }
            let self = this;
            self.statusText = 'Exporting ArchiMate XML...';
            fetch('/archimate/api/saved-viewpoints/' + self.currentSavedVpId + '/export?format=archimate_exchange') // raw-fetch-ok: blob download, Platform.fetch always parses JSON/text
                .then(function(resp) {
                    if (!resp.ok) throw new Error('Export failed: ' + resp.status);
                    return resp.blob();
                })
                .then(function(blob) {
                    let a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    let titleText = self.activeViewpointName || 'viewpoint';
                    let safeName = titleText.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 60);
                    a.download = safeName + '.xml';
                    a.click();
                    URL.revokeObjectURL(a.href);
                    self.statusText = 'ArchiMate Exchange XML exported';
                })
                .catch(function(err) {
                    self.statusText = 'XML export failed: ' + err.message;
                    _toast('error', self.statusText);
                });
        },

        /* ── Collect the LIVE canvas (elements + relationships + layout) ──
           so exports work on what's on screen, saved or not. */
        _collectLiveViewpoint: function() {
            let self = this;
            let elements = [];
            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                let eid = cell.get('elementId');
                if (!eid) return;
                let pos = cell.position();
                let size = cell.size();
                elements.push({
                    id: eid,
                    name: cell.get('elName') || '',
                    type: cell.get('elType') || 'ApplicationComponent',
                    layer: cell.get('elLayer') || '',
                    x: Math.round(pos.x), y: Math.round(pos.y),
                    w: Math.round(size.width), h: Math.round(size.height),
                });
            });
            let relationships = [];
            self.graph.getLinks().forEach(function(link) {
                let s = link.get('source'), t = link.get('target');
                if (!s || !t || !s.id || !t.id) return;
                let srcCell = self.graph.getCell(s.id), tgtCell = self.graph.getCell(t.id);
                if (!srcCell || !tgtCell) return;
                let sid = srcCell.get('elementId'), tid = tgtCell.get('elementId');
                if (!sid || !tid) return;
                relationships.push({
                    id: link.get('relId') || link.id,
                    source_id: sid, target_id: tid,
                    type: link.get('relType') || 'association',
                    label: link.get('customLabel') || '',
                });
            });
            return {
                viewpoint_name: self.activeViewpointName || 'Composer Diagram',
                elements: elements,
                relationships: relationships,
            };
        },

        /* ── Export the CURRENT canvas in an interchange format (works unsaved) ──
           fmt: backend format key; ext: download extension; label: UI label. */
        exportFormat: function(fmt, ext, label) {
            let self = this;
            let vp = self._collectLiveViewpoint();
            if (!vp.elements.length) {
                self.statusText = 'Nothing on the canvas to export';
                _toast('warning', 'Add elements to the canvas before exporting.');
                return;
            }
            vp.format = fmt;
            self.statusText = 'Exporting ' + label + '…';
            fetch('/archimate/api/composer/export', { // raw-fetch-ok: blob download, Platform.fetch always parses JSON/text
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
                body: JSON.stringify(vp),
            })
                .then(function(resp) {
                    if (!resp.ok) {
                        return resp.json().then(function(e) {
                            throw new Error(e.error || ('Export failed: ' + resp.status));
                        }, function() { throw new Error('Export failed: ' + resp.status); });
                    }
                    return resp.blob();
                })
                .then(function(blob) {
                    let a = document.createElement('a');
                    a.href = URL.createObjectURL(blob);
                    let titleText = self.activeViewpointName || 'diagram';
                    let safeName = titleText.replace(/[^a-zA-Z0-9_-]/g, '_').substring(0, 60);
                    a.download = safeName + '.' + ext;
                    a.click();
                    URL.revokeObjectURL(a.href);
                    self.statusText = label + ' exported';
                })
                .catch(function(err) {
                    self.statusText = label + ' export failed: ' + err.message;
                    _toast('error', self.statusText);
                });
        },

        /* ── GAP-CMP-006: Import Open Exchange Format (OEF) XML ── */
        importOef: function() {
            let self = this;
            let input = document.createElement('input');
            input.type = 'file';
            input.accept = '.xml,.archimate';
            input.onchange = function(e) {
                const file = e.target.files[0];
                if (!file) return;
                const formData = new FormData();
                formData.append('file', file);
                self.statusText = 'Importing ' + file.name + '...';
                Platform.fetch.post('/archimate/api/import/oef', formData, { silent: true })
                .then(function(data) {
                    if (data.error) { _toast('error', data.error); self.statusText = 'Import failed'; return; }
                    let elements = data.elements || [];
                    const rels = data.relationships || [];
                    const stats = data.stats || {};
                    /* Place elements on canvas in a grid layout */
                    let cols = Math.ceil(Math.sqrt(elements.length)) || 1;
                    elements.forEach(function(el, idx) {
                        let col = idx % cols;
                        let row = Math.floor(idx / cols);
                        let x = 50 + col * 250;
                        let y = 50 + row * 170;
                        let layer = (el.layer || '').toLowerCase() || guessLayer(el.type);
                        let node = createNode(el.id, el.name, el.type, layer, x, y);
                        self.graph.addCell(node);
                        self.canvasElements[el.id] = el;
                        self.elementCount++;
                    });
                    /* Wire relationships */
                    rels.forEach(function(rel) {
                        let srcCell = null, tgtCell = null;
                        self.graph.getElements().forEach(function(c) {
                            if (c.get('elementId') == rel.source_id) srcCell = c;
                            if (c.get('elementId') == rel.target_id) tgtCell = c;
                        });
                        if (srcCell && tgtCell) {
                            let link = createLink(srcCell, tgtCell, rel.type || 'association', rel.id);
                            self.graph.addCell(link);
                            self.relCount++;
                        }
                    });
                    _toast('success', 'Imported ' + (stats.elements_created || 0) + ' new + ' + (stats.elements_linked || 0) + ' linked elements');
                    self.statusText = 'Import complete: ' + elements.length + ' elements, ' + rels.length + ' relationships';
                    self.fitCanvas();
                    /* GAP-INT-003: Render annotation cards for imported relationships */
                    self.graph.getLinks().forEach(function(lnk) {
                        self._updateAnnotationCard(lnk);
                    });
                })
                .catch(function(err) {
                    _toast('error', 'Import failed: ' + ((err && err.message) || err));
                    self.statusText = 'Import failed';
                });
            };
            input.click();
        },

        /* ── Practitioner mistake warnings ────────────────── */
        _checkPractitionerWarning: function(relType, srcType, tgtType, srcLayer, tgtLayer) {
            let PASSIVE = { DataObject: true, BusinessObject: true, Artifact: true, Representation: true, Contract: true };
            let ACTIVE = { ApplicationComponent: true, ApplicationFunction: true, BusinessActor: true, BusinessRole: true, Node: true, Device: true, SystemSoftware: true };

            /* Mistake 1: Assignment from passive to active (should be Access) */
            if (relType === 'assignment' && PASSIVE[srcType] && ACTIVE[tgtType]) {
                return 'Assignment from ' + srcType + ' to ' + tgtType + ' — did you mean Access (read/write)?';
            }

            /* Mistake 2: Cross-layer Composition */
            if (relType === 'composition' && srcLayer && tgtLayer && srcLayer.toLowerCase() !== tgtLayer.toLowerCase()) {
                return 'Composition crosses layers (' + srcLayer + ' → ' + tgtLayer + ') — ArchiMate does not allow cross-layer composition.';
            }

            /* Mistake 3: Backwards Serving (consumer-to-provider) */
            if (relType === 'serving') {
                let LAYER_RANK = { strategy: 0, motivation: 1, business: 2, application: 3, technology: 4, physical: 5, implementation: 6 };
                let sRank = LAYER_RANK[(srcLayer || '').toLowerCase()];
                let tRank = LAYER_RANK[(tgtLayer || '').toLowerCase()];
                if (sRank !== undefined && tRank !== undefined && sRank < tRank) {
                    return 'Serving is drawn from provider to consumer — ' + srcLayer + ' is higher than ' + tgtLayer + '. Did you mean the reverse direction?';
                }
            }

            /* Mistake 4: Cross-layer Triggering without intermediary */
            if (relType === 'triggering' && srcLayer && tgtLayer && srcLayer.toLowerCase() !== tgtLayer.toLowerCase()) {
                return 'Triggering across layers (' + srcLayer + ' → ' + tgtLayer + ') usually needs an intermediary element.';
            }

            return null;
        },


        openQuickAdd: function() {
            if (this.mode === 'view') return;
            this.quickAddOpen = true;
            this.quickAddQuery = '';
            this.quickAddResults = [];
            this.quickAddLoading = false;
            let self = this;
            this.$nextTick(function() {
                let input = document.getElementById('quick-add-input');
                if (input) input.focus();
            });
        },

        closeQuickAdd: function() {
            this.quickAddOpen = false;
            this.quickAddQuery = '';
            this.quickAddResults = [];
            this.quickAddLoading = false;
        },

        doQuickAddSearch: function() {
            let self = this;
            let q = (this.quickAddQuery || '').trim();
            if (!q) { this.quickAddResults = []; return; }
            let ql = q.toLowerCase();

            /* CMP-11: the placeholder promises "search catalog" by element NAME,
               but this only ever filtered the PALETTE (element TYPE labels), so
               typing a real element name returned "no matching element types" and
               forced the create path. Now it searches the actual catalog by name
               (existing elements to REUSE) and still offers matching element
               TYPES to create — the two are visually distinguished and dispatched
               differently in quickAddPick. */
            let paletteMatches = [];
            let layers = Object.keys(PALETTE);
            for (let i = 0; i < layers.length; i++) {
                let items = PALETTE[layers[i]];
                for (let j = 0; j < items.length; j++) {
                    let t = items[j];
                    if (t.label.toLowerCase().indexOf(ql) !== -1 ||
                        t.type.toLowerCase().indexOf(ql) !== -1) {
                        paletteMatches.push({
                            id: t.type, name: t.label, type: t.type,
                            layer: layers[i], _existing: false,
                        });
                    }
                }
            }

            /* CMP-11 (revised): show the synchronous PALETTE matches IMMEDIATELY
               so the results panel is never empty while the catalog request is in
               flight — the first version left quickAddResults empty during the
               fetch, so the "No matching elements" empty-state + create UI flashed
               on every keystroke. We also set quickAddLoading and carry a request
               token so a slow earlier response can't overwrite a newer query. */
            self.quickAddResults = paletteMatches.slice(0, 15);
            self.quickAddLoading = true;
            let token = (self._quickAddToken = (self._quickAddToken || 0) + 1);
            let url = '/archimate/api/elements/search?limit=10&q=' + encodeURIComponent(q);
            Platform.fetch.get(url, null, { silent: true })
            .then(function(resp) {
                if (token !== self._quickAddToken) return;  /* stale response — ignore */
                let data = (resp && resp.data) || resp || [];
                let existing = (Array.isArray(data) ? data : [])
                    .filter(function(el) { return el && el.id && !self.canvasElements[el.id]; })
                    .map(function(el) {
                        return {
                            id: el.id, name: el.name, type: el.type,
                            layer: (el.layer || '').toLowerCase(), _existing: true,
                        };
                    });
                /* Existing elements first (reuse beats duplicate), then types. */
                self.quickAddResults = existing.concat(paletteMatches).slice(0, 15);
                self.quickAddLoading = false;
            })
            .catch(function() {
                if (token !== self._quickAddToken) return;
                /* Catalog unreachable — keep the palette types already shown. */
                self.quickAddResults = paletteMatches.slice(0, 15);
                self.quickAddLoading = false;
            });
        },

        quickAddPick: function(item) {
            this.closeQuickAdd();
            let self = this;
            let type = item.type;
            let name = item.name;
            let layer = (item.layer || '').toLowerCase() || guessLayer(type);

            /* Compute canvas center for placement */
            let vp = this.paper.translate();
            let s = this.paper.scale().sx;
            let rect = this.paper.el.getBoundingClientRect();
            let cx = (rect.width / 2 - vp.tx) / s;
            let cy = (rect.height / 2 - vp.ty) / s;
            let GRID = 12;
            cx = Math.round((cx - 100) / GRID) * GRID;
            cy = Math.round((cy - 65) / GRID) * GRID;
            let placed = self._placementFor(cx, cy);
            cx = placed.x; cy = placed.y;

            /* CMP-11: an existing catalog element is placed directly — creating a
               new one would duplicate the record. */
            if (item._existing && item.id) {
                let elx = { id: item.id, name: name, type: type, layer: layer };
                let nodex = createNode(elx.id, elx.name, elx.type, elx.layer, cx, cy);
                self.graph.addCell(nodex);
                self.canvasElements[elx.id] = elx;
                self.elementCount++;
                if (self.solutionId) self.linkElementToSolution(elx.id);
                self.statusText = 'Added: ' + elx.name;
                self.logAuditEvent('element_added', 'element', elx.id, elx.name, null, elx.type);
                self.refreshMaturityOverlay();
                return;
            }

            /* Create element in the repository, then place on canvas */
            self.statusText = 'Creating ' + name + '...';
            Platform.fetch.post('/api/architecture-assistant/create-element', { name: name, type: type, layer: layer }, { silent: true })
            .then(function(data) {
                let elem = data.element || data;
                if (elem.id) {
                    let el = { id: elem.id, name: elem.name || name, type: elem.type || type, layer: elem.layer || layer };
                    let node = createNode(el.id, el.name, el.type, el.layer, cx, cy);
                    self.graph.addCell(node);
                    self.canvasElements[el.id] = el;
                    self.elementCount++;
                    if (self.solutionId) self.linkElementToSolution(elem.id);
                    self.statusText = 'Added: ' + el.name;
                    self.logAuditEvent('element_added', 'element', el.id, el.name, null, el.type);
                    self.refreshMaturityOverlay();
                } else {
                    self.statusText = 'Create failed: ' + (data.error || 'unknown');
                    _toast('error', 'Failed to create element');
                }
            })
            .catch(function() {
                self.statusText = 'Create failed';
                _toast('error', 'Failed to create element');
            });
        },

        /* ── GAP-CMP-011: Sub-diagram drill-down ──────────── */
        linkSubDiagram: function() {
            this.ctxMenuOpen = false;
            if (!this.ctxMenuCell) return;
            let cell = this.ctxMenuCell;
            let existingId = cell.get('linkedSubDiagramId');

            /* If already linked, navigate to the sub-diagram */
            if (existingId) {
                window.open('/archimate/composer?viewpoint_id=' + existingId, '_blank');
                return;
            }

            /* Create a new viewpoint for this element's children */
            let self = this;
            let elementName = cell.get('elName') || 'Element';
            let elementId = cell.get('elementId');

            self.statusText = 'Creating sub-diagram for ' + elementName + '...';

            Platform.fetch.post('/api/architecture-assistant/viewpoints', {
                name: elementName + ' \u2014 Detail',
                description: 'Sub-diagram for ' + elementName + ' (auto-created from composer)',
                parent_element_id: elementId,
            }, { silent: true })
            .then(function(data) {
                let vpId = data.id || (data.data && data.data.id);
                if (vpId) {
                    cell.set('linkedSubDiagramId', vpId);
                    /* Add a visual "+" indicator on the node */
                    cell.attr('subDiagramBadge/text', '+');
                    cell.attr('subDiagramBadge/fontSize', 14);
                    cell.attr('subDiagramBadge/fontWeight', 'bold');
                    cell.attr('subDiagramBadge/fill', '#3b82f6');
                    cell.attr('subDiagramBadge/refX', '95%');
                    cell.attr('subDiagramBadge/refY', '5%');
                    self.statusText = 'Sub-diagram created: ' + elementName;
                    _toast('success', 'Sub-diagram linked. Right-click → "Open Sub-Diagram" to navigate.');
                } else {
                    self.statusText = 'Failed to create sub-diagram';
                    _toast('error', data.error || 'Failed to create viewpoint');
                }
            })
            .catch(function(err) {
                self.statusText = 'Error creating sub-diagram';
                _toast('error', (err && err.message) || 'Failed');
            });
        },

        /* ── Delete element from repository ──────────────── */
        /* GAP-DEL-001 (10 Sep 2026): this control has never worked. It called
           DELETE /architecture/elements/<id> -- no such route exists (the
           real one is archimate_crud's /architecture/api/elements/<id>,
           missing the /api segment here) -- and read data.viewpoint_count /
           data.solution_count from /archimate/api/elements/<id>/detail,
           whose response carries neither key, so the confirmation always
           read "0 viewpoint(s), 0 solution(s)" regardless of real usage.
           Both fixed: real usage endpoint, real delete endpoint, and the
           server (not this client) is the one that decides whether an
           in-use element may be force-deleted -- only an admin may. */
        deleteFromRepository: function() {
            this.ctxMenuOpen = false;
            if (this.mode === 'view') return;
            let cell = this.ctxMenuCell;
            if (!cell) return;

            let elId = cell.get('elementId');
            let name = cell.get('elName') || '(unnamed)';
            if (!elId) return;

            let self = this;
            let deleteUrl = '/applications/api/elements/' + elId;
            // Exclude the diagram currently open: being on THIS diagram is
            // not "in use elsewhere", it's the normal reason you'd be
            // right-clicking it at all. Without this every delete looks
            // in-use and the safe, no-force path is never reachable.
            let diagramQuery = self.currentSavedVpId ? ('diagram_id=' + self.currentSavedVpId) : '';

            let removeFromCanvas = function() {
                cell.remove();
                delete self.canvasElements[elId];
                self.elementCount = Math.max(0, self.elementCount - 1);
                self.statusText = 'Deleted from repository: ' + name;
            };

            let attemptDelete = function(force) {
                let qs = [diagramQuery, force ? 'force=true' : ''].filter(Boolean).join('&');
                return fetch(deleteUrl + (qs ? '?' + qs : ''), { // raw-fetch-ok: needs the raw status code (409/403) to branch, Platform.fetch treats non-2xx as a thrown error
                    method: 'DELETE',
                    headers: { 'X-CSRFToken': csrfToken() },
                }).then(function(resp) {
                    return resp.json().then(function(body) { return { status: resp.status, body: body }; });
                });
            };

            Platform.fetch('/applications/api/elements/' + elId + '/usage' + (diagramQuery ? '?' + diagramQuery : ''), { silent: true })
            .then(async function(data) {
                let usage = data.usage || { relationships: 0, diagrams: 0 };
                let inUse = usage.relationships > 0 || usage.diagrams > 0;
                let msg = inUse
                    ? ('DELETE "' + name + '" from the ArchiMate repository?\n\n'
                        + 'This element is referenced by:\n'
                        + '  • ' + usage.relationships + ' relationship(s)\n'
                        + '  • ' + usage.diagrams + ' other diagram(s)\n\n'
                        + 'Deleting it will remove those relationships too. This action CANNOT be undone.')
                    : ('DELETE "' + name + '" from the ArchiMate repository?\n\n'
                        + 'Not referenced by any relationship or other diagram.\n\n'
                        + 'This action CANNOT be undone.');
                if (!(await Platform.modal.confirm(msg))) return;

                let result = await attemptDelete(inUse);
                if (result.status === 200) {
                    removeFromCanvas();
                    return;
                }
                if (result.status === 403) {
                    _toast('error', 'Only an admin can delete an element still in use elsewhere.');
                    self.statusText = 'Delete blocked — element in use, admin required';
                    return;
                }
                _toast('error', (result.body && result.body.error) || 'Delete failed');
                self.statusText = 'Delete failed: ' + name;
            })
            .catch(function(err) {
                self.statusText = 'Error: ' + (err && err.message);
                _toast('error', (err && err.message) || 'Could not check element usage');
            });
        },

        /* ── Set maturity score from context menu ─────────── */
        setMaturityFromCtx: function() {
            this.ctxMenuOpen = false;
            let cell = this.ctxMenuCell;
            if (!cell) return;

            let elId = cell.get('elementId');
            let name = cell.get('elName') || '(unnamed)';
            if (!elId) return;

            let self = this;
            let existing = cell.get('maturityData');
            /* Reuse the save-name modal with a hint label as a lightweight input */
            self.saveNameValue = existing ? String(existing.pct) : '';
            self._saveNamePromptHint = 'Enter a score 0–100  (0–20 = M1, 21–40 = M2, 41–60 = M3, 61–80 = M4, 81–100 = M5)';
            self.saveNameOpen = true;
            self._saveNameCallback = function(input) {
                let pct = parseInt(input, 10);
                if (isNaN(pct) || pct < 0 || pct > 100) {
                    self._toast('warn', 'Invalid score — enter 0 to 100');
                    return;
                }
                let level = Math.max(1, Math.min(5, Math.ceil(pct / 20) || 1));
                let m = { level: level, label: 'M' + level, pct: pct, source: 'manual' };
                self._applyMaturityToCell(cell, m);
                self.statusText = name + ' → ' + m.label + ' (' + pct + '%)';
                Platform.fetch.put('/archimate/api/elements/' + elId + '/alignment-score', { score: pct }, { silent: true })
                    .catch(function() { _toast('error', 'Failed to save maturity score'); });
            };
        },

        /* ── GAP-INT-004: Event badge on elements ───────────── */
        setEventBadge: async function() {
            this.ctxMenuOpen = false;
            if (!this.ctxMenuCell || this.mode === 'view') return;
            let cell = this.ctxMenuCell;
            let existing = cell.get('eventSchedule') || '';
            const schedule = await Platform.modal.promptText('Enter schedule/event (e.g. "Monthly 27th at 12:45 AM EST"):', {
                title: 'Event badge', defaultValue: existing
            });
            if (schedule === null) return; // cancelled
            if (schedule.trim()) {
                cell.set('eventSchedule', schedule.trim());
                cell.attr('intelligenceBadge/text', schedule.trim());
                cell.attr('intelligenceBadge/display', 'block');
                cell.attr('intelligenceBadge/fill', '#ef4444');
                cell.attr('intelligenceBadge/fontSize', 8);
                cell.attr('intelligenceBadge/fontWeight', 600);
                let elId = cell.get('elementId');
                if (elId && parseInt(elId, 10) > 0) {
                    Platform.fetch.patch('/archimate/api/elements/' + elId, { custom_properties: { event_schedule: schedule.trim() } }, { silent: true })
                    .catch(function() {
                        _toast('error', 'Failed to save event schedule — it will be lost on reload');
                    });
                }
            }
        },

        clearEventBadge: function() {
            this.ctxMenuOpen = false;
            if (!this.ctxMenuCell) return;
            let cell = this.ctxMenuCell;
            cell.set('eventSchedule', '');
            cell.attr('intelligenceBadge/text', '');
            cell.attr('intelligenceBadge/display', 'none');
            let elId = cell.get('elementId');
            if (elId && parseInt(elId, 10) > 0) {
                Platform.fetch.patch('/archimate/api/elements/' + elId, { custom_properties: { event_schedule: '' } }, { silent: true })
                .catch(function() {
                    _toast('error', 'Failed to clear event schedule on the server — it may reappear on reload');
                });
            }
        },

        /* ── Set Lifecycle state (ArchiMate border style per phase) ──────────
         *
         * ArchiMate 3 lifecycle phases are expressed via border style:
         *   planned      — blue dashed border
         *   current      — solid border (default)
         *   phasing_out  — amber dashed border
         *   retired      — red dotted border, reduced opacity
         */
        setLifecycleFromCtx: async function(lifecycle) {
            this.ctxMenuOpen = false;
            let cell = this.ctxMenuCell;
            if (!cell) return;

            let LIFECYCLE_STYLES = {
                planned:     { stroke: '#3b82f6', strokeDasharray: '6,3', strokeWidth: 2, opacity: 1 },
                current:     { stroke: null,      strokeDasharray: null,  strokeWidth: 1.5, opacity: 1 },
                phasing_out: { stroke: '#f59e0b', strokeDasharray: '6,3', strokeWidth: 2, opacity: 0.8 },
                retired:     { stroke: '#ef4444', strokeDasharray: '2,4', strokeWidth: 1.5, opacity: 0.45 },
            };

            let style = LIFECYCLE_STYLES[lifecycle] || LIFECYCLE_STYLES.current;
            let c = layerColor((cell.get('elLayer') || '').toLowerCase());

            /* Apply border style to body shape */
            cell.attr('body/stroke', style.stroke || c.stroke || '#64748b');
            cell.attr('body/strokeDasharray', style.strokeDasharray || '');
            cell.attr('body/strokeWidth', style.strokeWidth);
            cell.attr('./opacity', style.opacity);

            /* GAP-CMP-004: Record lifecycle transition history */
            let previousLifecycle = cell.get('elLifecycle') || '';
            let reason = '';
            if (previousLifecycle && previousLifecycle !== lifecycle) {
                reason = await Platform.modal.promptText('Optional reason for lifecycle change (' + previousLifecycle.replace('_', ' ') + ' \u2192 ' + lifecycle.replace('_', ' ') + '):', { title: 'Lifecycle change', multiline: true }) || '';
            }

            /* Store on cell for save/load */
            cell.set('elLifecycle', lifecycle);
            this.statusText = (cell.get('elName') || 'Element') + ' \u2192 ' + lifecycle.replace('_', ' ');

            /* GAP-CMP-004: Persist lifecycle transition to element custom_properties */
            let elId = cell.get('elementId');
            if (elId && parseInt(elId, 10) > 0) {
                let self = this;
                let historyEntry = {
                    from: previousLifecycle || '',
                    to: lifecycle,
                    changed_at: new Date().toISOString(),
                    reason: reason,
                };
                Platform.fetch.patch('/archimate/api/elements/' + elId, {
                    custom_properties: {
                        lifecycle_current: lifecycle,
                        _lifecycle_history_append: historyEntry,
                    },
                }, { silent: true })
                .then(function(data) {
                    if (data.error) { _toast('error', data.error); return; }
                    /* Update selectedNode if this element is currently selected */
                    if (self.selectedNode && String(self.selectedNode.elementId) === String(elId)) {
                        self.loadCustomPropsFromServer(elId);
                    }
                })
                .catch(function() { _toast('error', 'Failed to save lifecycle transition'); });
            }
        },

        /* ── Helpers ──────────────────────────────────────── */

        /* GAP-CMP-007: Submit diagram for ARB review */
        submitForReview: async function() {
            if (!this.currentSavedVpId) { _toast('error', 'Save the diagram first'); return; }
            let self = this;
            if (!(await Platform.modal.confirm('Submit this diagram to the Architecture Review Board for review?'))) return;

            Platform.fetch.post('/archimate/api/saved-viewpoints/' + self.currentSavedVpId + '/submit-review', undefined, { silent: true })
            .then(function(data) {
                if (data.error) { _toast('error', data.error); return; }
                self.viewpointReviewStatus = 'submitted';
                _toast('success', 'Diagram submitted for ARB review');
            })
            .catch(function() { _toast('error', 'Failed to submit for review'); });
        },

        /* ── CMP-024: Comments ─────────────────────────────── */
        openComments: function(elementId) {
            let self = this;
            self.commentElementId = elementId;
            self.comments = [];
            self.newCommentText = '';
            self.commentsLoading = true;
            self.commentPanelOpen = true;
            self.ctxMenuOpen = false;

            let url = '/archimate/api/elements/' + elementId + '/comments';
            if (self.currentSavedVpId) {
                url += '?viewpoint_id=' + self.currentSavedVpId;
            }
            Platform.fetch(url, { silent: true })
                .then(function(data) {
                    self.comments = data.comments || [];
                    self.commentsLoading = false;
                })
                .catch(function() {
                    _toast('error', 'Failed to load comments');
                self.commentsLoading = false;
                });
        },

        addComment: function() {
            let self = this;
            let text = (self.newCommentText || '').trim();
            if (!text || !self.commentElementId) return;

            Platform.fetch.post('/archimate/api/elements/' + self.commentElementId + '/comments', {
                comment_text: text,
                viewpoint_id: self.currentSavedVpId || null,
            }, { silent: true })
            .then(function(data) {
                if (data.id) {
                    self.comments.push(data);
                    self.newCommentText = '';
                }
            })
            .catch(function() {
                _toast('error', 'Failed to post comment');
                self.statusText = 'Failed to post comment';
            });
        },

        closeComments: function() {
            this.commentPanelOpen = false;
        },

        /* ── CMP-024: Audit trail ────────────────────────────── */
        openAuditLog: function() {
            let self = this;
            self.auditLog = [];
            self.auditLoading = true;
            self.auditPanelOpen = true;

            let url = '/archimate/api/audit-log';
            if (self.currentSavedVpId) {
                url += '?viewpoint_id=' + self.currentSavedVpId;
            }
            Platform.fetch(url, { silent: true })
                .then(function(data) {
                    self.auditLog = data.entries || [];
                    self.auditLoading = false;
                })
                .catch(function() {
                    self.auditLoading = false;
                    _toast('error', 'Failed to load audit trail');
                });
        },

        logAuditEvent: function(action, entityType, entityId, entityName, oldVal, newVal) {
            let self = this;
            let payload = {
                viewpoint_id: self.currentSavedVpId || null,
                action: action,
                entity_type: entityType || null,
                entity_id: entityId || null,
                entity_name: entityName || null,
                old_value: oldVal || null,
                new_value: newVal || null,
            };
            /* Fire-and-forget — do not block the UI. CMP-03: an audit write must
               never nag the user; the toast here contradicted the non-blocking
               intent and fired on every removal because the server FK was broken.
               Requests that reach the server are logged there; requests that fail
               in transit remain visible through the platform logger. */
            Platform.fetch.post('/archimate/api/audit-log', payload, { silent: true })
                .catch(function(err) { Platform.log.error('Failed to record composer audit event', err); });
        },

        /* ── CMP-059: Validation API ─────────────────────────────── */
        runValidation: function() {
            let self = this;
            self.validationLoading = true;
            self.validationPanelOpen = true;
            self.validationReport = { passed: [], warnings: [], errors: [] };

            /* Collect elements from graph */
            let elements = [];
            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isNote')) return;
                elements.push({
                    id: String(cell.get('elementId') || cell.id),
                    type: cell.get('elType') || '',
                    layer: cell.get('elLayer') || '',
                    name: cell.get('elName') || '',
                });
            });

            /* Collect relationships from graph */
            let relationships = [];
            self.graph.getLinks().forEach(function(link) {
                let srcCell = link.getSourceCell();
                let tgtCell = link.getTargetCell();
                if (!srcCell || !tgtCell) return;
                relationships.push({
                    source_type: srcCell.get('elType') || '',
                    target_type: tgtCell.get('elType') || '',
                    rel_type: link.get('relType') || 'association',
                    source_name: srcCell.get('elName') || '',
                    target_name: tgtCell.get('elName') || '',
                });
            });

            Platform.fetch.post('/archimate/api/composer/validate', {
                elements: elements,
                relationships: relationships,
                phase: self.validationPhase || '',
                viewpoint_type: '',
            }, { silent: true })
            .then(function(data) {
                self.validationReport = {
                    passed: data.passed || [],
                    warnings: data.warnings || [],
                    errors: data.errors || [],
                };
                self.validationLoading = false;
            })
            .catch(function(err) {
                self.validationLoading = false;
                self.validationReport = {
                    passed: [],
                    warnings: [],
                    errors: [{ check: 'fetch_error', message: 'Validation request failed: ' + ((err && err.message) || err), element_ids: [] }],
                };
                _toast('error', 'Validation request failed');
            });
        },

        highlightValidationElements: function(elementIds) {
            if (!elementIds || elementIds.length === 0) return;
            let self = this;
            self._clearValidationHighlight();
            let idSet = {};
            elementIds.forEach(function(id) { idSet[String(id)] = true; });
            self.graph.getElements().forEach(function(cell) {
                let eid = String(cell.get('elementId') || cell.id);
                if (idSet[eid]) {
                    cell.attr('body/stroke', '#ef4444');
                    cell.attr('body/strokeWidth', 3);
                    cell.set('_validationHighlighted', true);
                }
            });
        },

        _clearValidationHighlight: function() {
            this.graph.getElements().forEach(function(cell) {
                if (cell.get('_validationHighlighted')) {
                    let layerCol = layerColor((cell.get('elLayer') || '').toLowerCase());
                    cell.attr('body/stroke', layerCol);
                    cell.attr('body/strokeWidth', 1);
                    cell.set('_validationHighlighted', false);
                }
            });
        },

        /* ── CMP-061: Presentation mode ──────────────────────────── */
        enterPresentationMode: function() {
            if (this.mode === 'view') return; // only from edit mode
            let self = this;
            /* Build slides from all canvas elements, ordered top-left to bottom-right */
            let cells = this.graph.getCells().filter(function(c) {
                return c.get('type') !== 'archimate.Link' && c.get('elName');
            });
            cells.sort(function(a, b) {
                let ap = a.get('position'), bp = b.get('position');
                return (ap.y === bp.y) ? ap.x - bp.x : ap.y - bp.y;
            });
            if (cells.length === 0) {
                _toast('info', 'Add elements to the canvas before entering presentation mode');
                return;
            }
            this.presentationSlides = cells.map(function(c) {
                return { id: c.id, name: c.get('elName') || '(unnamed)', type: c.get('elType') || '' };
            });
            this.presentationIndex = 0;
            this.presentationActive = true;
            this.moreDropdownOpen = false;
            /* Focus first element */
            self._presentationFocusSlide(0);
            /* Keyboard nav handler */
            self._presentationKeyHandler = function(e) {
                if (!self.presentationActive) return;
                if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { e.preventDefault(); self.presentationNext(); }
                if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp')   { e.preventDefault(); self.presentationPrev(); }
                if (e.key === 'Escape') { self.exitPresentationMode(); }
            };
            document.addEventListener('keydown', self._presentationKeyHandler);
        },

        exitPresentationMode: function() {
            this.presentationActive = false;
            this.presentationSlides = [];
            this.presentationIndex = 0;
            if (this._presentationKeyHandler) {
                document.removeEventListener('keydown', this._presentationKeyHandler);
                this._presentationKeyHandler = null;
            }
            /* Restore full canvas view */
            this.paper.scaleContentToFit({ padding: 40 });
        },

        presentationNext: function() {
            if (!this.presentationActive || !this.presentationSlides.length) return;
            this.presentationIndex = Math.min(this.presentationIndex + 1, this.presentationSlides.length - 1);
            this._presentationFocusSlide(this.presentationIndex);
        },

        presentationPrev: function() {
            if (!this.presentationActive || !this.presentationSlides.length) return;
            this.presentationIndex = Math.max(this.presentationIndex - 1, 0);
            this._presentationFocusSlide(this.presentationIndex);
        },

        _presentationFocusSlide: function(idx) {
            let slide = this.presentationSlides[idx];
            if (!slide) return;
            let cell = this.graph.getCell(slide.id);
            if (!cell) return;
            let pos  = cell.get('position');
            let size = cell.get('size');
            /* Pan & zoom to centre the element with generous padding */
            let PADDING = 120;
            let paperRect = this.paper.el.getBoundingClientRect();
            let targetW   = size.width  + PADDING * 2;
            let targetH   = size.height + PADDING * 2;
            let scaleX = paperRect.width  / targetW;
            let scaleY = paperRect.height / targetH;
            let scale  = Math.min(scaleX, scaleY, 2); /* cap at 200% */
            let tx = paperRect.width  / 2 - (pos.x + size.width  / 2) * scale;
            let ty = paperRect.height / 2 - (pos.y + size.height / 2) * scale;
            this.paper.scale(scale, scale);
            this.paper.translate(tx, ty);
            /* Highlight the current slide element */
            let self = this;
            this.graph.getCells().forEach(function(c) {
                let cv = self.paper.findViewByModel(c);
                if (!cv) return;
                if (c.id === slide.id) {
                    cv.el.style.opacity = '1';
                    cv.el.style.filter  = 'drop-shadow(0 0 12px rgba(59,130,246,0.8))';
                } else {
                    cv.el.style.opacity = '0.3';
                    cv.el.style.filter  = '';
                }
            });
        },

        cancelAction: function() {
            if (this.retirementSimActive) { this.clearRetirementSimulation(); return; }
            if (this.presentationActive) { this.exitPresentationMode(); return; }
            if (this.canvasSearchOpen) { this.closeCanvasSearch(); return; }
            if (this.commentPanelOpen) { this.commentPanelOpen = false; return; }
            if (this.auditPanelOpen) { this.auditPanelOpen = false; return; }
            if (this.explanationPanelOpen) { this.explanationPanelOpen = false; return; }
            if (this.impactPanelOpen) { this.impactPanelOpen = false; this._clearImpactHighlight(); return; }
            if (this.deltaPickerOpen) { this.deltaPickerOpen = false; return; }
            if (this.deltaMode) { this.exitDeltaMode(); return; }
            if (this.patternListOpen) { this.closePatternModal(); return; }
            if (this.savePatternOpen) { this.closeSavePatternModal(); return; }
            if (this.extractModalOpen) { this.extractModalOpen = false; return; }
            if (this.generateModalOpen) { this.generateModalOpen = false; return; }
            if (this.validationPanelOpen) { this.validationPanelOpen = false; this._clearValidationHighlight(); return; }
            if (this.bulkDeleteConfirmOpen) { this.cancelBulkDelete(); return; }
            if (this.relCtxMenuOpen) { this.closeRelCtxMenu(); return; }
            if (this.ctxMenuOpen) { this.ctxMenuOpen = false; return; }
            if (this.canvasCtxMenuOpen) { this.canvasCtxMenuOpen = false; return; }
            if (this.searchReplaceOpen) { this.closeSearchReplace(); return; }
            if (this.quickAddOpen) { this.closeQuickAdd(); return; }
            if (this.searchOpen) { this.closeSearch(); return; }
            if (this.relPickerOpen) { this.cancelRelPicker(); return; }
            if (this.vpDropdownOpen) { this.vpDropdownOpen = false; return; }
            if (this.snapshotListOpen) { this.snapshotListOpen = false; return; }
            if (this.templateListOpen) { this.templateListOpen = false; return; }
            /* Escape in view mode clears neighbor focus */
            if (this.mode === 'view') {
                this._clearNeighborFocus();
                this.selectedNode = null;
                this.selectedEdge = null;
            }
        },

        paletteColor: function(layer) {
            let c = layerColor(layer.toLowerCase());
            return { bg: c.fill, border: c.accent || c.stroke };
        },

        /* ── Zoom preset ─────────────────────────────────────── */
        setZoom: function(n) {
            let s = n / 100;
            this.paper.scale(s, s);
            this.zoomPercent = n;
        },

        /* ── Layer filter presets ────────────────────────────── */
        applyLayerPreset: function(preset) {
            let self = this;
            let presets = {
                all:              { strategy: true,  motivation: true,  business: true,  application: true,  technology: true,  implementation: true,  composite: true,  other: true },
                application_usage:{ strategy: false, motivation: false, business: true,  application: true,  technology: false, implementation: false, composite: true,  other: true },
                technology:       { strategy: false, motivation: false, business: false, application: true,  technology: true,  implementation: false, composite: true,  other: true },
                motivation:       { strategy: true,  motivation: true,  business: false, application: false, technology: false, implementation: false, composite: true,  other: true },
                business_only:    { strategy: false, motivation: false, business: true,  application: false, technology: false, implementation: false, composite: true,  other: true },
                migration:        { strategy: false, motivation: false, business: false, application: false, technology: false, implementation: true,  composite: true,  other: true },
            };
            let vp = presets[preset];
            if (!vp) return;
            Object.keys(vp).forEach(function(k) { self.layerVisibility[k] = vp[k]; });
            /* Re-apply visibility to all elements and links */
            self.graph.getElements().forEach(function(cell) {
                let layer = (cell.get('elLayer') || '').toLowerCase();
                cell.attr('./display', (!layer || self.layerVisibility[layer]) ? '' : 'none');
            });
            self.graph.getLinks().forEach(function(link) {
                let src = link.getSourceCell();
                let tgt = link.getTargetCell();
                let srcHidden = src && !self.layerVisibility[(src.get('elLayer') || '').toLowerCase()];
                let tgtHidden = tgt && !self.layerVisibility[(tgt.get('elLayer') || '').toLowerCase()];
                link.attr('./display', (srcHidden || tgtHidden) ? 'none' : '');
            });
        },


        nextCanvasSearchMatch: function() {
            if (this.canvasSearchMatches.length === 0) return;
            this.canvasSearchIdx = (this.canvasSearchIdx + 1) % this.canvasSearchMatches.length;
            this._panToCanvasSearchMatch(this.canvasSearchIdx);
        },

        prevCanvasSearchMatch: function() {
            if (this.canvasSearchMatches.length === 0) return;
            this.canvasSearchIdx = (this.canvasSearchIdx - 1 + this.canvasSearchMatches.length) % this.canvasSearchMatches.length;
            this._panToCanvasSearchMatch(this.canvasSearchIdx);
        },

        _panToCanvasSearchMatch: function(idx) {
            let cell = this.canvasSearchMatches[idx];
            if (!cell) return;
            let bbox = cell.getBBox();
            let cx = bbox.x + bbox.width / 2;
            let cy = bbox.y + bbox.height / 2;
            let paper = this.paper;
            let pw = paper.el.offsetWidth;
            let ph = paper.el.offsetHeight;
            let s = paper.scale().sx;
            paper.setOrigin(pw / 2 - cx * s, ph / 2 - cy * s);
            /* Flash highlight */
            let view = paper.findViewByModel(cell);
            if (view) {
                view.highlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 8, attrs: { stroke: '#ec5b13', 'stroke-width': 3 } } } });
                let self = this;
                setTimeout(function() {
                    if (self.canvasSearchMatches.indexOf(cell) !== -1) {
                        try { view.highlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 8, attrs: { stroke: '#ec5b13', 'stroke-width': 3 } } } }); } catch(e) { /* swallow-ok: cosmetic flash highlight on a search match; the match is still selected and centred */ }
                    }
                }, 100);
            }
        },

        _clearCanvasSearchHighlights: function() {
            let self = this;
            if (!this.canvasSearchMatches) return;
            this.canvasSearchMatches.forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (view) {
                    try { view.unhighlight(null, { highlighter: { name: 'stroke', options: { padding: 5, rx: 8, attrs: { stroke: '#ec5b13', 'stroke-width': 3 } } } }); } catch(e) { /* swallow-ok: cosmetic un-highlight while clearing search matches */ }
                }
            });
        },

        /* ── Canvas context menu helper ──────────────────────── */
        canvasPasteAtCursor: function() {
            this.canvasCtxMenuOpen = false;
            this._pasteClipboard(this._canvasCtxPaperCoords);
        },

        _clipboardHasData: function() {
            try { return !!(this._clipboard && this._clipboard.length > 0) || !!localStorage.getItem('archimate_clipboard'); } catch(e) { return false; }
        },

        /* ── CMP-052: Free-form annotation methods ── */
        addAnnotation: function() {
            let self = this;
            if (self.mode !== 'edit') return;
            let createAnnotation = _helpers.createAnnotation;
            /* CMP-064: Calculate viewport center accounting for scroll and zoom */
            let container = self.paper.el.parentElement || self.paper.el;
            let scale = self.paper.scale ? self.paper.scale().sx : 1;
            let scrollX = container.scrollLeft || 0;
            let scrollY = container.scrollTop || 0;
            let viewW = container.clientWidth || 900;
            let viewH = container.clientHeight || 600;
            let cx = (scrollX + viewW / 2) / scale - 90;
            let cy = (scrollY + viewH / 2) / scale - 50;
            let annot = createAnnotation(cx, cy, 'Annotation\u2026', 180, 100);
            self.graph.addCell(annot);
            self.annotationCells = self.graph.getElements().filter(function(c) { return c.get('isAnnotation'); });
            self._toast('Annotation added — double-click to edit text', 'info');
        },

        saveAnnotationText: function() {
            let self = this;
            if (!self._annotEditCell) return;
            let txt = self.annotEditText.trim() || 'Annotation…';
            self._annotEditCell.attr('label/text', txt);
            self._annotEditCell.set('annotText', txt);
            self.annotEditOpen = false;
            self._annotEditCell = null;
        },

        cancelAnnotationEdit: function() {
            this.annotEditOpen = false;
            this._annotEditCell = null;
        },

        /* ── CMP-043: Custom element properties ── */
        _customPropsKey: function() {
            return 'archimate_cprops_' + (this.activeViewpoint || 'default');
        },

        loadCustomPropsFromStorage: function() {
            try {
                let raw = localStorage.getItem(this._customPropsKey());
                this.customProperties = raw ? JSON.parse(raw) : {};
            } catch(e) {
                this.customProperties = {};
            }
        },

        _saveCustomPropsToStorage: function() {
            /* Local cache only — _syncCustomPropsToServer() is the real persistence
               path and reports its own failures, so a storage failure here is harmless. */
            try {
                localStorage.setItem(this._customPropsKey(), JSON.stringify(this.customProperties));
            } catch(e) { /* swallow-ok: local cache only; _syncCustomPropsToServer is the real persistence path and reports its own failures */ }
        },

        /** CMP-043: Persist custom properties to server API for a real DB element. */
        _syncCustomPropsToServer: function(elementId) {
            let id = parseInt(elementId, 10);
            if (!id || id <= 0) return;  /* Only sync real DB elements */
            let props = this.customProperties[String(elementId)] || [];
            /* Convert [{key,value}] array to {key: value} dict for the API */
            let payload = {};
            props.forEach(function(p) { payload[p.key] = p.value; });
            Platform.fetch.put('/archimate/api/elements/' + id + '/properties', payload, { silent: true })
            .catch(function() { _toast('error', 'Failed to save custom properties to the server — they are only in this browser.'); });
        },

        /** CMP-043: Load custom properties from server API for a real DB element. */
        loadCustomPropsFromServer: function(elementId) {
            let self = this;
            let id = parseInt(elementId, 10);
            if (!id || id <= 0) return;
            Platform.fetch('/archimate/api/elements/' + id + '/properties', { silent: true })
                /* A failed load must not leave the stale localStorage copy on screen
                   as though it were what the server holds — the catch below toasts. */
                .then(function(data) {
                    if (!data) return;
                    /* Convert {key: value} dict to [{key, value}] array */
                    let arr = [];
                    Object.keys(data).forEach(function(k) {
                        arr.push({ key: k, value: String(data[k]) });
                    });
                    self.customProperties[String(elementId)] = arr;
                    self.customProperties = Object.assign({}, self.customProperties);
                    self._saveCustomPropsToStorage();
                })
                .catch(function() { _toast('error', 'Failed to load custom properties from the server; any shown here are a local cache.'); });
        },

        getCustomProps: function(elementId) {
            return this.customProperties[String(elementId)] || [];
        },

        addCustomProp: function() {
            let self = this;
            if (!self.selectedNode || !self.selectedNode.elementId) return;
            let k = (self._customPropNewKey || '').trim();
            let v = (self._customPropNewVal || '').trim();
            if (!k) return;
            let id = String(self.selectedNode.elementId);
            if (!self.customProperties[id]) {
                self.customProperties[id] = [];
            }
            /* Overwrite if key already exists */
            let idx = self.customProperties[id].findIndex(function(p) { return p.key === k; });
            if (idx !== -1) {
                self.customProperties[id][idx].value = v;
            } else {
                self.customProperties[id].push({ key: k, value: v });
            }
            /* Force Alpine reactivity */
            self.customProperties = Object.assign({}, self.customProperties);
            self._customPropNewKey = '';
            self._customPropNewVal = '';
            self._saveCustomPropsToStorage();
            self._syncCustomPropsToServer(id);
        },

        deleteCustomProp: function(elementId, key) {
            let id = String(elementId);
            if (!this.customProperties[id]) return;
            this.customProperties[id] = this.customProperties[id].filter(function(p) { return p.key !== key; });
            this.customProperties = Object.assign({}, this.customProperties);
            this._saveCustomPropsToStorage();
            this._syncCustomPropsToServer(id);
        },

        /* ── CMP-040: Drill-down navigation ── */
        linkElementToViewpoint: function() {
            let self = this;
            if (!self._currentSelectedCell || self.mode !== 'edit') return;
            self.linkViewpointTargetCell = self._currentSelectedCell;
            self.linkViewpointModalOpen = true;
            /* Fetch saved viewpoints for picker */
            Platform.fetch('/archimate/api/saved-viewpoints', { silent: true })
                .then(function(data) {
                    self.linkViewpointList = (data.viewpoints || data || []).map(function(v) {
                        return { id: v.id, name: v.name || v.viewpoint_name || 'Unnamed' };
                    });
                })
                .catch(function() { self.linkViewpointList = []; _toast('error', 'Failed to load linked viewpoints'); });
        },

        confirmLinkViewpoint: function(vpId, vpName) {
            let self = this;
            if (!self.linkViewpointTargetCell) return;
            self.linkViewpointTargetCell.set('linkedViewpointId', vpId);
            self.linkViewpointTargetCell.set('linkedViewpointName', vpName);
            /* Visual indicator: small arrow badge */
            self.linkViewpointTargetCell.attr('drillIcon/text', '\u25BC');
            self.linkViewpointTargetCell.attr('drillIcon/refX', '95%');
            self.linkViewpointTargetCell.attr('drillIcon/refY', '5%');
            self.linkViewpointTargetCell.attr('drillIcon/fontSize', 10);
            self.linkViewpointTargetCell.attr('drillIcon/fill', '#2563eb');
            self.linkViewpointModalOpen = false;
            self.linkViewpointTargetCell = null;
            self.viewpointDirty = true;
            self._toast('info', 'Element linked to viewpoint: ' + vpName);
        },

        unlinkViewpoint: function() {
            let self = this;
            if (!self._currentSelectedCell) return;
            self._currentSelectedCell.unset('linkedViewpointId');
            self._currentSelectedCell.unset('linkedViewpointName');
            self._currentSelectedCell.removeAttr('drillIcon');
            self.viewpointDirty = true;
            self._toast('info', 'Viewpoint link removed');
        },

        drillDown: function(cell) {
            let self = this;
            let vpId = cell.get('linkedViewpointId');
            if (!vpId) return false;

            /* Push current state to breadcrumb stack */
            self.breadcrumbStack.push({
                viewpointId: self.currentSavedVpId,
                viewpointName: self.activeViewpointName || 'Untitled',
                scrollX: window.scrollX,
                scrollY: window.scrollY,
            });

            /* Navigate to linked viewpoint */
            self.loadSavedViewpoint(vpId, '');
            return true;
        },

        navigateBreadcrumb: function(index) {
            let self = this;
            if (index < 0 || index >= self.breadcrumbStack.length) return;
            let target = self.breadcrumbStack[index];
            /* Trim stack to this level */
            self.breadcrumbStack = self.breadcrumbStack.slice(0, index);
            self.loadSavedViewpoint(target.viewpointId, target.viewpointName);
        },

        breadcrumbUp: function() {
            let self = this;
            if (self.breadcrumbStack.length === 0) return;
            self.navigateBreadcrumb(self.breadcrumbStack.length - 1);
        },

        /* ── CMP-047: Dependency propagation visualization ── */
        toggleDependencyPropagation: function(cell) {
            let self = this;
            if (self.depPropagationActive && self.depPropagationRoot === cell.id) {
                self.clearDependencyPropagation();
                return;
            }
            self.depPropagationActive = true;
            self.depPropagationRoot = cell.id;
            self._showDependencyChain(cell);
        },

        _showDependencyChain: function(rootCell) {
            let self = this;
            let graph = self.graph;
            let maxHops = 4;

            /* Build adjacency from links */
            let downstream = {};  /* source -> [target] */
            let upstream = {};    /* target -> [source] */
            graph.getLinks().forEach(function(link) {
                let src = link.getSourceCell();
                let tgt = link.getTargetCell();
                if (!src || !tgt) return;
                if (!downstream[src.id]) downstream[src.id] = [];
                downstream[src.id].push(tgt.id);
                if (!upstream[tgt.id]) upstream[tgt.id] = [];
                upstream[tgt.id].push(src.id);
            });

            /* BFS downstream (orange) */
            let downIds = {};
            let queue = [{ id: rootCell.id, depth: 0 }];
            while (queue.length > 0) {
                let cur = queue.shift();
                if (cur.depth > maxHops) continue;
                if (cur.id !== rootCell.id) downIds[cur.id] = cur.depth;
                (downstream[cur.id] || []).forEach(function(nid) {
                    if (!(nid in downIds) && nid !== rootCell.id) {
                        queue.push({ id: nid, depth: cur.depth + 1 });
                    }
                });
            }

            /* BFS upstream (blue) */
            let upIds = {};
            queue = [{ id: rootCell.id, depth: 0 }];
            while (queue.length > 0) {
                let cur = queue.shift();
                if (cur.depth > maxHops) continue;
                if (cur.id !== rootCell.id) upIds[cur.id] = cur.depth;
                (upstream[cur.id] || []).forEach(function(nid) {
                    if (!(nid in upIds) && nid !== rootCell.id) {
                        queue.push({ id: nid, depth: cur.depth + 1 });
                    }
                });
            }

            let relatedIds = Object.assign({}, downIds, upIds);
            relatedIds[rootCell.id] = 0;

            /* Apply visual styles */
            graph.getElements().forEach(function(el) {
                if (el.get('isLayerZone') || el.get('isAnnotation')) return;
                let view = self.paper.findViewByModel(el);
                if (!view) return;
                let vel = view.vel;

                if (el.id === rootCell.id) {
                    /* Root: bold border */
                    vel.attr({ opacity: 1 });
                    try {
                        view.highlight(null, {
                            highlighter: { name: 'stroke', options: { padding: 6, rx: 8, attrs: { stroke: '#dc2626', 'stroke-width': 3 } } }
                        });
                    } catch(e) { /* swallow-ok: cosmetic dependency-chain outline; the opacity tint and the statusText summary still convey the result */ }
                } else if (el.id in downIds) {
                    /* Downstream: orange tint */
                    vel.attr({ opacity: Math.max(0.4, 1 - downIds[el.id] * 0.15) });
                    try {
                        view.highlight(null, {
                            highlighter: { name: 'stroke', options: { padding: 4, rx: 6, attrs: { stroke: '#f97316', 'stroke-width': 2, 'stroke-dasharray': '4,2' } } }
                        });
                    } catch(e) { /* swallow-ok: cosmetic dependency-chain outline; the opacity tint and the statusText summary still convey the result */ }
                } else if (el.id in upIds) {
                    /* Upstream: blue tint */
                    vel.attr({ opacity: Math.max(0.4, 1 - upIds[el.id] * 0.15) });
                    try {
                        view.highlight(null, {
                            highlighter: { name: 'stroke', options: { padding: 4, rx: 6, attrs: { stroke: '#3b82f6', 'stroke-width': 2, 'stroke-dasharray': '4,2' } } }
                        });
                    } catch(e) { /* swallow-ok: cosmetic dependency-chain outline; the opacity tint and the statusText summary still convey the result */ }
                } else {
                    /* Unrelated: dim to 20% */
                    vel.attr({ opacity: 0.2 });
                }
            });

            /* Dim unrelated links */
            graph.getLinks().forEach(function(link) {
                let src = link.getSourceCell();
                let tgt = link.getTargetCell();
                let view = self.paper.findViewByModel(link);
                if (!view) return;
                if (src && tgt && (src.id in relatedIds) && (tgt.id in relatedIds)) {
                    view.vel.attr({ opacity: 1 });
                } else {
                    view.vel.attr({ opacity: 0.1 });
                }
            });

            let total = Object.keys(downIds).length + Object.keys(upIds).length;
            self.statusText = 'Dependency chain: ' + Object.keys(upIds).length + ' upstream, ' + Object.keys(downIds).length + ' downstream';
        },

        clearDependencyPropagation: function() {
            let self = this;
            self.depPropagationActive = false;
            self.depPropagationRoot = null;

            /* Reset all element visuals */
            self.graph.getElements().forEach(function(el) {
                let view = self.paper.findViewByModel(el);
                if (!view) return;
                view.vel.attr({ opacity: 1 });
                try { view.unhighlight(null, { highlighter: { name: 'stroke' } }); } catch(e) { /* swallow-ok: cosmetic un-highlight while clearing the dependency overlay */ }
            });

            /* Reset all link visuals */
            self.graph.getLinks().forEach(function(link) {
                let view = self.paper.findViewByModel(link);
                if (!view) return;
                view.vel.attr({ opacity: 1 });
            });

            self.statusText = 'Ready';
        },

        /* ── CMP-053: Landscape map view ── */
        openLandscapeView: function() {
            this.landscapeViewOpen = true;
            this.loadLandscapeData();
        },

        closeLandscapeView: function() {
            this.landscapeViewOpen = false;
            this.landscapeData = null;
        },

        loadLandscapeData: function() {
            let self = this;
            self.landscapeLoading = true;
            Platform.fetch('/archimate/api/landscape?row_type=' + encodeURIComponent(self.landscapeRowType) + '&col_type=' + encodeURIComponent(self.landscapeColType), { silent: true })
            .then(function(data) {
                self.landscapeData = data;
                self.landscapeLoading = false;
            })
            .catch(function(e) {
                self.landscapeLoading = false;
                self.statusText = 'Landscape load failed: ' + ((e && e.message) || e);
                _toast('error', self.statusText);
            });
        },

        landscapeCell: function(rowId, colId) {
            if (!this.landscapeData || !this.landscapeData.cells) return null;
            return this.landscapeData.cells[rowId + '_' + colId] || null;
        },

        landscapeHeatColor: function(count) {
            if (!count || count === 0) return '';
            if (count === 1) return 'bg-success/10 dark:bg-success/20';
            if (count === 2) return 'bg-primary/10 dark:bg-primary/20';
            if (count <= 4) return 'bg-warning/10 dark:bg-warning/20';
            return 'bg-destructive/10 dark:bg-destructive/20';
        },

        /* ── CMP-041: Matrix cross-reference view ──────────────── */

        openMatrixView: function() {
            this.matrixViewOpen = true;
            this.fetchMatrix();
        },

        closeMatrixView: function() {
            this.matrixViewOpen = false;
            this.matrixRows = [];
            this.matrixCols = [];
            this._matrixIntersections = {};
        },

        fetchMatrix: function() {
            let self = this;
            self.matrixLoading = true;
            self.matrixRows = [];
            self.matrixCols = [];
            self._matrixIntersections = {};

            let url = '/archimate/api/matrix?row_type=' + encodeURIComponent(self.matrixRowType) +
                      '&col_type=' + encodeURIComponent(self.matrixColType);

            Platform.fetch(url, { silent: true })
                .then(function(data) {
                    self.matrixRows = data.rows || [];
                    self.matrixCols = data.columns || [];
                    self._matrixIntersections = data.intersections || {};
                    self.matrixLoading = false;
                })
                .catch(function(e) {
                    self.matrixLoading = false;
                    self.statusText = 'Matrix load failed: ' + ((e && e.message) || e);
                    _toast('error', self.statusText);
                });
        },

        matrixCellType: function(rowId, colId) {
            return this._matrixIntersections[rowId + '_' + colId] || null;
        },

        matrixCellAbbrev: function(rowId, colId) {
            let relType = this.matrixCellType(rowId, colId);
            if (!relType) return '\u00B7';
            let abbrevMap = {
                'Composition': 'Co',
                'Aggregation': 'Ag',
                'Assignment': 'As',
                'Realization': 'Re',
                'Serving': 'Sv',
                'Access': 'Ac',
                'Influence': 'In',
                'Association': 'Ao',
                'Triggering': 'Tr',
                'Flow': 'Fl',
                'Specialization': 'Sp',
            };
            return abbrevMap[relType] || relType.substring(0, 2);
        },

        matrixCellClick: function(rowId, colId) {
            let relType = this.matrixCellType(rowId, colId);
            if (relType) {
                this.statusText = 'Relationship: ' + relType + ' (row ' + rowId + ' \u2192 col ' + colId + ')';
            } else {
                this.statusText = 'No relationship between row ' + rowId + ' and col ' + colId;
            }
        },

        exportMatrixCsv: function() {
            let self = this;
            if (!self.matrixRows.length || !self.matrixCols.length) return;

            let lines = [];
            let header = [self.matrixRowType + ' \\ ' + self.matrixColType];
            self.matrixCols.forEach(function(col) {
                header.push('"' + (col.name || '').replace(/"/g, '""') + '"');
            });
            lines.push(header.join(','));

            self.matrixRows.forEach(function(row) {
                let cells = ['"' + (row.name || '').replace(/"/g, '""') + '"'];
                self.matrixCols.forEach(function(col) {
                    let rel = self.matrixCellType(row.id, col.id);
                    cells.push(rel ? '"' + rel + '"' : '');
                });
                lines.push(cells.join(','));
            });

            let csv = lines.join('\n');
            let blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            let url = URL.createObjectURL(blob);
            let a = document.createElement('a');
            a.href = url;
            a.download = 'archimate_matrix_' + self.matrixRowType + '_' + self.matrixColType + '.csv';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            self.statusText = 'Matrix exported as CSV';
        },

        /* ── CMP2-007: Relationship matrix view ─────────────────── */

        toggleRelMatrix: function() {
            this.relMatrixOpen = !this.relMatrixOpen;
            if (this.relMatrixOpen) {
                this.generateRelMatrix();
                this.statusText = 'Relationship matrix view';
            } else {
                this._clearRelMatrixHighlight();
                this.relMatrixData = null;
                this.statusText = 'Diagram view';
            }
        },

        generateRelMatrix: function() {
            let self = this;
            let elements = self.graph.getElements().filter(function(c) {
                return !c.get('isNote') && !c.get('isAnnotation') && !c.get('isLayerBand') && !c.get('isLayerZone');
            });
            let links = self.graph.getLinks();

            if (elements.length === 0) {
                self.relMatrixData = { layers: [], elements: [], cells: {}, gaps: [], linkMap: {} };
                return;
            }

            /* Build element list with layers, sorted by layer order then name */
            let LAYER_ORDER = { 'strategy': 0, 'motivation': 1, 'business': 2, 'application': 3, 'technology': 4, 'physical': 5, 'implementation': 6, 'other': 7 };
            let elList = elements.map(function(cell) {
                let layer = (cell.get('elLayer') || guessLayer(cell.get('elType') || '') || 'other').toLowerCase();
                return {
                    id: cell.id,
                    name: cell.get('elName') || '(unnamed)',
                    type: cell.get('elType') || '',
                    layer: layer,
                    layerOrder: LAYER_ORDER[layer] !== undefined ? LAYER_ORDER[layer] : 7,
                };
            });
            elList.sort(function(a, b) {
                if (a.layerOrder !== b.layerOrder) return a.layerOrder - b.layerOrder;
                return a.name.localeCompare(b.name);
            });

            /* Collect unique layers in order */
            let seenLayers = {};
            let layers = [];
            elList.forEach(function(el) {
                if (!seenLayers[el.layer]) {
                    seenLayers[el.layer] = true;
                    layers.push(el.layer);
                }
            });

            /* Build relationship abbreviation map */
            const REL_ABBREV = {
                'Serving': 'S', 'Composition': 'C', 'Access': 'A', 'Realization': 'R',
                'Triggering': 'T', 'Flow': 'F', 'Aggregation': 'Ag', 'Assignment': 'As',
                'Influence': 'In', 'Association': 'Ao', 'Specialization': 'Sp',
            };

            /* Build cells map: key = srcId_tgtId → { relType, abbrev, linkId } */
            let cells = {};
            const linkMap = {};
            links.forEach(function(link) {
                let src = link.getSourceCell();
                let tgt = link.getTargetCell();
                if (!src || !tgt) return;
                let relType = link.get('relType') || 'association';
                let displayType = relType.charAt(0).toUpperCase() + relType.slice(1).toLowerCase();
                /* Normalize to title case for lookup */
                const abbrev = REL_ABBREV[displayType] || relType.substring(0, 2).toUpperCase();
                let key = src.id + '_' + tgt.id;
                cells[key] = { relType: displayType, abbrev: abbrev, linkId: link.id };
                linkMap[key] = link.id;
            });

            /* Gap detection: identify expected cross-layer relationships that are missing.
               ArchiMate expects Serving/Realization between adjacent layers (e.g., App→Business, Tech→App). */
            const EXPECTED_CROSS = [
                { srcLayer: 'application', tgtLayer: 'business', expected: ['Serving', 'Realization'] },
                { srcLayer: 'technology', tgtLayer: 'application', expected: ['Serving', 'Realization'] },
                { srcLayer: 'business', tgtLayer: 'motivation', expected: ['Realization'] },
                { srcLayer: 'physical', tgtLayer: 'technology', expected: ['Serving', 'Realization'] },
            ];

            const gaps = [];
            const elById = {};
            elList.forEach(function(el) { elById[el.id] = el; });

            EXPECTED_CROSS.forEach(function(rule) {
                const srcEls = elList.filter(function(el) { return el.layer === rule.srcLayer; });
                const tgtEls = elList.filter(function(el) { return el.layer === rule.tgtLayer; });
                if (srcEls.length === 0 || tgtEls.length === 0) return;

                srcEls.forEach(function(srcEl) {
                    /* Check if this source has ANY relationship to ANY target in the expected layer */
                    let hasRelToLayer = false;
                    tgtEls.forEach(function(tgtEl) {
                        if (cells[srcEl.id + '_' + tgtEl.id] || cells[tgtEl.id + '_' + srcEl.id]) {
                            hasRelToLayer = true;
                        }
                    });
                    if (!hasRelToLayer) {
                        tgtEls.forEach(function(tgtEl) {
                            gaps.push({ srcId: srcEl.id, tgtId: tgtEl.id, expected: rule.expected.join('/') });
                        });
                    }
                });
            });

            self.relMatrixData = {
                layers: layers,
                elements: elList,
                cells: cells,
                gaps: gaps,
                linkMap: linkMap,
            };
        },

        relMatrixCellInfo: function(srcId, tgtId) {
            if (!this.relMatrixData) return null;
            return this.relMatrixData.cells[srcId + '_' + tgtId] || null;
        },

        relMatrixIsGap: function(srcId, tgtId) {
            if (!this.relMatrixData || !this.relMatrixData.gaps) return false;
            return this.relMatrixData.gaps.some(function(g) { return g.srcId === srcId && g.tgtId === tgtId; });
        },

        relMatrixCellClick: function(srcId, tgtId) {
            let self = this;
            self._clearRelMatrixHighlight();

            let info = self.relMatrixCellInfo(srcId, tgtId);
            if (info && info.linkId) {
                let link = self.graph.getCell(info.linkId);
                if (link) {
                    self._relMatrixHighlightedLink = link;
                    self._highlightLink(link);

                    /* Scroll the link into view on the canvas */
                    let view = self.paper.findViewByModel(link);
                    if (view && view.el) {
                        let bbox = view.el.getBBox();
                        if (bbox) {
                            let cx = bbox.x + bbox.width / 2;
                            let cy = bbox.y + bbox.height / 2;
                            self.paper.translate(
                                -cx * self.paper.scale().sx + self.paper.options.width / 2,
                                -cy * self.paper.scale().sy + self.paper.options.height / 2
                            );
                        }
                    }

                    self.statusText = info.relType + ': ' + (self.graph.getCell(srcId) ? self.graph.getCell(srcId).get('elName') : '?') + ' \u2192 ' + (self.graph.getCell(tgtId) ? self.graph.getCell(tgtId).get('elName') : '?');
                    _toast('info', 'Highlighted: ' + info.relType);
                }
            } else if (srcId === tgtId) {
                self.statusText = 'Self-reference — not applicable';
            } else {
                let gap = self.relMatrixIsGap(srcId, tgtId);
                if (gap) {
                    self.statusText = 'Potential gap — expected cross-layer relationship';
                    _toast('warning', 'Potential gap detected');
                } else {
                    self.statusText = 'No relationship between these elements';
                }
            }
        },

        _clearRelMatrixHighlight: function() {
            if (this._relMatrixHighlightedLink) {
                this._unhighlightLink(this._relMatrixHighlightedLink);
                this._relMatrixHighlightedLink = null;
            }
        },

        relMatrixLayerColor: function(layer) {
            const colors = {
                'strategy': 'bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-300',
                'motivation': 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
                'business': 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
                'application': 'bg-primary/10 text-primary/90 dark:bg-primary/20 dark:text-primary/80',
                'technology': 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
                'physical': 'bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300',
                'implementation': 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
            };
            return colors[layer] || 'bg-muted text-muted-foreground';
        },

        relMatrixElementsForLayer: function(layer) {
            if (!this.relMatrixData) return [];
            return this.relMatrixData.elements.filter(function(el) { return el.layer === layer; });
        },

        exportRelMatrixCsv: function() {
            let self = this;
            if (!self.relMatrixData || !self.relMatrixData.elements.length) return;

            let els = self.relMatrixData.elements;
            let lines = [];
            let header = ['Source \\ Target'];
            els.forEach(function(el) {
                header.push('"' + (el.name || '').replace(/"/g, '""') + ' [' + el.layer + ']"');
            });
            lines.push(header.join(','));

            els.forEach(function(srcEl) {
                let row = ['"' + (srcEl.name || '').replace(/"/g, '""') + ' [' + srcEl.layer + ']"'];
                els.forEach(function(tgtEl) {
                    let info = self.relMatrixData.cells[srcEl.id + '_' + tgtEl.id];
                    row.push(info ? '"' + info.relType + '"' : '');
                });
                lines.push(row.join(','));
            });

            let csv = lines.join('\n');
            let blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            let url = URL.createObjectURL(blob);
            let a = document.createElement('a');
            a.href = url;
            a.download = 'relationship_matrix.csv';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
            self.statusText = 'Relationship matrix exported as CSV';
            _toast('success', 'Relationship matrix exported');
        },

        /* ── CMP-049: Property-based filtering ── */
        toggleFilterBar: function() {
            this.filterBarVisible = !this.filterBarVisible;
            if (!this.filterBarVisible) this.clearFilter();
        },

        updateCursorPos: function(e) {
            let canvas = document.getElementById('composer-canvas');
            if (!canvas) return;
            let rect = canvas.getBoundingClientRect();
            this.canvasCursorX = Math.round(e.clientX - rect.left);
            this.canvasCursorY = Math.round(e.clientY - rect.top);
        },

        applyFilter: function() {
            let self = this;
            let expr = (self.activeFilter || '').trim().toLowerCase();
            if (!expr) { self.clearFilter(); return; }

            /* Parse filter: "key:value" format */
            let parts = expr.split(':');
            let filterKey = parts[0].trim();
            let filterVal = parts.length > 1 ? parts.slice(1).join(':').trim() : '';

            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                let view = self.paper.findViewByModel(cell);
                if (!view) return;

                let match = false;
                if (filterKey === 'layer') {
                    match = (cell.get('elLayer') || '').toLowerCase().indexOf(filterVal) >= 0;
                } else if (filterKey === 'type') {
                    match = (cell.get('elType') || '').toLowerCase().indexOf(filterVal) >= 0;
                } else if (filterKey === 'name') {
                    match = (cell.get('elName') || '').toLowerCase().indexOf(filterVal) >= 0;
                } else {
                    /* Default: search name */
                    match = (cell.get('elName') || '').toLowerCase().indexOf(expr) >= 0;
                }

                view.vel.attr({ opacity: match ? 1 : 0.15 });
            });

            /* Also dim non-matching links */
            self.graph.getLinks().forEach(function(link) {
                let view = self.paper.findViewByModel(link);
                if (!view) return;
                let src = link.getSourceCell();
                let tgt = link.getTargetCell();
                let srcVisible = src ? parseFloat(self.paper.findViewByModel(src).vel.attr('opacity') || '1') > 0.5 : false;
                let tgtVisible = tgt ? parseFloat(self.paper.findViewByModel(tgt).vel.attr('opacity') || '1') > 0.5 : false;
                view.vel.attr({ opacity: (srcVisible && tgtVisible) ? 1 : 0.1 });
            });
        },

        clearFilter: function() {
            let self = this;
            self.activeFilter = '';
            self.graph.getElements().forEach(function(cell) {
                let view = self.paper.findViewByModel(cell);
                if (view) view.vel.attr({ opacity: 1 });
            });
            self.graph.getLinks().forEach(function(link) {
                let view = self.paper.findViewByModel(link);
                if (view) view.vel.attr({ opacity: 1 });
            });
        },

        /* ── CMP-056: Diagram metadata ── */
        toggleMetadataPanel: function() {
            this.metadataPanelOpen = !this.metadataPanelOpen;
        },

        _getMetadataForSave: function() {
            return {
                description: this.diagramDescription,
                version: this.diagramVersion,
                audience: this.diagramAudience,
                lastModified: new Date().toISOString(),
            };
        },

        _loadMetadataFromSave: function(meta) {
            if (!meta) return;
            this.diagramDescription = meta.description || '';
            this.diagramVersion = meta.version || '1.0';
            this.diagramAudience = meta.audience || 'Technical';
            this.diagramLastModified = meta.lastModified || '';
        },

        /* ── CMP-020: Enterprise Intelligence toggle ─────────── */
        toggleIntelligence: function() {
            let self = this;

            /* If already enabled, disable and clear data */
            if (self.intelligenceEnabled) {
                self.intelligenceEnabled = false;
                self.intelligenceData = {};
                self.statusText = 'Intelligence disabled';
                return;
            }

            /* Collect element IDs from canvas */
            let elementIds = [];
            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isNote')) return;
                let eid = cell.get('elementId');
                if (eid) elementIds.push(eid);
            });

            if (elementIds.length === 0) {
                _toast('warning', 'Add elements to the canvas to use Enterprise Intelligence');
                return;
            }

            self.intelligenceLoading = true;
            self.statusText = 'Loading intelligence data…';

            let url = '/archimate/api/composer/intelligence?element_ids=' + elementIds.join(',');
            Platform.fetch(url, { silent: true })
            .then(function(data) {
                self.intelligenceData = data.enrichment || data || {};
                self.intelligenceEnabled = true;
                self.intelligenceLoading = false;
                let count = Object.keys(self.intelligenceData).length;
                self.statusText = 'Intelligence active — ' + count + ' element' + (count !== 1 ? 's' : '') + ' enriched';
                _toast('success', 'Enterprise Intelligence enabled');
            })
            .catch(function(err) {
                self.intelligenceLoading = false;
                self.statusText = 'Intelligence failed';
                _toast('error', 'Enterprise Intelligence failed: ' + ((err && err.message) || 'Unknown error'));
            });
        },

        /* ── Layer zones toggle ─────────────────────────────── */
        toggleLayerZones: function() {
            this.layerZonesActive = !this.layerZonesActive;
            this.statusText = this.layerZonesActive ? 'Layer zones active' : 'Layer zones off';
            if (this.layerZonesActive) {
                applyLayerBanding(this.graph, this.paper);
            } else {
                /* Remove banding rects from the graph */
                this.graph.getCells().forEach(function(cell) {
                    if (cell.get('isLayerBand')) cell.remove();
                });
            }
            _toast('info', this.layerZonesActive ? 'Layer banding enabled' : 'Layer banding disabled');
        },

        /* ── Lifecycle visualization toggle ─────────────────── */
        toggleLifecycleViz: function() {
            this.lifecycleVizEnabled = !this.lifecycleVizEnabled;
            this.statusText = this.lifecycleVizEnabled ? 'Lifecycle view enabled' : 'Lifecycle view disabled';
            _toast('info', this.lifecycleVizEnabled ? 'Lifecycle visualization enabled' : 'Lifecycle visualization disabled');
        },

        /* ── CMP2-001: Current/Target/Gap state toggle ─────────
         *
         * Each element carries an 'elState' property:
         *   'current'  — exists in current state only (to be retired in target)
         *   'target'   — exists in target state only (new in target)
         *   'both'     — exists in both current and target (may be modified)
         *
         * stateViewMode controls the canvas display:
         *   'all'     — show all elements with state colour badges
         *   'current' — highlight current/both, dim target-only to 15%
         *   'target'  — highlight target/both, dim current-only to 15%
         *   'gap'     — highlight differences: green border (target-only),
         *              red dashed border (current-only), amber (both/modified)
         */
        setStateViewMode: function(mode) {
            this.stateViewMode = mode;
            this.applyStateOverlay();
            let labels = { all: 'All States', current: 'Current State', target: 'Target State', gap: 'Gap Analysis' };
            this.statusText = labels[mode] || 'State view: ' + mode;
            _toast('info', labels[mode] + ' view active');
        },

        setElementStateFromCtx: function(state) {
            this.ctxMenuOpen = false;
            let cell = this.ctxMenuCell;
            if (!cell) return;

            cell.set('elState', state);
            this.viewpointDirty = true;
            this.applyStateOverlay();

            let labels = { current: 'Current only', target: 'Target only', both: 'Both (modified)' };
            this.statusText = (cell.get('elName') || 'Element') + ' → ' + (labels[state] || state);
        },

        applyStateOverlay: function() {
            let self = this;
            let mode = self.stateViewMode;
            let svgRoot = self.paper && self.paper.el ? self.paper.el.querySelector('svg') : null;
            if (!svgRoot) return;

            self.graph.getElements().forEach(function(cell) {
                if (cell.get('isLayerZone') || cell.get('isAnnotation')) return;
                let cellView = self.paper.findViewByModel(cell);
                if (!cellView || !cellView.el) return;

                let elState = cell.get('elState') || 'both';
                let el = cellView.el;

                /* Remove all state CSS classes first */
                el.classList.remove(
                    'state-current-only', 'state-target-only', 'state-both',
                    'state-dimmed', 'state-gap-current', 'state-gap-target', 'state-gap-both'
                );

                if (mode === 'all') {
                    /* Show state badge classes for visual indicator */
                    if (elState === 'current') el.classList.add('state-current-only');
                    else if (elState === 'target') el.classList.add('state-target-only');
                    else el.classList.add('state-both');
                } else if (mode === 'current') {
                    /* Current view: show current+both, dim target-only */
                    if (elState === 'target') {
                        el.classList.add('state-dimmed');
                    } else if (elState === 'current') {
                        el.classList.add('state-current-only');
                    } else {
                        el.classList.add('state-both');
                    }
                } else if (mode === 'target') {
                    /* Target view: show target+both, dim current-only */
                    if (elState === 'current') {
                        el.classList.add('state-dimmed');
                    } else if (elState === 'target') {
                        el.classList.add('state-target-only');
                    } else {
                        el.classList.add('state-both');
                    }
                } else if (mode === 'gap') {
                    /* Gap analysis: colour-coded borders */
                    if (elState === 'current') el.classList.add('state-gap-current');
                    else if (elState === 'target') el.classList.add('state-gap-target');
                    else el.classList.add('state-gap-both');
                }
            });

            /* Also handle relationship (link) dimming in filtered views */
            self.graph.getLinks().forEach(function(link) {
                let linkView = self.paper.findViewByModel(link);
                if (!linkView || !linkView.el) return;
                linkView.el.classList.remove('state-dimmed');

                if (mode === 'current' || mode === 'target') {
                    let src = link.getSourceElement();
                    let tgt = link.getTargetElement();
                    let srcState = src ? (src.get('elState') || 'both') : 'both';
                    let tgtState = tgt ? (tgt.get('elState') || 'both') : 'both';

                    if (mode === 'current' && (srcState === 'target' || tgtState === 'target')) {
                        linkView.el.classList.add('state-dimmed');
                    }
                    if (mode === 'target' && (srcState === 'current' || tgtState === 'current')) {
                        linkView.el.classList.add('state-dimmed');
                    }
                }
            });
        },

        clearStateOverlay: function() {
            this.stateViewMode = 'all';
            this.applyStateOverlay();
        },

        /* ── Heatmap overlay toggle ──────────────────────────── */
        toggleHeatmap: function(metric) {
            let self = this;
            if (self.heatmapEnabled && self.heatmapMetric === metric) {
                self.heatmapEnabled = false;
                self.heatmapMetric = '';
                self.statusText = 'Heatmap cleared';
                self.graph.getElements().forEach(function(cell) {
                    let orig = cell.get('_origFill');
                    if (orig !== undefined) cell.attr('body/fill', orig);
                });
                return;
            }
            self.heatmapEnabled = true;
            self.heatmapMetric = metric;
            self.heatmapLoading = true;
            self.statusText = metric + ' heatmap loading…';

            /* Fetch enriched data from intelligence endpoint, then colour-code */
            let ids = [];
            self.graph.getElements().forEach(function(c) { let e = c.get('elementId'); if (e) ids.push(e); });
            if (!ids.length) { self.heatmapLoading = false; _toast('warning', 'No elements on canvas'); return; }

            Platform.fetch('/archimate/api/composer/intelligence?element_ids=' + ids.join(','), { silent: true })
            .then(function(data) {
                let enrichment = data.enrichment || {};
                let HEAT = { maturity: ['#ef4444','#f97316','#eab308','#84cc16','#22c55e'],
                             risk:     ['#22c55e','#84cc16','#eab308','#f97316','#ef4444'],
                             adoption: ['#ef4444','#f97316','#eab308','#84cc16','#22c55e'] };
                let colours = HEAT[metric] || HEAT.maturity;
                self.graph.getElements().forEach(function(cell) {
                    let eid = cell.get('elementId');
                    if (!eid) return;
                    let info = enrichment[eid] || {};
                    let score = metric === 'maturity' ? (info.maturity_score || 0)
                              : metric === 'risk'     ? (info.risk_score || 0)
                              : (info.adoption_score || 0);
                    let idx = Math.min(4, Math.floor(score / 20));
                    cell.set('_origFill', cell.attr('body/fill'));
                    cell.attr('body/fill', colours[idx]);
                });
                self.heatmapLoading = false;
                self.statusText = metric.charAt(0).toUpperCase() + metric.slice(1) + ' heatmap active';
                _toast('success', metric.charAt(0).toUpperCase() + metric.slice(1) + ' heatmap applied');
            })
            .catch(function(e) {
                self.heatmapLoading = false;
                self.heatmapEnabled = false;
                _toast('error', 'Heatmap failed: ' + ((e && e.message) || e));
            });
        },

        /* ── Derived relationships toggle ───────────────────── */
        toggleDerived: function() {
            this.derivedEnabled = !this.derivedEnabled;
            this.statusText = this.derivedEnabled ? 'Derived relationships shown' : 'Derived relationships hidden';
            _toast('info', this.derivedEnabled ? 'Derived relationships visible' : 'Derived relationships hidden');
        },

        /* ── Delta compare (opens snapshot panel) ───────────── */
        openDeltaCompare: function() {
            this.deltaMode = true;
            this.snapshotListOpen = true;
            this.statusText = 'Delta compare — select a snapshot baseline';
            _toast('info', 'Select a snapshot to compare against');
        },

        /* ── AI pattern modal (delegates to generate modal) ─── */
        openPatternModal: function() {
            this.generateModalOpen = true;
            this.statusText = 'Pattern library — use Generate to apply patterns';
        },

        /* ── AI explain diagram ─────────────────────────────── */
        explainDiagram: function() {
            let self = this;
            if (self.explanationLoading) return;

            let elementIds = [];
            self.graph.getElements().forEach(function(cell) {
                if (!cell.get('isNote')) {
                    let eid = cell.get('elementId');
                    if (eid) elementIds.push(eid);
                }
            });

            if (elementIds.length === 0) {
                _toast('warning', 'Add elements to the canvas before explaining the diagram');
                return;
            }

            self.explanationLoading = true;
            self.statusText = 'Generating explanation…';

            Platform.fetch.post('/archimate/api/composer/explain', { element_ids: elementIds }, { silent: true })
            .then(function(data) {
                self.explanationLoading = false;
                let text = data.explanation || data.message || 'No explanation returned';
                self.statusText = 'Explanation ready';
                self.validationReport = { passed: [text], warnings: [], errors: [] };
                self.validationPanelOpen = true;
                _toast('success', 'Diagram explained');
            })
            .catch(function(err) {
                self.explanationLoading = false;
                self.statusText = 'Explain failed';
                _toast('error', 'Diagram explanation failed: ' + ((err && err.message) || 'Unknown error'));
            });
        },

    };

    /* Merge all module methods into the base data object */
    return Object.assign(_base,
        ComposerAI.getMethods(_helpers),
        ComposerPersistence.getMethods(_helpers),
        ComposerGraph.getMethods(_helpers),
        ComposerSearch.getMethods(_helpers)
    );

}
