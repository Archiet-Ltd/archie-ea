/** ai_chat/business_output - External JavaScript
 *  Extracted from app/templates/ai_chat/business_output.html
 *  Uses window.__APP_CONFIG__ bridge for server-side values
 */
let APP_CONFIG = window.__APP_CONFIG__ || {};

// Business Output Display JavaScript
class BusinessOutputDisplay {
    constructor() {
        this.roleSelector = document.getElementById('stakeholder-role');
        this.outputContainer = document.getElementById('business-output-content');
        this.mainContainer = document.getElementById('business-output-container');

        this.roleSelector.addEventListener('change', function() { this.handleRoleChange(); }.bind(this));
    }

    showOutput(aiResponse, originalRole) {
        originalRole = originalRole || null;
        this.mainContainer.classList.remove('hidden');
        this.currentResponse = aiResponse;

        // Auto-detect role or use provided
        let detectedRole = this.detectStakeholderRole(aiResponse) || originalRole || 'business_analyst';
        this.roleSelector.value = detectedRole;

        this.renderOutput(detectedRole);
    }

    detectStakeholderRole(response) {
        let message = response.response || '';
        let domain = response.domain || '';

        // Simple role detection based on content
        if (message.includes('strategic') || message.includes('executive') || message.includes('financial')) {
            return 'executive';
        } else if (message.includes('architecture') || message.includes('enterprise')) {
            return 'enterprise_architect';
        } else if (message.includes('capability') || message.includes('business process')) {
            return 'business_architect';
        } else if (message.includes('product') || message.includes('user story')) {
            return 'product_owner';
        } else if (message.includes('requirement') || message.includes('process')) {
            return 'business_analyst';
        } else if (message.includes('technical') || message.includes('implementation')) {
            return 'technical_lead';
        }

        return null;
    }

    async handleRoleChange() {
        let selectedRole = this.roleSelector.value;
        await this.renderOutput(selectedRole);
    }

    async renderOutput(role) {
        try {
            // Call backend to transform output for selected role
            let transformedData = await Platform.fetch.post('/ai-chat/transform-output', {
                ai_response: this.currentResponse,
                stakeholder_role: role
            });
            this.displayTransformedOutput(transformedData, role);
        } catch (error) {
            // Platform.fetch already shows a toast; we also paint an inline error.
            // Pass silent:true to avoid duplicate toast, but we still need to surface
            // the failure to the user via displayError.
            this.displayError('Failed to transform output');
            // Re-throw to preserve the existing behaviour of not swallowing the error.
            throw error;
        }
    }

    displayTransformedOutput(data, role) {
        safeHTML(this.outputContainer, DOMPurify.sanitize(this.generateOutputHTML(data, role)));

        // Initialize any interactive components
        this.initializeComponents();

        // Show visualizations
        this.renderVisualizations(data.visualizations || {});
    }

    generateOutputHTML(data, role) {
        let html = '<div class="business-output-' + role + '">' +
                '<div class="mb-6">' +
                    '<h2 class="text-2xl font-bold mb-2">' + DOMPurify.sanitize(data.role) + ' View</h2>' +
                    '<p class="text-muted-foreground">' + DOMPurify.sanitize(data.summary || '') + '</p>' +
                '</div>';

        // Role-specific sections
        if (role === 'business_analyst') {
            html += this.generateBusinessAnalystHTML(data);
        } else if (role === 'product_owner') {
            html += this.generateProductOwnerHTML(data);
        } else if (role === 'business_architect') {
            html += this.generateBusinessArchitectHTML(data);
        } else if (role === 'enterprise_architect') {
            html += this.generateEnterpriseArchitectHTML(data);
        } else if (role === 'executive') {
            html += this.generateExecutiveHTML(data);
        } else if (role === 'technical_lead') {
            html += this.generateTechnicalLeadHTML(data);
        }

        html += '</div>';
        return html;
    }

