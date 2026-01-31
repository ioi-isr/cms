/* Contest Management System
 * Copyright © 2012-2014 Stefano Maggiolo <s.maggiolo@gmail.com>
 * Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
 *
 * Form utilities for AWS.
 * Extracted from aws_utils.js for better code organization.
 */

"use strict";

var CMS = CMS || {};
CMS.AWSFormUtils = CMS.AWSFormUtils || {};


/**
 * Initialize password strength indicator for a password field.
 * Uses zxcvbn library to calculate password strength and displays
 * a colored bar with text feedback.
 *
 * fieldSelector (string): jQuery selector for the password input field.
 * barSelector (string): jQuery selector for the strength bar element.
 * textSelector (string): jQuery selector for the strength text element.
 */
CMS.AWSFormUtils.initPasswordStrength = function(fieldSelector, barSelector, textSelector) {
    var strengthMessages = ["Very weak", "Weak", "Fair", "Strong", "Very strong"];
    var strengthColors = ["#dc3545", "#dc3545", "#ffc107", "#28a745", "#28a745"];
    var strengthWidths = ["20%", "40%", "60%", "80%", "100%"];

    var $field = $(fieldSelector);
    if (!$field.length) {
        return;
    }

    var $bar = $(barSelector);
    var $text = $(textSelector);

    $field.on("input", function() {
        var pwd = $(this).val();

        if (!pwd) {
            $bar.hide();
            $text.text("");
            return;
        }

        if (typeof zxcvbn === "function") {
            var result = zxcvbn(pwd);
            var score = result.score;

            $bar.css({
                "background-color": strengthColors[score],
                "width": strengthWidths[score]
            }).show();
            $text.text("Password strength: " + strengthMessages[score]);
            $text.css("color", strengthColors[score]);
        }
    });
};


/**
 * Validates that end time is after start time for datetime-local inputs.
 * Attaches to a form's submit event and prevents submission if invalid.
 *
 * formSelector (string): jQuery selector for the form element.
 * startSelector (string): jQuery selector for the start datetime-local input.
 * stopSelector (string): jQuery selector for the stop/end datetime-local input.
 */
CMS.AWSFormUtils.initDateTimeValidation = function(formSelector, startSelector, stopSelector) {
    var form = document.querySelector(formSelector);
    if (!form) return;

    form.addEventListener('submit', function(e) {
        // Use form-scoped selectors to avoid matching inputs in other forms
        var startInput = form.querySelector(startSelector);
        var stopInput = form.querySelector(stopSelector);
        if (!startInput || !stopInput) return;

        // Use valueAsNumber for reliable datetime-local comparison
        var startValue = startInput.valueAsNumber;
        var stopValue = stopInput.valueAsNumber;
        if (startValue && stopValue && stopValue <= startValue) {
            alert('End time must be after start time');
            e.preventDefault();
        }
    });
};


/**
 * Initializes a remove page with task handling options.
 * Handles the radio button selection, dropdown enable/disable, and form submission.
 *
 * config (object): Configuration object with the following properties:
 *   - removeUrl (string): The base URL for the DELETE request.
 *   - hasTaskOptions (boolean): Whether task handling options are shown.
 *   - targetSelectId (string): ID of the target dropdown (e.g., 'target_contest_select').
 *   - targetParamName (string): Query param name for target (e.g., 'target_contest_id').
 *   - targetLabel (string): Label for validation alert (e.g., 'contest').
 */
CMS.AWSFormUtils.initRemovePage = function(config) {
    if (config.hasTaskOptions) {
        // Cache DOM elements and check they exist
        var targetSelectEl = document.getElementById(config.targetSelectId);
        var moveRadioEl = document.getElementById('action_move');
        if (!targetSelectEl || !moveRadioEl) return;

        // Enable/disable the target dropdown based on the selected action
        document.querySelectorAll('input[name="action"]').forEach(function(radio) {
            radio.addEventListener('change', function() {
                if (moveRadioEl.checked) {
                    targetSelectEl.disabled = false;
                } else {
                    targetSelectEl.disabled = true;
                }
            });
        });

        // Initialize the dropdown state
        targetSelectEl.disabled = true;
    }

    // Attach the remove function to CMS.AWSFormUtils namespace
    // Also attach to window for backward compatibility with onclick handlers
    CMS.AWSFormUtils.cmsDoRemove = function () {
        var url = config.removeUrl;

        if (config.hasTaskOptions) {
            var actionRadios = document.querySelectorAll('input[name="action"]');
            var selectedAction = null;
            for (var i = 0; i < actionRadios.length; i++) {
                if (actionRadios[i].checked) {
                    selectedAction = actionRadios[i].value;
                    break;
                }
            }

            if (!selectedAction) {
                alert('Please select an option for handling tasks.');
                return;
            }

            url += '?action=' + encodeURIComponent(selectedAction);

            if (selectedAction === 'move') {
                var targetSelect = document.getElementById(config.targetSelectId);
                if (targetSelect && targetSelect.value) {
                    url += '&' + config.targetParamName + '=' + encodeURIComponent(targetSelect.value);
                } else {
                    alert('Please select a ' + config.targetLabel + ' to move tasks to.');
                    return;
                }
            }
        }

        if (confirm('Are you sure you want to remove this?')) {
            CMS.AWSUtils.ajax_delete(url);
        }
    };
    // Backward compatibility alias
    window.cmsDoRemove = CMS.AWSFormUtils.cmsDoRemove;
};


