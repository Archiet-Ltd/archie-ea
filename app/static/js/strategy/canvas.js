/**
 * Alpine component for a strategy framework canvas (SWOT, PESTLE).
 *
 * "Draft from my model" asks the engine for cited proposals per box; the
 * person accepts, edits (by changing the proposal text before accepting) or
 * rejects each. Accepting writes an Assessment element and shows the point in
 * its box without a reload; rejecting discards the proposal and writes
 * nothing. Server-rendered accepted points (from a previous visit) stay in the
 * page markup; points accepted this session are added to `accepted[box]`.
 */
document.addEventListener('alpine:init', () => {
  Alpine.data('strategyCanvas', (config) => ({
    framework: config.framework,
    draftUrl: config.draftUrl,
    acceptUrl: config.acceptUrl,
    boxKeys: config.boxKeys || [],

    // Points accepted this session (not yet on the server-rendered list).
    accepted: {},
    // Proposals drafted, per box, awaiting accept/reject.
    proposals: {},
    // How many points each box already had server-side on load.
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
            citations: p.citations || []
          }));
        });
      } catch (e) {
        this.draftError = e.message || 'Draft failed';
        throw e;
      } finally {
        this.drafting = false;
      }
    },

    async acceptProposal(boxKey, index) {
      const proposal = this.proposals[boxKey][index];
      if (!proposal) return;
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
      // Remove the accepted proposal from the pending list.
      this.proposals[boxKey].splice(index, 1);
    },

    rejectProposal(boxKey, index) {
      // Reject is a discard: the proposal was never written, so removing it
      // from the pending list is all there is to do.
      this.proposals[boxKey].splice(index, 1);
    }
  }));
});
