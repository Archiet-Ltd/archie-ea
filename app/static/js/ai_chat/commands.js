/* /ai-chat slash commands and the two interceptors that run before the chat
 * pipeline.
 *
 * All 11 commands, ported verbatim from the inline script in
 * app/templates/ai_chat/index.html: same endpoints, same payloads, same
 * response text, same follow-up data-action buttons. Nothing was restyled
 * while moving, so old and new can be diffed.
 *
 * NOT ported: handleCommand -> generateArchimatePreview / mapApqcPreview ->
 * renderActionCard -> window.applyAction, ~190 lines the submit handler never
 * called (it calls handleChatCommand), and applyArchimateElements, which had
 * no caller either. With them go the four endpoints left with no live call
 * site: /ai-chat/generate-archimate, /ai-chat/map-apqc,
 * /ai-chat/apply-archimate, /ai-chat/apply-apqc.
 */
(function (window, document) {
    'use strict';

    var ArchieChat = (window.ArchieChat = window.ArchieChat || {});
    var state = ArchieChat.state;
    var render = ArchieChat.render;
    var appendMessage = render.appendMessage;
    var appendSystemMessage = render.appendSystemMessage;
    var getColorClass = render.getColorClass;
    /* The template used to shadow `fetch` with a CSRF-injecting wrapper for
       its whole inline block. Outside that block the wrapper has to be named. */
    var fetch = ArchieChat.transport.apiFetch;

    function messagesContainer() { return document.getElementById('messages-container'); }
    function domainConfig() { return window.domainConfig || {}; }

    // ==========================================================================
    // Actionable Chat Commands (PRD Implementation)
    // ==========================================================================

    async function handleChatCommand(message) {
        const parts = message.trim().split(/\s+/);
        const command = parts[0].toLowerCase();
        const args = parts.slice(1).join(' ');

        const commands = {
            '/generate-archimate': handleGenerateArchimate,
            '/map-apqc': handleMapApqc,
            '/save-insights': handleSaveInsights,
            '/bulk-process': handleBulkProcess,
            '/gap-analysis': handleGapAnalysis,
            '/discover-vendors': handleDiscoverVendors,
            '/create-diagram': handleCreateDiagram,
            '/analyze': handleArchitectAnalyze,
            '/viewpoints': handleArchitectViewpoints,
            '/arb-ready': handleArbReady,
            '/help': showCommandHelp
        };

        if (commands[command]) {
            await commands[command](args);
            return true;
        }
        return false;
    }

    async function handleGenerateArchimate(args) {
        const appId = args.trim() || state.contextElement?.id;
        if (!appId) {
            appendMessage('ai', `**Usage:** \`/generate-archimate [application_id]\`\n\nPlease provide an application ID or select an application from context first.`, { domain: 'architecture' });
            return;
        }

        appendSystemMessage(`Generating ArchiMate diagram for application ${appId}…`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/chat/generate-archimate', {
                application_id: parseInt(appId),
                preview_only: false
            });

            if (data.success) {
                const solutionId = data.architecture_id;
                const elementCount = data.elements_created || data.elements?.length || 0;
                const composerUrl = solutionId ? `/archimate/composer?solution_id=${solutionId}` : `/archimate/composer`;

                appendMessage('ai',
                    `## ArchiMate Diagram Ready: ${data.application_name}\n\n` +
                    `Created **${elementCount}** ArchiMate element(s). Opening in Composer…\n\n` +
                    `[Open Composer →](${composerUrl})`,
                    { domain: 'architecture' }
                );

                setTimeout(() => { window.location.href = composerUrl; }, 1500);
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'architecture' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'architecture' });
        }
    }

    async function handleMapApqc(args) {
        const appId = args.trim() || state.contextElement?.id;
        if (!appId) {
            appendMessage('ai', `**Usage:** \`/map-apqc [application_id]\`\n\nPlease provide an application ID or select an application from context first.`, { domain: 'business_capability' });
            return;
        }

        appendSystemMessage(`Mapping APQC processes for application ${appId}...`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/chat/map-apqc', {
                application_id: parseInt(appId),
                preview_only: true
            });

            if (data.success) {
                const highConf = data.mappings.filter(m => m.confidence >= 0.85);
                const medConf = data.mappings.filter(m => m.confidence >= 0.7 && m.confidence < 0.85);
                const lowConf = data.mappings.filter(m => m.confidence < 0.7);

                let responseText = `## APQC Process Mappings for ${data.application_name}

**High Confidence (≥85%):**
${highConf.length > 0 ? highConf.map(m => `- ✅ ${m.process_code}: ${m.process_name} (${Math.round(m.confidence * 100)}%)`).join('\n') : 'None'}

**Medium Confidence (70-85%):**
${medConf.length > 0 ? medConf.map(m => `- ⚠️ ${m.process_code}: ${m.process_name} (${Math.round(m.confidence * 100)}%)`).join('\n') : 'None'}

**Low Confidence (<70%):**
${lowConf.length > 0 ? lowConf.map(m => `- ❓ ${m.process_code}: ${m.process_name} (${Math.round(m.confidence * 100)}%)`).join('\n') : 'None'}

<div class="mt-4 flex gap-2">
<button type="button" data-action="apply-apqc" data-app-id="${appId}" data-high-conf="1" class="px-3 py-1.5 bg-primary text-primary-foreground rounded-lg text-sm hover:bg-primary/90">✅ Apply High Confidence</button>
<button type="button" data-action="apply-apqc" data-app-id="${appId}" data-high-conf="0" class="px-3 py-1.5 border rounded-lg text-sm hover:bg-accent">Apply All</button>
</div>`;

                appendMessage('ai', responseText, { domain: 'business_capability' });
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'business_capability' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'business_capability' });
        }
    }

    async function applyApqcMappings(appId, highConfOnly) {
        try {
            const data = await Platform.fetch.post('/ai-chat/chat/map-apqc', {
                application_id: parseInt(appId),
                preview_only: false,
                apply_high_confidence: highConfOnly
            });

            if (data.success) {
                appendSystemMessage(`✅ Applied ${data.mappings_applied} APQC mappings — <a href="/applications/${appId}" class="underline font-medium">View Application →</a>`, 'info');
            } else {
                appendSystemMessage(`❌ ${data.error}`, 'error');
            }
        } catch (error) {
            appendSystemMessage(`❌ ${error.message}`, 'error');
        }
    }

    async function handleSaveInsights(args) {
        const appId = parseInt(args) || (state.contextElement?.type === 'Application' ? parseInt(state.contextElement.id) : null);
        if (!appId) {
            appendMessage('ai', `**Save Insights**\n\nTo save insights from our conversation to an application, use:\n\`/save-insights [application_id]\`\n\nOr select an application from context and run \`/save-insights\`.`, { domain: 'general' });
            return;
        }

        const lastAiMessage = [...state.chatHistory].reverse().find(m => m.role === 'assistant' || m.role === 'ai');
        const insightContent = lastAiMessage?.content || 'AI-generated insight from chat session';

        appendSystemMessage(`Saving insights to application ${appId}...`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/chat/save-insights', {
                application_id: appId,
                insights: { description: insightContent }
            });

            if (data.success) {
                appendMessage('ai', `## ✅ Insights Saved\n\n**Application:** ${data.application_name}\n**Fields Updated:** ${data.fields_updated?.join(', ') || 'description'}\n\n[View Application →](/applications/${data.application_id})`, { domain: 'general' });
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'general' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'general' });
        }
    }

    async function handleBulkProcess(args) {
        const maxApps = parseInt(args) || 10;

        appendSystemMessage(`Starting bulk processing for up to ${maxApps} applications...`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/chat/bulk-process', {
                max_applications: maxApps,
                map_capabilities: true,
                map_processes: true,
                generate_archimate: false,
                auto_create: false
            });

            if (data.success) {
                let responseText = `## Bulk Processing Results

**Applications Processed:** ${data.total_processed}
**Capability Mappings Created:** ${data.capability_mappings_created}
**Process Mappings Created:** ${data.process_mappings_created}
**ArchiMate Elements Created:** ${data.archimate_elements_created}

${data.applications?.slice(0, 5).map(app => `- ${app.application_name}: ${app.capabilities?.length || 0} caps, ${app.processes?.length || 0} procs`).join('\n') || ''}

Use \`/bulk-process [number]\` to process more applications.`;

                responseText += `\n\n<a href="/applications" class="inline-block mt-2 text-primary underline text-sm"><!-- token-migration-ok -->View Application Portfolio →</a>`;

                appendMessage('ai', responseText, { domain: 'general' });
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'general' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'general' });
        }
    }

    async function handleGapAnalysis(args) {
        const analysisType = args.trim() || 'capability';

        appendSystemMessage(`Running ${analysisType} gap analysis...`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/chat/gap-analysis', {
                analysis_type: analysisType
            });

            if (data.success) {
                const criticalGaps = data.gaps.filter(g => g.severity === 'high' || g.severity === 'critical');

                let responseText = `## Gap Analysis: ${data.analysis_type}

**Total Gaps Found:** ${data.total_gaps}
**Critical/High Priority:** ${data.critical_gaps}

### Top Gaps:
${data.gaps.slice(0, 10).map(g => `
- **${g.severity === 'high' ? '' : ''} ${g.name}**
  ${g.description}
  *Recommendation:* ${g.recommendation}
  ${g.capability_id ? `<a href="/capability_map/capabilities?id=${g.capability_id}">→ View Capability</a>` : ''}
  ${g.vendor_name ? `<a href="/applications?filter_vendor=${encodeURIComponent(g.vendor_name)}">→ Find Vendors</a>` : ''}
`).join('\n')}

<div class="mt-4 flex gap-2 flex-wrap">
<button type="button" data-action="gap-analysis-tab" data-analysis-type="capability" class="px-3 py-1.5 ${analysisType === 'capability' ? 'bg-primary text-primary-foreground' : 'border'} rounded-lg text-sm">Capability</button>
<button type="button" data-action="gap-analysis-tab" data-analysis-type="vendor" class="px-3 py-1.5 ${analysisType === 'vendor' ? 'bg-primary text-primary-foreground' : 'border'} rounded-lg text-sm">Vendor</button>
<button type="button" data-action="gap-analysis-tab" data-analysis-type="process" class="px-3 py-1.5 ${analysisType === 'process' ? 'bg-primary text-primary-foreground' : 'border'} rounded-lg text-sm">Process</button>
<a href="/solutions" class="px-3 py-1.5 border rounded-lg text-sm hover:bg-accent inline-block">Solutions Portfolio</a>
</div>`;

                appendMessage('ai', responseText, { domain: 'gap_analysis' });
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'gap_analysis' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'gap_analysis' });
        }
    }

    async function handleDiscoverVendors(args) {
        const capabilityName = args.trim();
        if (!capabilityName) {
            appendMessage('ai', `**Usage:** \`/discover-vendors [capability name]\`\n\nExample: \`/discover-vendors Customer Relationship Management\``, { domain: 'vendor_intelligence' });
            return;
        }

        appendSystemMessage(`Discovering vendors for "${capabilityName}"...`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/chat/discover-vendors', {
                capability_name: capabilityName,
                calculate_tco: true
            });

            if (data.success) {
                let responseText = `## Vendor Discovery: ${data.capability_searched}

**Vendors Found:** ${data.vendors_found}

${data.vendors.map((v, i) => `
### ${i + 1}. <a href="/vendors/${v.vendor_id}">${v.vendor_name}</a> (${Math.round(v.capability_fit * 100)}% fit)
**Products:** ${v.products.map(p => p.product_name).join(', ')}
${v.tco_estimate ? `**3-Year TCO:** $${v.tco_estimate.three_year.toLocaleString()}` : ''}
`).join('\n')}

<div class="mt-4 flex gap-2">
<button type="button" data-action="discover-vendors" data-capability="${capabilityName.replace(/"/g, '&quot;')}" class="px-3 py-1.5 border rounded-lg text-sm hover:bg-accent">Refresh</button>
<a href="/applications/vendors" class="px-3 py-1.5 border rounded-lg text-sm hover:bg-accent inline-block">All Vendors</a>
</div>`;

                appendMessage('ai', responseText, { domain: 'vendor_intelligence' });
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'vendor_intelligence' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'vendor_intelligence' });
        }
    }

    async function handleCreateDiagram(args) {
        const solutionId = parseInt(args);
        if (!solutionId) {
            appendMessage('ai', `**Usage:** \`/create-diagram [solution_id]\`\n\nCreates an ArchiMate diagram from a solution's elements and opens it in Composer.\n\nExample: \`/create-diagram 42\``, { domain: 'architecture' });
            return;
        }

        appendSystemMessage(`Creating diagram for solution ${solutionId}...`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/chat/create-solution-diagram', {
                solution_id: solutionId
            });

            if (data.success) {
                appendMessage('ai',
                    `## Diagram Created: ${data.diagram_name}\n\n` +
                    `Created **${data.element_count}** ArchiMate element(s). Opening in Composer…\n\n` +
                    `[Open Composer →](${data.redirect_url})`,
                    { domain: 'architecture' }
                );
                setTimeout(() => { window.location.href = data.redirect_url; }, 1500);
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'architecture' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'architecture' });
        }
    }

    async function handleArchitectAnalyze(args) {
        const problemStatement = args.trim();
        if (!problemStatement || problemStatement.length < 10) {
            appendMessage('ai', `**Usage:** \`/analyze [problem statement]\`\n\nRuns a full TOGAF ADM analysis on your problem statement.\n\nExample: \`/analyze Modernise customer onboarding to reduce time-to-activate\``, { domain: 'architecture' });
            return;
        }

        appendSystemMessage('Running TOGAF ADM analysis... this may take a moment.', 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/architect/analyze', {
                problem_statement: problemStatement
            });

            if (data.success) {
                const phases = data.phases || {};
                const phaseNames = Object.keys(phases);
                let responseText = `## TOGAF ADM Analysis\n\n`;

                phaseNames.forEach(phase => {
                    const p = phases[phase];
                    if (p && typeof p === 'object') {
                        responseText += `### ${phase.charAt(0).toUpperCase() + phase.slice(1).replace(/_/g, ' ')}\n`;
                        if (p.summary) responseText += `${p.summary}\n\n`;
                        else responseText += `${JSON.stringify(p).slice(0, 200)}\n\n`;
                    }
                });

                if (data.solution_id) {
                    responseText += `\n[View Solution →](/solutions/${data.solution_id})\n\n`;
                    responseText += `<div class="mt-3 flex gap-2 flex-wrap">`;
                    responseText += `<button type="button" data-action="architect-viewpoints" data-solution-id="${data.solution_id}" class="px-3 py-1.5 border rounded-lg text-sm hover:bg-accent">Generate Viewpoints</button>`;
                    responseText += `<button type="button" data-action="arb-ready" data-solution-id="${data.solution_id}" class="px-3 py-1.5 border rounded-lg text-sm hover:bg-accent">Check ARB Readiness</button>`;
                    responseText += `</div>`;
                }

                appendMessage('ai', responseText, { domain: 'architecture' });
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'architecture' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'architecture' });
        }
    }

    async function handleArchitectViewpoints(args) {
        const solutionId = parseInt(args);
        if (!solutionId) {
            appendMessage('ai', `**Usage:** \`/viewpoints [solution_id]\`\n\nGenerates stakeholder viewpoints for a solution.\n\nExample: \`/viewpoints 42\``, { domain: 'architecture' });
            return;
        }

        appendSystemMessage(`Generating viewpoints for solution ${solutionId}...`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/architect/viewpoints', {
                solution_id: solutionId
            });

            if (data.success) {
                let responseText = `## Stakeholder Viewpoints: ${data.solution_name}\n\nGenerated **${data.viewpoints.length}** viewpoints:\n\n`;

                data.viewpoints.forEach(vp => {
                    responseText += `### ${vp.name}\n**Elements:** ${vp.element_count}\n`;
                    if (vp.composer_url) {
                        responseText += `<a href="${vp.composer_url}" class="text-primary underline text-sm"><!-- token-migration-ok -->→ Open in Composer</a>\n\n`;
                    } else {
                        responseText += `*(No elements in this layer)*\n\n`;
                    }
                });

                appendMessage('ai', responseText, { domain: 'architecture' });
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'architecture' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'architecture' });
        }
    }

    async function handleArbReady(args) {
        const solutionId = parseInt(args);
        if (!solutionId) {
            appendMessage('ai', `**Usage:** \`/arb-ready [solution_id]\`\n\nScores a solution's ARB readiness.\n\nExample: \`/arb-ready 42\``, { domain: 'architecture' });
            return;
        }

        appendSystemMessage(`Checking ARB readiness for solution ${solutionId}...`, 'info');

        try {
            const data = await Platform.fetch.post('/ai-chat/architect/arb-ready', {
                solution_id: solutionId
            });

            if (data.success) {
                const gradeColor = data.grade === 'A' ? '' : data.grade === 'B' ? '' : data.grade === 'C' ? '' : '';
                let responseText = `## ${gradeColor} ARB Readiness: ${data.solution_name}\n\n**Score:** ${data.score}/100  **Grade:** ${data.grade}\n\n`;

                const c = data.criteria || {};
                responseText += `### Scoring Breakdown\n`;
                responseText += `- Architectural Principles: ${c.principles_alignment?.score || 0}/${c.principles_alignment?.max || 40}\n`;
                responseText += `- Technology Standards: ${c.technology_standards?.score || 0}/${c.technology_standards?.max || 30}\n`;
                responseText += `- Risk Coverage: ${c.risk_coverage?.score || 0}/${c.risk_coverage?.max || 20}\n`;
                responseText += `- Completeness: ${c.completeness?.score || 0}/${c.completeness?.max || 10}\n\n`;

                if (data.blocking_issues?.length > 0) {
                    responseText += `### Issues\n`;
                    data.blocking_issues.forEach(issue => {
                        const icon = issue.severity === 'blocking' ? '' : '⚠️';
                        responseText += `- ${icon} **${issue.criterion}:** ${issue.message}\n`;
                    });
                    responseText += '\n';
                }

                if (data.solution_id) {
                    responseText += `[View Solution →](/solutions/${data.solution_id})`;
                }

                appendMessage('ai', responseText, { domain: 'architecture' });
            } else {
                appendMessage('ai', `**Error:** ${data.error}`, { domain: 'architecture' });
            }
        } catch (error) {
            appendMessage('ai', `**Error:** ${error.message}`, { domain: 'architecture' });
        }
    }

    function showCommandHelp() {
        const helpText = `## Available Commands

### Architecture & Mapping
- \`/generate-archimate [app_id]\` - Generate ArchiMate 3.2 elements for an application
- \`/map-apqc [app_id]\` - Map APQC PCF processes for an application
- \`/create-diagram [solution_id]\` - Create an ArchiMate diagram from a solution and open in Composer

### TOGAF ADM Architect Workflow
- \`/analyze [problem statement]\` - Run full TOGAF ADM analysis (scope → roadmap → ARB draft)
- \`/viewpoints [solution_id]\` - Generate 4 stakeholder viewpoints for a solution
- \`/arb-ready [solution_id]\` - Score ARB readiness (0–100 with grade and blocking issues)

### Analysis
- \`/gap-analysis [type]\` - Run gap analysis (capability, vendor, or process)
- \`/discover-vendors [capability]\` - Find vendors for a capability

### Bulk Operations
- \`/bulk-process [count]\` - Bulk process applications (default: 10)

### Other
- \`/save-insights [app_id]\` - Save chat insights to application
- \`/help\` - Show this help message

**Tip:** Select an application from the context panel first, then commands will use it automatically.
**Architect Workflow:** \`/analyze\` → \`/viewpoints\` → \`/arb-ready\` chains automatically.`;

        appendMessage('ai', helpText, { domain: 'general' });
    }

    // ==========================================================================
    // ENT-122: Natural Language ArchiMate Detection
    // ==========================================================================
    const ARCHIMATE_NL_PATTERNS = [
        /\b(draw|create|generate|make|build|design|sketch)\b.{0,60}\b(archimate|archi-mate|architecture diagram|arch diagram)\b/i,
        /\b(archimate|archi-mate)\b.{0,60}\b(diagram|model|for|of)\b/i,
        /\b(diagram|model)\b.{0,60}\b(for|of)\b.{0,80}\b(system|application|service|platform|crm|erp|hrm)\b/i,
        /\b(show|visuali[sz]e|map out)\b.{0,60}\b(architecture|components|layers|system)\b/i,
    ];

    function detectArchimateFreeformIntent(text) {
        return ARCHIMATE_NL_PATTERNS.some(p => p.test(text));
    }

    async function handleArchimateFreeform(description) {
        const loadingId = 'ent122-loading-' + Date.now();
        const loadingDiv = document.createElement('div');
        loadingDiv.id = loadingId;
        loadingDiv.className = 'flex gap-4';
        const loadingGradient = getColorClass(domainConfig()['architecture']?.color || 'purple', 'gradient');
        loadingDiv.innerHTML = `
            <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br ${loadingGradient} text-primary-foreground shadow-lg"><!-- token-migration-ok -->
                <i data-lucide="layout-dashboard" class="h-5 w-5 animate-pulse"></i>
            </div>
            <div class="rounded-xl bg-muted/50 p-4 text-sm flex items-center gap-3">
                <div class="flex space-x-1">
                    <div class="w-2 h-2 bg-primary rounded-full animate-bounce" style="animation-delay: 0ms"></div>
                    <div class="w-2 h-2 bg-primary rounded-full animate-bounce" style="animation-delay: 150ms"></div>
                    <div class="w-2 h-2 bg-primary rounded-full animate-bounce" style="animation-delay: 300ms"></div>
                </div>
                <span class="text-muted-foreground">Generating ArchiMate diagram…</span>
            </div>`;
        messagesContainer().appendChild(loadingDiv);
        messagesContainer().scrollTop = messagesContainer().scrollHeight;
        lucide.createIcons();

        try {
            const data = await Platform.fetch.post('/ai-chat/chat/generate-archimate-description', {
                description
            });
            document.getElementById(loadingId)?.remove();

            if (data.success && data.elements && data.elements.length > 0) {
                const elemCount = data.element_count || data.elements.length;
                const relCount = data.relationship_count || (data.relationships || []).length;
                const summary = `**ArchiMate diagram generated** — ${elemCount} elements, ${relCount} relationships.\n\nModel: *${data.model_name || 'Generated Architecture'}*`;
                appendMessage('ai', summary, {
                    domain: 'architecture',
                    archimate_elements: data.elements,
                    archimate_relationships: data.relationships || [],
                    archimate_model_name: data.model_name
                });
            } else {
                const errMsg = data.error || 'Could not generate diagram. Try /generate-archimate or add more detail.';
                appendMessage('ai', `**ArchiMate Generation:** ${errMsg}`, { domain: 'architecture' });
            }
        } catch (err) {
            document.getElementById(loadingId)?.remove();
            appendMessage('ai', `**Network Error:** ${err.message}`, { domain: 'architecture' });
        }
    }

    // The Query tab is a structured ENTITY lookup (it lists apps/vendors/caps by
    // filter). Advisory questions — recommend / assess / summarize / why / risks /
    // consolidation — are not entity filters; the entity service degrades them to a
    // plain list. Detect those and route them to the conversational chat instead,
    // which answers them properly and records them in history.
    function isAdvisoryQuery(q) {
        const s = q.toLowerCase();
        const lookup = /\b(list|show me|show all|how many|count|find all|get all|display all)\b/.test(s);
        // Stems use a leading word-boundary only so suffixes match (e.g. "consolidat"
        // catches consolidate/consolidation, "duplicat" catches duplicates).
        const advisory = /\b(recommend|advice|advis|assess|evaluat|analy[sz]|summar|overview|brief me|why|how (should|do|can|would)|what should|explain|compare|pros and cons|trade-?off|consolidat|duplicat|overlap|rationali|strateg|roadmap|prioriti|opportunit|optimi|best (approach|option|way)|next step)/.test(s)
            || /\brisks?\b/.test(s);
        return advisory && !lookup;
    }

    ArchieChat.commands = {
        handle: handleChatCommand,
        isAdvisoryQuery: isAdvisoryQuery,
        detectArchimateFreeformIntent: detectArchimateFreeformIntent,
        handleArchimateFreeform: handleArchimateFreeform,
        applyApqcMappings: applyApqcMappings,
        handleGapAnalysis: handleGapAnalysis,
        handleDiscoverVendors: handleDiscoverVendors,
        handleArchitectViewpoints: handleArchitectViewpoints,
        handleArbReady: handleArbReady,
        showCommandHelp: showCommandHelp
    };
})(window, document);
