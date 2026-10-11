/**
 * blueprintChat() — Alpine.js component for the blueprint slide-out chat panel.
 *
 * Injected into blueprint.html with solution context pre-populated from Jinja.
 * Communicates with POST /ai-chat/message, renders action cards inline.
 */
function blueprintChat() {
    return {
        open: false,
        // ENFORCED (agent_runner.py AgentRunner._should_queue): mirrors the
        // server-side session flag agent_auto_execute, which toggleAutoExecute()
        // below flips via POST /ai-chat/session/toggle-auto-execute. Server
        // default is OFF (false) for a fresh session, so this starts false too
        // rather than assuming a state the backend has not granted yet. When
        // false, every tool whose registry schema has mutates:true is queued
        // for confirmation instead of executing immediately; approve-tier
        // tools (update_application_status, submit_for_arb_review,
        // generate_blueprint_narrative) always queue regardless of this flag.
        // Read-only tools (mutates:false) always execute immediately.
        //
        // This starting value is only a placeholder until init() below
        // overwrites it with the real session state via GET
        // /ai-chat/session/auto-execute — the session flag can already be ON
        // from an earlier page visit (Flask sessions outlive a page load), so
        // hardcoding false here and never re-checking would show OFF in the
        // UI while the server was actually queuing nothing.
        autoExecute: false,
        messages: [],
        inputText: '',
        loading: false,
        csrfToken: document.querySelector('meta[name="csrf-token"]')?.content || '',
        solutionId: null,
        solutionName: '',
        phase: 'A',

        init() {
            // Read context from data-* attributes to avoid HTML attribute quote issues
            this.solutionId = parseInt(this.$el.dataset.solutionId) || null;
            this.solutionName = this.$el.dataset.solutionName || '';
            this.phase = this.$el.dataset.phase || 'A';

            // Handle pre-populated queries from diagram context menu and insight banners
            window.addEventListener('blueprint-chat-open-with', (e) => {
                this.open = true;
                this.inputText = e.detail.query || '';
                this.$nextTick(() => {
                    this.$refs.chatInput?.focus();
                    this.$refs.chatInput?.select();
                });
            });

            // Sync the placeholder above with the real session state. Read-only
            // GET — never flips the flag, unlike toggleAutoExecute() below.
            this.syncAutoExecute();
        },

        async syncAutoExecute() {
            try {
                const data = await Platform.fetch.get('/ai-chat/session/auto-execute');
                this.autoExecute = !!data.auto_execute;
            } catch (err) {
                // Leave the placeholder value in place — a stale local guess
                // is a smaller failure than blocking the panel on this call.
                // Platform.fetch has already toasted the failure, so the stale value
                // is visible as stale rather than presented as fresh truth.
                // Suppressed here only to avoid a duplicate, because this is a
                // background sync that failing should not block the UI.
            }
        },

        toggle() {
            this.open = !this.open;
            if (this.open) this.$nextTick(() => this.$refs.chatInput?.focus());
        },

        async toggleAutoExecute() {
            try {
                const data = await Platform.fetch.post('/ai-chat/session/toggle-auto-execute');
                this.autoExecute = data.auto_execute;
            } catch (err) {
                // Platform.fetch has already toasted this failure; the catch only
                // stops it becoming an unhandled rejection.
            }
        },

        async sendMessage() {
            const text = this.inputText.trim();
            if (!text || this.loading) return;

            this.messages.push({ role: 'user', text });
            this.inputText = '';
            this.loading = true;

            // Create streaming assistant message placeholder
            const assistantMsg = { role: 'assistant', text: '', streaming: true };
            this.messages.push(assistantMsg);
            const streamIdx = this.messages.length - 1;

            try {
                // raw-fetch-ok: streaming response requires raw Response object to access body.getReader()
                const resp = await fetch('/ai-chat/message/stream', {  // raw-fetch-ok: SSE stream; needs body.getReader() on the raw Response
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': this.csrfToken,
                    },
                    body: JSON.stringify({
                        message: text,
                        domain: 'architecture',
                        solution_id: this.solutionId,
                    }),
                });

                if (!resp.ok || !resp.body) {
                    throw new Error('Stream request failed: ' + resp.status);
                }

                const reader = resp.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                this._pendingToolCards = {};

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // Keep incomplete line for next chunk

                    for (const line of lines) {
                        if (!line.startsWith('data: ')) continue;
                        const payload = line.slice(6).trim();
                        if (!payload || payload === '[DONE]') continue;

                        let event;
                        try { event = JSON.parse(payload); } catch { continue; }

                        if (event.type === 'token') {
                            this.messages[streamIdx].text += event.text;
                            this.$nextTick(() => this.scrollToBottom());

                        } else if (event.type === 'tool_start') {
                            const card = { role: 'tool_thinking', tool: event.tool, args: event.args };
                            this._pendingToolCards[event.tool] = this.messages.length;
                            this.messages.push(card);

                        } else if (event.type === 'tool_result') {
                            const cardIdx = this._pendingToolCards[event.tool];
                            if (cardIdx !== undefined && event.result && event.result.success) {
                                const action = {
                                    tool: event.tool,
                                    message: event.result.message,
                                    result: event.result.result,
                                    arguments: event.args || {},
                                    undone: false,
                                    undoExpired: false,
                                    undoTimer: null,
                                };
                                action.undoTimer = setTimeout(() => { action.undoExpired = true; }, 60000);
                                this.messages[cardIdx] = { role: 'actions', actions: [action] };
                                delete this._pendingToolCards[event.tool];
                                const entityType = (event.result.result && event.result.result.entity_type) || event.tool;
                                window.dispatchEvent(new CustomEvent('bp-agent-wrote', {
                                    detail: { entity_type: entityType, solution_id: this.solutionId },
                                }));
                            } else if (cardIdx !== undefined) {
                                this.messages[cardIdx] = {
                                    role: 'error',
                                    text: (event.result && event.result.error) || 'Tool failed',
                                };
                                delete this._pendingToolCards[event.tool];
                            }

                        } else if (event.type === 'approval_queued') {
                            this.messages.push({
                                role: 'approvals',
                                approvals: [{ ...event, dismissed: false }],
                            });

                        } else if (event.type === 'done') {
                            this.messages[streamIdx].streaming = false;
                            // Use buffered response if no tokens were streamed (non-streaming LLM path)
                            /* Order matters, and it used to be wrong. The
                               AgentRunner fallback sends BOTH `response` (the
                               "add an API key under Admin -> API Settings"
                               text) and `error`. Assigning `response` first
                               made the `event.error` branch unreachable — its
                               own `!text` guard was already false — so an
                               administrative "AI is not configured" notice was
                               painted as an ordinary assistant answer,
                               indistinguishable from real architecture advice.
                               Entelim is a system of record: that is the exact
                               confusion we must not create. Check `error`
                               first, so a failed turn always reads as failed. */
                            if (event.error) {
                                this.messages[streamIdx].role = 'error';
                                if (!this.messages[streamIdx].text) {
                                    this.messages[streamIdx].text =
                                        event.response || event.error;
                                }
                                this.$nextTick(() => this.scrollToBottom());
                            } else if (event.response && !this.messages[streamIdx].text) {
                                this.messages[streamIdx].text = event.response;
                                this.$nextTick(() => this.scrollToBottom());
                            }
                        }
                    }
                }

            } catch (err) {
                // Platform.fetch would have thrown a structured error for non‑streaming errors,
                // but we keep raw fetch for streaming. The error is already surfaced to the user
                // via the inline error message placed in the chat thread.
                this.messages[streamIdx].streaming = false;
                this.messages[streamIdx].role = 'error';
                this.messages[streamIdx].text = 'Request failed. Please try again.';
            } finally {
                this.loading = false;
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        async undoAction(action) {
            if (action.undoExpired || action.undone) return;
            const entityType = action.result && action.result.entity_type;
            const entityId = action.result && action.result.id;
            if (!entityId) return;

            // Map entity_type to DELETE endpoint
            const deleteMap = {
                driver: `/solutions/${this.solutionId}/drivers/${entityId}`,
                goal: `/solutions/${this.solutionId}/goals/${entityId}`,
                constraint: `/solutions/${this.solutionId}/constraints/${entityId}`,
                requirement: `/solutions/${this.solutionId}/requirements/${entityId}`,
                risk: `/solutions/${this.solutionId}/risks/${entityId}`,
                option: `/solutions/${this.solutionId}/options/${entityId}`,
            };
            const url = deleteMap[entityType];
            if (!url) return; // No undo for link operations

            try {
                await Platform.fetch.delete(url);
                action.undone = true;
                clearTimeout(action.undoTimer);
                window.dispatchEvent(new CustomEvent('bp-agent-wrote', {
                    detail: { entity_type: entityType, solution_id: this.solutionId },
                }));
            } catch (err) {
                // Platform.fetch has already toasted this failure; the catch only
                // stops it becoming an unhandled rejection.
            }
        },

        async approveAction(approval) {
            try {
                const data = await Platform.fetch.post(`/ai-chat/tools/approve/${approval.approval_id}`);
                if (data.success) {
                    approval.dismissed = true;
                    this.messages.push({
                        role: 'actions',
                        actions: [{ ...data, undone: false, undoExpired: true }],
                    });
                    if (data.result && data.result.entity_type) {
                        window.dispatchEvent(new CustomEvent('bp-agent-wrote', {
                            detail: { entity_type: data.result.entity_type, solution_id: this.solutionId },
                        }));
                    }
                } else {
                    // Platform.fetch would have thrown on non‑ok response, so this branch
                    // only runs if the server returns a 200 with success:false.
                    // We keep the existing inline error toast.
                    Platform.toast.error('Approve failed: ' + (data.error || 'Unknown error'));
                }
            } catch (err) {
                // Platform.fetch has already toasted this failure; the catch only
                // stops it becoming an unhandled rejection.
            }
        },

        dismissApproval(approval) {
            approval.dismissed = true;
        },

        handleKeydown(event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                this.sendMessage();
            }
        },

        scrollToBottom() {
            const thread = this.$refs.chatThread;
            if (thread) thread.scrollTop = thread.scrollHeight;
        },
    };
}

