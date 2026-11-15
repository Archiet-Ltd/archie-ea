/**
 * Architecture Analytics Visualization Module
 *
 * Provides advanced architectural visualizations including:
 * - Capability heatmaps
 * - Dependency graphs
 * - Gap analysis dashboards
 * - Quality attribute radars
 * - Investment portfolio charts
 * - Architecture pattern recommendations
 */

class ArchitectureAnalytics {
  constructor(options = {}) {
    this.apiBaseUrl = options.apiBaseUrl || '/api/architecture/analytics';
    this.chartColors = {
      core: '#10b981',      // green
      supporting: '#3b82f6', // blue
      optional: '#8b5cf6',   // purple
      gap: '#ef4444',        // red
      warning: '#f59e0b'     // amber
    };
  }

  /**
   * Fetch analytics data from API
   * @param {string} endpoint - API endpoint
   * @param {object} params - Query parameters
   * @returns {Promise<object>}
   */
  async fetchAnalytics(endpoint, params) {
    try {
      const queryString = new URLSearchParams(params).toString();
      const url = `${this.apiBaseUrl}/${endpoint}?${queryString}`;
      this.log(`Fetching: ${url}`);

      // Platform.fetch automatically injects CSRF token for mutating methods,
      // serializes plain objects to JSON, and throws on non-ok responses.
      // We pass { silent: true } because the caller paints its own inline error state.
      const data = await Platform.fetch(url, {
        headers: { 'Accept': 'application/json' },
        silent: true
      });

      this.log(`Fetched analytics data`, data);
      return data;
    } catch (error) {
      // Value-returning: rethrow so callers render their own error state rather
      // than reading `undefined` as "no analytics for this scope".
      throw error;
    }
  }

  /**
   * Render capability heatmap showing strategic importance vs maturity/coverage
   * @param {string} containerId - DOM element ID
   * @param {string} entityType - 'solution' or 'application'
   * @param {number} entityId - Entity ID
   * @param {string} dimension - 'maturity' or 'coverage'
   */
  async renderCapabilityHeatmap(containerId, entityType, entityId, dimension = 'coverage') {
    const container = document.getElementById(containerId);
    if (!container) return;

    safeHTML(container, '<div class="text-center py-8"><div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div><p class="mt-2 text-muted-foreground">Loading heatmap...</p></div>');

    try {
      const data = await this.fetchAnalytics('capability-heatmap', {
        entity_type: entityType,
        entity_id: entityId,
        dimension: dimension
      });

      if (!data.success || !data.heatmap || data.heatmap.length === 0) {
        safeHTML(container, this.renderEmptyState('No heatmap data available'));
        return;
      }

      safeHTML(container, this.buildHeatmapHTML(data.heatmap, dimension));

      // Reinitialize lucide icons
      if (typeof lucide !== 'undefined') {
        lucide.createIcons();
      }
    } catch (error) {
      safeHTML(container, this.renderErrorState(`Failed to load heatmap: ${error.message}`));
    }
  }

