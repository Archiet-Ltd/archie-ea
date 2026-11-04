/**
 * Currency Manager - Frontend Currency Handling System
 *
 * This module provides comprehensive currency formatting and management
 * for the frontend, matching the backend CurrencyService functionality.
 */

class CurrencyManager {
    constructor(config = null) {
        // H-04: the org's currency code (config.CurrencyConfig.get_org_currency_code,
        // injected via window.__APP_CONFIG__.currency in partials/_head.html) is the
        // default here — NOT a hardcoded 'GBP' — so this matches whatever the server
        // rendered for the same organisation.
        let orgCurrency = (window.__APP_CONFIG__ && window.__APP_CONFIG__.currency) || 'GBP';

        // Default configuration - can be overridden with server config
        this.defaultConfig = config || {
            defaultCurrency: orgCurrency,
            supportedCurrencies: {
                'GBP': {
                    symbol: '£',
                    code: 'GBP',
                    position: 'prefix',
                    decimalPlaces: 2,
                    thousandsSeparator: ',',
                    name: 'British Pound Sterling'
                },
                'USD': {
                    symbol: '$',
                    code: 'USD',
                    position: 'prefix',
                    decimalPlaces: 2,
                    thousandsSeparator: ',',
                    name: 'United States Dollar'
                },
                'EUR': {
                    symbol: '€',
                    code: 'EUR',
                    position: 'prefix',
                    decimalPlaces: 2,
                    thousandsSeparator: ',',
                    name: 'Euro'
                },
                'JPY': {
                    symbol: '¥',
                    code: 'JPY',
                    position: 'prefix',
                    decimalPlaces: 0,
                    thousandsSeparator: ',',
                    name: 'Japanese Yen'
                },
                'CAD': {
                    symbol: 'C$',
                    code: 'CAD',
                    position: 'prefix',
                    decimalPlaces: 2,
                    thousandsSeparator: ',',
                    name: 'Canadian Dollar'
                },
                'AUD': {
                    symbol: 'A$',
                    code: 'AUD',
                    position: 'prefix',
                    decimalPlaces: 2,
                    thousandsSeparator: ',',
                    name: 'Australian Dollar'
                }
            }
        };

        this.currentCurrency = this.defaultConfig.defaultCurrency;
        this.currentCurrencyConfig = this.defaultConfig.supportedCurrencies[this.currentCurrency];

        // Initialize event listeners
        this.initEventListeners();

        // Load saved currency preference
        this.loadCurrencyPreference();
    }

    /**
     * Format amount with proper currency symbol and formatting
     * @param {number|string} amount - The monetary amount to format
     * @param {string} currencyCode - Currency code (optional, defaults to current)
     * @returns {string} Formatted currency string
     */
    format(amount, currencyCode = null) {
        const currency = currencyCode ? this.defaultConfig.supportedCurrencies[currencyCode] : this.currentCurrencyConfig;

        if (!currency) {
            throw new Error(`Unsupported currency: ${currencyCode}`);
        }

        // Convert to number if string
        const numAmount = typeof amount === 'string' ? parseFloat(amount) : amount;

        if (isNaN(numAmount)) {
            throw new Error(`Invalid amount: ${amount}`);
        }

        // Format with proper decimal places
        const formattedAmount = numAmount.toLocaleString('en-US', {
            minimumFractionDigits: currency.decimalPlaces,
            maximumFractionDigits: currency.decimalPlaces
        });

        // Apply thousands separator if needed
        let finalAmount = formattedAmount;
        if (currency.thousandsSeparator !== ',') {
            finalAmount = formattedAmount.replace(/,/g, currency.thousandsSeparator);
        }

        // Add symbol in correct position
        return currency.position === 'prefix'
            ? `${currency.symbol}${finalAmount}`
            : `${finalAmount}${currency.symbol}`;
    }

    /**
     * Format amount with the currency CODE shown alongside it (e.g. "GBP £1,234")
     * — H-04 acceptance criteria: no screen should show a currency figure with no
     * indication of which currency it is.
     * @param {number|string} amount
     * @param {string} currencyCode
     * @returns {string}
     */
    formatWithCode(amount, currencyCode = null) {
        const currency = currencyCode ? this.defaultConfig.supportedCurrencies[currencyCode] : this.currentCurrencyConfig;
        return (currency ? currency.code : '') + ' ' + this.format(amount, currencyCode);
    }