/**
 * diagramContextMenu() — Alpine.js component for the AI right-click menu on diagram elements.
 * Listens for 'bp-element-context-menu' events, shows a floating 4-option menu.
 * Clicking an option pre-populates the chat panel and opens it.
 */
function diagramContextMenu() {
    return {
        visible: false,
        x: 0,
        y: 0,
        element: null,

        init() {
            const self = this;

            window.addEventListener('bp-element-context-menu', function (e) {
                self.element = e.detail;
                self.x = e.detail.x;
                self.y = e.detail.y;
                self.visible = true;
            });

            document.addEventListener('click', function () {
                self.visible = false;
            });

            document.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') self.visible = false;
            });
        },

        actions() {
            const el = this.element;
            if (!el) return [];
            return [
                {
                    label: 'Explain this element',
                    icon: '💡',
                    query: `Explain the role of "${el.elementName}" (${el.elementType}) in this solution architecture and why it is included.`,
                },
                {
                    label: 'Find similar in portfolio',
                    icon: '🔍',
                    query: `Find other solutions in our portfolio that use "${el.elementName}" or a similar ${el.elementType}. Show solution names and how they use it.`,
                },
                {
                    label: 'Suggest missing relationships',
                    icon: '🔗',
                    query: `Run the inference engine on "${el.elementName}" and identify any missing ArchiMate relationships. What should be connected to it?`,
                },
                {
                    label: 'Assess impact of removing',
                    icon: '⚠️',
                    query: `What is the impact of removing "${el.elementName}" from this solution? Trace all dependencies and identify what would break.`,
                },
            ];
        },

        selectAction(action) {
            this.visible = false;
            window.dispatchEvent(new CustomEvent('blueprint-chat-open-with', {
                detail: { query: action.query },
            }));
        },
    };
}