  /**
   * Build heatmap HTML visualization
   * @param {Array} heatmapData - Heatmap data points
   * @param {string} dimension - Dimension being visualized
   * @returns {string} HTML string
   */
  buildHeatmapHTML(heatmapData, dimension) {
    // Group by strategic importance
    const grouped = { 5: [], 4: [], 3: [], 2: [], 1: [] };
    heatmapData.forEach(item => {
      const importance = item.strategic_importance || 1;
      if (grouped[importance]) {
        grouped[importance].push(item);
      }
    });

    let html = `
      <div class="space-y-4">
        <div class="flex items-center justify-between mb-4">
          <h4 class="text-sm font-semibold text-foreground">Capability Heatmap</h4>
          <span class="text-xs text-muted-foreground">Strategic Importance vs ${dimension === 'maturity' ? 'Maturity' : 'Coverage'}</span>
        </div>
    `;

    // Render each importance level
    const importanceLabels = {
      5: 'Critical (Core)',
      4: 'High',
      3: 'Medium (Supporting)',
      2: 'Low',
      1: 'Optional'
    };

    for (let importance = 5; importance >= 1; importance--) {
      const items = grouped[importance] || [];
      if (items.length === 0) continue;

      const avgValue = items.reduce((sum, item) => sum + (item.value || 0), 0) / items.length;
      const color = this.getHeatmapColor(avgValue);

      html += `
        <div class="border rounded-lg p-3 ${color.bg}">
          <div class="flex items-center justify-between mb-2">
            <h5 class="text-sm font-medium ${color.text}">${importanceLabels[importance]}</h5>
            <span class="text-xs ${color.text} opacity-75">${items.length} capabilities</span>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-2">
      `;

      items.forEach(item => {
        const cellColor = this.getHeatmapColor(item.value);
        html += `
          <div class="group relative">
            <div class="${cellColor.cellBg} ${cellColor.border} border rounded p-2 cursor-pointer hover:shadow-md transition-all">
              <div class="text-xs font-medium truncate" title="${this.escapeHtml(item.capability_name)}">
                ${this.escapeHtml(item.capability_name.substring(0, 20))}${item.capability_name.length > 20 ? '...' : ''}
              </div>
              <div class="text-xs ${cellColor.text} font-bold mt-1">${item.value}${dimension === 'maturity' ? '/5' : '%'}</div>
            </div>
            <div class="hidden group-hover:block absolute z-10 bg-popover text-popover-foreground p-2 rounded border shadow-lg -top-2 left-0 w-64">
              <p class="font-semibold text-sm">${this.escapeHtml(item.capability_name)}</p>
              <p class="text-xs mt-1"><strong>Category:</strong> ${this.escapeHtml(item.category || 'N/A')}</p>
              <p class="text-xs"><strong>${dimension === 'maturity' ? 'Maturity' : 'Coverage'}:</strong> ${item.value}${dimension === 'maturity' ? '/5' : '%'}</p>
              <p class="text-xs"><strong>Type:</strong> ${item.coverage_type}</p>
              ${item.notes ? `<p class="text-xs mt-1 italic">${this.escapeHtml(item.notes)}</p>` : ''}
            </div>
          </div>
        `;
      });

      html += `
          </div>
        </div>
      `;
    }

    html += `
      </div>
      <div class="mt-4 flex items-center justify-between text-xs">
        <div class="flex items-center gap-4">
          <div class="flex items-center gap-1">
            <div class="w-3 h-3 rounded ${this.getHeatmapColor(80).cellBg}"></div>
            <span>High</span>
          </div>
          <div class="flex items-center gap-1">
            <div class="w-3 h-3 rounded ${this.getHeatmapColor(50).cellBg}"></div>
            <span>Medium</span>
          </div>
          <div class="flex items-center gap-1">
            <div class="w-3 h-3 rounded ${this.getHeatmapColor(30).cellBg}"></div>
            <span>Low</span>
          </div>
        </div>
      </div>
    `;

    return html;
  }

  /**
   * Get color classes for heatmap cells based on value
   * @param {number} value - Value (0-100 for coverage, 0-5 for maturity)
   * @returns {object} Color classes
   */
  getHeatmapColor(value) {
    // Normalize to 0-100 scale
    const normalized = value > 5 ? value : (value / 5) * 100;

    if (normalized >= 75) {
      return {
        bg: 'bg-emerald-500/5',
        text: 'text-emerald-700',
        cellBg: 'bg-emerald-500/10',
        border: 'border-emerald-200'
      };
    } else if (normalized >= 50) {
      return {
        bg: 'bg-primary/5',
        text: 'text-primary',
        cellBg: 'bg-primary/10',
        border: 'border-primary/20'
      };
    } else if (normalized >= 25) {
      return {
        bg: 'bg-amber-50',
        text: 'text-amber-700',
        cellBg: 'bg-amber-100',
        border: 'border-amber-200'
      };
    } else {
      return {
        bg: 'bg-destructive/5',
        text: 'text-destructive',
        cellBg: 'bg-destructive/10',
        border: 'border-destructive/20'
      };
    }
  }

