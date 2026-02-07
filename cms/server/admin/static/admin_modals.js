/* Contest Management System
 * Copyright © 2024 IOI-ISR
 *
 * Centralized modal management using MicroModal and SweetAlert2.
 * Provides global initialization, URL-driven auto-open, and generic
 * confirm/delete helpers so individual templates don't duplicate logic.
 */

"use strict";

var AdminModals = AdminModals || {};

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
 * Opens a SweetAlert2 confirmation dialog.
 * @param {Object} opts
 * @param {string} opts.title - Modal title
 * @param {string} opts.message - Main question text (supports HTML)
 * @param {string|null} [opts.warningHtml] - Warning details HTML (optional)
 * @param {string} [opts.confirmLabel] - Confirm button label (default "Confirm")
 * @param {function} opts.onConfirm - Callback when confirmed
 */
AdminModals.confirm = function(opts) {
    var htmlContent = opts.message;
    if (opts.warningHtml) {
        htmlContent += '<div style="background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; padding: 12px; margin-top: 16px; text-align: left;">' +
            '<p style="margin: 0; color: #991b1b; font-weight: 600;">⚠ Warning</p>' +
            '<div style="color: #7f1d1d; font-size: 0.9rem; margin-top: 4px;">' + opts.warningHtml + '</div>' +
            '</div>';
    }

    Swal.fire({
        title: opts.title,
        html: htmlContent,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: opts.confirmLabel || 'Confirm',
        cancelButtonText: 'Cancel',
        reverseButtons: true,
        customClass: {
            confirmButton: 'swal2-confirm-danger'
        }
    }).then(function(result) {
        if (result.isConfirmed) {
            opts.onConfirm();
        }
    });
};

/**
 * Specialized delete helper that handles XSRF and page reload.
 * @param {Object} opts
 * @param {string} opts.title - Modal title
 * @param {string} opts.message - Main question text
 * @param {string|null} [opts.warningHtml] - Warning details HTML (optional)
 * @param {string} opts.deleteUrl - URL to send DELETE request to
 * @param {string} [opts.confirmLabel] - Confirm button label (default "Yes, Remove")
 * @param {function} [opts.onSuccess] - Optional callback on success (default: reload page)
 */
AdminModals.deleteResource = function(opts) {
    AdminModals.confirm({
        title: opts.title,
        message: opts.message,
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
                Swal.fire('Error', 'Missing XSRF token', 'error');
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
                    Swal.fire('Error', 'Failed to delete resource', 'error');
                }
            }).catch(function(error) {
                Swal.fire('Error', error.message, 'error');
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
        icon: 'warning',
        showCancelButton: true,
        confirmButtonText: opts.confirmButtonText || 'Yes',
        cancelButtonText: opts.cancelButtonText || 'Cancel',
        reverseButtons: true,
        customClass: {
            confirmButton: 'swal2-confirm-danger'
        }
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
    AdminModals.simpleConfirm(message, options).then(function(confirmed) {
        if (confirmed) {
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
