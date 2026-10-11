/* The Ask page.
 *
 * A person opens a question card, types a business noun, chooses a match and
 * gets that question's answer for it. All six lenses of the catalogue are
 * here now: impact (L1, "what breaks"), strategy (L2, "what are we trying
 * to achieve"), portfolio (L3, a deep link to rationalization planning),
 * accountability (L4, "who's accountable"), programme (L5, "what's
 * changing") and risk (L6, "what could hurt"). One question is open at a
 * time
 * (openKey), and one shared picker (fixed input id / $refs.pickerInput --
 * see _entity_picker.html) sits in whichever panel is open; onSelect()
 * dispatches by openKey rather than always loading the impact answer.
 * answeredKey records which question the CURRENT result set belongs to, so
 * switching the open question does not change which results are showing
 * until a new answer actually arrives.
 *
 * Registered as a top-level window factory and referenced as
 * x-data="askSurface()"; the CSP-safe expression interpreter resolves names
 * against the component scope and window, never against Alpine.data().
 */
function askSurface() {
    var Intelligence = window.Intelligence;
    return Object.assign(Intelligence.picker('ask'), Intelligence.drawerState(), {
        openKey: null,
        answeredKey: null,
        state: 'idle',
        busy: false,
        centreId: null,
        rows: [],
        notComputed: false,
        stale: false,
        withheld: false,
        staleNotice: '',
        twinMapUrl: '',
        recomputing: false,
        recomputeLine: '',
        recomputeFailed: false,
        riskState: 'idle',
        riskBusy: false,
        risks: [],
        portfolioState: 'idle',
        portfolioBusy: false,
        portfolioComponentId: null,
        rationalizationPlanningUrlBase: '',
        programmeState: 'idle',
        programmeBusy: false,
        workPackages: [],
        strategyState: 'idle',
        strategyBusy: false,
        initiatives: [],
        accountabilityState: 'idle',
        accountabilityBusy: false,
        owners: [],
        dataState: 'idle',
        dataBusy: false,
        dataObjects: [],
        dataFlows: [],
        complianceState: 'idle',
        complianceBusy: false,
        controls: [],
        violations: [],
        lastScanAt: null,

        init() {
            this.twinMapUrl = this.$el.getAttribute('data-twin-map-url') || '';
            // The route ends in a literal "/0" (a real, URL-safe int, built
            // via url_for(..., app_id=0) since Werkzeug's int converter
            // rejects a string placeholder at build time) -- drop that one
            // trailing character and append the real id per lookup.
            var base = this.$el.getAttribute('data-rationalization-planning-url-base') || '';
            this.rationalizationPlanningUrlBase = base ? base.slice(0, -1) : '';
        },

        toggleQuestion(key) {
            this.openKey = this.openKey === key ? null : key;
            if (this.openKey) {
                var self = this;
                this.$nextTick(function () { self.$refs.pickerInput.focus(); });
            }
        },

        twinMapHref(row) {
            return this.twinMapUrl + '?element=' + row.elementId;
        },

        portfolioPlanningHref() {
            return this.rationalizationPlanningUrlBase + this.portfolioComponentId;
        },

        onSelect(option) {
            if (this.openKey === 'risk') {
                this.loadRisk(option.id);
            } else if (this.openKey === 'portfolio') {
                this.loadPortfolio(option.id);
            } else if (this.openKey === 'programme') {
                this.loadProgramme(option.id);
            } else if (this.openKey === 'strategy') {
                this.loadStrategy(option.id);
            } else if (this.openKey === 'accountability') {
                this.loadAccountability(option.id);
            } else if (this.openKey === 'data') {
                this.loadData(option.id);
            } else if (this.openKey === 'compliance') {
                this.loadCompliance(option.id);
            } else {
                this.load(option.id);
            }
        },

        async load(elementId) {
            this.answeredKey = 'impact';
            this.centreId = elementId;
            this._loadSeq = (this._loadSeq || 0) + 1;
            var seq = this._loadSeq;
            this.busy = true;
            this.state = 'loading';
            this.notComputed = false;
            this.stale = false;
            this.withheld = false;
            this.syncDrawer();
            try {
                var payload = await Intelligence.fetchImpact(elementId, { maxDepth: 3, includeDerived: true });
                if (seq !== this._loadSeq) return;
                this.rows = Intelligence.buildRows(payload, elementId);
                var answer = Intelligence.answerState(payload, this.rows, true);
                this.notComputed = answer.notComputed;
                this.stale = answer.stale;
                this.withheld = answer.withheld;
                this.staleNotice = answer.notice;
                this.state = this.rows.length ? 'ready' : 'empty';
                var name = Intelligence.nameOf(payload.elements || {}, elementId) || this.term.trim();
                this.statusText = Intelligence.connectionsText(this.rows.length, name, answer.withheld);
            } catch (err) {
                if (seq !== this._loadSeq) return;
                this.rows = [];
                this.state = 'error';
            }
            this.busy = false;
            this.syncDrawer();
            var self = this;
            this.$nextTick(function () {
                Intelligence.refreshIcons();
                if (self.state === 'ready' || self.state === 'empty') {
                    Intelligence.showResults(self.$refs.resultsHeading);
                }
            });
        },

        /* L6 counterpart of load(). No provenance-drawer sync -- the drawer
           reads impact-shaped detail (detailOf()/row.key); risk cards do not
           open it. No stale/withheld notices either: those describe the
           derivation engine's own staleness, a concept this endpoint does
           not surface at the top level (each risk's own affectedSummary
           carries its blast radius's derivation_state if a caller needs it). */
        async loadRisk(elementId) {
            this.answeredKey = 'risk';
            this.riskCentreId = elementId;
            this._riskLoadSeq = (this._riskLoadSeq || 0) + 1;
            var seq = this._riskLoadSeq;
            this.riskBusy = true;
            this.riskState = 'loading';
            try {
                var payload = await Intelligence.fetchRisk(elementId, { maxDepth: 3, includeDerived: true });
                if (seq !== this._riskLoadSeq) return;
                this.risks = Intelligence.buildRisks(payload);
                this.riskState = this.risks.length ? 'ready' : 'empty';
            } catch (err) {
                if (seq !== this._riskLoadSeq) return;
                this.risks = [];
                this.riskState = 'error';
            }
            this.riskBusy = false;
            var self = this;
            this.$nextTick(function () {
                Intelligence.refreshIcons();
                if (self.riskState === 'ready' || self.riskState === 'empty') {
                    Intelligence.showResults(self.$refs.riskResultsHeading);
                }
            });
        },

        /* L3: no rows, no drawer -- just resolves whether a deep link exists
           for the picked element and holds the id to build it. "empty" here
           means the element genuinely is not an ApplicationComponent (the
           honest no_application_component reason), not a fetch failure. */
        async loadPortfolio(elementId) {
            this.answeredKey = 'portfolio';
            this.portfolioComponentId = null;
            this._portfolioLoadSeq = (this._portfolioLoadSeq || 0) + 1;
            var seq = this._portfolioLoadSeq;
            this.portfolioBusy = true;
            this.portfolioState = 'loading';
            try {
                var payload = await Intelligence.fetchPortfolioComponent(elementId);
                if (seq !== this._portfolioLoadSeq) return;
                if (payload.application_component_id) {
                    this.portfolioComponentId = payload.application_component_id;
                    this.portfolioState = 'ready';
                } else {
                    this.portfolioState = 'empty';
                }
            } catch (err) {
                if (seq !== this._portfolioLoadSeq) return;
                this.portfolioState = 'error';
            }
            this.portfolioBusy = false;
            var self = this;
            this.$nextTick(function () {
                if (self.portfolioState === 'ready' || self.portfolioState === 'empty') {
                    Intelligence.showResults(self.$refs.portfolioResultsHeading);
                }
            });
        },

        /* L5 counterpart of loadRisk(). No provenance-drawer sync, same
           reasoning as risk cards. costVariancePct/costReason on each row
           already carry the not_costed distinction from the server --
           this method does not recompute or guess a variance. */
        async loadProgramme(elementId) {
            this.answeredKey = 'programme';
            this.programmeCentreId = elementId;
            this._programmeLoadSeq = (this._programmeLoadSeq || 0) + 1;
            var seq = this._programmeLoadSeq;
            this.programmeBusy = true;
            this.programmeState = 'loading';
            try {
                var payload = await Intelligence.fetchProgramme(elementId, { maxDepth: 3, includeDerived: true });
                if (seq !== this._programmeLoadSeq) return;
                this.workPackages = Intelligence.buildWorkPackages(payload);
                this.programmeState = this.workPackages.length ? 'ready' : 'empty';
            } catch (err) {
                if (seq !== this._programmeLoadSeq) return;
                this.workPackages = [];
                this.programmeState = 'error';
            }
            this.programmeBusy = false;
            var self = this;
            this.$nextTick(function () {
                Intelligence.refreshIcons();
                if (self.programmeState === 'ready' || self.programmeState === 'empty') {
                    Intelligence.showResults(self.$refs.programmeResultsHeading);
                }
            });
        },

        /* L2 counterpart of loadProgramme(). No provenance-drawer sync, same
           reasoning as risk/programme cards. budgetVariancePct/budgetReason
           on each row already carry the not-costed distinction from the
           server -- this method does not recompute or guess a variance. */
        async loadStrategy(elementId) {
            this.answeredKey = 'strategy';
            this.strategyCentreId = elementId;
            this._strategyLoadSeq = (this._strategyLoadSeq || 0) + 1;
            var seq = this._strategyLoadSeq;
            this.strategyBusy = true;
            this.strategyState = 'loading';
            try {
                var payload = await Intelligence.fetchStrategy(elementId, { maxDepth: 3, includeDerived: true });
                if (seq !== this._strategyLoadSeq) return;
                this.initiatives = Intelligence.buildInitiatives(payload);
                this.strategyState = this.initiatives.length ? 'ready' : 'empty';
            } catch (err) {
                if (seq !== this._strategyLoadSeq) return;
                this.initiatives = [];
                this.strategyState = 'error';
            }
            this.strategyBusy = false;
            var self = this;
            this.$nextTick(function () {
                Intelligence.refreshIcons();
                if (self.strategyState === 'ready' || self.strategyState === 'empty') {
                    Intelligence.showResults(self.$refs.strategyResultsHeading);
                }
            });
        },

        /* L4 counterpart of loadStrategy(). No provenance-drawer sync, no
           blast-radius rows -- this lens is a pure ownership lookup, not a
           traversal, so there is nothing to sync the drawer against. */
        async loadAccountability(elementId) {
            this.answeredKey = 'accountability';
            this.accountabilityCentreId = elementId;
            this._accountabilityLoadSeq = (this._accountabilityLoadSeq || 0) + 1;
            var seq = this._accountabilityLoadSeq;
            this.accountabilityBusy = true;
            this.accountabilityState = 'loading';
            try {
                var payload = await Intelligence.fetchAccountability(elementId);
                if (seq !== this._accountabilityLoadSeq) return;
                this.owners = Intelligence.buildOwners(payload);
                this.accountabilityState = this.owners.length ? 'ready' : 'empty';
            } catch (err) {
                if (seq !== this._accountabilityLoadSeq) return;
                this.owners = [];
                this.accountabilityState = 'error';
            }
            this.accountabilityBusy = false;
            var self = this;
            this.$nextTick(function () {
                Intelligence.refreshIcons();
                if (self.accountabilityState === 'ready' || self.accountabilityState === 'empty') {
                    Intelligence.showResults(self.$refs.accountabilityResultsHeading);
                }
            });
        },

        /* L7: what data the element holds or produces and where it flows. No
           provenance-drawer sync -- like L4 this is a lookup, not a traversal. */
        async loadData(elementId) {
            this.answeredKey = 'data';
            this._dataLoadSeq = (this._dataLoadSeq || 0) + 1;
            var seq = this._dataLoadSeq;
            this.dataBusy = true;
            this.dataState = 'loading';
            try {
                var payload = await Intelligence.fetchData(elementId);
                if (seq !== this._dataLoadSeq) return;
                this.dataObjects = Intelligence.buildDataObjects(payload);
                this.dataFlows = Intelligence.buildFlows(payload);
                this.dataState = (this.dataObjects.length || this.dataFlows.length) ? 'ready' : 'empty';
            } catch (err) {
                if (seq !== this._dataLoadSeq) return;
                this.dataObjects = [];
                this.dataFlows = [];
                this.dataState = 'error';
            }
            this.dataBusy = false;
            var self = this;
            this.$nextTick(function () {
                Intelligence.refreshIcons();
                if (self.dataState === 'ready' || self.dataState === 'empty') {
                    Intelligence.showResults(self.$refs.dataResultsHeading);
                }
            });
        },

        /* Compliance (under L6): a lookup, no provenance-drawer sync. */
        async loadCompliance(elementId) {
            this.answeredKey = 'compliance';
            this._complianceLoadSeq = (this._complianceLoadSeq || 0) + 1;
            var seq = this._complianceLoadSeq;
            this.complianceBusy = true;
            this.complianceState = 'loading';
            try {
                var payload = await Intelligence.fetchCompliance(elementId);
                if (seq !== this._complianceLoadSeq) return;
                this.controls = Intelligence.buildControls(payload);
                this.violations = Intelligence.buildViolations(payload);
                this.lastScanAt = payload.last_scan_at || null;
                this.complianceState = (this.controls.length || this.violations.length) ? 'ready' : 'empty';
            } catch (err) {
                if (seq !== this._complianceLoadSeq) return;
                this.controls = [];
                this.violations = [];
                this.lastScanAt = null;
                this.complianceState = 'error';
            }
            this.complianceBusy = false;
            var self = this;
            this.$nextTick(function () {
                Intelligence.refreshIcons();
                if (self.complianceState === 'ready' || self.complianceState === 'empty') {
                    Intelligence.showResults(self.$refs.complianceResultsHeading);
                }
            });
        },

        async recomputeNow() {
            this.recomputing = true;
            this.recomputeLine = '';
            this.recomputeFailed = false;
            try {
                await Intelligence.recompute();
            } catch (err) {
                this.recomputing = false;
                if (Intelligence.failureStatus(err) === 409) {
                    this.recomputeLine = Intelligence.BUSY_LINE;
                } else {
                    this.recomputeLine = Intelligence.ERROR_LINE;
                    this.recomputeFailed = true;
                }
                return;
            }
            this.recomputing = false;
            await this.load(this.centreId);
            Intelligence.keepPlace(this.$refs.resultsHeading, this.stale || this.notComputed);
        }
    });
}
window.askSurface = askSurface;