/**
 * Initializes read-only Tagify display on input element(s).
 * Used to display tags in a visually consistent way without editing capability.
 *
 * inputSelector (string): CSS selector for the input element(s).
 */
CMS.AWSFormUtils.initReadOnlyTagify = function(inputSelector) {
    // Defensive check for Tagify library
    if (typeof Tagify === 'undefined') {
        return;
    }

    document.querySelectorAll(inputSelector).forEach(function(input) {
        if (!input.value.trim()) return;

        new Tagify(input, {
            delimiters: ",",
            readonly: true,
            editTags: false,
            originalInputValueFormat: function(valuesArr) {
                return valuesArr.map(function(item) {
                    return item.value;
                }).join(', ');
            }
        });
    });
};


/**
 * Initializes Tagify on input element(s) with confirmation dialogs and save-on-confirm.
 * Provides a unified interface for tag inputs across the admin interface.
 *
 * All tag operations (add, edit, remove) require confirmation before saving.
 * Automatic removals (like duplicate detection) do not require confirmation but still save.
 *
 * config (object): Configuration object with the following properties:
 *   - inputSelector (string): CSS selector for the input element(s).
 *   - whitelist (array): Array of existing tags for autocomplete suggestions.
 *   - getSaveUrl (function): Function that receives the input element and returns the save URL.
 *   - saveParamName (string): Parameter name for the save request (e.g., 'student_tags').
 *   - xsrfSelector (string): CSS selector for the XSRF token input (default: 'input[name="_xsrf"]').
 *   - placeholder (string): Placeholder text (default: 'Type tags').
 *   - editable (boolean): Whether tags can be edited by double-clicking (default: false).
 *   - enforceWhitelist (boolean): Whether to only allow tags from whitelist (default: false).
 *   - pattern (RegExp): Pattern for tag validation (default: null).
 *   - invalidMessage (string): Message to show when pattern validation fails.
 */
