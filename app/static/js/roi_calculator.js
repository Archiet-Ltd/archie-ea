/**
 * ROI Calculator Utilities
 * 
 * Financial calculations and data persistence for investment ROI analysis.
 * Used by roi_calculator_modal.html and scenario_comparison_modal.html.
 */

/**
 * Calculate comprehensive ROI metrics for an investment
 * 
 * @param {number} investment - Initial investment amount ($)
 * @param {number} annualBenefit - Expected annual benefit ($)
 * @param {number} timeline - Implementation timeline (months)
 * @param {number} discountRate - Discount rate for NPV calculation (%)
 * @returns {Object} ROI metrics: { roi, paybackPeriod, npv, breakEvenDate }
 */
function calculateROI(investment, annualBenefit, timeline, discountRate) {
    // Validate inputs
    if (investment <= 0) {
        return {
            roi: null,
            paybackPeriod: null,
            npv: null,
            breakEvenDate: null,
            error: 'Investment must be greater than zero'
        };
    }

    // Calculate ROI percentage
    const years = timeline / 12;
    const totalBenefits = annualBenefit * years;
    const roi = ((totalBenefits - investment) / investment) * 100;

    // Calculate Payback Period (months)
    const monthlyBenefit = annualBenefit / 12;
    const paybackPeriod = monthlyBenefit > 0 ? investment / monthlyBenefit : null;

    // Calculate Net Present Value (NPV)
    const rate = discountRate / 100;
    let npv = -investment; // Start with negative investment
    
    for (let year = 1; year <= Math.ceil(years); year++) {
        const yearBenefit = year <= years ? annualBenefit : annualBenefit * (years % 1);
        npv += yearBenefit / Math.pow(1 + rate, year);
    }

    // Calculate Break-even Date
    const breakEvenDate = paybackPeriod !== null 
        ? new Date(Date.now() + paybackPeriod * 30 * 24 * 60 * 60 * 1000)
        : null;

    return {
        roi: roi,
        paybackPeriod: paybackPeriod,
        npv: npv,
        breakEvenDate: breakEvenDate
    };
}

/**
 * Save custom ROI scenario to localStorage
 * 
 * @param {number} capabilityId - Capability ID
 * @param {Object} inputs - Custom inputs { investment, timeline, annualBenefit, discountRate }
 */
function saveScenario(capabilityId, inputs) {
    const key = `roi_scenario_${capabilityId}`;
    const data = {
        ...inputs,
        savedAt: new Date().toISOString()
    };
    localStorage.setItem(key, JSON.stringify(data));
}

/**
 * Load saved ROI scenario from localStorage
 * 
 * @param {number} capabilityId - Capability ID
 * @returns {Object|null} Saved inputs or null if not found
 */
function loadScenario(capabilityId) {
    const key = `roi_scenario_${capabilityId}`;
    const data = localStorage.getItem(key);
    return data ? JSON.parse(data) : null;
}

/**
 * Delete saved ROI scenario from localStorage
 * 
 * @param {number} capabilityId - Capability ID
 */
function deleteScenario(capabilityId) {
    const key = `roi_scenario_${capabilityId}`;
    localStorage.removeItem(key);
}

/**
 * Get all saved scenarios from localStorage
 * 
 * @returns {Array} Array of { capabilityId, inputs } objects
 */
function getAllScenarios() {
    const scenarios = [];
    for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key && key.startsWith('roi_scenario_')) {
            const capabilityId = parseInt(key.replace('roi_scenario_', ''));
            const data = JSON.parse(localStorage.getItem(key));
            scenarios.push({ capabilityId, inputs: data });
        }
    }
    return scenarios;
}

/**
 * Format currency value
 * 
 * @param {number} value - Numeric value
 * @returns {string} Formatted currency string
 */
function formatCurrency(value) {
    if (value === null || value === undefined) return 'N/A';
    const symbol = currencySymbol();
    const digits = Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 });
    return (value < 0 ? '-' : '') + symbol + digits;
}

/**
 * The organisation's currency symbol, from the one currency manager.
 *
 * @returns {string} Currency symbol
 */
function currencySymbol() {
    return window.currencyManager ? window.currencyManager.getSymbol() : '';
}

/**
 * Format a value in millions with the organisation's currency symbol
 *
 * @param {number|string} millions - Value already expressed in millions
 * @returns {string} For example "£0.1M"
 */
function formatCurrencyMillions(millions) {
    return currencySymbol() + millions + 'M';
}

/**
 * Format percentage value
 * 
 * @param {number} value - Numeric percentage
 * @param {number} decimals - Decimal places (default: 2)
 * @returns {string} Formatted percentage string
 */
function formatPercentage(value, decimals = 2) {
    if (value === null || value === undefined) return 'N/A';
    return `${value.toFixed(decimals)}%`;
}

/**
 * Format months as "X years Y months" or "Y months"
 * 
 * @param {number} months - Number of months
 * @returns {string} Formatted duration string
 */
function formatDuration(months) {
    if (months === null || months === undefined) return 'N/A';
    
    const years = Math.floor(months / 12);
    const remainingMonths = Math.round(months % 12);
    
    if (years > 0 && remainingMonths > 0) {
        return `${years} year${years > 1 ? 's' : ''} ${remainingMonths} month${remainingMonths > 1 ? 's' : ''}`;
    } else if (years > 0) {
        return `${years} year${years > 1 ? 's' : ''}`;
    } else {
        return `${remainingMonths} month${remainingMonths > 1 ? 's' : ''}`;
    }
}

/**
 * Format date as "MMM DD, YYYY"
 * 
 * @param {Date} date - Date object
 * @returns {string} Formatted date string
 */
function formatDate(date) {
    if (!date) return 'N/A';
    return new Intl.DateTimeFormat('en-US', { 
        year: 'numeric', 
        month: 'short', 
        day: 'numeric' 
    }).format(date);
}

/**
 * Get ROI badge color based on percentage
 * 
 * @param {number} roi - ROI percentage
 * @returns {string} Color class (red/yellow/green)
 */
function getROIBadgeColor(roi) {
    if (roi === null || roi === undefined) return 'gray';
    if (roi < 0) return 'red';
    if (roi < 20) return 'yellow';
    return 'green';
}

/**
 * Export comparison data to CSV
 * 
 * @param {Array} scenarios - Array of scenario objects
 * @param {string} filename - CSV filename
 */
function exportToCSV(scenarios, filename = 'scenario_comparison.csv') {
    if (!scenarios || scenarios.length === 0) {
        if (window.Platform && Platform.toast) {
            Platform.toast.warning('There are no scenarios to export.');
        }
        return;
    }

    // CSV headers
    const headers = [
        'Capability Name',
        'Initial Investment',
        'Annual Benefit',
        'ROI %',
        'Payback Period (months)',
        'NPV',
        'Strategic Score',
        'Risk Score',
        'Priority'
    ];

    // CSV rows
    const rows = scenarios.map(scenario => [
        scenario.name || 'Unknown',
        scenario.investment ?? 'Not estimated',
        scenario.annualBenefit ?? 'Not recorded',
        scenario.roi?.toFixed(2) || 'N/A',
        scenario.paybackPeriod?.toFixed(1) || 'N/A',
        scenario.npv?.toFixed(0) || 'N/A',
        scenario.strategic_score || 0,
        scenario.risk_score || 0,
        scenario.priority || 'Not scored'
    ]);

    // Build CSV content
    const csvContent = [
        headers.join(','),
        ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n');

    // Trigger download
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', filename);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}
