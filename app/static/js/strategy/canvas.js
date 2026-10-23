/**
 * Alpine component for the one strategy canvas view (every framework).
 *
 * "Draft from my model" asks the engine for cited proposals per box; the
 * person accepts, edits (by changing the proposal text before accepting) or
 * rejects each. Accepting writes an element and shows the point in its box
 * without a reload; rejecting discards the proposal and writes nothing.
 * Server-rendered points (from a previous visit) stay in the page markup;
 * points accepted this session are added to `accepted[box]`.
 *
 * Accept is guarded so a double-click cannot create two elements and a
 * rejected proposal cannot be removed by a stale index while a POST is in
 * flight: the proposal is removed by identity, not by position, and a failed
 * accept leaves the proposal on screen with its error, never silently.
 */
document.addEventListener('alpine:init', () => {
  Alpine.data('strategyCanvas', (config) => ({
    framework: config.framework,
    draftable: !!config.draftable,
    draftUrl: config.draftUrl,
    acceptUrl: config.acceptUrl,
    boxKeys: config.boxKeys || [],

    accepted: {},
    proposals: {},
    serverCounts: {},

    drafting: false,
    draftError: '',

    init() {
      const server = config.accepted || {};
      this.boxKeys.forEach((key) => {
        this.accepted[key] = [];
        this.proposals[key] = [];
        this.serverCounts[key] = (server[key] || []).length;
      });
    },

    boxCount(key) {
      return (this.serverCounts[key] || 0) + (this.accepted[key] || []).length;
    },

    isBoxEmpty(key) {
      return this.boxCount(key) === 0 && (this.proposals[key] || []).length === 0;
    },

    async draft() {
      if (!this.draftable || this.drafting) return;
      this.drafting = true;
      this.draftError = '';
      try {
        const json = await Platform.fetch.post(this.draftUrl, {});
        const data = json.data ?? json;
        const boxes = data.boxes || {};
        this.boxKeys.forEach((key) => {
          this.proposals[key] = (boxes[key] || []).map((p) => ({
            point: p.point,
            citation_ids: p.citation_ids || [],
            citations: p.citations || [],
            saving: false,
            error: ''
          }));
        });
      } catch (e) {
        // Shown in the panel; not re-thrown, so there is no unhandled rejection.
        this.draftError = (e && e.message) || 'The model could not be reached. Try again.';
      } finally {
        this.drafting = false;
      }
    },

    async acceptProposal(boxKey, index) {
      const proposal = this.proposals[boxKey][index];
      if (!proposal || proposal.saving) return;
      proposal.saving = true;
      proposal.error = '';
      try {
        const json = await Platform.fetch.post(this.acceptUrl, {
          box_key: boxKey,
          point: proposal.point,
          citations: proposal.citation_ids
        });
        const saved = json.data ?? json;
        this.accepted[boxKey].push({
          element_id: saved.element_id,
          point: saved.point,
          citations: saved.citations || []
        });
        // Remove the accepted proposal by identity, so a concurrent reject of
        // another item cannot drop the wrong one.
        const at = this.proposals[boxKey].indexOf(proposal);
        if (at !== -1) this.proposals[boxKey].splice(at, 1);
      } catch (e) {
        proposal.saving = false;
        proposal.error = (e && e.message) || 'Could not save this point. Try again.';
      }
    },

    rejectProposal(boxKey, index) {
      const proposal = this.proposals[boxKey][index];
      if (!proposal || proposal.saving) return;
      // Reject is a discard: the proposal was never written. Remove by
      // identity, not by a position that another in-flight accept may shift.
      const at = this.proposals[boxKey].indexOf(proposal);
      if (at !== -1) this.proposals[boxKey].splice(at, 1);
    }
  }));
});
