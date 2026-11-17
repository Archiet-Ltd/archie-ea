/**
 * vendors/mapping.js: the vendor picker on the Mapping tab.
 *
 * One row per system with no vendor recorded. The person searches the vendors
 * already known (300 ms debounce), picks one, or saves the name they typed as a
 * new vendor; the page then reloads so every figure follows the saved link.
 *
 * Requires: Platform.fetch, Platform.toast
 */
(function () {
    'use strict';

    document.addEventListener('alpine:init', function () {
        Alpine.data('vendorMapRow', function (applicationId) {
            return {
                applicationId: applicationId,
                query: '',
                choices: [],
                open: false,
                saving: false,
                error: '',
                timer: null,

                search: function () {
                    var self = this;
                    self.error = '';
                    if (self.timer) clearTimeout(self.timer);
                    self.timer = setTimeout(function () {
                        Platform.fetch.get('/api/vendors/choices?q=' + encodeURIComponent(self.query.trim()), null, { silent: true })
                            .then(function (data) {
                                self.choices = (data && data.vendors) || [];
                                self.open = true;
                            })
                            .catch(function () {
                                self.choices = [];
                                self.error = 'Vendors could not be loaded. Type the name and save to add it.';
                            });
                    }, 300);
                },

                choose: function (vendor) {
                    this.query = vendor.name;
                    this.open = false;
                    this.save({ vendor_id: vendor.id });
                },

                saveTyped: function () {
                    var name = this.query.trim();
                    if (!name) {
                        this.error = 'Type a vendor name first.';
                        return;
                    }
                    var match = this.choices.find(function (v) { return v.name.toLowerCase() === name.toLowerCase(); });
                    this.save(match ? { vendor_id: match.id } : { vendor_name: name });
                },

                save: function (payload) {
                    var self = this;
                    if (self.saving) return;
                    self.saving = true;
                    self.error = '';
                    payload.application_id = self.applicationId;
                    Platform.fetch.post('/api/vendors/map-system', payload)
                        .then(function (result) {
                            if (!result || result.success === false) {
                                throw new Error((result && result.error) || 'Could not save the vendor.');
                            }
                            window.location.assign('/applications/vendors?tab=mapping&saved=1');
                        })
                        .catch(function (e) {
                            self.saving = false;
                            self.error = (e && e.message) || 'Could not save the vendor.';
                        });
                }
            };
        });
    });
})();
