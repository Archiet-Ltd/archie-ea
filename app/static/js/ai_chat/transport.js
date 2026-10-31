/* /ai-chat network transport.
 *
 * Every request the chat page makes, in one place: the SSE parser, the
 * non-streaming fallback, models, threads and feedback. Extracted verbatim
 * from the inline script in app/templates/ai_chat/index.html — the framing,
 * the [DONE] sentinel, the empty-output fallback signal and the thread_id
 * handling are unchanged on purpose, so old and new can be diffed.
 *
 * No DOM. Callers pass handlers; what the transcript does with an event is
 * the caller's problem.
 */
(function (window, document) {
    'use strict';

    var ArchieChat = (window.ArchieChat = window.ArchieChat || {});

    /* The template used to shadow `fetch` with this wrapper for the whole
       inline block. Re-stated here because an external module cannot inherit
       that shadowing — drop it and every POST loses its CSRF token. */
    function csrfToken() {
        return (window.getCSRFToken && window.getCSRFToken()) ||
            window.csrfToken ||
            (document.querySelector('meta[name=csrf-token]') || {}).content ||
            '';
    }

    function apiFetch(url, options) {
        var requestOptions = Object.assign({}, options || {});
        var method = (requestOptions.method || 'GET').toUpperCase();
        var headers = new Headers(requestOptions.headers || {});
        if (['POST', 'PUT', 'PATCH', 'DELETE'].indexOf(method) !== -1 && !headers.has('X-CSRFToken')) {
            var token = csrfToken();
            if (token) headers.set('X-CSRFToken', token);
        }
        if (!requestOptions.credentials) requestOptions.credentials = 'same-origin';
        requestOptions.headers = headers;
        return window.fetch(url, requestOptions);
    }

    function jsonHeaders() {
        return { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() };
    }

    /* HTTP 503 means only that a service is temporarily unavailable. The
       chat feature gate supplies the narrower, structured signal required to
       diagnose an administrative provider-configuration fault. Keeping this
       classification independent of the status prevents a proxy outage or a
       transient provider failure from being presented as missing setup. */
    function isProviderConfigurationFault(detail) {
        return Boolean(
            detail &&
            detail.error === 'service_unavailable' &&
            detail.feature === 'chat'
        );
    }

    // ---------------------------------------------------------------- chat

    /* Non-streaming turn. The fallback the streaming path drops to — losing
       it means one proxy that buffers SSE yields no answers at all. */
    function sendMessage(payload) {
        return apiFetch('/ai-chat/message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: payload
        });
    }

    /* A question about the organisation's own estate is answered from its recorded
       connections, as cited cards, before the model is asked. Resolves to the
       answer ({found: true, html, page_url}) or {found: false}; rejects when the
       answer could not be worked out, so the chat shows an error rather than
       handing a grounded question to a model. */
    async function askEstate(message, options) {
        var remember = !(options && options.remember === false);
        var resp = await apiFetch('/ai-chat/estate-answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message, thread_id: window.__threadId || null, remember: remember })
        });
        var data = await resp.json().catch(function () { return {}; });
        if (!resp.ok || data.success === false) throw new Error(data.error || 'estate answer ' + resp.status);
        return data;
    }

    /* Work out the indirect connections of the whole organisation. A run already in
       progress (409) is reported by status so the caller can say so in plain words. */
    async function recomputeConnections() {
        var resp = await apiFetch('/api/v1/intelligence/derivation/recompute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scope: 'tenant' })
        });
        if (!resp.ok) {
            var err = new Error('recompute ' + resp.status);
            err.status = resp.status;
            throw err;
        }
    }

    var _controller = null;

    /* No SSE event — not even a keepalive — for this long means the
       connection itself has gone dead rather than the agent merely being
       slow. chat_core.py emits a real `keepalive` event at least every 15s
       even while its own queue is empty (_STREAM_KEEPALIVE_INTERVAL_S) —
       specifically so this 30s window sits comfortably ABOVE that interval
       rather than inside it. It used to wrap a 95s server backstop with a
       30s client timer, which is the false-positive Task 3's review caught:
       any turn slower than 30s to first token — a legitimately busy model,
       not a wedge — was killed as a transport failure. Two missed
       keepalives (30s) plus the wait for a third (up to 15s) puts genuine
       wedge detection at ~45s, matching the plan's target, with no
       false positive on a merely-slow turn as long as the server keeps
       emitting on schedule.
       Idle, not total: reset on every chunk (see _readWithTimeout), so a
       normal multi-second answer that keeps producing tokens — or nothing
       but keepalives while a tool runs — is never punished for its total
       length. */
    var STREAM_IDLE_TIMEOUT_MS = 30000;

    /* Stream a turn over SSE.
     *
     * Resolves true when it produced an answer (the caller stops there),
     * resolves FALSE when the stream closed with no text — the caller must
     * then fall back to sendMessage() — and throws when the stream could not
     * be established, went idle past STREAM_IDLE_TIMEOUT_MS (name
     * 'ChatTimeoutError'), or the server reported a hard error with no
     * response text to show. A `done` event carrying BOTH an error and a
     * response (the persisted "couldn't be completed" message) is not
     * thrown — it resolves onDone with that text and the error attached, so
     * the caller can render it as the assistant's answer instead of
     * discarding it.
     *
     * handlers:
     *   onOpen()                 response is OK, before the first byte is read.
     *                            The caller creates its streaming bubble here,
     *                            exactly where the inline version did.
     *   onToken(text)            one token of answer text.
     *   onToolStart(evt)         {tool, args} — the server has always emitted
     *   onToolResult(evt)        {tool, result} these and the client always
     *                            discarded them. Surfaced now so the evidence
     *                            trail needs no transport change.
     *   onThreadId(id)           the conversation this turn was persisted to.
     *   onDone({text, domain, sources, error})  called once, after the stream
     *                            closes, only when there is text to render.
     *                            `error` is set when the text is the
     *                            server's own failure message.
     */
    async function streamMessage(payload, handlers) {
        var h = handlers || {};
        _controller = new AbortController();
        try {
            return await _stream(payload, h);
        } finally {
            /* Every exit resets the controller, not just the success path. It
               used to be nulled only after a clean finish, so any throw — a 502,
               a bad SSE frame, a `done` carrying an error — left it set:
               isStreaming() kept reporting true and Stop then aborted a
               controller for a request that had already ended. */
            _controller = null;
        }
    }

    /* reader.read() with an idle timeout. Rejects with a distinctly-named
       error (not the AbortError a user-initiated Stop produces) so the
       caller can tell "nothing came back in time" from "the user cancelled"
       apart — the two need opposite UI treatment. */
    function _readWithTimeout(reader, ms) {
        var timer;
        var timeoutPromise = new Promise(function (_, reject) {
            timer = setTimeout(function () {
                var err = new Error(
                    'The AI service did not respond within ' + Math.round(ms / 1000) + 's.'
                );
                err.name = 'ChatTimeoutError';
                reject(err);
            }, ms);
        });
        return Promise.race([reader.read(), timeoutPromise]).finally(function () {
            clearTimeout(timer);
        });
    }

    async function _stream(payload, h) {
        var resp = await apiFetch('/ai-chat/message/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
            body: payload,
            signal: _controller.signal
        });
        if (!resp.ok || !resp.body) {
            /* The body carries the only actionable sentence we get. The
               no-provider gate answers this endpoint with 503
               {"error":"service_unavailable","message":"AI feature 'chat' is
               not available. LLM provider must be configured."}; throwing a
               bare 'stream 503' discarded it and the user was shown a status
               code with a Retry that could never succeed. Read it, and mark a
               configuration fault so the caller can offer the remedy instead
               of a retry. Parsing is best-effort: a proxy-generated 502 is
               HTML, not JSON, and must still produce a usable error. */
            var _detail = null;
            try { _detail = await resp.json(); } catch (_) { /* swallow-ok: non-JSON error body (proxy/CDN HTML); the status-code message below still applies */ }
            var _err = new Error(
                (_detail && _detail.message) || ('stream ' + resp.status)
            );
            _err.status = resp.status;
            _err.isConfigFault = isProviderConfigurationFault(_detail);
            throw _err;
        }

        if (h.onOpen) h.onOpen();

        var reader = resp.body.getReader();
        var dec = new TextDecoder();
        var buf = '', full = '', meta = {};
        try {
            while (true) {
                var chunk = await _readWithTimeout(reader, STREAM_IDLE_TIMEOUT_MS);
                if (chunk.done) break;
                buf += dec.decode(chunk.value, { stream: true });
                var lines = buf.split('\n');
                buf = lines.pop();
                for (var i = 0; i < lines.length; i++) {
                    var s = lines[i].trim();
                    if (!s.startsWith('data:')) continue;
                    var raw = s.slice(5).trim();
                    if (raw === '[DONE]') continue;
                    var ev;
                    try { ev = JSON.parse(raw); } catch (_) { continue; }
                    if (ev.thread_id && h.onThreadId) h.onThreadId(ev.thread_id);
                    if (ev.type === 'token' && ev.text) {
                        full += ev.text;
                        if (h.onToken) h.onToken(ev.text, full);
                    } else if (ev.type === 'tool_start') {
                        if (h.onToolStart) h.onToolStart(ev);
                    } else if (ev.type === 'tool_result') {
                        if (h.onToolResult) h.onToolResult(ev);
                    } else if (ev.type === 'done') {
                        /* Only a hard failure with nothing to show is fatal.
                           agent_runner._fallback() persists a friendly
                           explanation ALONGSIDE the raw error — that pairing
                           used to make this branch throw unconditionally,
                           discarding the very message the server had just
                           written to the transcript. */
                        if (ev.error && !ev.response) throw new Error(ev.error);
                        /* When the server's citation gate refused the answer, its
                           response is what the user sees, whatever was streamed
                           before it. */
                        if (ev.error && ev.response) {
                            full = ev.response; /* partial tokens of a failed call are not shown */
                        } else if (ev.citation_gate && ev.citation_gate.refused && ev.response) {
                            full = ev.response;
                        } else if (ev.genome_answer && ev.response) {
                            /* Checked and drawn on the server; nothing streamed is kept. */
                            full = ev.response;
                        } else if (!full && ev.response) {
                            full = ev.response;
                        }
                        // actions_taken rides the done event already (chat_core
                        // spreads the whole agent result); it names every tool that
                        // ran and what it returned, which is what the evidence trail
                        // and the write receipts are built from.
                        meta = {
                            domain: ev.domain,
                            sources: ev.sources,
                            actions: ev.actions_taken || [],
                            pendingApprovals: ev.pending_approvals || [],
                            answeredBy: ev.answered_by || null,
                            genomeHtml: (ev.genome_answer && ev.genome_answer.html) || null,
                            paused: !!ev.paused,
                            error: ev.error || null
                        };
                    } else if (ev.error) {
                        throw new Error(ev.error);
                    }
                }
            }
        } catch (streamErr) {
            /* swallow-ok: reader.cancel() on an already-closed/errored stream
               throws in some browsers purely because there is nothing left to
               cancel; streamErr — the real failure — is rethrown below
               regardless of whether this cleanup succeeds, so a failure here
               is neither the user-facing error nor worth logging twice. */
            try { reader.cancel(); } catch (_) { /* swallow-ok: see above */ }
            /* Idle timeout: also abort the underlying connection so the
               browser and the server both stop treating it as live, rather
               than leaving a socket the reader has merely stopped reading
               from. A real AbortError from the user's own Stop button would
               reach here as a plain rethrow with no controller left to
               abort — abort() already nulled it. */
            if (streamErr && streamErr.name === 'ChatTimeoutError' && _controller) {
                /* swallow-ok: same reasoning — abort() on an already-aborted
                   controller is a no-op we don't need to surface, and
                   streamErr is rethrown unconditionally next. */
                try { _controller.abort(); } catch (_) { /* swallow-ok: see above */ }
            }
            throw streamErr;
        }

        if (!full || !full.trim()) return false;  // let the caller fall back
        if (h.onDone) await h.onDone({
            text: full,
            domain: meta.domain,
            sources: meta.sources,
            actions: meta.actions || [],
            pendingApprovals: meta.pendingApprovals || [],
            answeredBy: meta.answeredBy || null,
            genomeHtml: meta.genomeHtml || null,
            paused: !!meta.paused,
            error: meta.error || null
        });
        return true;
    }

    /* #stop-btn. The working implementation already existed in the 169 KB
       ai_chat.js that no template loaded; this is that AbortController,
       salvaged rather than reinvented. */
    function abort() {
        if (_controller) {
            _controller.abort();
            _controller = null;
            return true;
        }
        return false;
    }

    function isStreaming() {
        return _controller !== null;
    }

    // -------------------------------------------------------------- models

    async function loadModels() {
        var response = await apiFetch('/ai-chat/models');
        var data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'models ' + response.status);
        }
        return data.models || [];
    }

    // ------------------------------------------------------------- threads

    function listThreads() {
        return apiFetch('/ai-chat/threads', { headers: jsonHeaders() });
    }

    function loadThread(id) {
        return apiFetch('/ai-chat/threads/' + encodeURIComponent(id), { headers: jsonHeaders() });
    }

    function deleteThread(id) {
        return apiFetch('/ai-chat/threads/' + encodeURIComponent(id), {
            method: 'DELETE',
            headers: jsonHeaders()
        });
    }

    // ------------------------------------------------------------ feedback

    async function submitFeedback(rating, meta) {
        var body = Object.assign({ rating: rating }, meta || {});
        var resp = await apiFetch('/ai-chat/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        var data = await resp.json();
        if (!resp.ok || data.error) throw new Error(data.error || 'feedback ' + resp.status);
    }

    ArchieChat.transport = {
        apiFetch: apiFetch,
        csrfToken: csrfToken,
        jsonHeaders: jsonHeaders,
        isProviderConfigurationFault: isProviderConfigurationFault,
        sendMessage: sendMessage,
        askEstate: askEstate,
        recomputeConnections: recomputeConnections,
        streamMessage: streamMessage,
        abort: abort,
        isStreaming: isStreaming,
        loadModels: loadModels,
        listThreads: listThreads,
        loadThread: loadThread,
        deleteThread: deleteThread,
        submitFeedback: submitFeedback
    };
})(window, document);
