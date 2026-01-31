/**
 * Shared table utilities for CMS Admin interface.
 * Provides drag-and-drop reordering and Tagify filter initialization.
 *
 * Note: For table sorting, use CMS.AWSUtils.init_table_sort from aws_utils.js
 * which provides a unified sorting solution across the admin interface.
 */

/**
 * Initialize drag-and-drop reordering for table rows.
 *
 * @param {Object} options - Configuration options.
 * @param {string} options.tbodyId - The ID of the tbody element.
 * @param {string} options.rowSelector - CSS selector for draggable rows.
 * @param {string} options.rowIdAttr - Data attribute name for row ID (e.g., 'task-id' reads data-task-id).
 * @param {number} options.colSpan - Number of columns for placeholder (default: 4).
 * @param {string} options.confirmMessage - Confirmation message before saving (optional).
 * @param {Function} options.onSave - Callback function(orderedIds) when order is saved.
 *   orderedIds is an array of {id: string, position: number} objects.
 * @param {boolean} options.enabled - Whether drag-drop is enabled (default: true).
 */
function initDragDropReorder(options) {
    if (options.enabled === false) return;

    var tbody = document.getElementById(options.tbodyId);
    if (!tbody) return;

    var rowSelector = options.rowSelector || 'tr';
    var rowIdAttr = options.rowIdAttr || 'id';
    var colSpan = options.colSpan || 4;
    var confirmMessage = options.confirmMessage;

    var draggedRow = null;
    var placeholder = null;
    var originalOrder = null;

    function createPlaceholder() {
        var tr = document.createElement('tr');
        tr.className = 'drag-placeholder';
        tr.innerHTML = '<td colspan="' + colSpan + '"></td>';
        return tr;
    }

    function getRows() {
        return Array.from(tbody.querySelectorAll(rowSelector + '[data-' + rowIdAttr + ']'));
    }

    tbody.addEventListener('dragstart', function(e) {
        var targetRow = e.target.closest(rowSelector);
        if (!targetRow || !targetRow.hasAttribute('data-' + rowIdAttr)) return;

        draggedRow = targetRow;
        originalOrder = getRows();
        draggedRow.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/plain', '');
        placeholder = createPlaceholder();
    });

    tbody.addEventListener('dragend', function(e) {
        if (draggedRow) {
            draggedRow.classList.remove('dragging');
            if (placeholder && placeholder.parentNode) {
                placeholder.parentNode.removeChild(placeholder);
            }
            draggedRow = null;
            placeholder = null;
            saveNewOrder();
        }
    });

    tbody.addEventListener('dragover', function(e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';

        var targetRow = e.target.closest(rowSelector);
        if (!targetRow || targetRow === draggedRow || targetRow === placeholder) return;

        var rect = targetRow.getBoundingClientRect();
        var midY = rect.top + rect.height / 2;

        if (e.clientY < midY) {
            targetRow.parentNode.insertBefore(placeholder, targetRow);
        } else {
            targetRow.parentNode.insertBefore(placeholder, targetRow.nextSibling);
        }
    });

    tbody.addEventListener('drop', function(e) {
        e.preventDefault();
        if (placeholder && placeholder.parentNode && draggedRow) {
            placeholder.parentNode.insertBefore(draggedRow, placeholder);
            placeholder.parentNode.removeChild(placeholder);
        }
    });

    function restoreOriginalOrder() {
        if (!originalOrder) return;
        originalOrder.forEach(function(row) {
            tbody.appendChild(row);
        });
        originalOrder = null;
    }

    function saveNewOrder() {
        var rows = getRows();

        if (originalOrder &&
            rows.length === originalOrder.length &&
            rows.every(function(row, i) { return row === originalOrder[i]; })) {
            originalOrder = null;
            return;
        }

        if (confirmMessage && !confirm(confirmMessage)) {
            restoreOriginalOrder();
            return;
        }

        var orderedIds = rows.map(function(row, index) {
            return {
                id: row.getAttribute('data-' + rowIdAttr),
                position: index
            };
        });

        if (options.onSave) {
            options.onSave(orderedIds);
        }

        originalOrder = null;
    }
}

/**
 * Initialize read-only Tagify filter inputs for training program filter forms.
 *
 * @param {Object} options - Configuration options.
 * @param {string} options.trainingDayTypesSelector - CSS selector for training day types input.
 * @param {Array} options.trainingDayTypesWhitelist - Whitelist for training day types.
 * @param {string} options.studentTagsSelector - CSS selector for student tags input.
 * @param {Array} options.studentTagsWhitelist - Whitelist for student tags.
 */
function initFilterTagify(options) {
    if (typeof Tagify === 'undefined') return;

    var tagifyConfig = {
        delimiters: ",",
        enforceWhitelist: true,
        editTags: false,
        dropdown: { enabled: 0, maxItems: 20, closeOnSelect: true },
        originalInputValueFormat: function(valuesArr) {
            return valuesArr.map(function(item) { return item.value; }).join(', ');
        }
    };

    if (options.trainingDayTypesSelector) {
        var filterInput = document.querySelector(options.trainingDayTypesSelector);
        if (filterInput) {
            new Tagify(filterInput, Object.assign({}, tagifyConfig, {
                whitelist: options.trainingDayTypesWhitelist || []
            }));
        }
    }

    if (options.studentTagsSelector) {
        var studentTagsInput = document.querySelector(options.studentTagsSelector);
        if (studentTagsInput) {
            new Tagify(studentTagsInput, Object.assign({}, tagifyConfig, {
                whitelist: options.studentTagsWhitelist || []
            }));
        }
    }
}

window.AdminTableUtils = {
    initDragDropReorder: initDragDropReorder,
    initFilterTagify: initFilterTagify
};
