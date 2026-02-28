/* Contest Management System
 * Copyright © 2024 IOI-ISR
 *
 * Centralized modal management using MicroModal.
 * SweetAlert2 is used as a drop-in replacement for native confirm() dialogs.
 * Provides global initialization, URL-driven auto-open, and generic
 * confirm/delete helpers so individual templates don't duplicate logic.
 */

"use strict";

window.AdminModals = window.AdminModals || {};
var AdminModals = window.AdminModals;

/**
 * Utility function to escape HTML and prevent XSS (BUG-0001)
 * @param {string} text - Text to escape
 * @return {string} - Escaped HTML-safe text
 */
AdminModals.escapeHtml = function (text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
};

// Expose globally for convenience
window.escapeHtml = AdminModals.escapeHtml;

/**
 * Archive Training Day Modal functionality
 */
window.archiveModal = window.archiveModal || {};
var archiveModal = window.archiveModal;

archiveModal.toggleNetwork = function (event) {
    if (event.target.type === 'checkbox') return;
    var group = event.currentTarget.closest('.network-group');
    var ipsContainer = group.querySelector('.network-ips');
    var isExpanded = group.classList.contains('expanded');
    if (isExpanded) {
        group.classList.remove('expanded');
        ipsContainer.style.display = 'none';
    } else {
        group.classList.add('expanded');
        ipsContainer.style.display = '';
    }
};

archiveModal.handleNetworkKeydown = function (event) {
    if (event.target.type === 'checkbox') return;
    if (event.key === 'Enter' || event.key === ' ') {
        archiveModal.toggleNetwork(event);
        event.preventDefault();
    }
};

archiveModal.toggleNetworkIps = function (networkCheckbox) {
    var networkIdx = networkCheckbox.getAttribute('data-network');
    var modal = networkCheckbox.closest('.modal');
    var ipCheckboxes = modal.querySelectorAll('.ip-checkbox[data-network="' + networkIdx + '"]');
    var checked = networkCheckbox.checked;
    for (var i = 0; i < ipCheckboxes.length; i++) {
        ipCheckboxes[i].checked = checked;
    }
    // Clear indeterminate state when explicitly setting checked state
    networkCheckbox.indeterminate = false;
    var group = networkCheckbox.closest('.network-group');
    if (checked && !group.classList.contains('expanded')) {
        group.classList.add('expanded');
        group.querySelector('.network-ips').style.display = '';
    }
};

archiveModal.syncNetworkCheckbox = function (ipCheckbox) {
    var networkIdx = ipCheckbox.getAttribute('data-network');
    var modal = ipCheckbox.closest('.modal');
    var ipCheckboxes = modal.querySelectorAll('.ip-checkbox[data-network="' + networkIdx + '"]');
    var networkCheckbox = modal.querySelector('.network-checkbox[data-network="' + networkIdx + '"]');
    var allChecked = true;
    var someChecked = false;
    for (var i = 0; i < ipCheckboxes.length; i++) {
        if (ipCheckboxes[i].checked) someChecked = true;
        else allChecked = false;
    }
    networkCheckbox.checked = allChecked;
    networkCheckbox.indeterminate = someChecked && !allChecked;
};

document.addEventListener('DOMContentLoaded', function() {
    if (typeof MicroModal === 'undefined') return;

    MicroModal.init({
        onShow: function(modal) {
            var form = modal.querySelector('form');
            if (form && !modal.hasAttribute('data-no-reset')) {
                form.reset();
                var event = new CustomEvent('modal-reset', { detail: { modal: modal } });
                modal.dispatchEvent(event);
            }
        },
        disableScroll: true,
        awaitOpenAnimation: true,
        awaitCloseAnimation: true
    });

    var urlParams = new URLSearchParams(window.location.search);
    var modalId = urlParams.get('open_modal');

    if (modalId) {
        var targetId = modalId;
        if (!document.getElementById(targetId) && document.getElementById('modal-' + targetId)) {
            targetId = 'modal-' + targetId;
        }

        var targetModal = document.getElementById(targetId);
        if (targetModal) {
            MicroModal.show(targetId);

            urlParams.delete('open_modal');
            var newUrl = window.location.pathname + (urlParams.toString() ? '?' + urlParams.toString() : '');
            window.history.replaceState({}, '', newUrl);
        }
    }
});

