/* The application's Vendors tab: suppliers the model links to the application.
 *
 * "Record as vendor" asks the server to record the supplier as the application's
 * vendor in the one vendor register, then re-reads the tab's own figures and lists
 * from the server, so the counts and both lists always agree.
 */
(function (global) {
    'use strict';

    function vendorLinks(linkUrl) {
        return {
            saving: false,

            async record(supplier) {
                this.saving = true;
                try {
                    await Platform.fetch.post(
                        linkUrl,
                        { element_id: supplier.id },
                        { silent: true }
                    );
                    Platform.toast.success(supplier.name + ' recorded as vendor.');
                    await this.refresh();
                } catch (err) {
                    Platform.toast.error((err && err.message) || 'The vendor could not be recorded.');
                } finally {
                    this.saving = false;
                }
            },

            /* Swap in the server's current vendors panel contents. */
            async refresh() {
                var resp = await fetch(window.location.pathname + '?tab=vendors', { credentials: 'same-origin' });
                var html = await resp.text();
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var fresh = doc.querySelector('[data-vendor-links]');
                if (fresh) {
                    this.$root.innerHTML = fresh.innerHTML;
                }
            }
        };
    }

    global.vendorLinks = vendorLinks;
})(window);