    /**
     * Get just the currency symbol
     * @param {string} currencyCode - Currency code (optional)
     * @returns {string} Currency symbol
     */
    getSymbol(currencyCode = null) {
        const currency = currencyCode ? this.defaultConfig.supportedCurrencies[currencyCode] : this.currentCurrencyConfig;
        return currency ? currency.symbol : '£';
    }

    /**
     * Get the full currency name
     * @param {string} currencyCode - Currency code (optional)
     * @returns {string} Currency name
     */
    getCurrencyName(currencyCode = null) {
        const currency = currencyCode ? this.defaultConfig.supportedCurrencies[currencyCode] : this.currentCurrencyConfig;
        return currency ? currency.name : '';
    }

    /**
     * Check if currency is supported
     * @param {string} currencyCode - Currency code to check
     * @returns {boolean} True if supported
     */
    isSupported(currencyCode) {
        return currencyCode in this.defaultConfig.supportedCurrencies;
    }

    /**
     * Get all supported currencies
     * @returns {object} Supported currencies object
     */
    getSupportedCurrencies() {
        return { ...this.defaultConfig.supportedCurrencies };
    }

    /**
     * Get all supported currency codes
     * @returns {array} Array of currency codes
     */
    getSupportedCurrencyCodes() {
        return Object.keys(this.defaultConfig.supportedCurrencies);
    }

    /**
     * Set current currency and update all currency elements
     * @param {string} currencyCode - New currency code
     */
    setCurrency(currencyCode) {
        if (!this.isSupported(currencyCode)) {
            throw new Error(`Unsupported currency: ${currencyCode}`);
        }

        this.currentCurrency = currencyCode;
        this.currentCurrencyConfig = this.defaultConfig.supportedCurrencies[currencyCode];

        // Save preference
        this.saveCurrencyPreference();

        // Update all currency elements
        this.updateAllCurrencyElements();

        // Trigger change event
        this.dispatchCurrencyChangeEvent();
    }

    /**
     * Parse numeric amount from formatted currency string
     * @param {string} amountString - Formatted currency string
     * @param {string} currencyCode - Currency code for context
     * @returns {number} Numeric amount
     */
    parseAmount(amountString, currencyCode = null) {
        const currency = currencyCode ? this.defaultConfig.supportedCurrencies[currencyCode] : this.currentCurrencyConfig;

        if (!currency) {
            throw new Error(`Unsupported currency: ${currencyCode}`);
        }

        // Remove currency symbol
        let cleanString = amountString.replace(currency.symbol, '');

        // Remove thousands separator
        cleanString = cleanString.replace(new RegExp(`\\${currency.thousandsSeparator}`, 'g'), '');

        // Convert to number
        const result = parseFloat(cleanString);
        if (isNaN(result)) {
            throw new Error(`Cannot parse amount from: ${amountString}`);
        }

        return result;
    }

    /**
     * Format currency data for API responses
     * @param {number|string} amount - The monetary amount
     * @param {string} currencyCode - Currency code (optional)
     * @returns {object} Formatted currency data object
     */
    formatForAPI(amount, currencyCode = null) {
        const currency = currencyCode ? this.defaultConfig.supportedCurrencies[currencyCode] : this.currentCurrencyConfig;
        const numAmount = typeof amount === 'string' ? parseFloat(amount) : amount;

        return {
            amount: numAmount,
            currency_code: currency.code,
            formatted_amount: this.format(amount, currencyCode),
            symbol: currency.symbol,
            name: currency.name
        };
    }