  /**
   * Render dependency graph visualization
   * @param {string} containerId - DOM element ID
   * @param {string} entityType - 'solution' or 'application'
   * @param {number} entityId - Entity ID
   * @param {number} depth - Depth of analysis
   */
  async renderDependencyGraph(containerId, entityType, entityId, depth = 2) {
    const container = document.getElementById(containerId);
    if (!container) return;

    safeHTML(container, '<div class="text-center py-8"><div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div><p class="mt-2 text-muted-foreground">Loading dependency graph...</p></div>');

    try {
      const data = await this.fetchAnalytics('dependency-graph', {
        entity_type: entityType,
        entity_id: entityId,
        depth: depth
      });

      if (!data.success || !data.graph) {
        safeHTML(container, this.renderEmptyState('No dependency data available'));
        return;
      }

      safeHTML(container, this.buildDependencyGraphHTML(data.graph));

      if (typeof lucide !== 'undefined') {
        lucide.createIcons();
      }
    } catch (error) {
      safeHTML(container, this.renderErrorState(`Failed to load dependency graph: ${error.message}`));
    }
  }

  /**
   * Build dependency graph HTML (simplified network visualization)
   * @param {object} graph - Graph data with nodes and edges
   * @returns {string} HTML string
   */
  buildDependencyGraphHTML(graph) {
    const { nodes, edges } = graph;

    // Group nodes by type
    const groupedNodes = {};
    nodes.forEach(node => {
      const type = node.type || 'other';
      if (!groupedNodes[type]) {
        groupedNodes[type] = [];
      }
      groupedNodes[type].push(node);
    });

    let html = `
      <div class="space-y-4">
        <div class="flex items-center justify-between mb-4">
          <h4 class="text-sm font-semibold text-foreground">Dependency Graph</h4>
          <span class="text-xs text-muted-foreground">${nodes.length} nodes, ${edges.length} connections</span>
        </div>
    `;

    // Render central node (application/solution)
    if (groupedNodes.application) {
      const centralNode = groupedNodes.application[0];
      html += `
        <div class="flex justify-center mb-6">
          <div class="relative inline-block">
            <div class="flex items-center gap-2 px-6 py-4 bg-primary text-primary-foreground rounded-lg shadow-lg">
              <i data-lucide="package" class="w-5 h-5"></i>
              <span class="font-semibold">${this.escapeHtml(centralNode.label)}</span>
            </div>
          </div>
        </div>
      `;
    }

    // Render capability nodes grouped by coverage type
    if (groupedNodes.capability) {
      const capsByType = {};
      groupedNodes.capability.forEach(cap => {
        const type = cap.coverage_type || 'optional';
        if (!capsByType[type]) {
          capsByType[type] = [];
        }
        capsByType[type].push(cap);
      });

      html += '<div class="grid grid-cols-1 md:grid-cols-3 gap-4">';

      ['core', 'supporting', 'optional'].forEach(coverageType => {
        const caps = capsByType[coverageType] || [];
        if (caps.length === 0) return;

        const typeColors = {
          'core': 'bg-emerald-500/5 border-emerald-200 text-emerald-700',
          'supporting': 'bg-primary/5 border-primary/20 text-primary',
          'optional': 'bg-muted/30 border-border text-foreground'
        };

        html += `
          <div class="border rounded-lg p-3 ${typeColors[coverageType]}">
            <h5 class="text-sm font-medium mb-2 capitalize">${coverageType} (${caps.length})</h5>
            <div class="space-y-2">
        `;

        caps.forEach(cap => {
          html += `
            <div class="bg-background rounded p-2 border border-inherit text-xs">
              <div class="font-medium">${this.escapeHtml(cap.label)}</div>
              ${cap.category ? `<div class="text-muted-foreground mt-1">${this.escapeHtml(cap.category)}</div>` : ''}
            </div>
          `;
        });

        html += `
            </div>
          </div>
        `;
      });

      html += '</div>';
    }

    html += '</div>';

    return html;
  }

  /**
   * Render gap analysis dashboard
   * @param {string} containerId - DOM element ID
   * @param {string} entityType - 'solution' or 'application'
   * @param {number} entityId - Entity ID
   */
  async renderGapAnalysis(containerId, entityType, entityId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    safeHTML(container, '<div class="text-center py-8"><div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div><p class="mt-2 text-muted-foreground">Analyzing gaps...</p></div>');

    try {
      const data = await this.fetchAnalytics('gap-analysis', {
        entity_type: entityType,
        entity_id: entityId
      });

      if (!data.success || !data.gap_analysis) {
        safeHTML(container, this.renderEmptyState('No gap analysis data available'));
        return;
      }

      safeHTML(container, this.buildGapAnalysisHTML(data.gap_analysis));

      if (typeof lucide !== 'undefined') {
        lucide.createIcons();
      }
    } catch (error) {
      safeHTML(container, this.renderErrorState(`Failed to load gap analysis: ${error.message}`));
    }
  }

