/* The table alternative for a graph or chart (components/_graph_table_view.html).
 *
 * Reads the rows from the chart already on the page, when the table is opened:
 *   - a Chart.js chart on the <canvas> named by data-for, through Chart.getChart;
 *   - or rows a page registered with GraphTableView.register(id, fn), where fn()
 *     returns {columns: [..], rows: [[..], ..]}.
 * Nothing is fetched and nothing is drawn twice, so the table cannot disagree
 * with the picture.
 *
 * The table is a grid a keyboard can walk: one cell is in the tab order at a
 * time, the arrow keys move between cells, Home/End go to the ends of a row,
 * Ctrl+Home/Ctrl+End to the first and last cell, and Escape goes back to the
 * button that opened it. All text is written with textContent.
 *
 * Loaded by every include, so it guards against running twice; one click
 * listener on the document serves every table view on the page.
 */
(function (global) {
    'use strict';
    if (global.GraphTableView) return;

    var EMPTY = 'No data to show yet.';
    var MISSING = '\u2014';
    var CELL_CLASS = 'px-3 py-2 align-middle text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring';
    var HEAD_CLASS = 'h-10 px-3 text-left align-middle font-medium text-muted-foreground';
    var sources = {};

    function text(value) {
        if (value === null || value === undefined || value === '' || (typeof value === 'number' && isNaN(value))) return MISSING;
        if (typeof value === 'number') return value.toLocaleString();
        return String(value);
    }

    /* A Chart.js chart as columns and rows. Points that are objects (scatter,
       bubble) become one row per point; everything else is one row per label
       with one column per series. */
    function fromChart(chart) {
        var data = chart && chart.data ? chart.data : null;
        if (!data || !data.datasets) return null;
        var datasets = data.datasets;
        var labels = data.labels || [];
        var pointData = datasets.some(function (ds) {
            return (ds.data || []).some(function (p) { return p !== null && typeof p === 'object'; });
        });
        if (pointData) {
            var hasR = datasets.some(function (ds) {
                return (ds.data || []).some(function (p) { return p && typeof p === 'object' && p.r !== undefined; });
            });
            var columns = ['Series', 'X', 'Y'].concat(hasR ? ['Size'] : []);
            var rows = [];
            datasets.forEach(function (ds, d) {
                (ds.data || []).forEach(function (p, i) {
                    if (!p || typeof p !== 'object') return;
                    var name = p.label || labels[i] || ds.label || ('Series ' + (d + 1));
                    rows.push([name, p.x, p.y].concat(hasR ? [p.r] : []));
                });
            });
            return { columns: columns, rows: rows };
        }
        var count = labels.length;
        datasets.forEach(function (ds) { count = Math.max(count, (ds.data || []).length); });
        var cols = [''].concat(datasets.map(function (ds, d) { return ds.label || (datasets.length === 1 ? 'Value' : 'Series ' + (d + 1)); }));
        var out = [];
        for (var i = 0; i < count; i++) {
            out.push([labels[i] !== undefined ? labels[i] : 'Item ' + (i + 1)].concat(datasets.map(function (ds) {
                return ds.data ? ds.data[i] : null;
            })));
        }
        return { columns: cols, rows: out };
    }

    function readRows(id) {
        if (sources[id]) return sources[id]();
        var el = document.getElementById(id);
        if (el && global.Chart && typeof global.Chart.getChart === 'function') {
            return fromChart(global.Chart.getChart(el));
        }
        return null;
    }

    function captionFor(view) {
        var given = view.getAttribute('data-caption');
        if (given) return given;
        var el = document.getElementById(view.getAttribute('data-for'));
        return (el && el.getAttribute('aria-label')) || 'Chart data';
    }

    function cellsOf(table) {
        return Array.prototype.map.call(table.rows, function (row) {
            return Array.prototype.slice.call(row.cells);
        });
    }

    function moveTo(table, cell) {
        Array.prototype.forEach.call(table.querySelectorAll('[tabindex="0"]'), function (c) { c.setAttribute('tabindex', '-1'); });
        cell.setAttribute('tabindex', '0');
        cell.focus();
    }

    function onKey(event, table, toggle) {
        var cell = event.target.closest('th, td');
        if (!cell || !table.contains(cell)) return;
        var grid = cellsOf(table);
        var r = cell.parentElement.rowIndex;
        var c = cell.cellIndex;
        var next = null;
        switch (event.key) {
            case 'ArrowRight': next = grid[r][c + 1]; break;
            case 'ArrowLeft': next = grid[r][c - 1]; break;
            case 'ArrowDown': next = grid[r + 1] ? grid[r + 1][c] : null; break;
            case 'ArrowUp': next = grid[r - 1] ? grid[r - 1][c] : null; break;
            case 'Home': next = event.ctrlKey ? grid[0][0] : grid[r][0]; break;
            case 'End':
                next = event.ctrlKey ? grid[grid.length - 1][grid[grid.length - 1].length - 1] : grid[r][grid[r].length - 1];
                break;
            case 'Escape':
                event.preventDefault();
                toggle.focus();
                return;
            default: return;
        }
        event.preventDefault();
        if (next) moveTo(table, next);
    }

    function build(view) {
        var region = view.querySelector('[data-graph-table-region]');
        var toggle = view.querySelector('[data-graph-table-toggle]');
        while (region.firstChild) region.removeChild(region.firstChild);
        var data = readRows(view.getAttribute('data-for'));
        if (!data || !data.rows || !data.rows.length) {
            var empty = document.createElement('p');
            empty.className = 'px-3 py-2 text-sm text-muted-foreground';
            empty.setAttribute('data-graph-table-empty', '');
            empty.textContent = EMPTY;
            region.appendChild(empty);
            return;
        }
        var table = document.createElement('table');
        table.className = 'w-full text-sm';
        table.setAttribute('role', 'grid');
        table.setAttribute('aria-readonly', 'true');
        table.setAttribute('data-graph-table', '');
        var caption = document.createElement('caption');
        caption.className = 'px-3 py-2 text-left text-sm font-medium text-foreground';
        caption.textContent = captionFor(view);
        table.appendChild(caption);
        var head = document.createElement('thead');
        var headRow = document.createElement('tr');
        headRow.className = 'border-b border-border';
        data.columns.forEach(function (name, i) {
            var th = document.createElement('th');
            th.scope = 'col';
            th.className = HEAD_CLASS;
            th.setAttribute('tabindex', '-1');
            th.textContent = name || (i === 0 ? 'Item' : '');
            headRow.appendChild(th);
        });
        head.appendChild(headRow);
        table.appendChild(head);
        var body = document.createElement('tbody');
        data.rows.forEach(function (values) {
            var tr = document.createElement('tr');
            tr.className = 'border-b border-border';
            tr.setAttribute('data-graph-table-row', '');
            values.forEach(function (value, i) {
                var cell = document.createElement(i === 0 ? 'th' : 'td');
                if (i === 0) cell.scope = 'row';
                cell.className = CELL_CLASS + (i === 0 ? ' text-left text-sm font-medium normal-case' : ' tabular-nums');
                cell.setAttribute('tabindex', '-1');
                cell.textContent = text(value);
                tr.appendChild(cell);
            });
            body.appendChild(tr);
        });
        table.appendChild(body);
        table.rows[0].cells[0].setAttribute('tabindex', '0');
        table.addEventListener('keydown', function (event) { onKey(event, table, toggle); });
        table.addEventListener('click', function (event) {
            var cell = event.target.closest('th, td');
            if (cell) moveTo(table, cell);
        });
        region.appendChild(table);
    }

    /* One listener for the whole page, so a chart that appears later (inside
       a panel that opens, or a block the page draws after loading) works the
       same as one that was there from the start. */
    function onClick(event) {
        var toggle = event.target.closest ? event.target.closest('[data-graph-table-toggle]') : null;
        if (!toggle) return;
        var view = toggle.closest('[data-graph-table-view]');
        if (!view) return;
        var region = view.querySelector('[data-graph-table-region]');
        var label = view.querySelector('[data-graph-table-label]');
        var open = toggle.getAttribute('aria-expanded') !== 'true';
        if (open) build(view);
        region.hidden = !open;
        toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (label) label.textContent = open ? 'Hide table' : 'Show as table';
    }

    global.GraphTableView = {
        register: function (id, fn) { sources[id] = fn; },
        fromChart: fromChart
    };

    document.addEventListener('click', onClick);
})(window);