    generateBusinessAnalystHTML(data) {
        let nextStepsHtml = '';
        if (data.next_steps && data.next_steps.length > 0) {
            nextStepsHtml = data.next_steps.map(function(step) { return '<li>\u2022 ' + DOMPurify.sanitize(step) + '</li>'; }).join('');
        } else {
            nextStepsHtml = '<li>No next steps defined</li>';
        }

        return '<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Requirements Analysis</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Functional Requirements:</span>' +
                            '<span class="font-medium">' + (data.requirements_analysis?.functional_requirements?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Non-Functional Requirements:</span>' +
                            '<span class="font-medium">' + (data.requirements_analysis?.non_functional_requirements?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Compliance Requirements:</span>' +
                            '<span class="font-medium">' + (data.requirements_analysis?.compliance_requirements?.length || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Process Impact</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Affected Processes:</span>' +
                            '<span class="font-medium">' + (data.process_impact?.affected_processes?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Process Gaps:</span>' +
                            '<span class="font-medium">' + (data.process_impact?.process_gaps?.length || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Stakeholder Analysis</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Impacted Stakeholders:</span>' +
                            '<span class="font-medium">' + (data.stakeholder_analysis?.impacted_stakeholders?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Communication Needs:</span>' +
                            '<span class="font-medium">' + (data.stakeholder_analysis?.communication_needs?.length || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Next Steps</h3>' +
                    '<ul class="space-y-1 text-sm">' + nextStepsHtml + '</ul>' +
                '</div>' +
            '</div>' +
            '<div class="mt-6">' +
                '<h3 class="font-semibold mb-3">Visualizations</h3>' +
                '<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Requirements Matrix</h4>' +
                        '<div id="requirements-matrix-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Requirements Matrix</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Process Flow Impact</h4>' +
                        '<div id="process-flow-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Process Flow</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Stakeholder Map</h4>' +
                        '<div id="stakeholder-map-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Stakeholder Map</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }

    generateProductOwnerHTML(data) {
        let actionItemsHtml = '';
        if (data.action_items && data.action_items.length > 0) {
            actionItemsHtml = data.action_items.map(function(item) { return '<li>\u2022 ' + DOMPurify.sanitize(item) + '</li>'; }).join('');
        } else {
            actionItemsHtml = '<li>No action items defined</li>';
        }

        return '<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Product Impact</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>User Stories:</span>' +
                            '<span class="font-medium">' + (data.product_impact?.user_stories?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Acceptance Criteria:</span>' +
                            '<span class="font-medium">' + (data.product_impact?.acceptance_criteria?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Epic Breakdown:</span>' +
                            '<span class="font-medium">' + (data.product_impact?.epic_breakdown?.length || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Backlog Management</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Total Story Points:</span>' +
                            '<span class="font-medium">' + this.calculateTotalStoryPoints(data.backlog_management?.story_points || {}) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Dependencies:</span>' +
                            '<span class="font-medium">' + (data.backlog_management?.dependencies?.length || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Stakeholder Value</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Customer Impact:</span>' +
                            '<span class="font-medium">' + (data.stakeholder_value?.customer_impact?.score || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Business Value:</span>' +
                            '<span class="font-medium">' + (data.stakeholder_value?.business_value?.score || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>ROI Analysis:</span>' +
                            '<span class="font-medium">' + (data.stakeholder_value?.roi_analysis?.roi || 'Not assessed') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Action Items</h3>' +
                    '<ul class="space-y-1 text-sm">' + actionItemsHtml + '</ul>' +
                '</div>' +
            '</div>' +
            '<div class="mt-6">' +
                '<h3 class="font-semibold mb-3">Product Visualizations</h3>' +
                '<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Product Roadmap</h4>' +
                        '<div id="product-roadmap-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Product Roadmap</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Value Stream Map</h4>' +
                        '<div id="value-stream-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Value Stream</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Backlog Burndown</h4>' +
                        '<div id="backlog-burndown-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Burndown Chart</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }

    generateExecutiveHTML(data) {
        let recsHtml = '';
        if (data.strategic_recommendations && data.strategic_recommendations.length > 0) {
            recsHtml = data.strategic_recommendations.map(function(rec) { return '<li>\u2022 ' + DOMPurify.sanitize(rec) + '</li>'; }).join('');
        } else {
            recsHtml = '<li>No recommendations defined</li>';
        }

        return '<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Business Impact</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Strategic Value:</span>' +
                            '<span class="font-medium text-emerald-600">' + (data.business_impact?.strategic_value?.score || 'High') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Financial Impact:</span>' +
                            '<span class="font-medium">' + (data.business_impact?.financial_impact?.amount || '$0') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Competitive Advantage:</span>' +
                            '<span class="font-medium">' + (data.business_impact?.competitive_advantage?.level || 'Medium') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Investment Analysis</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>ROI Projection:</span>' +
                            '<span class="font-medium text-emerald-600">' + (data.investment_analysis?.roi_projection?.roi || '0%') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Payback Period:</span>' +
                            '<span class="font-medium">' + (data.investment_analysis?.payback_period?.months || '0 months') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>NPV Analysis:</span>' +
                            '<span class="font-medium">' + (data.investment_analysis?.npv_analysis?.npv || '$0') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Risk Assessment</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Business Risks:</span>' +
                            '<span class="font-medium">' + (data.risk_assessment?.business_risks?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Risk Appetite:</span>' +
                            '<span class="font-medium">' + (data.risk_assessment?.risk_appetite?.level || 'Medium') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Strategic Recommendations</h3>' +
                    '<ul class="space-y-1 text-sm">' + recsHtml + '</ul>' +
                '</div>' +
            '</div>' +
            '<div class="mt-6">' +
                '<h3 class="font-semibold mb-3">Executive Dashboard</h3>' +
                '<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">KPI Dashboard</h4>' +
                        '<div id="executive-dashboard-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Executive Dashboard</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Strategic Roadmap</h4>' +
                        '<div id="strategic-roadmap-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Strategic Roadmap</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Financial Projections</h4>' +
                        '<div id="financial-projections-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Financial Projections</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }

    generateBusinessArchitectHTML(data) {
        let decisionsHtml = '';
        if (data.architecture_decisions && data.architecture_decisions.length > 0) {
            decisionsHtml = data.architecture_decisions.map(function(dec) { return '<li>\u2022 ' + DOMPurify.sanitize(dec) + '</li>'; }).join('');
        } else {
            decisionsHtml = '<li>No decisions defined</li>';
        }

        return '<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Architecture View</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Capability Map:</span>' +
                            '<span class="font-medium">' + (data.architecture_view?.capability_map?.capabilities || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Value Streams:</span>' +
                            '<span class="font-medium">' + (data.architecture_view?.value_stream_analysis?.streams || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Business Processes:</span>' +
                            '<span class="font-medium">' + (data.architecture_view?.business_process_model?.processes || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Strategic Alignment</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Strategy Alignment:</span>' +
                            '<span class="font-medium">' + (data.strategic_alignment?.business_strategy_alignment?.score || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Operating Model Impact:</span>' +
                            '<span class="font-medium">' + (data.strategic_alignment?.operating_model_impact?.level || 'Medium') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Transformation Roadmap</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Capability Maturity:</span>' +
                            '<span class="font-medium">' + (data.transformation_roadmap?.capability_maturity?.current_level || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Transformation Phases:</span>' +
                            '<span class="font-medium">' + (data.transformation_roadmap?.transformation_phases?.length || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Architecture Decisions</h3>' +
                    '<ul class="space-y-1 text-sm">' + decisionsHtml + '</ul>' +
                '</div>' +
            '</div>' +
            '<div class="mt-6">' +
                '<h3 class="font-semibold mb-3">Architecture Visualizations</h3>' +
                '<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Capability Heatmap</h4>' +
                        '<div id="capability-heatmap-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Capability Heatmap</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Value Stream Canvas</h4>' +
                        '<div id="value-stream-canvas-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Value Stream Canvas</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Business Model Canvas</h4>' +
                        '<div id="business-model-canvas-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Business Model Canvas</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }

    generateEnterpriseArchitectHTML(data) {
        return '<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Enterprise View</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Business Architecture:</span>' +
                            '<span class="font-medium">' + (data.enterprise_view?.business_architecture?.components || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Application Architecture:</span>' +
                            '<span class="font-medium">' + (data.enterprise_view?.application_architecture?.applications || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Technology Architecture:</span>' +
                            '<span class="font-medium">' + (data.enterprise_view?.technology_architecture?.technologies || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Data Architecture:</span>' +
                            '<span class="font-medium">' + (data.enterprise_view?.data_architecture?.data_entities || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Strategic Impact</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Digital Transformation:</span>' +
                            '<span class="font-medium">' + (data.strategic_impact?.digital_transformation?.readiness || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Technology Strategy:</span>' +
                            '<span class="font-medium">' + (data.strategic_impact?.technology_strategy?.alignment || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Innovation Opportunities:</span>' +
                            '<span class="font-medium">' + (data.strategic_impact?.innovation_opportunities?.length || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Governance & Compliance</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Enterprise Governance:</span>' +
                            '<span class="font-medium">' + (data.governance_compliance?.enterprise_governance?.maturity || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Compliance Framework:</span>' +
                            '<span class="font-medium">' + (data.governance_compliance?.compliance_framework?.coverage || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Audit Readiness:</span>' +
                            '<span class="font-medium">' + (data.governance_compliance?.audit_readiness?.status || 'Not assessed') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Investment Prioritization</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Portfolio Analysis:</span>' +
                            '<span class="font-medium">' + (data.investment_prioritization?.portfolio_analysis?.projects || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Investment Roadmap:</span>' +
                            '<span class="font-medium">' + (data.investment_prioritization?.investment_roadmap?.phases || 0) + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>' +
            '<div class="mt-6">' +
                '<h3 class="font-semibold mb-3">Enterprise Visualizations</h3>' +
                '<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Enterprise Blueprint</h4>' +
                        '<div id="enterprise-blueprint-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Enterprise Blueprint</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Technology Radar</h4>' +
                        '<div id="technology-radar-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Technology Radar</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Investment Portfolio</h4>' +
                        '<div id="investment-portfolio-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Investment Portfolio</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }

    generateTechnicalLeadHTML(data) {
        let recsHtml = '';
        if (data.technical_recommendations && data.technical_recommendations.length > 0) {
            recsHtml = data.technical_recommendations.map(function(rec) { return '<li>\u2022 ' + DOMPurify.sanitize(rec) + '</li>'; }).join('');
        } else {
            recsHtml = '<li>No recommendations defined</li>';
        }

        return '<div class="grid grid-cols-1 lg:grid-cols-2 gap-6">' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Technical Analysis</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Implementation Approach:</span>' +
                            '<span class="font-medium">' + (data.technical_analysis?.implementation_approach?.method || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Integration Points:</span>' +
                            '<span class="font-medium">' + (data.technical_analysis?.integration_points?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Data Model Complexity:</span>' +
                            '<span class="font-medium">' + (data.technical_analysis?.data_model?.complexity || 'Medium') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Delivery Planning</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Development Phases:</span>' +
                            '<span class="font-medium">' + (data.delivery_planning?.development_phases?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Resource Requirements:</span>' +
                            '<span class="font-medium">' + (data.delivery_planning?.resource_requirements?.total || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Technical Debt:</span>' +
                            '<span class="font-medium">' + (data.delivery_planning?.technical_debt?.level || 'Low') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Operational Considerations</h3>' +
                    '<div class="space-y-2">' +
                        '<div class="flex justify-between">' +
                            '<span>Deployment Strategy:</span>' +
                            '<span class="font-medium">' + (data.operational_considerations?.deployment_strategy?.approach || 'Not assessed') + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Monitoring Requirements:</span>' +
                            '<span class="font-medium">' + (data.operational_considerations?.monitoring_requirements?.length || 0) + '</span>' +
                        '</div>' +
                        '<div class="flex justify-between">' +
                            '<span>Scalability:</span>' +
                            '<span class="font-medium">' + (data.operational_considerations?.scalability_considerations?.level || 'Medium') + '</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
                '<div class="bg-card p-4 rounded-lg border">' +
                    '<h3 class="font-semibold mb-3">Technical Recommendations</h3>' +
                    '<ul class="space-y-1 text-sm">' + recsHtml + '</ul>' +
                '</div>' +
            '</div>' +
            '<div class="mt-6">' +
                '<h3 class="font-semibold mb-3">Technical Visualizations</h3>' +
                '<div class="grid grid-cols-1 lg:grid-cols-3 gap-4">' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Technical Architecture</h4>' +
                        '<div id="technical-architecture-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Technical Architecture</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">Implementation Roadmap</h4>' +
                        '<div id="implementation-roadmap-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">Implementation Roadmap</span>' +
                        '</div>' +
                    '</div>' +
                    '<div class="bg-card p-4 rounded-lg border">' +
                        '<h4 class="font-medium mb-2">System Integration</h4>' +
                        '<div id="system-integration-viz" class="h-64 bg-muted rounded flex items-center justify-center">' +
                            '<span class="text-muted-foreground">System Integration</span>' +
                        '</div>' +
                    '</div>' +
                '</div>' +
            '</div>';
    }

    calculateTotalStoryPoints(storyPoints) {
        if (!storyPoints || typeof storyPoints !== 'object') return 0;
        return Object.values(storyPoints).reduce(function(sum, points) { return sum + (points || 0); }, 0);
    }

    initializeComponents() {
        // Initialize any interactive components like tabs, accordions, etc.
        // This would use Alpine.js or similar for interactivity
    }

    renderVisualizations(visualizations) {
        // Render charts, graphs, and other visualizations
        // This would integrate with charting libraries like Chart.js, D3, etc.

        Object.keys(visualizations).forEach(function(vizKey) {
            let vizElement = document.getElementById(vizKey + '-viz');
            if (vizElement && visualizations[vizKey]) {
                // Render the visualization
                this.renderSingleVisualization(vizElement, visualizations[vizKey]);
            }
        }.bind(this));
    }

    renderSingleVisualization(element, data) {
        // Render visualization using Chart.js (if available) or fallback to data table
        if (!data || !data.type) {
            safeHTML(element, '<div class="flex flex-col items-center justify-center h-full text-muted-foreground">' +
                    '<i data-lucide="bar-chart" class="h-8 w-8 mb-2 opacity-50"></i>' +
                    '<p class="text-sm">No visualization data available</p>' +
                '</div>');
            return;
        }

        let canvasId = 'chart-' + element.id + '-' + Date.now();
        safeHTML(element, '<canvas id="' + canvasId + '"></canvas>');

        // Wait for Chart.js to be available
        if (typeof Chart !== 'undefined') {
            setTimeout(function() {
                let ctx = document.getElementById(canvasId);
                if (ctx) {
                    try {
                        new Chart(ctx, {
                            type: data.type || 'bar',
                            data: data.data || { labels: [], datasets: [] },
                            options: data.options || {
                                responsive: true,
                                maintainAspectRatio: false
                            }
                        });
                    } catch (error) {
                        safeHTML(element, '<div class="p-4 text-sm text-muted-foreground">' +
                                '<p class="font-medium mb-2">Visualization Data</p>' +
                                '<pre class="text-xs overflow-auto">' + JSON.stringify(data, null, 2).substring(0, 500) + '</pre>' +
                            '</div>');
                    }
                }
            }, 100);
        } else {
            // Fallback: Show data as table
            safeHTML(element, '<div class="p-4 text-sm">' +
                    '<p class="font-medium mb-2">Data Visualization</p>' +
                    '<div class="text-xs text-muted-foreground overflow-auto max-h-64">' +
                        '<pre>' + JSON.stringify(data, null, 2).substring(0, 1000) + '</pre>' +
                    '</div>' +
                '</div>');
        }
    }

    displayError(message) {
        safeHTML(this.outputContainer, DOMPurify.sanitize(
            '<div class="bg-destructive/5 border border-destructive/20 rounded-lg p-4">' +
                '<h3 class="text-destructive-emphasis font-semibold mb-2">Error</h3>' +
                '<p class="text-destructive">' + DOMPurify.sanitize(message) + '</p>' +
            '</div>'
        ));
    }

    hide() {
        this.mainContainer.classList.add('hidden');
    }
}

// Initialize the business output display
document.addEventListener('DOMContentLoaded', function() {
    window.businessOutputDisplay = new BusinessOutputDisplay();
});

// Integration with existing chat system
function showBusinessOutput(aiResponse) {
    if (window.businessOutputDisplay) {
        window.businessOutputDisplay.showOutput(aiResponse);
    }
}

function hideBusinessOutput() {
    if (window.businessOutputDisplay) {
        window.businessOutputDisplay.hide();
    }
}