/**
 * Opens a confirmation modal.
 * @param {Object} opts
 * @param {string} opts.title - Modal title
 * @param {string} [opts.message] - Main question text (safe, set via textContent)
 * @param {string} [opts.messageHtml] - Main question HTML (set via innerHTML, opt-in)
 * @param {string} [opts.warning] - Single warning text (safe, set via textContent)
 * @param {string} [opts.warningHtml] - Single warning HTML (set via innerHTML, opt-in)
 * @param {string[]} [opts.warnings] - Array of warning strings rendered as a list
 * @param {string} [opts.confirmLabel] - Confirm button label (default "Confirm")
 * @param {function} opts.onConfirm - Callback when confirmed
 */
AdminModals.confirm = function(opts) {
    var messageEl = document.getElementById('modal-confirm-message');
    document.getElementById('modal-confirm-title').textContent = opts.title;
    if (opts.messageHtml) {
        messageEl.innerHTML = opts.messageHtml;
    } else {
        messageEl.textContent = opts.message || '';
    }

    var warningBox = document.getElementById('modal-confirm-warning-box');
    var warningText = document.getElementById('modal-confirm-warning-text');
    var warningList = document.getElementById('modal-confirm-warning-list');

    var hasWarning = opts.warningHtml || opts.warning || (opts.warnings && opts.warnings.length > 0);

    if (hasWarning) {
        if (warningBox) {
            warningBox.style.display = 'block';
        }

        if (opts.warningHtml) {
            if (warningText) warningText.innerHTML = opts.warningHtml;
        } else if (opts.warning) {
            if (warningText) warningText.textContent = opts.warning;
        } else {
            if (warningText) warningText.textContent = '';
        }

        if (warningList) {
            warningList.innerHTML = '';
        }
        if (warningList && opts.warnings && opts.warnings.length > 0) {
            opts.warnings.forEach(function (w) {
                var li = document.createElement('li');
                li.textContent = w;
                warningList.appendChild(li);
            });
        }
    } else {
        if (warningBox) {
            warningBox.style.display = 'none';
        }
        if (warningText) {
            warningText.textContent = '';
        }
        if (warningList) {
            warningList.innerHTML = '';
        }
    }

    var btn = document.getElementById('modal-confirm-btn');
    var newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);

    newBtn.textContent = opts.confirmLabel || 'Confirm';

    newBtn.addEventListener('click', function() {
        opts.onConfirm();
        MicroModal.close('modal-generic-confirm');
    });

    MicroModal.show('modal-generic-confirm');
};

/**
 * Specialized delete helper that handles XSRF and page reload.
 * @param {Object} opts
 * @param {string} opts.title - Modal title
 * @param {string} [opts.message] - Main question text (safe, set via textContent)
 * @param {string} [opts.messageHtml] - Main question HTML (set via innerHTML, opt-in)
 * @param {string} [opts.warning] - Single warning text (safe, set via textContent)
 * @param {string} [opts.warningHtml] - Single warning HTML (set via innerHTML, opt-in)
 * @param {string[]} [opts.warnings] - Array of warning strings rendered as a list
 * @param {string} opts.deleteUrl - URL to send DELETE request to
 * @param {string} [opts.confirmLabel] - Confirm button label (default "Yes, Remove")
 * @param {function} [opts.onSuccess] - Optional callback on success (default: reload page)
 */
AdminModals.deleteResource = function(opts) {
    AdminModals.confirm({
        title: opts.title,
        message: opts.message,
        messageHtml: opts.messageHtml,
        warning: opts.warning,
        warningHtml: opts.warningHtml || null,
        warnings: opts.warnings || null,
        confirmLabel: opts.confirmLabel || 'Yes, Remove',
        onConfirm: function() {
            var xsrfToken = null;
            var xsrfInput = document.querySelector('input[name="_xsrf"]');
            if (xsrfInput) {
                xsrfToken = xsrfInput.value;
            } else if (typeof get_cookie === 'function') {
                xsrfToken = get_cookie('_xsrf');
            }
            if (!xsrfToken) {
                AdminModals.showError('Missing XSRF token');
                return;
            }
            fetch(opts.deleteUrl, {
                method: 'DELETE',
                headers: { 'X-XSRFToken': xsrfToken }
            }).then(function(resp) {
                if (resp.ok) {
                    if (opts.onSuccess) {
                        resp.text().then(opts.onSuccess);
                    } else {
                        window.location.reload();
                    }
                } else {
                    AdminModals.showError('Failed to delete resource');
                }
            }).catch(function(error) {
                AdminModals.showError(error.message);
            });
        }
    });
};