  /**
   * Build gap analysis HTML dashboard
   * @param {object} gapAnalysis - Gap analysis data
   * @returns {string} HTML string
   */
  buildGapAnalysisHTML(gapAnalysis) {
    const { missing_capabilities, under_invested, redundancies, summary } = gapAnalysis;

    let html = `
      <div class="space-y-6">
        <!-- Summary Cards -->
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div class="bg-card border rounded-lg p-4">
            <div class="flex items-center gap-2 mb-2">
              <i data-lucide="check-circle" class="w-5 h-5 text-emerald-600"></i>
              <span class="text-xs font-medium text-muted-foreground">Current</span>
            </div>
            <p class="text-2xl font-bold text-foreground">${summary.current_count}</p>
          </div>
          <div class="bg-card border rounded-lg p-4">
            <div class="flex items-center gap-2 mb-2">
              <i data-lucide="alert-circle" class="w-5 h-5 text-destructive"></i>
              <span class="text-xs font-medium text-muted-foreground">Missing</span>
            </div>
            <p class="text-2xl font-bold text-foreground">${summary.missing_count}</p>
          </div>
          <div class="bg-card border rounded-lg p-4">
            <div class="flex items-center gap-2 mb-2">
              <i data-lucide="trending-down" class="w-5 h-5 text-amber-600"></i>
              <span class="text-xs font-medium text-muted-foreground">Under-Invested</span>
            </div>
            <p class="text-2xl font-bold text-foreground">${summary.under_invested_count}</p>
          </div>
          <div class="bg-card border rounded-lg p-4">
            <div class="flex items-center gap-2 mb-2">
              <i data-lucide="copy" class="w-5 h-5 text-primary"></i>
              <span class="text-xs font-medium text-muted-foreground">Redundancies</span>
            </div>
            <p class="text-2xl font-bold text-foreground">${summary.redundancy_count}</p>
          </div>
        </div>
    `;

    // Missing Capabilities
    if (missing_capabilities && missing_capabilities.length > 0) {
      html += `
        <div class="border rounded-lg p-4 bg-destructive/5 border-destructive/20">
          <h5 class="font-semibold text-destructive mb-3 flex items-center gap-2">
            <i data-lucide="alert-triangle" class="w-5 h-5"></i>
            Missing Capabilities (Top ${missing_capabilities.length})
          </h5>
          <div class="space-y-2">
      `;

      missing_capabilities.slice(0, 10).forEach(cap => {
        html += `
          <div class="bg-background rounded p-3 border border-destructive/20">
            <div class="flex items-start justify-between">
              <div class="flex-1">
                <p class="font-medium text-sm text-foreground">${this.escapeHtml(cap.capability_name)}</p>
                <p class="text-xs text-muted-foreground mt-1">${this.escapeHtml(cap.category || 'N/A')}</p>
                ${cap.recommended ? '<span class="inline-block mt-2 px-2 py-1 bg-amber-100 text-amber-800 text-xs rounded">Recommended</span>' : ''}
              </div>
              <span class="text-xs text-muted-foreground">${this.escapeHtml(cap.reason || '')}</span>
            </div>
          </div>
        `;
      });

      html += `
          </div>
        </div>
      `;
    }

    // Under-Invested Capabilities
    if (under_invested && under_invested.length > 0) {
      html += `
        <div class="border rounded-lg p-4 bg-amber-50 border-amber-200">
          <h5 class="font-semibold text-amber-700 mb-3 flex items-center gap-2">
            <i data-lucide="trending-down" class="w-5 h-5"></i>
            Under-Invested Capabilities
          </h5>
          <div class="space-y-2">
      `;

      under_invested.forEach(cap => {
        html += `
          <div class="bg-background rounded p-3 border border-amber-200">
            <div class="flex items-start justify-between">
              <div class="flex-1">
                <p class="font-medium text-sm text-foreground">${this.escapeHtml(cap.capability_name)}</p>
                <div class="flex items-center gap-4 mt-2 text-xs">
                  <span>Coverage: <strong>${Platform.figure.percent(cap.coverage_percentage)}</strong></span>
                  <span>Maturity: <strong>${Platform.figure.score(cap.maturity_level, '/5')}</strong></span>
                  <span class="px-2 py-1 bg-primary/10 text-primary/90 rounded">${cap.coverage_type}</span>
                </div>
              </div>
            </div>
            <p class="text-xs text-muted-foreground mt-2 italic">${this.escapeHtml(cap.recommendation || '')}</p>
          </div>
        `;
      });

      html += `
          </div>
        </div>
      `;
    }

    // Redundancies
    if (redundancies && redundancies.length > 0) {
      html += `
        <div class="border rounded-lg p-4 bg-purple-50 border-purple-200">
          <h5 class="font-semibold text-purple-700 mb-3 flex items-center gap-2">
            <i data-lucide="copy" class="w-5 h-5"></i>
            Capability Redundancies
          </h5>
          <div class="space-y-2">
      `;

      redundancies.forEach(redundancy => {
        html += `
          <div class="bg-background rounded p-3 border border-purple-200">
            <div class="flex items-start justify-between">
              <div class="flex-1">
                <p class="font-medium text-sm text-foreground">${this.escapeHtml(redundancy.capability_name)}</p>
                <p class="text-xs text-muted-foreground mt-1">${redundancy.provider_count} providers detected</p>
              </div>
            </div>
            <p class="text-xs text-muted-foreground mt-2 italic">${this.escapeHtml(redundancy.recommendation || '')}</p>
          </div>
        `;
      });

      html += `
          </div>
        </div>
      `;
    }

    html += '</div>';

    return html;
  }

