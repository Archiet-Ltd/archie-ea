/**
 * AI Chat CRUD Approval Manager — Alpine.js component.
 *
 * Polls /ai-chat/approvals/queue every 30 seconds, renders a badge count
 * in the chat header, and provides a modal for a second authorized user to
 * review / approve / reject pending CRUD operations requested via AI chat.
 *
 * State lives in a single Alpine.store('approvals') instance, not in the
 * Alpine.data factory below. Two DOM mounts use x-data="approvalManager"
 * (the header badge trigger and the approvals modal, ai_chat/index.html) —
 * Alpine.data factories are instantiated fresh per mount, so with state and
 * init() living on the factory each mount ran its own fetchPending() +
 * setInterval(), doubling the /ai-chat/approvals/pending poll (shell-overhaul
 * Wave 2, Task 5). The factory now just proxies to the shared store, so
 * there is exactly one poll loop no matter how many mounts exist.
 */
function registerApprovalManager() {
    if (!window.Alpine || window.__approvalManagerRegistered) {
        return;
    }
    window.__approvalManagerRegistered = true;

    window.Alpine.store("approvals", {
        /** State */
        approvals: [],
        loading: false,
        error: null,
        hasLoaded: false,
        /** ID of the approval currently being rejected (shows reason textarea) */
        rejectingId: null,
        rejectReason: "",
        /** Busy flags keyed by approval id — prevents double-click */
        busyIds: {},
        /** Polling handle */
        _pollHandle: null,
        _initialized: false,
        _fetchSequence: 0,

        /* ------------------------------------------------------------------ */
        /*  Lifecycle                                                          */
        /* ------------------------------------------------------------------ */

        init() {
            if (this._initialized) {
                return;
            }
            this._initialized = true;
            this.fetchPending();
            this._pollHandle = setInterval(() => this.fetchPending(), 30000);
        },

        destroy() {
            if (this._pollHandle) {
                clearInterval(this._pollHandle);
                this._pollHandle = null;
            }
            this._initialized = false;
        },

        /* ------------------------------------------------------------------ */
        /*  Data fetching                                                      */
        /* ------------------------------------------------------------------ */

        async fetchPending() {
            const fetchSequence = ++this._fetchSequence;
            this.loading = true;
            try {
                const data = await Platform.fetch("/ai-chat/approvals/queue", {
                    credentials: "same-origin",
                    silent: true, // We handle errors inline below
                });
                if (!data.success) {
                    throw new Error(data.error || "Approval queue was unavailable");
                }
                if (fetchSequence !== this._fetchSequence) {
                    return;
                }
                this.approvals = data.approvals || [];
                this.hasLoaded = true;
                this.error = null;
            } catch (err) {
                if (fetchSequence !== this._fetchSequence) {
                    return;
                }
                // Platform.fetch already logged the error; we set a user‑visible error state.
                this.error = this.hasLoaded
                    ? "Unable to refresh approvals. Showing the last known results."
                    : "Unable to load approvals. Approval status is unavailable.";
                // Do not overwrite previously displayed rows on a transient
                // failure: the visible error marks them as stale rather than
                // presenting an empty queue as a fact.
            } finally {
                if (fetchSequence === this._fetchSequence) {
                    this.loading = false;
                }
            }
        },

        /* ------------------------------------------------------------------ */
        /*  Actions                                                            */
        /* ------------------------------------------------------------------ */

        async approve(approvalId) {
            if (this.busyIds[approvalId]) return;
            this.busyIds[approvalId] = true;
            this.error = null;
            try {
                const data = await Platform.fetch.post(
                    "/ai-chat/approvals/" + approvalId + "/approve",
                    null, // no body
                    {
                        credentials: "same-origin",
                        silent: true, // We handle errors inline
                    }
                );
                if (data.success) {
                    this.approvals = this.approvals.filter(
                        (a) => a.id !== approvalId
                    );
                } else {
                    this.error = data.error || "Approval failed";
                }
            } catch (err) {
                this.error = "Network error — please try again";
            } finally {
                delete this.busyIds[approvalId];
            }
        },

        startReject(approvalId) {
            this.rejectingId = approvalId;
            this.rejectReason = "";
            this.error = null;
        },

        cancelReject() {
            this.rejectingId = null;
            this.rejectReason = "";
        },

        async confirmReject(approvalId) {
            if (this.busyIds[approvalId]) return;
            this.busyIds[approvalId] = true;
            this.error = null;
            try {
                const data = await Platform.fetch.post(
                    "/ai-chat/approvals/" + approvalId + "/reject",
                    { reason: this.rejectReason }, // plain object, auto‑JSON
                    {
                        credentials: "same-origin",
                        silent: true, // We handle errors inline
                    }
                );
                if (data.success) {
                    this.approvals = this.approvals.filter(
                        (a) => a.id !== approvalId
                    );
                    this.rejectingId = null;
                    this.rejectReason = "";
                } else {
                    this.error = data.error || "Rejection failed";
                }
            } catch (err) {
                this.error = "Network error — please try again";
            } finally {
                delete this.busyIds[approvalId];
            }
        },

        /* ------------------------------------------------------------------ */
        /*  Modal helpers                                                      */
        /* ------------------------------------------------------------------ */

        // openModal()/closeModal() were removed here (shell-overhaul Wave 3,
        // Task 5): the modal's open/closed state has never been driven by
        // this store — ai_chat/index.html toggles it via
        // data-modal-open/data-modal-close (Platform.modal, ui/modal.js), so
        // neither method nor the `this.open` flag they wrote had a caller or
        // a reader anywhere in the template.

        /** Format ISO date to a short human-readable form */
        formatDate(iso) {
            if (!iso) return "";
            const d = new Date(iso);
            return d.toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
            });
        },

        /** Return a Tailwind color token name for an operation type */
        opColor(type) {
            if (type === "create") return "success";
            if (type === "update") return "warning";
            if (type === "delete") return "destructive";
            return "muted-foreground";
        },

        /** Pending count for the badge */
        get pendingCount() {
            return this.approvals.length;
        },
    });

    // Starts the single fetchPending()/setInterval() loop (guarded by
    // _initialized above, so re-registering the Alpine.data factory below
    // across mounts never starts a second one).
    window.Alpine.store("approvals").init();

    // Thin per-mount facade — every x-data="approvalManager" mount reads
    // and writes the same Alpine.store('approvals') instance above, so the
    // header badge trigger and the approvals modal share one copy of state
    // instead of each polling and tracking it independently.
    // Exposed as a top-level window factory and used as
    // x-data="approvalManager()". The CSP-safe Alpine evaluator
    // (app/static/js/csp/csp-evaluator.js) resolves a bare x-data identifier
    // against the component scope and then window -- it never consults
    // Alpine.data() registrations -- so the bare-name form mounted an empty
    // component. See scripts/check_alpine_data_binding.py.
    window.approvalManager = () => ({
        get approvals() {
            return Alpine.store("approvals").approvals;
        },
        get loading() {
            return Alpine.store("approvals").loading;
        },
        get error() {
            return Alpine.store("approvals").error;
        },
        get hasLoaded() {
            return Alpine.store("approvals").hasLoaded;
        },
        get rejectingId() {
            return Alpine.store("approvals").rejectingId;
        },
        set rejectingId(value) {
            Alpine.store("approvals").rejectingId = value;
        },
        get rejectReason() {
            return Alpine.store("approvals").rejectReason;
        },
        set rejectReason(value) {
            Alpine.store("approvals").rejectReason = value;
        },
        get busyIds() {
            return Alpine.store("approvals").busyIds;
        },
        get pendingCount() {
            return Alpine.store("approvals").pendingCount;
        },
        fetchPending() {
            return Alpine.store("approvals").fetchPending();
        },
        approve(approvalId) {
            return Alpine.store("approvals").approve(approvalId);
        },
        startReject(approvalId) {
            return Alpine.store("approvals").startReject(approvalId);
        },
        cancelReject() {
            return Alpine.store("approvals").cancelReject();
        },
        confirmReject(approvalId) {
            return Alpine.store("approvals").confirmReject(approvalId);
        },
        formatDate(iso) {
            return Alpine.store("approvals").formatDate(iso);
        },
        opColor(type) {
            return Alpine.store("approvals").opColor(type);
        },
    });
}

if (window.Alpine) {
    registerApprovalManager();
} else {
    document.addEventListener("alpine:init", registerApprovalManager, { once: true });
}
