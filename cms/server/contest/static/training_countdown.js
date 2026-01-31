/**
 * Training countdown utilities for training day pages.
 * Shared between training_days.html and training_program_overview.html
 */

(function(global) {
    'use strict';

    var reloadScheduled = false;
    var reloadBackoffMs = 1000;

    /**
     * Check if the server time has passed the start time and reload if so.
     * Uses exponential backoff to avoid hammering the server.
     */
    function checkAndMaybeReload(startTimeMs) {
        fetch(window.location.href, { method: 'HEAD', cache: 'no-store' })
            .then(function(res) {
                var dateHeader = res.headers.get('Date');
                var serverNow = dateHeader ? Date.parse(dateHeader) : NaN;
                if (!Number.isNaN(serverNow) && serverNow < startTimeMs) {
                    reloadBackoffMs = Math.min(reloadBackoffMs * 2, 30000);
                    setTimeout(function() { checkAndMaybeReload(startTimeMs); }, reloadBackoffMs);
                    return;
                }
                window.location.reload();
            })
            .catch(function() {
                window.location.reload();
            });
    }

    /**
     * Initialize and run countdown timers.
     *
     * @param {number} serverTimestamp - Server timestamp in milliseconds
     * @param {number} clientLoadTime - Client load time in milliseconds (Date.now())
     */
    function initCountdowns(serverTimestamp, clientLoadTime) {
        function updateCountdowns() {
            var countdowns = document.querySelectorAll('.countdown');
            var elapsed = Date.now() - clientLoadTime;
            var serverNow = serverTimestamp + elapsed;

            countdowns.forEach(function(el) {
                var startTimeAttr = el.getAttribute('data-start-time');
                if (!startTimeAttr) return;
                var startTime = parseFloat(startTimeAttr);
                if (isNaN(startTime)) return;

                var diff = startTime - serverNow;

                var daysEl = el.querySelector('.countdown-days');
                var hoursEl = el.querySelector('.countdown-hours');
                var minutesEl = el.querySelector('.countdown-minutes');
                var secondsEl = el.querySelector('.countdown-seconds');

                if (diff <= 0) {
                    if (daysEl) daysEl.textContent = '0';
                    if (hoursEl) hoursEl.textContent = '0';
                    if (minutesEl) minutesEl.textContent = '0';
                    if (secondsEl) secondsEl.textContent = '0';
                    if (!reloadScheduled) {
                        reloadScheduled = true;
                        checkAndMaybeReload(startTime);
                    }
                    return;
                }

                var days = Math.floor(diff / (1000 * 60 * 60 * 24));
                var hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
                var minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
                var seconds = Math.floor((diff % (1000 * 60)) / 1000);

                if (daysEl) daysEl.textContent = days;
                if (hoursEl) hoursEl.textContent = hours;
                if (minutesEl) minutesEl.textContent = minutes;
                if (secondsEl) secondsEl.textContent = seconds;
            });
        }

        updateCountdowns();
        var intervalId = setInterval(updateCountdowns, 1000);

        // Clear interval on page unload to prevent memory leaks
        window.addEventListener('beforeunload', function () {
            clearInterval(intervalId);
        });

        return intervalId;
    }

    /**
     * Generic table sorting function.
     *
     * @param {string} tableId - The ID of the table to sort
     * @param {number} columnIndex - The column index to sort by
     * @param {Object} sortDirection - Object to track sort direction per column
     * @param {Function} getValueFn - Function to get sortable value from row and column index
     */
    function sortTable(tableId, columnIndex, sortDirection, getValueFn) {
        var table = document.getElementById(tableId);
        if (!table) return;

        var tbody = table.querySelector('tbody');
        if (!tbody) return;
        var rows = Array.from(tbody.querySelectorAll('tr'));
        var headers = table.querySelectorAll('th');

        sortDirection[columnIndex] = !sortDirection[columnIndex];
        var ascending = sortDirection[columnIndex];

        headers.forEach(function(h, i) {
            h.classList.remove('sort-asc', 'sort-desc');
            if (i === columnIndex) {
                h.classList.add(ascending ? 'sort-asc' : 'sort-desc');
            }
        });

        rows.sort(function(a, b) {
            var aVal = getValueFn(a, columnIndex);
            var bVal = getValueFn(b, columnIndex);

            if (aVal < bVal) return ascending ? -1 : 1;
            if (aVal > bVal) return ascending ? 1 : -1;
            return 0;
        });

        rows.forEach(function(row) {
            tbody.appendChild(row);
        });
    }

    /**
     * Escape HTML to prevent XSS.
     */
    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Export to global namespace
    global.TrainingCountdown = {
        initCountdowns: initCountdowns,
        sortTable: sortTable,
        escapeHtml: escapeHtml
    };

})(window);