  /**
   * Render quality attributes radar/dashboard
   * @param {string} containerId - DOM element ID
   * @param {string} entityType - 'solution' or 'application'
   * @param {number} entityId - Entity ID
   */
  async renderQualityAttributes(containerId, entityType, entityId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    safeHTML(container, '<div class="text-center py-8"><div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div><p class="mt-2 text-muted-foreground">Assessing quality attributes...</p></div>');

    try {
      const data = await this.fetchAnalytics('quality-attributes', {
        entity_type: entityType,
        entity_id: entityId
      });

      if (!data.success || !data.quality_attributes) {
        safeHTML(container, this.renderEmptyState('No quality assessment data available'));
        return;
      }

      safeHTML(container, this.buildQualityAttributesHTML(data.quality_attributes));

      if (typeof lucide !== 'undefined') {
        lucide.createIcons();
      }
    } catch (error) {
      safeHTML(container, this.renderErrorState(`Failed to load quality attributes: ${error.message}`));
    }
  }

  /**
   * Build quality attributes HTML dashboard
   * @param {object} qualityData - Quality attributes data
   * @returns {string} HTML string
   */
  buildQualityAttributesHTML(qualityData) {
    const { overall_scores, recommendations } = qualityData;

    let html = `
      <div class="space-y-6">
        <h4 class="text-sm font-semibold text-foreground mb-4">Quality Attributes Assessment</h4>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
    `;

    const attributeIcons = {
      'performance': 'zap',
      'scalability': 'trending-up',
      'security': 'shield',
      'reliability': 'check-circle',
      'maintainability': 'tool'
    };

    Object.entries(overall_scores).forEach(([attribute, score]) => {
      const color = this.getQualityScoreColor(score);
      const icon = attributeIcons[attribute] || 'activity';

      html += `
        <div class="border rounded-lg p-4 bg-card">
          <div class="flex items-center justify-between mb-3">
            <div class="flex items-center gap-2">
              <i data-lucide="${icon}" class="w-5 h-5 ${color.icon}"></i>
              <span class="font-medium text-sm capitalize">${attribute}</span>
            </div>
            <span class="text-2xl font-bold ${color.text}">${score}</span>
          </div>
          <div class="w-full bg-muted rounded-full h-2">
            <div class="${color.bar} h-2 rounded-full transition-all" style="width: ${score}%"></div>
          </div>
          <p class="text-xs text-muted-foreground mt-2">${this.getQualityLabel(score)}</p>
        </div>
      `;
    });

    html += '</div>';

    // Recommendations
    if (recommendations && recommendations.length > 0) {
      html += `
        <div class="border rounded-lg p-4 bg-amber-50 border-amber-200">
          <h5 class="font-semibold text-amber-700 mb-3 flex items-center gap-2">
            <i data-lucide="lightbulb" class="w-5 h-5"></i>
            Recommendations
          </h5>
          <div class="space-y-3">
      `;

      recommendations.forEach(rec => {
        const priorityColors = {
          'high': 'bg-destructive/10 text-red-800 border-destructive/20',
          'medium': 'bg-amber-100 text-amber-800 border-amber-200',
          'low': 'bg-primary/10 text-primary/90 border-primary/20'
        };

        html += `
          <div class="bg-background rounded p-3 border border-amber-200">
            <div class="flex items-start gap-3">
              <span class="px-2 py-1 text-xs rounded ${priorityColors[rec.priority] || priorityColors.medium}">${rec.priority}</span>
              <div class="flex-1">
                <p class="font-medium text-sm text-foreground capitalize">${rec.attribute}</p>
                <p class="text-xs text-muted-foreground mt-1">${this.escapeHtml(rec.recommendation)}</p>
                ${rec.actions && rec.actions.length > 0 ? `
                  <ul class="mt-2 space-y-1">
                    ${rec.actions.map(action => `<li class="text-xs text-muted-foreground">• ${this.escapeHtml(action)}</li>`).join('')}
                  </ul>
                ` : ''}
              </div>
            </div>
          </div>
        `;
      });

      html += `
          </div>
        </div>
      `;
    }

    html += '</div>';

    return html;
  }

