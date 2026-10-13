// Person picker for the programme-structure preview (R1-06).
// One instance per field (programme owner, each workstream lead). A debounced
// search against the minimal, rate-limited, organisation-scoped lead-search
// endpoint; the chosen id travels in a hidden input, never free text
// (DESIGN.md entity-field rule). Top-level window factory: the CSP-safe
// Alpine evaluator resolves x-data names against window.
function personPicker(searchUrl, label) {
  return {
    searchUrl: searchUrl,
    label: label || '',
    query: '',
    results: [],
    selectedId: '',
    open: false,
    searching: false,
    message: '',

    async search() {
      if (this.selectedId) this.selectedId = '';
      const query = this.query.trim();
      if (query.length < 2) {
        this.results = [];
        this.open = false;
        this.message = '';
        return;
      }
      this.searching = true;
      this.open = true;
      this.message = '';
      try {
        const body = await Platform.fetch.get(this.searchUrl, { q: query }, { silent: true });
        const rows = body && body.data ? body.data : body;
        this.results = Array.isArray(rows && rows.people) ? rows.people : [];
        if (!this.results.length) this.message = 'No people match.';
      } catch (error) {
        this.results = [];
        this.message = error.message || 'People could not be searched.';
      } finally {
        this.searching = false;
      }
    },

    choose(person) {
      this.selectedId = String(person.id);
      this.query = person.display_name;
      this.open = false;
    },

    close() {
      this.open = false;
    },
  };
}
window.personPicker = personPicker;

// Deliverable element credit and completion (R1-07). Config arrives as a
// server-rendered JSON data attribute; every write is a Platform.fetch call
// (CSRF header injected) and the page reloads on success so what the user
// sees after a change is what was persisted.
function deliverableCredit() {
  return {
    journeyId: 0,
    deliverableId: 0,
    declared: [],
    mode: '',
    query: '',
    results: [],
    selectedId: '',
    newType: '',
    newName: '',
    reason: '',
    error: '',
    busy: false,
    searching: false,

    init() {
      const raw = this.$root.dataset.config || '{}';
      let config = {};
      try { config = JSON.parse(raw); } catch (e) { config = {}; }
      this.journeyId = config.journey_id;
      this.deliverableId = config.deliverable_id;
      this.declared = config.declared || [];
      this.newType = this.declared.length ? this.declared[0].key : '';
    },

    base() {
      return '/architecture-journey/work/' + this.journeyId + '/deliverables/' + this.deliverableId;
    },

    showExisting() { this.mode = 'existing'; this.error = ''; },
    showNew() { this.mode = 'new'; this.error = ''; },
    closeForms() { this.mode = ''; this.error = ''; },

    async search() {
      this.selectedId = '';
      const q = this.query.trim();
      if (q.length < 2) { this.results = []; return; }
      this.searching = true;
      try {
        const body = await Platform.fetch.get(this.base() + '/element-search', { q: q }, { silent: true });
        const data = body && body.data ? body.data : body;
        this.results = Array.isArray(data && data.elements) ? data.elements : [];
      } catch (error) {
        this.results = [];
        this.error = error.message || 'Elements could not be searched.';
      } finally {
        this.searching = false;
      }
    },

    choose(element) {
      this.selectedId = String(element.id);
      this.query = element.name;
      this.results = [];
    },

    async write(request) {
      if (this.busy) return;
      this.busy = true;
      this.error = '';
      try {
        await request();
        window.location.reload();
      } catch (error) {
        this.error = error.message || 'That change could not be saved.';
        this.busy = false;
      }
    },

    addExisting() {
      if (!this.selectedId) { this.error = 'Choose an element from the list.'; return; }
      return this.write(() => Platform.fetch.post(this.base() + '/elements', {
        element_id: parseInt(this.selectedId, 10),
        command_key: crypto.randomUUID(),
      }, { silent: true }));
    },

    addNew() {
      return this.write(() => Platform.fetch.post(this.base() + '/elements', {
        element_type: this.newType,
        name: this.newName,
        command_key: crypto.randomUUID(),
      }, { silent: true }));
    },

    removeElement(elementId) {
      return this.write(() => Platform.fetch.delete(this.base() + '/elements/' + elementId, { silent: true }));
    },

    complete() {
      return this.write(() => Platform.fetch.post(this.base() + '/complete', {
        reason: this.reason,
        command_key: crypto.randomUUID(),
      }, { silent: true }));
    },
  };
}
window.deliverableCredit = deliverableCredit;