CMS.AWSFormUtils.initTagify = function(config) {
    var inputs = document.querySelectorAll(config.inputSelector);
    if (!inputs.length) return;

    var xsrfSelector = config.xsrfSelector || 'input[name="_xsrf"]';

    inputs.forEach(function(input) {
        var tagifyOptions = {
            delimiters: ",",
            maxTags: 20,
            placeholder: config.placeholder || "Type tags",
            whitelist: config.whitelist || [],
            dropdown: {
                maxItems: 20,
                classname: "tags-look",
                enabled: 0,
                closeOnSelect: true
            },
            originalInputValueFormat: function(valuesArr) {
                return valuesArr.map(function(item) {
                    return item.value;
                }).join(', ');
            }
        };

        tagifyOptions.editTags = config.editable ? { clicks: 2, keepInvalid: false } : false;
        tagifyOptions.enforceWhitelist = !!config.enforceWhitelist;
        if (config.pattern) tagifyOptions.pattern = config.pattern;

        // Flag to track if a save should happen on the next 'change' event
        var pendingSave = false;
        // Flag to track if we're rolling back a cancelled add (to skip confirmation)
        var isRollback = false;
        // Flag to prevent confirmations during initial page load
        var armed = false;

        function saveTags(tagifyInstance) {
            // Use tagify.value (canonical state) instead of input.value
            // input.value may be stale if Tagify's debounced update() hasn't run yet
            var tags = tagifyInstance.value.map(function(t) { return t.value; }).join(', ');
            var formData = new FormData();
            formData.append(config.saveParamName, tags);
            var xsrfInput = document.querySelector(xsrfSelector);
            if (xsrfInput) {
                formData.append('_xsrf', xsrfInput.value);
            }

            var saveUrl = config.getSaveUrl(input);
            fetch(saveUrl, {
                method: 'POST',
                body: formData
            }).then(function(response) {
                if (!response.ok) {
                    console.error('Failed to save tags');
                }
            }).catch(function(error) {
                console.error('Error saving tags:', error);
            });
        }

        // Track user-initiated removals (X click or backspace)
        var userRemovalTriggeredAt = 0;

        tagifyOptions.hooks = {
            beforeRemoveTag: function(tags) {
                return new Promise(function(resolve, reject) {
                    // If this is a rollback from cancelled add, skip confirmation
                    if (isRollback) {
                        resolve();
                        return;
                    }

                    var now = Date.now();
                    var isUserInitiated = (now - userRemovalTriggeredAt) < 200;
                    userRemovalTriggeredAt = 0;

                    // Auto-removals (duplicates, etc.) don't need confirmation
                    if (!isUserInitiated) {
                        pendingSave = true;
                        resolve();
                        return;
                    }

                    // User-initiated removal needs confirmation
                    var tagValue = tags[0].data.value;
                    if (confirm('Remove tag "' + tagValue + '"?')) {
                        pendingSave = true;
                        resolve();
                    } else {
                        reject();
                    }
                });
            }
        };

        var tagify = new Tagify(input, tagifyOptions);

        // Detect X button clicks
        tagify.DOM.scope.addEventListener('click', function(e) {
            if (e.target.closest('.tagify__tag__removeBtn')) {
                userRemovalTriggeredAt = Date.now();
            }
        }, true);

        // Detect backspace/delete key presses
        tagify.DOM.input.addEventListener('keydown', function(e) {
            if (e.key === 'Backspace' || e.key === 'Delete') {
                userRemovalTriggeredAt = Date.now();
            }
        }, true);

        // Handle add confirmation
        tagify.on('add', function(e) {
            // Skip confirmation if not armed yet (initial page load)
            if (!armed) return;

            var tagValue = e.detail.data.value;
            if (confirm('Add tag "' + tagValue + '"?')) {
                pendingSave = true;
            } else {
                // Roll back the add - use isRollback flag to skip beforeRemoveTag confirmation
                // Use non-silent removal so Tagify properly updates its internal state
                isRollback = true;
                tagify.removeTags(e.detail.tag);
                isRollback = false;
            }
        });

        // Handle edit confirmation
        if (config.editable) {
            var editingTagValue = null;

            tagify.on('edit:start', function(e) {
                editingTagValue = e.detail.data.value;
            });

            tagify.on('edit:beforeUpdate', function(e) {
                var oldVal = editingTagValue;
                var newVal = e.detail.data && e.detail.data.value;

                // No change, no confirmation needed
                if (oldVal === newVal) {
                    return;
                }

                if (confirm('Change tag "' + oldVal + '" to "' + newVal + '"?')) {
                    pendingSave = true;
                } else {
                    // Revert to old value
                    e.detail.data.value = oldVal;
                }
            });
        }

        // Save on 'change' event - this fires AFTER Tagify updates its internal state
        tagify.on('change', function() {
            if (pendingSave) {
                saveTags(tagify);
                pendingSave = false;
            }
        });

        if (config.pattern && config.invalidMessage) {
            tagify.on('invalid', function(e) {
                if (e.detail.message === 'pattern mismatch') {
                    alert(config.invalidMessage);
                }
            });
        }

        // Arm the confirmations after a short delay to skip initial load events
        setTimeout(function() {
            armed = true;
        }, 100);
    });
};


// Backward compatibility aliases on CMS.AWSUtils
// These will be set up after aws_utils.js loads
$(document).ready(function() {
    if (typeof CMS.AWSUtils !== 'undefined') {
        // Alias the new functions to the old names for backward compatibility
        CMS.AWSUtils.initPasswordStrength = CMS.AWSFormUtils.initPasswordStrength;
        CMS.AWSUtils.initDateTimeValidation = CMS.AWSFormUtils.initDateTimeValidation;
        CMS.AWSUtils.initRemovePage = CMS.AWSFormUtils.initRemovePage;
        CMS.AWSUtils.initReadOnlyTagify = CMS.AWSFormUtils.initReadOnlyTagify;
        CMS.AWSUtils.initTagify = CMS.AWSFormUtils.initTagify;
    }
});
