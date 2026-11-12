/**
 * vendors/linked_suppliers.js: "Record as vendor" on the Vendors page.
 *
 * Records a supplier the model links to a system as that system's vendor, through
 * the same route the system's own Vendors tab uses, then reloads the page so every
 * count follows the saved link.
 *
 * Requires: Platform.fetch, Platform.toast
 */
(function () {
    'use strict';

    document.addEventListener('alpine:init', function () {
        Alpine.data('linkedSuppliers', function () {
            return {
                saving: false,

                record: function (url, elementId) {
                    var self = this;
                    if (self.saving) return;
                    self.saving = true;
                    Platform.fetch.post(url, { element_id: elementId }, { silent: true })
                        .then(function () {
                            window.location.assign('/applications/vendors');
                        })
                        .catch(function (err) {
                            self.saving = false;
                            Platform.toast.error((err && err.message) || 'The vendor could not be recorded.');
                        });
                }
            };
        });
    });
})();