/**
 * Simple SweetAlert2 confirmation that returns a Promise<boolean>.
 * Drop-in async replacement for native confirm().
 * @param {string} message - The confirmation message
 * @param {Object} [options] - Optional overrides
 * @param {string} [options.title] - Dialog title (default "Are you sure?")
 * @param {string} [options.confirmButtonText] - Confirm button text (default "Yes")
 * @param {string} [options.cancelButtonText] - Cancel button text (default "Cancel")
 * @returns {Promise<boolean>}
 */
AdminModals.simpleConfirm = function(message, options) {
    var opts = options || {};
    return Swal.fire({
        title: opts.title || 'Are you sure?',
        text: message,
        icon: opts.icon || 'warning',
        showCancelButton: true,
        confirmButtonText: opts.confirmButtonText || 'Yes',
        cancelButtonText: opts.cancelButtonText || 'Cancel',
        reverseButtons: true
    }).then(function(result) {
        return result.isConfirmed;
    });
};

/**
 * Intercepts a form submission, shows a SweetAlert2 confirmation,
 * and only submits the form if confirmed.
 * Use via: onsubmit="return AdminModals.confirmSubmit(event, 'message')"
 * @param {Event} event - The submit event
 * @param {string} message - Confirmation message
 * @param {Object} [options] - Optional SweetAlert2 overrides
 * @returns {boolean} Always returns false to prevent default submission
 */
AdminModals.confirmSubmit = function(event, message, options) {
    event.preventDefault();
    var form = event.target;
    var submitter = event.submitter;
    AdminModals.simpleConfirm(message, options).then(function(confirmed) {
        if (confirmed) {
            if (submitter && submitter.name) {
                var hidden = document.createElement('input');
                hidden.type = 'hidden';
                hidden.name = submitter.name;
                hidden.value = submitter.value;
                form.appendChild(hidden);
            }
            form.submit();
        }
    });
    return false;
};

/**
 * Shows a SweetAlert2 confirmation, then navigates to the given URL if confirmed.
 * Use via: onclick="return AdminModals.confirmLink(event, 'message')"
 * @param {Event} event - The click event
 * @param {string} message - Confirmation message
 * @param {Object} [options] - Optional SweetAlert2 overrides
 * @returns {boolean} Always returns false to prevent default navigation
 */
AdminModals.confirmLink = function(event, message, options) {
    event.preventDefault();
    var href = event.currentTarget.getAttribute('href');
    AdminModals.simpleConfirm(message, options).then(function(confirmed) {
        if (confirmed) {
            window.location.href = href;
        }
    });
    return false;
};

/**
 * Shows a SweetAlert2 error dialog.
 * @param {string} message - Error message
 * @param {string} [title] - Dialog title (default "Error")
 * @returns {Promise}
 */
AdminModals.showError = function(message, title) {
    return Swal.fire({
        title: title || 'Error',
        text: message,
        icon: 'error'
    });
};

/**
 * Shows a SweetAlert2 confirmation, then calls the callback if confirmed.
 * Use for inline onclick handlers that need async confirmation.
 * @param {string} message - Confirmation message
 * @param {function} callback - Function to call if confirmed
 * @param {Object} [options] - Optional SweetAlert2 overrides
 */
AdminModals.confirmThen = function(message, callback, options) {
    AdminModals.simpleConfirm(message, options).then(function(confirmed) {
        if (confirmed) {
            callback();
        }
    });
};

/**
 * Internal helper: shared HTML and style for the team code/name form.
 * @private
 */
AdminModals._teamFormHtml =
    '<div class="swal-custom-form">' +
        '<div class="form-group">' +
            '<label for="swal-team-code">Team Code</label>' +
            '<input id="swal-team-code" class="swal2-input" placeholder="e.g. ISR" maxlength="3" style="text-transform: uppercase;">' +
        '</div>' +
        '<div class="form-group">' +
            '<label for="swal-team-name">Team Name</label>' +
            '<input id="swal-team-name" class="swal2-input" placeholder="e.g. Israel">' +
        '</div>' +
    '</div>' +
    '<style>' +
        '.swal-custom-form { text-align: left; }' +
        '.swal-custom-form .form-group { margin-bottom: 1rem; }' +
        '.swal-custom-form label { display: block; font-weight: 600; font-size: 0.9em; color: #333; margin-bottom: 5px; }' +
        '.swal-custom-form .swal2-input { margin: 0 !important; width: 100% !important; box-sizing: border-box; height: 2.5em; }' +
    '</style>';

