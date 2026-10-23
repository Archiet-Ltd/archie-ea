/* Extracted from _traceability_sankey.html (ARCH-064: keep large JS out of the
   server-rendered payload; cacheable across the 4 pages that embed the sankey).
   Pure JS, zero Jinja — defines the global traceabilitySankey() Alpine factory. */
// Loads a script once and resolves when it has run. Used for the d3 bundles on
// pages that only draw the diagram when the person opens its tab.
function loadSankeyScript(src) {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-sankey-dep="${src}"]`);
    if (existing) {
      if (existing.dataset.ready === '1') { resolve(); return; }
      existing.addEventListener('load', () => resolve());
      existing.addEventListener('error', () => reject(new Error(`Could not load ${src}`)));
      return;
    }
    const el = document.createElement('script');
    el.src = src;
    el.dataset.sankeyDep = src;
    el.addEventListener('load', () => { el.dataset.ready = '1'; resolve(); });
    el.addEventListener('error', () => reject(new Error(`Could not load ${src}`)));
    document.head.appendChild(el);
  });
}

function traceabilitySankey(solutionId, apiUrl, lazy) {
  return {
    solutionId,
    lazy: !!lazy,
    apiUrl: apiUrl || '',
    loading: false,
    loaded: false,
    error: null,
    nodes: [],
    links: [],
    hasCode: false,
    synthesized: false,
    selectedNode: null,
    colorMode: 'layer',
    columnLabels: [
      {name:'Motivation'},{name:'Strategy'},{name:'Business'},
      {name:'Application'},{name:'Technology'},{name:'Implementation'}
    ],

    colorModes: [
      {id:'layer',  label:'Layer',  icon:'<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7h18M3 12h18M3 17h18"/></svg>'},
      {id:'health', label:'Health', icon:'<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M4.318 6.318a4.5 4.5 0 016.364 0L12 7.636l1.318-1.318a4.5 4.5 0 116.364 6.364L12 20.364l-7.682-7.682a4.5 4.5 0 010-6.364z"/></svg>'},
      {id:'status', label:'Status', icon:'<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'},
      {id:'origin', label:'Origin', icon:'<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>'},
      {id:'risk',   label:'Risk',   icon:'<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>'},
    ],

    // Every colour is a design token name (a `--<token>` custom property in
    // shadcn_tokens.css). Legend swatches take the matching bg-* class; the SVG
    // resolves the same token at draw time, so light and dark both follow it.
    swatchClass: {
      'layer-motivation': 'bg-layer-motivation', 'layer-strategy': 'bg-layer-strategy',
      'layer-business': 'bg-layer-business', 'layer-application': 'bg-layer-application',
      'layer-technology': 'bg-layer-technology', 'layer-implementation': 'bg-layer-implementation',
      'layer-risk': 'bg-layer-risk', 'primary': 'bg-primary', 'success': 'bg-success',
      'warning': 'bg-warning', 'destructive': 'bg-destructive', 'info': 'bg-info',
      'muted-foreground': 'bg-muted-foreground',
    },

    legends: {
      layer:  [{label:'Motivation',token:'layer-motivation'},{label:'Strategy',token:'layer-strategy'},{label:'Business',token:'layer-business'},{label:'Application',token:'layer-application'},{label:'Technology',token:'layer-technology'},{label:'Implementation',token:'layer-implementation'},{label:'Code',token:'primary'}],
      health: [{label:'Complete (4/4)',token:'success'},{label:'Partial (2-3/4)',token:'warning'},{label:'Minimal (1/4)',token:'layer-implementation'},{label:'Empty (0/4)',token:'destructive'}],
      status: [{label:'Implemented',token:'success'},{label:'Approved',token:'info'},{label:'Proposed',token:'layer-motivation'},{label:'Deprecated',token:'destructive'},{label:'Unknown',token:'muted-foreground'}],
      origin: [{label:'Human (primary)',token:'info'},{label:'Human (supporting)',token:'layer-implementation'},{label:'AI derived',token:'layer-motivation'},{label:'Impacted',token:'warning'}],
      risk:   [{label:'Critical path',token:'destructive'},{label:'Critical dep.',token:'layer-implementation'},{label:'High dep.',token:'warning'},{label:'Medium dep.',token:'success'},{label:'Low / none',token:'muted-foreground'}],
    },

    get activeLegend() {
      return (this.legends[this.colorMode] || []).map(i => ({...i, swatch: this.swatchClass[i.token]}));
    },

    setColorMode(mode) { this.colorMode = mode; this.render(); },

    // Resolve a token name to a colour string the SVG can use.
    tokenColor(token) {
      const resolve = window.ArchieColorTokens && window.ArchieColorTokens.cssHSL;
      return (resolve && resolve('--' + token)) || 'currentColor';
    },

    nodeToken(node) {
      switch (this.colorMode) {
        case 'health': return this.healthToken(node);
        case 'status': return this.statusToken(node);
        case 'origin': return this.originToken(node);
        case 'risk':   return this.riskToken(node);
        default:       return this.layerToken(node.layerName ?? node.layer);
      }
    },

    nodeColor(node) { return this.tokenColor(this.nodeToken(node)); },

    nodeSwatch(node) { return this.swatchClass[this.nodeToken(node)]; },

    healthSwatch(node) { return this.swatchClass[this.healthToken(node)]; },

    layerToken(layer) {
      const map = {motivation:'layer-motivation',strategy:'layer-strategy',business:'layer-business',application:'layer-application',technology:'layer-technology',implementation:'layer-implementation',code:'primary'};
      return map[layer] || 'muted-foreground';
    },

    layerColor(layer) { return this.tokenColor(this.layerToken(layer)); },

    healthToken(node) {
      const h = node.health ?? 0;
      if (h >= 4) return 'success';
      if (h >= 2) return 'warning';
      if (h >= 1) return 'layer-implementation';
      return 'destructive';
    },

    statusToken(node) {
      const map = {implemented:'success',approved:'info',proposed:'layer-motivation',deprecated:'destructive'};
      return map[(node.status||'').toLowerCase()] || 'muted-foreground';
    },

    originToken(node) {
      const map = {primary:'info',supporting:'layer-implementation',ai_derived:'layer-motivation',impacted:'warning'};
      return map[node.origin||'primary'] || 'info';
    },

    riskToken(node) {
      if (node.critical_path) return 'destructive';
      const map = {critical:'layer-implementation',high:'warning',medium:'success',low:'muted-foreground'};
      return map[(node.dependency_level||'').toLowerCase()] || 'muted-foreground';
    },

    get linkedNodes() {
      if (!this.selectedNode) return [];
      const idx = this.nodes.indexOf(this.selectedNode);
      const linked = new Set();
      this.links.forEach(l => {
        if (l.source===idx||l.source?.index===idx) linked.add(l.target?.index??l.target);
        if (l.target===idx||l.target?.index===idx) linked.add(l.source?.index??l.source);
      });
      return [...linked].map(i => this.nodes[i]).filter(Boolean);
    },

    async ensureD3() {
      if (typeof d3 === 'undefined') await loadSankeyScript('/static/vendor/d3.min.js');
      if (typeof d3.sankey === 'undefined') await loadSankeyScript('/static/vendor/d3-sankey.min.js');
    },

    init() {
      if (!this.lazy) this.load();
      window.addEventListener(`load-sankey-${this.solutionId}`, () => this.load());
    },

    async load() {
      if (this.loaded || this.loading) return;
      this.loading = true;
      this.error = null;
      try {
        await this.ensureD3();
        const endpoint = this.apiUrl || `/architecture-journey/${this.solutionId}/traceability-flow`;
        // Platform.fetch will throw a structured PlatformError on non‑ok responses.
        // It returns the parsed JSON directly, or null for 204 No Content.
        // CSRF token injection and JSON serialisation are automatic.
        const data = await Platform.fetch(endpoint);
        this.nodes = data.nodes || [];
        this.links = data.links || [];
        this.hasCode = data.has_code || false;
        this.synthesized = data.synthesized || false;
        if (data.column_labels && data.column_labels.length) {
          this.columnLabels = data.column_labels;
        } else if (this.hasCode && !this.columnLabels.find(c => c.name==='Code')) {
          this.columnLabels.push({name:'Code'});
        }
        this.loaded = true;
        await this.$nextTick();
        this.render();
      } catch(e) {
        // The error is already surfaced to the user via Platform.fetch's built‑in toast.
        // We also set an inline error state for the component, so pass silent:true to
        // avoid a duplicate toast.
        this.error = `Failed to load traceability data: ${e.message}`;
        // Re‑throw to preserve the existing error propagation (the caller expects the
        // promise to reject). This ensures the failure is not swallowed.
        throw e;
      } finally {
        this.loading = false;
      }
    },

    render() {
      const container = this.$refs.sankey;
      if (!container || !this.nodes.length || typeof d3==='undefined' || typeof d3.sankey==='undefined') {
        if (typeof d3!=='undefined' && typeof d3.sankey==='undefined') setTimeout(()=>this.render(), 300);
        return;
      }

      const linkedNodeIdxs = new Set();
      this.links.forEach(l => { linkedNodeIdxs.add(l.source); linkedNodeIdxs.add(l.target); });
      const connectedNodes = this.nodes.filter((_,i) => linkedNodeIdxs.has(i));

      if (connectedNodes.length === 0) {
        d3.select(container).selectAll('*').remove();
        d3.select(container).append('p')
          .attr('class','text-xs text-muted-foreground p-4')
          .text(`${this.nodes.length} elements found across layers, but no cross-layer relationships exist yet.`);
        return;
      }

      const oldToNew = new Map();
      connectedNodes.forEach((n,i) => oldToNew.set(this.nodes.indexOf(n), i));
      const connectedLinks = this.links
        .filter(l => oldToNew.has(l.source) && oldToNew.has(l.target))
        .map(l => ({...l, source: oldToNew.get(l.source), target: oldToNew.get(l.target)}));

      const W = container.clientWidth || 800;
      const H = Math.max(300, connectedNodes.length * 22);
      const margin = {top:10, right:10, bottom:10, left:10};

      d3.select(container).selectAll('*').remove();
      const svg = d3.select(container).append('svg')
        .attr('width',W).attr('height',H).attr('viewBox',`0 0 ${W} ${H}`);

      const sankey = d3.sankey()
        .nodeId(d => d.index)
        .nodeAlign(d3.sankeyLeft)
        .nodeWidth(14).nodePadding(8)
        .extent([[margin.left,margin.top],[W-margin.right,H-margin.bottom]]);

      const graph = sankey({
        // d3-sankey overwrites `layer` with the column number, so keep the name.
        nodes: connectedNodes.map((n,i) => ({...n, index:i, layerName:n.layer})),
        links: connectedLinks.map(l => ({...l, value:l.value||1})),
      });

      const self = this;

      // Links — always source-layer colour
      svg.append('g').attr('fill','none')
        .selectAll('path').data(graph.links).join('path')
        .attr('d', d3.sankeyLinkHorizontal())
        .attr('stroke', d => self.layerColor(graph.nodes[d.source.index]?.layerName||''))
        .attr('stroke-width', d => Math.max(1,d.width))
        .attr('stroke-opacity', 0.22)
        .on('mouseover', function() { d3.select(this).attr('stroke-opacity',0.5); })
        .on('mouseout',  function() { d3.select(this).attr('stroke-opacity',0.22); });

      const node = svg.append('g').selectAll('g').data(graph.nodes).join('g')
        .style('cursor','pointer')
        .on('click', (event,d) => { self.selectedNode = self.selectedNode===d ? null : d; });

      node.append('rect')
        .attr('x', d=>d.x0).attr('y', d=>d.y0)
        .attr('height', d=>Math.max(2,d.y1-d.y0))
        .attr('width',  d=>d.x1-d.x0)
        .attr('fill',   d=>self.nodeColor(d))
        .attr('rx',2).attr('opacity',0.88);

      // Spec / code dot
      node.filter(d => d.has_spec||d.has_code)
        .append('circle')
        .attr('cx', d=>d.x1+4).attr('cy', d=>(d.y0+d.y1)/2)
        .attr('r',3).attr('fill', d=>self.tokenColor(d.has_code?'layer-motivation':'success'));

      // AI-origin marker
      node.filter(d => d.origin==='ai_derived')
        .append('circle')
        .attr('cx', d=>d.x0+2).attr('cy', d=>d.y0+2)
        .attr('r',2).attr('fill', self.tokenColor('layer-motivation')).attr('opacity',0.9);

      // Critical-path ring
      node.filter(d => d.critical_path)
        .append('rect')
        .attr('x', d=>d.x0-2).attr('y', d=>d.y0-2)
        .attr('height', d=>Math.max(2,d.y1-d.y0)+4)
        .attr('width',  d=>(d.x1-d.x0)+4)
        .attr('fill','none').attr('stroke', self.tokenColor('destructive'))
        .attr('stroke-width',1.5).attr('rx',3).attr('opacity',0.6);

      node.filter(d => (d.y1-d.y0)>=14)
        .append('text')
        .attr('x', d=>d.x0<W/2 ? d.x1+8 : d.x0-8)
        .attr('y', d=>(d.y0+d.y1)/2)
        .attr('dy','0.35em')
        .attr('text-anchor', d=>d.x0<W/2?'start':'end')
        .attr('font-size','10px')
        .attr('class','fill-foreground')
        .text(d=>d.name.length>28 ? d.name.slice(0,25)+'\u2026' : d.name);
    },
  };
}
