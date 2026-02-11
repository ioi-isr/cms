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
