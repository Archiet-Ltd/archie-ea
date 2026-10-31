/* The list table's behaviour (components/list_table.html): filter, Reset, column visibility, sorting, rows per page
   and paging, for every [data-slot="data-table"] on the page. A plain file so the content security policy allows it;
   the markup works without it (the first page is rendered, the rest hidden). Mirrors AdminTable in flask-basecoat's
   examples/ported/_admin.html. */
(function () {
  'use strict';

  function setup(root) {
    if (root.__dataTable) return;
    var body = root.querySelector('[data-table-body]');
    if (!body) return;
    var state = { page: 0, size: Number(root.getAttribute('data-page-size')) || 25, query: '' };
    var filter = root.querySelector('[data-table-filter]');
    var reset = root.querySelector('[data-table-reset]');

    function rows() { return Array.prototype.slice.call(body.querySelectorAll('[data-row]')); }
    function shown() { return rows().filter(function (r) { return !r.hasAttribute('data-filtered'); }); }
    function pages() { return Math.max(1, Math.ceil(shown().length / state.size)); }

    function render() {
      var list = shown(), total = pages();
      if (state.page > total - 1) state.page = total - 1;
      rows().forEach(function (r) { r.hidden = true; });
      list.forEach(function (r, i) { r.hidden = !(i >= state.page * state.size && i < (state.page + 1) * state.size); });
      var empty = body.querySelector('[data-table-empty]');
      if (empty) empty.hidden = list.length > 0;
      root.querySelectorAll('[data-table-pager]').forEach(function (b) {
        var p = b.getAttribute('data-table-pager');
        b.disabled = (p === 'first' || p === 'prev') ? state.page === 0 : state.page >= total - 1;
      });
      var label = root.querySelector('[data-table-page]');
      if (label) label.textContent = 'Page ' + (state.page + 1) + ' of ' + total;
      var count = root.querySelector('[data-table-count]');
      if (count) {
        var all = rows().length;
        count.textContent = (list.length === all ? all : list.length + ' of ' + all) + (all === 1 ? ' row' : ' rows');
      }
      if (reset) reset.hidden = !state.query;
    }

    function applyFilter() {
      var q = state.query;
      rows().forEach(function (r) {
        r.toggleAttribute('data-filtered', !!q && r.textContent.toLowerCase().indexOf(q) === -1);
      });
      state.page = 0;
      render();
    }

    function setColumn(index, visible) {
      root.querySelectorAll('[data-col="' + index + '"]').forEach(function (c) { c.hidden = !visible; });
      body.querySelectorAll('[data-row]').forEach(function (r) {
        var cell = r.children[index];
        if (cell) cell.hidden = !visible;
      });
    }

    function sortBy(index, dir) {
      var key = function (r) {
        var cell = r.children[index];
        if (!cell) return '';
        var v = cell.hasAttribute('data-sort') ? cell.getAttribute('data-sort') : cell.textContent.trim().toLowerCase();
        var n = Number(v);
        return v !== '' && !isNaN(n) ? n : v;
      };
      var list = rows();
      list.sort(function (a, b) {
        var x = key(a), y = key(b);
        if (typeof x !== typeof y) { x = String(x); y = String(y); }
        return (x < y ? -1 : x > y ? 1 : 0) * (dir === 'desc' ? -1 : 1);
      });
      var tail = body.querySelector('[data-table-empty]');
      list.forEach(function (r) { body.insertBefore(r, tail); });
      root.querySelectorAll('[data-table-sort-trigger]').forEach(function (t) {
        var active = t.getAttribute('data-table-sort-trigger') === String(index);
        t.querySelectorAll('[data-sort-icon]').forEach(function (i) { i.hidden = i.getAttribute('data-sort-icon') !== (active ? dir : 'none'); });
      });
      state.page = 0;
      render();
    }

    if (filter) {
      filter.addEventListener('input', function () { state.query = filter.value.trim().toLowerCase(); applyFilter(); });
    }
    if (reset) {
      reset.addEventListener('click', function () { if (filter) filter.value = ''; state.query = ''; applyFilter(); if (filter) filter.focus(); });
    }
    var size = root.querySelector('[data-table-size]');
    if (size) {
      size.addEventListener('change', function () { state.size = Number(size.value) || state.size; state.page = 0; render(); });
    }
    root.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-table-pager]');
      if (b && root.contains(b) && !b.disabled) {
        var p = b.getAttribute('data-table-pager');
        if (p === 'first') state.page = 0;
        else if (p === 'prev') state.page = Math.max(0, state.page - 1);
        else if (p === 'next') state.page = Math.min(pages() - 1, state.page + 1);
        else state.page = pages() - 1;
        render();
      }
    });
    // Menu contents may be moved out of the table, so these listen on the document and match the owner the markup names.
    var id = root.getAttribute('data-table-id');
    function owns(el) { return el && el.getAttribute && el.getAttribute('data-table-owner') === id; }
    document.addEventListener('click', function (e) {
      var s = e.target.closest && e.target.closest('[data-table-sort]');
      if (owns(s)) sortBy(Number(s.getAttribute('data-table-sort')), s.getAttribute('data-dir'));
    });
    document.addEventListener('basecoat:change', function (e) {
      var t = e.target;
      if (owns(t) && t.hasAttribute('data-table-toggle')) {
        setColumn(Number(t.getAttribute('data-table-toggle')), !!(e.detail && e.detail.checked));
      }
    });

    root.__dataTable = { render: render, state: state };
    render();
  }

  function init() { document.querySelectorAll('[data-slot="data-table"]').forEach(setup); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