  /**
   * Get color classes for quality scores
   * @param {number} score - Score 0-100
   * @returns {object} Color classes
   */
  getQualityScoreColor(score) {
    if (score >= 75) {
      return {
        text: 'text-emerald-600',
        icon: 'text-emerald-600',
        bar: 'bg-emerald-600'
      };
    } else if (score >= 50) {
      return {
        text: 'text-primary',
        icon: 'text-primary',
        bar: 'bg-primary'
      };
    } else if (score >= 25) {
      return {
        text: 'text-amber-600',
        icon: 'text-amber-600',
        bar: 'bg-amber-600'
      };
    } else {
      return {
        text: 'text-destructive',
        icon: 'text-destructive',
        bar: 'bg-destructive'
      };
    }
  }

  /**
   * Get quality score label
   * @param {number} score - Score 0-100
   * @returns {string} Label
   */
  getQualityLabel(score) {
    if (score >= 75) return 'Excellent';
    if (score >= 60) return 'Good';
    if (score >= 45) return 'Fair';
    if (score >= 30) return 'Needs Improvement';
    return 'Critical';
  }

  /**
   * Render architecture patterns analysis
   * @param {string} containerId - DOM element ID
   * @param {string} entityType - 'solution' or 'application'
   * @param {number} entityId - Entity ID
   */
  async renderArchitecturePatterns(containerId, entityType, entityId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    safeHTML(container, '<div class="text-center py-8"><div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div><p class="mt-2 text-muted-foreground">Analyzing patterns...</p></div>');

    try {
      const data = await this.fetchAnalytics('architecture-patterns', {
        entity_type: entityType,
        entity_id: entityId
      });

      if (!data.success || !data.architecture_patterns) {
        safeHTML(container, this.renderEmptyState('No pattern analysis data available'));
        return;
      }

      safeHTML(container, this.buildArchitecturePatternsHTML(data.architecture_patterns));

      if (typeof lucide !== 'undefined') {
        lucide.createIcons();
      }
    } catch (error) {
      safeHTML(container, this.renderErrorState(`Failed to load architecture patterns: ${error.message}`));
    }
  }