/**
 * Internal helper: show a SweetAlert2 dialog for creating or editing a team.
 * Handles form rendering, validation, XSRF token, and AJAX submission.
 * @private
 * @param {Object} opts
 * @param {string} opts.title        - Dialog title
 * @param {string} opts.confirmText  - Confirm button label
 * @param {string} opts.postUrl      - URL to POST to
 * @param {string} opts.errorVerb    - Verb for error messages (e.g. "create" or "update")
 * @param {string} [opts.initialCode] - Pre-fill code (for edit)
 * @param {string} [opts.initialName] - Pre-fill name (for edit)
 * @param {Function} [opts.onSuccess] - Called with result data on success (defaults to reload)
 */
AdminModals._showTeamDialog = function (opts) {
    Swal.fire({
        title: opts.title,
        html: AdminModals._teamFormHtml,
        focusConfirm: false,
        showCancelButton: true,
        confirmButtonText: opts.confirmText,
        cancelButtonText: 'Cancel',
        reverseButtons: true,

        didOpen: function () {
            var codeInput = Swal.getPopup().querySelector('#swal-team-code');
            var nameInput = Swal.getPopup().querySelector('#swal-team-name');
            if (codeInput) {
                if (opts.initialCode) codeInput.value = opts.initialCode;
                codeInput.focus();
                if (opts.initialCode) codeInput.select();
            }
            if (nameInput && opts.initialName) nameInput.value = opts.initialName;

            [codeInput, nameInput].forEach(function (input) {
                if (input) input.addEventListener('keyup', function (e) {
                    if (e.key === 'Enter') Swal.clickConfirm();
                });
            });
        },

        preConfirm: function () {
            var codeInput = document.getElementById('swal-team-code');
            var nameInput = document.getElementById('swal-team-name');
            var code = codeInput.value.trim().toUpperCase();
            var name = nameInput.value.trim();

            if (!code) {
                Swal.showValidationMessage('Team code is required');
                setTimeout(function () { codeInput.focus(); }, 100);
                return false;
            }
            if (!name) {
                Swal.showValidationMessage('Team name is required');
                setTimeout(function () { nameInput.focus(); }, 100);
                return false;
            }

            var xsrfToken = null;
            var xsrfInput = document.querySelector('input[name="_xsrf"]');
            if (xsrfInput) {
                xsrfToken = xsrfInput.value;
            } else if (typeof get_cookie === 'function') {
                xsrfToken = get_cookie('_xsrf');
            }
            if (!xsrfToken) {
                AdminModals.showError('Missing XSRF token');
                return false;
            }

            codeInput.value = code;
            var formData = new FormData();
            formData.append('code', code);
            formData.append('name', name);

            return fetch(opts.postUrl, {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'X-XSRFToken': xsrfToken
                },
                body: formData
            }).then(function (response) {
                return response.json().then(function (data) {
                    if (!response.ok) {
                        throw new Error(data.error || 'Failed to ' + opts.errorVerb + ' team');
                    }
                    return data;
                });
            }).catch(function (error) {
                Swal.showValidationMessage(error.message || 'Network error occurred');
                return false;
            });
        }
    }).then(function (result) {
        if (result.isConfirmed && result.value) {
            if (opts.onSuccess) {
                opts.onSuccess(result.value);
            } else {
                window.location.reload();
            }
        }
    });
};

/**
 * Shows a SweetAlert2 dialog for adding a new team.
 * Posts via AJAX to the given URL and reloads on success.
 * @param {string} postUrl - The URL to POST the new team to
 */
AdminModals.showAddTeamDialog = function (postUrl) {
    AdminModals._showTeamDialog({
        title: 'Add New Team',
        confirmText: 'Create Team',
        postUrl: postUrl,
        errorVerb: 'create',
        onSuccess: function (data) {
            Swal.fire({
                icon: 'success',
                title: 'Team Created',
                text: 'Team "' + data.code + '" has been created successfully.',
                timer: 1500,
                showConfirmButton: false
            }).then(function () {
                window.location.reload();
            });
        }
    });
};