    /**
     * Update all elements with currency formatting
     */
    updateAllCurrencyElements() {
        // Update elements with data-currency attribute
        document.querySelectorAll('[data-currency]').forEach(element => {
            const amount = parseFloat(element.dataset.amount);
            const currencyCode = element.dataset.currencyCode || this.currentCurrency;

            if (!isNaN(amount)) {
                element.textContent = this.format(amount, currencyCode);
            }
        });

        // Update elements with data-currency-symbol attribute
        document.querySelectorAll('[data-currency-symbol]').forEach(element => {
            const currencyCode = element.dataset.currencyCode || this.currentCurrency;
            element.textContent = this.getSymbol(currencyCode);
        });

        // Update currency display elements
        document.querySelectorAll('[data-currency-display]').forEach(element => {
            const displayType = element.dataset.currencyDisplay || 'symbol';
            const currencyCode = element.dataset.currencyCode || this.currentCurrency;

            switch (displayType) {
                case 'symbol':
                    element.textContent = this.getSymbol(currencyCode);
                    break;
                case 'code':
                    element.textContent = currencyCode;
                    break;
                case 'name':
                    element.textContent = this.getCurrencyName(currencyCode);
                    break;
            }
        });
    }

    /**
     * Initialize event listeners for dynamic content
     */
    initEventListeners() {
        // Listen for currency change events — only update on explicit currency change,
        // NOT on every DOM mutation (that causes a freeze during Alpine hydration).
        document.addEventListener('currency:change', (event) => {
            this.updateAllCurrencyElements();
        });

        // MutationObserver intentionally removed: observing subtree childList mutations
        // fires updateAllCurrencyElements() on every Alpine/Lucide DOM write, creating
        // a mutation storm that freezes the browser. Currency elements are updated
        // explicitly via the currency:change event and the DOMContentLoaded handler.
    }

    /**
     * Save currency preference to localStorage
     */
    saveCurrencyPreference() {
        try {
            // Private-mode browsers throw on localStorage writes; the preference just
            // won't persist across sessions, which is harmless.
            localStorage.setItem('preferred_currency', this.currentCurrency);
        } catch (e) { /* swallow-ok: localStorage writes throw in private mode; the currency the user picked is already applied, it just will not persist across sessions */
        }
    }

    /**
     * Load currency preference from localStorage
     */
    loadCurrencyPreference() {
        try {
            // Private-mode browsers throw on localStorage reads; falls back to the default currency.
            const saved = localStorage.getItem('preferred_currency');
            if (saved && this.isSupported(saved)) {
                this.setCurrency(saved);
            }
        } catch (e) { /* swallow-ok: localStorage reads throw in private mode; the app falls back to the default currency, which is a working state and not an error the user can act on */
        }
    }

    /**
     * Dispatch currency change event
     */
    dispatchCurrencyChangeEvent() {
        const event = new CustomEvent('currency:change', {
            detail: {
                oldCurrency: this.currentCurrency,
                newCurrency: this.currentCurrency,
                currencyConfig: this.currentCurrencyConfig
            }
        });
        document.dispatchEvent(event);
    }

    /**
     * Validate currency code format
     * @param {string} currencyCode - Currency code to validate
     * @returns {boolean} True if valid format and supported
     */
    validateCurrencyCode(currencyCode) {
        return typeof currencyCode === 'string' &&
               currencyCode.length === 3 &&
               this.isSupported(currencyCode);
    }

    /**
     * Create currency selector dropdown HTML
     * @param {string} selectedCurrency - Currently selected currency
     * @param {string} cssClass - CSS class for the select element
     * @returns {string} HTML string for currency selector
     */
    createCurrencySelector(selectedCurrency = null, cssClass = 'currency-selector') {
        const current = selectedCurrency || this.currentCurrency;
        let options = '';

        Object.entries(this.defaultConfig.supportedCurrencies).forEach(([code, config]) => {
            const selected = code === current ? 'selected' : '';
            options += `<option value="${code}" ${selected}>${config.symbol} ${config.name} (${code})</option>`;
        });

        return `<select class="${cssClass}" data-currency-selector>${options}</select>`;
    }
}

// Global instance, created as soon as this script runs so that Alpine
// expressions evaluated before DOMContentLoaded always find it.
window.currencyManager = new CurrencyManager();

// Auto-initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Initialize any currency selectors
    document.querySelectorAll('[data-currency-selector]').forEach(selector => {
        selector.addEventListener('change', (event) => {
            const newCurrency = event.target.value;
            if (window.currencyManager.isSupported(newCurrency)) {
                window.currencyManager.setCurrency(newCurrency);
            }
        });
    });

    // Initial update of all currency elements
    window.currencyManager.updateAllCurrencyElements();
});

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = CurrencyManager;
}
