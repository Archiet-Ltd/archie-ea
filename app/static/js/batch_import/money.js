/**
 * Money placement for the batch import pages. The page sets
 * window.__BATCH_MONEY__ once, from the server (symbol, placement, decimals,
 * thousands separator of the organisation's currency); every figure painted by
 * script goes through BatchMoney so it reads exactly like the server-rendered ones.
 */
(function () {
    function config() {
        return window.__BATCH_MONEY__ || null;
    }

    function format(amount, options) {
        var cfg = config();
        var value = Number(amount);
        if (!cfg || !isFinite(value)) return '—';
        var signed = !!(options && options.signed);
        var decimals = (options && options.decimals != null) ? options.decimals : cfg.decimals;
        var abs = Math.abs(value);
        var parts = abs.toFixed(decimals).split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, cfg.thousands || ',');
        var body = parts.join('.');
        var text = cfg.position === 'prefix' ? cfg.symbol + body : body + cfg.symbol;
        if (value < 0) return '-' + text;
        return (signed && value > 0 ? '+' : '') + text;
    }

    window.BatchMoney = { format: format };
})();