/**
 * Shows a SweetAlert2 dialog for editing an existing team.
 * Posts via AJAX to the given URL and reloads on success.
 * @param {string} postUrl - The URL to POST the updated team to
 * @param {string} currentCode - The current team code
 * @param {string} currentName - The current team name
 */
AdminModals.showEditTeamDialog = function (postUrl, currentCode, currentName) {
    AdminModals._showTeamDialog({
        title: 'Edit Team',
        confirmText: 'Save Changes',
        postUrl: postUrl,
        errorVerb: 'update',
        initialCode: currentCode,
        initialName: currentName
    });
};

/**
 * Shows a SweetAlert2 input dialog to rename a dataset description inline.
 * Posts via AJAX and reloads the page on success.
 * @param {string} renameUrl - The URL to POST the new description to
 * @param {string} currentDescription - The current dataset description
 */
AdminModals.renameDataset = function (renameUrl, currentDescription) {
    Swal.fire({
        title: 'Rename Dataset',
        html: '<div class="swal-custom-form">' +
            '<div class="form-group">' +
            '<label for="swal-dataset-desc">Description</label>' +
            '<input id="swal-dataset-desc" class="swal2-input" placeholder="Dataset description">' +
            '<small class="form-hint">Each dataset must have a unique description.</small>' +
            '</div></div>' +
            '<style>' +
            '.swal-custom-form { text-align: left; }' +
            '.swal-custom-form .form-group { margin-bottom: 1rem; }' +
            '.swal-custom-form label { display: block; font-weight: 600; font-size: 0.9em; color: #333; margin-bottom: 5px; }' +
            '.swal-custom-form .swal2-input { margin: 0 !important; width: 100% !important; box-sizing: border-box; height: 2.5em; }' +
            '.swal-custom-form .form-hint { display: block; margin-top: 6px; font-size: 0.8em; color: #6b7280; }' +
            '</style>',
        focusConfirm: false,
        showCancelButton: true,
        confirmButtonText: 'Rename',
        cancelButtonText: 'Cancel',
        reverseButtons: true,

        didOpen: function () {
            var descInput = Swal.getPopup().querySelector('#swal-dataset-desc');
            if (descInput) {
                descInput.value = currentDescription;
                descInput.focus();
                descInput.select();
                descInput.addEventListener('keyup', function (e) {
                    if (e.key === 'Enter') Swal.clickConfirm();
                });
            }
        },

        preConfirm: function () {
            var descInput = document.getElementById('swal-dataset-desc');
            var description = descInput.value.trim();

            if (!description) {
                Swal.showValidationMessage('Description is required');
                setTimeout(function () { descInput.focus(); }, 100);
                return false;
            }

            var xsrfToken = null;
            var xsrfInput = document.querySelector('input[name="_xsrf"]');
            if (xsrfInput) {
                xsrfToken = xsrfInput.value;
            } else if (typeof get_cookie === 'function') {
                xsrfToken = get_cookie('_xsrf');
            }

            if (!xsrfToken) {
                AdminModals.showError('Missing XSRF token');
                return false;
            }

            var formData = new FormData();
            formData.append('description', description);

            return fetch(renameUrl, {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'X-XSRFToken': xsrfToken
                },
                body: formData
            }).then(function (response) {
                return response.json().then(function (data) {
                    if (!response.ok) {
                        throw new Error(data.error || 'Failed to rename dataset');
                    }
                    return data;
                });
            }).catch(function (error) {
                Swal.showValidationMessage(error.message || 'Network error occurred');
                return false;
            });
        }
    }).then(function (result) {
        if (result.isConfirmed && result.value) {
            window.location.reload();
        }
    });
};

