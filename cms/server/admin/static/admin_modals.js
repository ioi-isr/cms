/* Contest Management System
 * Copyright © 2024 IOI-ISR
 *
 * Centralized modal management using MicroModal.
 * Provides global initialization, URL-driven auto-open, and generic
 * confirm/delete helpers so individual templates don't duplicate logic.
 */

"use strict";

var AdminModals = AdminModals || {};

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
 * @param {string} opts.message - Main question text
 * @param {string|null} [opts.warningHtml] - Warning details HTML (optional)
 * @param {string} [opts.confirmLabel] - Confirm button label (default "Confirm")
 * @param {function} opts.onConfirm - Callback when confirmed
 */
AdminModals.confirm = function(opts) {
    document.getElementById('modal-confirm-title').textContent = opts.title;
    document.getElementById('modal-confirm-message').innerHTML = opts.message;

    var warningBox = document.getElementById('modal-confirm-warning-box');
    var warningText = document.getElementById('modal-confirm-warning-text');

    if (opts.warningHtml) {
        warningBox.style.display = 'block';
        warningText.innerHTML = opts.warningHtml;
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
            var xsrfInput = document.querySelector('input[name="_xsrf"]');
            if (!xsrfInput) {
                alert('Missing XSRF token');
                return;
            }
            fetch(opts.deleteUrl, {
                method: 'DELETE',
                headers: { 'X-XSRFToken': xsrfInput.value }
            }).then(function(resp) {
                if (resp.ok) {
                    if (opts.onSuccess) {
                        resp.text().then(opts.onSuccess);
                    } else {
                        window.location.reload();
                    }
                } else {
                    alert('Error: Failed to delete resource');
                }
            }).catch(function(error) {
                alert('Error: ' + error.message);
            });
        }
    });
};