  /**
   * Build architecture patterns HTML
   * @param {object} patternsData - Patterns analysis data
   * @returns {string} HTML string
   */
  buildArchitecturePatternsHTML(patternsData) {
    const { detected, recommended, capability_profile } = patternsData;

    let html = `
      <div class="space-y-6">
        <div>
          <h4 class="text-sm font-semibold text-foreground mb-2">Capability Profile</h4>
          <div class="flex items-center gap-4 text-sm">
            <span>Total: <strong>${capability_profile.total_capabilities}</strong></span>
            <span>Complexity: <strong class="capitalize">${capability_profile.complexity}</strong></span>
            <span>Categories: <strong>${capability_profile.categories.length}</strong></span>
          </div>
        </div>
    `;

    // Detected Patterns
    if (detected && detected.length > 0) {
      html += `
        <div class="border rounded-lg p-4 bg-emerald-500/5 border-emerald-200">
          <h5 class="font-semibold text-emerald-700 mb-3 flex items-center gap-2">
            <i data-lucide="check-square" class="w-5 h-5"></i>
            Detected Patterns
          </h5>
          <div class="space-y-3">
      `;

      detected.forEach(pattern => {
        const confidence = Math.round(pattern.confidence * 100);
        html += `
          <div class="bg-background rounded p-4 border border-emerald-200">
            <div class="flex items-start justify-between mb-2">
              <h6 class="font-semibold text-foreground">${this.escapeHtml(pattern.pattern)}</h6>
              <span class="px-2 py-1 bg-emerald-500/10 text-green-800 text-xs rounded">${confidence}% confidence</span>
            </div>
            <p class="text-sm text-muted-foreground mb-3">${this.escapeHtml(pattern.evidence)}</p>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <p class="text-xs font-medium text-emerald-700 mb-1">Benefits:</p>
                <ul class="text-xs space-y-1">
                  ${pattern.benefits.map(b => `<li class="text-muted-foreground">✓ ${this.escapeHtml(b)}</li>`).join('')}
                </ul>
              </div>
              <div>
                <p class="text-xs font-medium text-amber-700 mb-1">Considerations:</p>
                <ul class="text-xs space-y-1">
                  ${pattern.considerations.map(c => `<li class="text-muted-foreground">⚠ ${this.escapeHtml(c)}</li>`).join('')}
                </ul>
              </div>
            </div>
          </div>
        `;
      });

      html += `
          </div>
        </div>
      `;
    }

    // Recommended Patterns
    if (recommended && recommended.length > 0) {
      html += `
        <div class="border rounded-lg p-4 bg-primary/5 border-primary/20">
          <h5 class="font-semibold text-primary mb-3 flex items-center gap-2">
            <i data-lucide="lightbulb" class="w-5 h-5"></i>
            Recommended Patterns
          </h5>
          <div class="space-y-3">
      `;

      recommended.forEach(pattern => {
        const suitability = Math.round(pattern.suitability * 100);
        html += `
          <div class="bg-background rounded p-4 border border-primary/20">
            <div class="flex items-start justify-between mb-2">
              <h6 class="font-semibold text-foreground">${this.escapeHtml(pattern.pattern)}</h6>
              <span class="px-2 py-1 bg-primary/10 text-primary/90 text-xs rounded">${suitability}% suitable</span>
            </div>
            <p class="text-sm text-muted-foreground mb-2">${this.escapeHtml(pattern.rationale)}</p>
            <div class="bg-primary/5 rounded p-2 mt-2">
              <p class="text-xs font-medium text-primary mb-1">Implementation Notes:</p>
              <p class="text-xs text-muted-foreground">${this.escapeHtml(pattern.implementation_notes)}</p>
            </div>
            <div class="mt-2">
              <p class="text-xs font-medium text-emerald-700 mb-1">Benefits:</p>
              <ul class="text-xs space-y-1">
                ${pattern.benefits.map(b => `<li class="text-muted-foreground">✓ ${this.escapeHtml(b)}</li>`).join('')}
              </ul>
            </div>
          </div>
        `;
      });

      html += `
          </div>
        </div>
      `;
    }

    html += '</div>';

    return html;
  }

  /**
   * Render investment portfolio visualization
   * @param {string} containerId - DOM element ID
   * @param {string} entityType - 'solution' or 'application'
   * @param {number} entityId - Entity ID
   */
  async renderInvestmentPortfolio(containerId, entityType, entityId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    safeHTML(container, '<div class="text-center py-8"><div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div><p class="mt-2 text-muted-foreground">Analyzing investment...</p></div>');

    try {
      const data = await this.fetchAnalytics('investment-portfolio', {
        entity_type: entityType,
        entity_id: entityId
      });

      if (!data.success || !data.investment_portfolio) {
        safeHTML(container, this.renderEmptyState('No investment data available'));
        return;
      }

      safeHTML(container, this.buildInvestmentPortfolioHTML(data.investment_portfolio));

      if (typeof lucide !== 'undefined') {
        lucide.createIcons();
      }
    } catch (error) {
      safeHTML(container, this.renderErrorState(`Failed to load investment portfolio: ${error.message}`));
    }
  }

