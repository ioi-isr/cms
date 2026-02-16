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
 * @param {string} [opts.warning] - Warning details text (safe, set via textContent)
 * @param {string} [opts.warningHtml] - Warning details HTML (set via innerHTML, opt-in)
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

    if (opts.warningHtml) {
        warningBox.style.display = 'block';
        warningText.innerHTML = opts.warningHtml;
    } else if (opts.warning) {
        warningBox.style.display = 'block';
        warningText.textContent = opts.warning;
    } else {
        warningBox.style.display = 'none';
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
 * @param {string} [opts.warning] - Warning details text (safe, set via textContent)
 * @param {string} [opts.warningHtml] - Warning details HTML (set via innerHTML, opt-in)
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
 * Shows a SweetAlert2 dialog for adding a new team.
 * Posts via AJAX to the given URL and reloads on success.
 * @param {string} postUrl - The URL to POST the new team to
 */
AdminModals.showAddTeamDialog = function (postUrl) {
    Swal.fire({
        title: 'Add New Team',
        html: `
            <div class="swal-custom-form">
                <div class="form-group">
                    <label for="swal-team-code">Team Code</label>
                    <input id="swal-team-code" class="swal2-input" placeholder="e.g. ISR" maxlength="3" style="text-transform: uppercase;">
                </div>
                <div class="form-group">
                    <label for="swal-team-name">Team Name</label>
                    <input id="swal-team-name" class="swal2-input" placeholder="e.g. Israel">
                </div>
            </div>
            <style>
                .swal-custom-form { text-align: left; }
                .swal-custom-form .form-group { margin-bottom: 1rem; }
                .swal-custom-form label { display: block; font-weight: 600; font-size: 0.9em; color: #333; margin-bottom: 5px; }
                .swal-custom-form .swal2-input { margin: 0 !important; width: 100% !important; box-sizing: border-box; height: 2.5em; }
            </style>
        `,
        focusConfirm: false,
        showCancelButton: true,
        confirmButtonText: 'Create Team',
        cancelButtonText: 'Cancel',
        reverseButtons: true,

        didOpen: () => {
            const codeInput = Swal.getPopup().querySelector('#swal-team-code');
            const nameInput = Swal.getPopup().querySelector('#swal-team-name');
            if (codeInput) codeInput.focus();

            [codeInput, nameInput].forEach(input => {
                if (input) input.addEventListener('keyup', (e) => {
                    if (e.key === 'Enter') Swal.clickConfirm();
                });
            });
        },

        preConfirm: async () => {
            const codeInput = document.getElementById('swal-team-code');
            const nameInput = document.getElementById('swal-team-name');
            const code = codeInput.value.trim();
            const name = nameInput.value.trim();

            if (!code) {
                Swal.showValidationMessage('Team code is required');
                setTimeout(() => codeInput.focus(), 100);
                return false;
            }
            if (!name) {
                Swal.showValidationMessage('Team name is required');
                setTimeout(() => nameInput.focus(), 100);
                return false;
            }

            let xsrfToken = document.querySelector('input[name="_xsrf"]')?.value;
            if (!xsrfToken && typeof get_cookie === 'function') {
                xsrfToken = get_cookie('_xsrf');
            }
            if (!xsrfToken) {
                AdminModals.showError('Missing XSRF token');
                return false;
            }

            const formData = new FormData();
            formData.append('code', code);
            formData.append('name', name);

            try {
                const response = await fetch(postUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'X-XSRFToken': xsrfToken
                    },
                    body: formData
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to create team');
                }
                return data;
            } catch (error) {
                Swal.showValidationMessage(error.message || 'Network error occurred');
                return false;
            }
        }
    }).then((result) => {
        if (result.isConfirmed && result.value) {
            Swal.fire({
                icon: 'success',
                title: 'Team Created',
                text: `Team "${result.value.code}" has been created successfully.`,
                timer: 1500,
                showConfirmButton: false
            }).then(() => {
                window.location.reload();
            });
        }
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

            var formData = new FormData();
            formData.append('description', description);

            return fetch(renameUrl, {
                method: 'POST',
                headers: {
                    'Accept': 'application/json',
                    'X-XSRFToken': xsrfToken || ''
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

AdminModals.showAddTaskDialog = function (postUrl, taskBaseUrl) {
    Swal.fire({
        title: 'Add New Task',
        html: `
            <div class="swal-custom-form">
                <div class="form-group">
                    <label for="swal-task-name">Task Name</label>
                    <input id="swal-task-name" class="swal2-input" placeholder="e.g. aplusb">
                    <small class="form-hint">A short name using only letters, numbers and underscores.</small>
                </div>
            </div>
            <style>
                .swal-custom-form { text-align: left; }
                .swal-custom-form .form-group { margin-bottom: 1rem; }
                .swal-custom-form label { display: block; font-weight: 600; font-size: 0.9em; color: #333; margin-bottom: 5px; }
                .swal-custom-form .swal2-input { margin: 0 !important; width: 100% !important; box-sizing: border-box; height: 2.5em; }
                .swal-custom-form .form-hint { display: block; margin-top: 6px; font-size: 0.8em; color: #6b7280; }
            </style>
        `,
        focusConfirm: false,
        showCancelButton: true,
        confirmButtonText: 'Create Task',
        cancelButtonText: 'Cancel',
        reverseButtons: true,

        didOpen: () => {
            const nameInput = Swal.getPopup().querySelector('#swal-task-name');
            if (nameInput) {
                nameInput.focus();
                nameInput.addEventListener('keyup', (e) => {
                    if (e.key === 'Enter') Swal.clickConfirm();
                });
            }
        },

        preConfirm: async () => {
            const nameInput = document.getElementById('swal-task-name');
            const name = nameInput.value.trim();

            if (!name) {
                Swal.showValidationMessage('Task name is required');
                setTimeout(() => nameInput.focus(), 100);
                return false;
            }

            let xsrfToken = document.querySelector('input[name="_xsrf"]')?.value;
            if (!xsrfToken && typeof get_cookie === 'function') {
                xsrfToken = get_cookie('_xsrf');
            }
            if (!xsrfToken) {
                AdminModals.showError('Missing XSRF token');
                return false;
            }

            const formData = new FormData();
            formData.append('name', name);

            try {
                const response = await fetch(postUrl, {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json',
                        'X-XSRFToken': xsrfToken
                    },
                    body: formData
                });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to create task');
                }
                return data;
            } catch (error) {
                Swal.showValidationMessage(error.message || 'Network error occurred');
                return false;
            }
        }
    }).then((result) => {
        if (result.isConfirmed && result.value) {
            window.location.href = taskBaseUrl + result.value.id;
        }
    });
};
