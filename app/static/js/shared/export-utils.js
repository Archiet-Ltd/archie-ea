/**
 * Image Export Utility
 * PNG/JPG export functionality using html2canvas
 *
 * Provides high-quality image export for dashboards, roadmaps, and visualizations
 */

class ExportManager {
    constructor() {
        this.isHtml2CanvasLoaded = false;
        this.loadHtml2Canvas();
    }

    /**
     * Load html2canvas library dynamically
     */
    async loadHtml2Canvas() {
        if (window.html2canvas) {
            this.isHtml2CanvasLoaded = true;
            return;
        }

        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = '/static/vendor/html2canvas.min.js';
            script.integrity = 'sha512-BNaRQnYJYiPSqHHDb58B0yaPfCu+Wgds8Gp/gU33kqBtgNS4tSPHuGibyoeqMV/TJlSKda6FXzoEyYGjTe+vXA==';
            script.crossOrigin = 'anonymous';
            script.onload = () => {
                this.isHtml2CanvasLoaded = true;
                resolve();
            };
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    /**
     * Ensure html2canvas is loaded
     */
    async ensureLoaded() {
        if (!this.isHtml2CanvasLoaded) {
            await this.loadHtml2Canvas();
        }
    }

    /**
     * Export element to PNG
     * @param {string} elementId - ID of element to export
     * @param {string} filename - Output filename (without extension)
     * @param {Object} options - Export options
     */
    async exportToPNG(elementId, filename = 'export', options = {}) {
        const {
            scale = 2,
            backgroundColor = getComputedStyle(document.body).backgroundColor,
            quality = 1.0,
            showToast = true
        } = options;

        try {
            await this.ensureLoaded();

            if (showToast) {
                window.toast?.loading('Generating PNG export...', { duration: 0 });
            }

            const element = document.getElementById(elementId);
            if (!element) {
                throw new Error(`Element with ID "${elementId}" not found`);
            }

            // Prepare element for export
            this.prepareElement(element);

            const canvas = await html2canvas(element, {
                scale: scale,
                backgroundColor: backgroundColor,
                useCORS: true,
                logging: false,
                allowTaint: true,
                foreignObjectRendering: true,
                imageTimeout: 15000,
                removeContainer: true
            });

            // Restore element
            this.restoreElement(element);

            // Download
            const link = document.createElement('a');
            link.download = `${filename}-${this.getTimestamp()}.png`;
            link.href = canvas.toDataURL('image/png', quality);
            link.click();

            if (showToast) {
                window.toast?.dismissAll();
                window.toast?.success('PNG exported successfully', {
                    description: `Downloaded as ${link.download}`
                });
            }

            return true;

        } catch (error) {

            if (showToast) {
                window.toast?.dismissAll();
                window.toast?.error('PNG Export Failed', {
                    description: error.message || 'Unable to generate PNG. Please try again.',
                    duration: 6000
                });
            }

            return false;
        }
    }

    /**
     * Export element to JPG
     * @param {string} elementId - ID of element to export
     * @param {string} filename - Output filename (without extension)
     * @param {Object} options - Export options
     */
    async exportToJPG(elementId, filename = 'export', options = {}) {
        const {
            scale = 2,
            backgroundColor = getComputedStyle(document.body).backgroundColor,
            quality = 0.95,
            showToast = true
        } = options;

        try {
            await this.ensureLoaded();

            if (showToast) {
                window.toast?.loading('Generating JPG export...', { duration: 0 });
            }

            const element = document.getElementById(elementId);
            if (!element) {
                throw new Error(`Element with ID "${elementId}" not found`);
            }

            // Prepare element for export
            this.prepareElement(element);

            const canvas = await html2canvas(element, {
                scale: scale,
                backgroundColor: backgroundColor,
                useCORS: true,
                logging: false,
                allowTaint: true,
                foreignObjectRendering: true,
                imageTimeout: 15000,
                removeContainer: true
            });

            // Restore element
            this.restoreElement(element);

            // Download
            const link = document.createElement('a');
            link.download = `${filename}-${this.getTimestamp()}.jpg`;
            link.href = canvas.toDataURL('image/jpeg', quality);
            link.click();

            if (showToast) {
                window.toast?.dismissAll();
                window.toast?.success('JPG exported successfully', {
                    description: `Downloaded as ${link.download}`
                });
            }

            return true;

        } catch (error) {

            if (showToast) {
                window.toast?.dismissAll();
                window.toast?.error('JPG Export Failed', {
                    description: error.message || 'Unable to generate JPG. Please try again.',
                    duration: 6000
                });
            }

            return false;
        }
    }

    /**
     * Prepare element for export (hide scrollbars, fix dimensions, etc.)
     */
    prepareElement(element) {
        // Store original styles
        element._originalOverflow = element.style.overflow;
        element._originalHeight = element.style.height;
        element._originalWidth = element.style.width;

        // Set styles for export
        element.style.overflow = 'visible';

        // If element is a scrollable container, expand it
        if (element.scrollHeight > element.clientHeight) {
            element.style.height = element.scrollHeight + 'px';
        }
    }

    /**
     * Restore element after export
     */
    restoreElement(element) {
        // Restore original styles
        if (element._originalOverflow !== undefined) {
            element.style.overflow = element._originalOverflow;
            delete element._originalOverflow;
        }
        if (element._originalHeight !== undefined) {
            element.style.height = element._originalHeight;
            delete element._originalHeight;
        }
        if (element._originalWidth !== undefined) {
            element.style.width = element._originalWidth;
            delete element._originalWidth;
        }
    }

    /**
     * Get timestamp for filename
     */
    getTimestamp() {
        const now = new Date();
        return now.toISOString().slice(0, 19).replace(/[:-]/g, '').replace('T', '-');
    }

    /**
     * Export with quality selection modal
     */
    async exportWithOptions(elementId, filename, defaultFormat = 'png') {
        // Create modal for export options
        const modal = document.createElement('div');
        modal.className = 'fixed inset-0 z-50 flex items-center justify-center bg-black/50';
        safeHTML(modal, `
            <div class="bg-background rounded-lg shadow-xl p-6 max-w-md w-full mx-4">
                <h3 class="text-lg font-semibold mb-4">Export Options</h3>

                <div class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-2">Format</label>
                        <select id="exportFormat" class="w-full border rounded px-3 py-2">
                            <option value="png" ${defaultFormat === 'png' ? 'selected' : ''}>PNG (Lossless)</option>
                            <option value="jpg" ${defaultFormat === 'jpg' ? 'selected' : ''}>JPG (Smaller file)</option>
                        </select>
                    </div>

                    <div>
                        <label class="block text-sm font-medium mb-2">Quality</label>
                        <select id="exportQuality" class="w-full border rounded px-3 py-2">
                            <option value="1">High (1x)</option>
                            <option value="2" selected>Very High (2x) - Recommended</option>
                            <option value="3">Ultra (3x)</option>
                        </select>
                    </div>

                    <div class="flex gap-2 pt-4">
                        <button id="exportConfirm" class="flex-1 bg-primary text-primary-foreground px-4 py-2 rounded hover:bg-primary/90">
                            Export
                        </button>
                        <button id="exportCancel" class="flex-1 border px-4 py-2 rounded hover:bg-muted/30">
                            Cancel
                        </button>
                    </div>
                </div>
            </div>
        `);

        document.body.appendChild(modal);

        return new Promise((resolve) => {
            modal.querySelector('#exportConfirm').addEventListener('click', async () => {
                const format = modal.querySelector('#exportFormat').value;
                const scale = parseInt(modal.querySelector('#exportQuality').value);

                document.body.removeChild(modal);

                if (format === 'png') {
                    await this.exportToPNG(elementId, filename, { scale });
                } else {
                    await this.exportToJPG(elementId, filename, { scale });
                }

                resolve(true);
            });

            modal.querySelector('#exportCancel').addEventListener('click', () => {
                document.body.removeChild(modal);
                resolve(false);
            });
        });
    }
}

// Create global instance
window.exportManager = new ExportManager();

// Convenience functions
window.exportToPNG = (elementId, filename, options) =>
    window.exportManager.exportToPNG(elementId, filename, options);

window.exportToJPG = (elementId, filename, options) =>
    window.exportManager.exportToJPG(elementId, filename, options);

window.exportWithOptions = (elementId, filename, defaultFormat) =>
    window.exportManager.exportWithOptions(elementId, filename, defaultFormat);