/**
 * copilotInsights(solutionId) — Alpine.js component for proactive insight banners.
 * Fetches unseen insights on init, renders them in the completeness strip.
 * "Discuss" opens the chat panel with the suggested query pre-loaded.
 */
function copilotInsights(solutionId) {
    return {
        insights: [],
        solutionId: solutionId,

        async load() {
            try {
                const data = await Platform.fetch.get(`/solutions/${this.solutionId}/copilot-insights`);
                this.insights = data.insights || [];
            } catch (err) {
                // Proactive insights are an enhancement, not a record: the panel simply
                // stays empty. Platform.fetch has already toasted the failure, so the
                // user is not left thinking there were no insights.
            }
        },

        discuss(insight) {
            window.dispatchEvent(new CustomEvent('blueprint-chat-open-with', {
                detail: { query: insight.suggested_query || insight.body },
            }));
        },

        async dismiss(insight) {
            try {
                await Platform.fetch.post(`/solutions/${this.solutionId}/copilot-insights/${insight.id}/dismiss`);
            } catch {
                // Silent fail — proactive insights are non‑critical.
                // Platform.fetch may have shown a toast; we suppress duplicate user‑facing error.
            }
            this.insights = this.insights.filter(i => i.id !== insight.id);
        },

        severityClasses(severity) {
            if (severity === 'critical') return 'border-destructive/30 bg-destructive/10 text-destructive-emphasis';
            if (severity === 'warning') return 'border-amber-300 bg-amber-50 text-amber-700';
            return 'border-violet-300 bg-violet-50 text-violet-700';
        },
    };
}
