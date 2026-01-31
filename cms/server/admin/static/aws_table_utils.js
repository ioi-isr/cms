/* Contest Management System
 * Copyright © 2012-2014 Stefano Maggiolo <s.maggiolo@gmail.com>
 * Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
 *
 * Table sorting and filtering utilities for AWS.
 * Extracted from aws_utils.js for better code organization.
 */

"use strict";

var CMS = CMS || {};
CMS.AWSTableUtils = CMS.AWSTableUtils || {};


/**
 * Provides table row comparator for specified column and order.
 *
 * column_idx (int): Index of the column to sort by.
 * numeric (boolean): Whether to sort numerically.
 * ascending (boolean): Whether to sort in ascending order.
 * return (function): Comparator function for Array.sort().
 */
CMS.AWSTableUtils.getRowComparator = function(column_idx, numeric, ascending) {
    return function(a, b) {
        var cellA = $(a).children("td").eq(column_idx);
        var cellB = $(b).children("td").eq(column_idx);

        // Use data-value if present, otherwise fallback to text
        var valA = cellA.attr("data-value");
        if (typeof valA === "undefined" || valA === "") valA = cellA.text().trim();

        var valB = cellB.attr("data-value");
        if (typeof valB === "undefined" || valB === "") valB = cellB.text().trim();

        var result;
        if (numeric) {
            var numA = parseFloat(valA);
            var numB = parseFloat(valB);

            // Treat non-numeric/empty values so they always sink to bottom regardless of sort direction
            if (isNaN(numA)) numA = ascending ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY;
            if (isNaN(numB)) numB = ascending ? Number.POSITIVE_INFINITY : Number.NEGATIVE_INFINITY;

            result = numA - numB;
            return ascending ? result : -result;
        } else {
            result = valA.localeCompare(valB);
            return ascending ? result : -result;
        }
    };
};


/**
 * Sorts specified table by specified column in specified order.
 *
 * table (jQuery): The table element to sort.
 * column_idx (int): Index of the column to sort by.
 * ascending (boolean): Whether to sort in ascending order.
 * header_element (Element): Optional header element for the column.
 */
CMS.AWSTableUtils.sortTable = function(table, column_idx, ascending, header_element) {
    var initial_column_idx = table.data("initial_sort_column_idx");
    var ranks_column = table.data("ranks_column");
    var data_column_idx = column_idx + (ranks_column ? 1 : 0);
    var table_rows = table
        .children("tbody")
        .children("tr");

    // Use provided header element if available, otherwise find by index
    var column_header;
    if (header_element) {
        column_header = $(header_element);
    } else {
        column_header = table
            .children("thead")
            .children("tr")
            .children("th")
            .eq(data_column_idx);
    }
    var settings = (column_header.attr("data-sort-settings") || "").split(" ");

    var numeric = settings.indexOf("numeric") >= 0;

    // If specified, flip column's natural order, e.g. due to meaning of values.
    if (settings.indexOf("reversed") >= 0) {
        ascending = !ascending;
    }

    // Normalize column index for data access, converting negative to positive from the end.
    if (data_column_idx < 0) {
        // For negative indices, calculate from the number of columns in data rows
        var first_data_row = table_rows.first();
        var num_cols = first_data_row.children("td,th").length;
        data_column_idx = num_cols + data_column_idx;
    }

    // Reassign arrows to headers
    table.find(".column-sort").html("&varr;");
    column_header.find(".column-sort").html(ascending ? "&uarr;" : "&darr;");

    // Do the sorting, by initial column and then by selected column.
    table_rows
        .sort(CMS.AWSTableUtils.getRowComparator(initial_column_idx, numeric, ascending))
        .sort(CMS.AWSTableUtils.getRowComparator(data_column_idx, numeric, ascending))
        .each(function(idx, row) {
            table.children("tbody").append(row);
        });

    if (ranks_column) {
        table_rows.each(function(idx, row) {
            $(row).children("td").first().text(idx + 1);
        });
    }
};


/**
 * Makes table sortable, adding ranks column and sorting buttons in header.
 *
 * table (jQuery): The table element to make sortable.
 * ranks_column (boolean): Whether to add a ranks column.
 * initial_column_idx (int): Index of the column to initially sort by.
 * initial_ascending (boolean): Whether to initially sort in ascending order.
 */
CMS.AWSTableUtils.initTableSort = function(table, ranks_column, initial_column_idx, initial_ascending) {
    table.addClass("sortable");
    var table_column_headers = table
        .children("thead")
        .children("tr");
    var table_rows = table
        .children("tbody")
        .children("tr");

    // Normalize column index, converting negative to positive from the end.
    initial_column_idx = table_column_headers
        .children("th")
        .eq(initial_column_idx)
        .index();

    table.data("ranks_column", ranks_column);
    table.data("initial_sort_column_idx", initial_column_idx);

    // Declaring sort settings.
    var previous_column_idx = initial_column_idx;
    var ascending = initial_ascending;

    // Add sorting indicators to column headers
    // Skip headers with the "no-sort" class
    // Use data-sort-column attribute if present for correct column index
    table_column_headers
        .children("th")
        .not(".no-sort")
        .each(function(idx, header) {
            var $header = $(header);
            // Use data-sort-column if specified, otherwise use the header's index
            var sortColumn = $header.data("sort-column");
            if (sortColumn === undefined) {
                sortColumn = $header.index();
            }
            $("<a/>", {
                href: "#",
                class: "column-sort",
                click: function(e) {
                    e.preventDefault();
                    ascending = !ascending && previous_column_idx == sortColumn;
                    previous_column_idx = sortColumn;
                    CMS.AWSTableUtils.sortTable(table, sortColumn, ascending, header);
                }
            }).appendTo(header);
        });

    // Add ranks column
    if (ranks_column) {
        table_column_headers.prepend("<th>#</th>");
        table_rows.prepend("<td></td>");
    }

    // Do initial sorting
    CMS.AWSTableUtils.sortTable(table, initial_column_idx, initial_ascending);
};


/**
 * Filters table rows based on search text.
 *
 * table_id (string): The id of the table to filter.
 * search_text (string): The text to search for in table rows.
 */
CMS.AWSTableUtils.filterTable = function(table_id, search_text) {
    var table = document.getElementById(table_id);
    if (!table) {
        return;
    }
    var rows = table.querySelectorAll("tbody tr");
    var search_lower = search_text.toLowerCase().trim();

    rows.forEach(function(row) {
        if (search_lower === "") {
            row.style.display = "";
            return;
        }
        var text = row.textContent.toLowerCase();
        if (text.indexOf(search_lower) !== -1) {
            row.style.display = "";
        } else {
            row.style.display = "none";
        }
    });
};


// Backward compatibility aliases on CMS.AWSUtils
// These will be set up after aws_utils.js loads
document.addEventListener('DOMContentLoaded', function () {
    if (typeof CMS.AWSUtils !== 'undefined') {
        // Alias the new functions to the old names for backward compatibility
        CMS.AWSUtils.sort_table = CMS.AWSTableUtils.sortTable;
        CMS.AWSUtils.init_table_sort = CMS.AWSTableUtils.initTableSort;
        CMS.AWSUtils.filter_table = CMS.AWSTableUtils.filterTable;
    }
});