  /**
   * Build investment portfolio HTML
   * @param {object} portfolioData - Investment portfolio data
   * @returns {string} HTML string
   */
  buildInvestmentPortfolioHTML(portfolioData) {
    const { by_coverage_type, by_category, top_investments } = portfolioData;

    // Calculate totals
    const totalByCoverage = Object.values(by_coverage_type).reduce((sum, val) => sum + val, 0);
    const totalByCategory = Object.values(by_category).reduce((sum, val) => sum + val, 0);

    let html = `
      <div class="space-y-6">
        <h4 class="text-sm font-semibold text-foreground">Investment Portfolio</h4>

        <!-- Investment by Coverage Type -->
        <div class="border rounded-lg p-4 bg-card">
          <h5 class="text-sm font-medium mb-3">Investment by Coverage Type</h5>
          <div class="space-y-3">
    `;

    const coverageColors = {
      'core': { bg: 'bg-emerald-600', text: 'text-emerald-700' },
      'supporting': { bg: 'bg-primary', text: 'text-primary' },
      'optional': { bg: 'bg-muted-foreground/20', text: 'text-foreground' }
    };

    Object.entries(by_coverage_type).forEach(([coverage, value]) => {
      const percentage = totalByCoverage > 0 ? Math.round((value / totalByCoverage) * 100) : 0;
      const colors = coverageColors[coverage] || coverageColors.optional;

      html += `
        <div>
          <div class="flex items-center justify-between mb-1">
            <span class="text-sm capitalize ${colors.text}">${coverage}</span>
            <span class="text-sm font-semibold">${percentage}%</span>
          </div>
          <div class="w-full bg-muted rounded-full h-2">
            <div class="${colors.bg} h-2 rounded-full" style="width: ${percentage}%"></div>
          </div>
        </div>
      `;
    });

    html += `
          </div>
        </div>

        <!-- Top Investments by Category -->
        <div class="border rounded-lg p-4 bg-card">
          <h5 class="text-sm font-medium mb-3">Top Investments by Category</h5>
          <div class="space-y-2">
    `;

    top_investments.slice(0, 5).forEach(([category, value]) => {
      const percentage = totalByCategory > 0 ? Math.round((value / totalByCategory) * 100) : 0;

      html += `
        <div class="flex items-center justify-between p-2 bg-muted/30 rounded">
          <span class="text-sm font-medium">${this.escapeHtml(category)}</span>
          <div class="flex items-center gap-2">
            <div class="w-24 bg-muted rounded-full h-1.5">
              <div class="bg-primary h-1.5 rounded-full" style="width: ${percentage}%"></div>
            </div>
            <span class="text-sm font-semibold text-primary w-12 text-right">${percentage}%</span>
          </div>
        </div>
      `;
    });

    html += `
          </div>
        </div>
      </div>
    `;

    return html;
  }

  /**
   * Render empty state
   * @param {string} message - Message to display
   * @returns {string} HTML string
   */
  renderEmptyState(message) {
    return `
      <div class="text-center py-8">
        <i data-lucide="inbox" class="w-16 h-16 mx-auto mb-4 text-muted-foreground opacity-50"></i>
        <h4 class="text-lg font-semibold text-foreground mb-2">No Data Available</h4>
        <p class="text-muted-foreground text-sm">${this.escapeHtml(message)}</p>
      </div>
    `;
  }

  /**
   * Render error state
   * @param {string} message - Error message
   * @returns {string} HTML string
   */
  renderErrorState(message) {
    return `
      <div class="text-center py-8">
        <i data-lucide="alert-circle" class="w-16 h-16 mx-auto mb-4 text-destructive opacity-50"></i>
        <h4 class="text-lg font-semibold text-foreground mb-2">Error Loading Data</h4>
        <p class="text-muted-foreground text-sm">${this.escapeHtml(message)}</p>
      </div>
    `;
  }

  /**
   * Escape HTML to prevent XSS
   * @param {string} text
   * @returns {string}
   */
  escapeHtml(text) {
    if (!text) return '';
    const map = {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    };
    return String(text).replace(/[&<>"']/g, m => map[m]);
  }
}

// Initialize global instance
const architectureAnalytics = new ArchitectureAnalytics({});