AdminModals._showAddResourceDialog = function (opts) {
    var inputId = 'swal-' + opts.resourceType + '-name';
    Swal.fire({
        title: opts.title,
        html:
            '<div class="swal-custom-form">' +
                '<div class="form-group" id="swal-' + opts.resourceType + '-group"></div>' +
            '</div>' +
            '<style>' +
                '.swal-custom-form { text-align: left; }' +
                '.swal-custom-form .form-group { margin-bottom: 1rem; }' +
                '.swal-custom-form label { display: block; font-weight: 600; font-size: 0.9em; color: #333; margin-bottom: 5px; }' +
                '.swal-custom-form .swal2-input { margin: 0 !important; width: 100% !important; box-sizing: border-box; height: 2.5em; }' +
                '.swal-custom-form .form-hint { display: block; margin-top: 6px; font-size: 0.8em; color: #6b7280; }' +
            '</style>',
        focusConfirm: false,
        showCancelButton: true,
        confirmButtonText: opts.confirmText,
        cancelButtonText: 'Cancel',
        reverseButtons: true,

        didOpen: function () {
            var group = Swal.getPopup().querySelector('#swal-' + opts.resourceType + '-group');
            if (group) {
                var label = document.createElement('label');
                label.setAttribute('for', inputId);
                label.textContent = opts.inputLabel || 'Name';

                var input = document.createElement('input');
                input.id = inputId;
                input.className = 'swal2-input';
                input.placeholder = opts.placeholder || '';

                group.appendChild(label);
                group.appendChild(input);

                if (opts.hint) {
                    var hint = document.createElement('small');
                    hint.className = 'form-hint';
                    hint.textContent = opts.hint;
                    group.appendChild(hint);
                }

                input.focus();
                input.addEventListener('keyup', function (e) {
                    if (e.key === 'Enter') Swal.clickConfirm();
                });
            }
        },

        preConfirm: async function () {
            var nameInput = document.getElementById(inputId);
            var name = nameInput.value.trim();

            if (!name) {
                Swal.showValidationMessage(opts.inputLabel + ' is required');
                setTimeout(function () { nameInput.focus(); }, 100);
                return false;
            }
            if (opts.validate) {
                var error = opts.validate(name);
                if (error) {
                    Swal.showValidationMessage(error);
                    setTimeout(function () { nameInput.focus(); }, 100);
                    return false;
                }
            }

            var xsrfToken = document.querySelector('input[name="_xsrf"]');
            xsrfToken = xsrfToken ? xsrfToken.value : null;
            if (!xsrfToken && typeof get_cookie === 'function') {
                xsrfToken = get_cookie('_xsrf');
            }
            if (!xsrfToken) {
                AdminModals.showError('Missing XSRF token');
                return false;
            }

            var formData = new FormData();
            formData.append(opts.fieldName || 'name', name);

            try {
                var response = await fetch(opts.postUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'X-XSRFToken': xsrfToken
                    },
                    body: formData
                });
                var data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to create ' + opts.resourceType);
                }
                return data;
            } catch (error) {
                Swal.showValidationMessage(error.message || 'Network error occurred');
                return false;
            }
        }
    }).then(function (result) {
        if (result.isConfirmed && result.value) {
            if (opts.onSuccess) {
                opts.onSuccess(result.value);
            } else if (opts.redirectBaseUrl) {
                window.location.href = opts.redirectBaseUrl + result.value.id;
            }
        }
    });
};

AdminModals.showAddContestDialog = function (postUrl, contestBaseUrl) {
    AdminModals._showAddResourceDialog({
        resourceType: 'contest',
        title: 'Add New Contest',
        inputLabel: 'Contest Name',
        placeholder: 'e.g. contest1',
        hint: 'A short name for the contest, preferably using only letters, numbers and underscores.',
        confirmText: 'Create Contest',
        postUrl: postUrl,
        redirectBaseUrl: contestBaseUrl,
        validate: function (name) {
            if (name.startsWith('__')) {
                return "Contest name cannot start with '__' (reserved for system contests)";
            }
            return null;
        }
    });
};

AdminModals.showAddTaskDialog = function (postUrl, taskBaseUrl) {
    AdminModals._showAddResourceDialog({
        resourceType: 'task',
        title: 'Add New Task',
        inputLabel: 'Task Name',
        placeholder: 'e.g. aplusb',
        hint: 'A short name using only letters, numbers and underscores.',
        confirmText: 'Create Task',
        postUrl: postUrl,
        redirectBaseUrl: taskBaseUrl,
        validate: function (name) {
            if (!/^[A-Za-z0-9_]+$/.test(name)) {
                return 'Task name may contain only letters, numbers, and underscores';
            }
            return null;
        }
    });
};
