/* Contest Management System
 * Copyright © 2012-2014 Stefano Maggiolo <s.maggiolo@gmail.com>
 * Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
 * Copyright © 2013 Fabian Gundlach <320pointsguy@gmail.com>
 * Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
 * Copyright © 2018 Gregor Eesmaa <gregoreesmaa1@gmail.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */

"use strict";

/**
 * Utility functions needed by AWS front-end.
 */

var CMS = CMS || {};

CMS.AWSUtils = function(url_root, timestamp,
                        contest_start, contest_stop,
                        analysis_start, analysis_stop,
                        analysis_enabled) {
    this.url = CMS.AWSUtils.create_url_builder(url_root);
    this.first_date = new Date();
    this.last_notification = timestamp;
    this.timestamp = timestamp;
    this.contest_start = contest_start;
    this.contest_stop = contest_stop;
    this.analysis_start = analysis_start;
    this.analysis_stop = analysis_stop;
    this.analysis_enabled = analysis_enabled;
    this.file_asked_name = "";
    this.file_asked_url = "";
};


CMS.AWSUtils.create_url_builder = function(url_root) {
    return function() {
        var url = url_root;
        for (var i = 0; i < arguments.length; ++i) {
            if (url.substr(-1) != "/") {
                url += "/";
            }
            url += encodeURIComponent(arguments[i]);
        }
        return url;
    };
};


/**
 * Displays a subpage over the current page with the specified
 * content.
 */
CMS.AWSUtils.prototype.display_subpage = function(elements) {
    var content = $("#subpage_content");
    content.empty();
    for (var i = 0; i < elements.length; ++i) {
        elements[i].appendTo(content);
    }
    $('#modal-show-file-title').text('');
    $('#modal-show-file-download').hide();
    $('#modal-show-file-copy').hide();
    MicroModal.show('modal-show-file');
};

CMS.AWSUtils.filename_to_lang = function(file_name) {
    // TODO: update if adding a new language to cms
    // (need to also update the prism bundle then)
    var extension_to_lang = {
        'cs': 'csharp',
        'cpp': 'cpp',
        'c': 'c',
        'h': 'c',
        'go': 'go',
        'hs': 'haskell',
        'java': 'java',
        'js': 'javascript',
        'php': 'php',
        'py': 'python',
        'rs': 'rust',
    }
    var file_ext = file_name.split('.').pop();
    return extension_to_lang[file_ext] || file_ext;
}

/**
 * Enable/disable language options based on selected file extensions.
 *
 * options: jQuery collection or array-like of <option> elements.
 * inputs: jQuery collection or array-like of <input type="file"> elements.
 * languages: mapping { langName: { '.ext': true, ... }, ... }.
 */
CMS.AWSUtils.filter_languages = function(options, inputs, languages) {
    languages = languages || {};

    var exts = [];
    for (var i = 0; i < inputs.length; i++) {
        var value = inputs[i].value || "";
        var lastDot = value.lastIndexOf(".");
        if (lastDot !== -1) {
            exts.push(value.slice(lastDot).toLowerCase());
        }
    }

    var enabled = {};
    var anyEnabled = false;
    for (var lang in languages) {
        var langExts = languages[lang];
        if (!langExts) {
            continue;
        }
        for (var j = 0; j < exts.length; j++) {
            if (langExts[exts[j]]) {
                enabled[lang] = true;
                anyEnabled = true;
                break;
            }
        }
    }

    var selectedDisabled = false;
    for (var k = 0; k < options.length; k++) {
        var option = options[k];
        var shouldEnable = !anyEnabled || enabled[option.value];
        option.disabled = !shouldEnable;
        if (!shouldEnable && option.selected) {
            selectedDisabled = true;
        }
    }

    if (selectedDisabled) {
        for (var m = 0; m < options.length; m++) {
            if (!options[m].disabled) {
                options[m].selected = true;
                break;
            }
        }
    }
};

/**
 * This is called when we receive file content, or an error message.
 *
 * file_name (string): the name of the requested file
 * url (string): the url of the file
 * response (string): the file content
 * error (string): The error message, or null if the request is
 *     successful.
 */
CMS.AWSUtils.prototype.file_received = function(response, error) {
    var file_name = this.file_asked_name;
    var url = this.file_asked_url;
    if (error != null) {
        if (window.AdminModals && typeof AdminModals.showError === 'function') {
            AdminModals.showError('File request failed.');
        } else {
            alert('File request failed.');
        }
        return;
    }

    var content = $('#subpage_content');
    content.empty();

    if (response.length > 100000) {
        $('#modal-show-file-title').text(file_name);
        $('#modal-show-file-download').attr('href', url).show();
        $('#modal-show-file-copy').hide();
        content.append($('<p>').text('File is too large to display. Use the Download link above.'));
        MicroModal.show('modal-show-file');
        return;
    }

    var lang_name = CMS.AWSUtils.filename_to_lang(file_name);
    var codearea = $('<code>').text(response).addClass('line-numbers').addClass('language-' + lang_name);
    content.append($('<pre>').css('margin', '0').append(codearea));

    $('#modal-show-file-title').text(file_name);
    $('#modal-show-file-download').attr('href', url).show();
    $('#modal-show-file-copy').show().off('click').on('click', function(event) {
        var code_el = $('#subpage_content code')[0];
        if (code_el) {
            var range = document.createRange();
            range.setStartBefore(code_el);
            range.setEndAfter(code_el);
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(range);
            document.execCommand('copy');
        }
        event.preventDefault();
    });

    MicroModal.show('modal-show-file');
    Prism.highlightAllUnder(document.getElementById('subpage_content'));
};


/**
 * Displays a subpage with the content of the file at the specified
 * url.
 */
CMS.AWSUtils.prototype.show_file = function(file_name, url) {
    this.file_asked_name = file_name;
    this.file_asked_url = url;
    var file_received = this.bind_func(this, this.file_received);
    this.ajax_request(url, null, file_received);
};


/**
 * To be added to the onclick of an element named title_XXX. Hide/show
 * an element named XXX, and change the class of title_XXX between
 * toggling_on and toggling_off.
 */
CMS.AWSUtils.prototype.toggle_visibility = function() {
    var title = $(this);
    var item = $(this.id.replace("title_", "#").replace(".", "\\."));
    item.slideToggle("normal", function() {
        title.toggleClass("toggling_on toggling_off");
    });
};


/**
 * Display the notification to the user.
 *
 * type (string): can be "notification", "message", "question",
 *     "announcement".
 * timestamp (number): time of the notification.
 * subject (string): subject.
 * text (string): body of notification.
 */
CMS.AWSUtils.prototype.display_notification = function(type, timestamp,
                                                       subject, text,
                                                       contest_id) {
    if (this.last_notification < timestamp) {
        this.last_notification = timestamp;
    }
    var timestamp_int = parseInt(timestamp);
    var subject_element = $('<span>');
    if (type == "message") subject_element.text("Private message. ");
    else if (type == "announcement") subject_element.text("Announcement. ");
    else if (type == "question") subject_element.text("Reply to your question. ");
    else if (type == "new_question") {
        subject_element = $("<a>").text("New question: ")
            .prop("href", this.url("contest", contest_id, "questions"));
    } else if (type == "new_delay_request") {
        subject_element = $("<a>").text("New delay request: ")
            .prop("href", this.url("contest", contest_id, "delays_and_extra_times"));
    }

    var colorClass = "is-danger";
    if (subject === "Operation successful.") colorClass = "is-success";
    else if (type === "new_question" || type === "new_delay_request") colorClass = "is-warning";

    var self = this;
    var outer = $("#notifications");
    var close_btn = $('<button>').addClass("delete")
        .click(function() { self.close_notification(this); });
    var header_div = $('<div>')
        .addClass("is-flex is-justify-content-space-between is-align-items-start pr-6 mb-1");

    var title_container = $("<strong>")
        .append(subject_element)
        .append($("<span>").text(" " + subject));

    var timestamp_str = timestamp_int != 0 ? this.format_time_or_date(timestamp_int) : "";
    var timestamp_span = $("<span>")
        .addClass("is-size-7 has-text-grey")
        .css("white-space", "nowrap") // CSS needed here to prevent wrapping
        .text(timestamp_str);

    header_div.append(title_container).append(timestamp_span);

    var text_div = $("<div>");
    if (subject === "Manager compilation failed") {
        text_div.addClass("content is-small").append(
            $('<pre>').text(text).css({ 'margin': 0 })
        );
    } else if (text) {
        text_div.text(text);
    }

    var inner = $('<div>')
        .addClass("notification is-light " + colorClass + " notification_type_" + type)
        .append(close_btn)
        .append(header_div)
        .append(text_div);
    outer.append(inner);

    if (type !== "notification") {
        this.desktop_notification(type, timestamp, subject, text);
    }
};


CMS.AWSUtils.prototype.desktop_notification = function(type, timestamp,
                                                       subject, text) {
    // Check desktop notifications support
    if (!("Notification" in window)) {
        return;
    }

    // Only show notification if permission was granted
    if (Notification.permission !== "granted") {
        return;
    }

    new Notification(subject, {
        "body": text,
        "icon": this.url("static", "favicon.ico")
    });
};


/**
 * Update the number of unread private and public messages in the span
 * next to the title of the sections "overview" and "communication".
 *
 * delta_public (int): how many public unreads to add.
 * delta_private (int): how many public unreads to add.
 */
CMS.AWSUtils.prototype.update_unread_counts = function(delta_public, delta_private) {
    var unread_public = $("#unread_public");
    var unread_private = $("#unread_private");
    if (unread_public) {
        var msgs_public = parseInt(unread_public.text());
        msgs_public += delta_public;
        unread_public.text(msgs_public);
        if (msgs_public > 0) {
            unread_public.show();
        } else {
            unread_public.hide();
        }
    }
    if (unread_private) {
        var msgs_private = parseInt(unread_private.text());
        msgs_private += delta_private;
        unread_private.text(msgs_private);
        if (msgs_private > 0) {
            unread_private.show();
        } else {
            unread_private.hide();
        }
    }
};


/**
 * Ask CWS (via ajax, not rpc) to send to the user the new
 * notifications.
 */
CMS.AWSUtils.prototype.update_notifications = function() {
    var display_notification = this.bind_func(this, this.display_notification);
    var update_unread_counts = this.bind_func(this, this.update_unread_counts);
    this.ajax_request(
        this.url("notifications"),
        "last_notification=" + this.last_notification,
        function(response, error) {
            if (error == null) {
                response = JSON.parse(response);
                var msgs_public = 0;
                var msgs_private = 0;
                for (var i = 0; i < response.length; i++) {
                    display_notification(
                        response[i].type,
                        response[i].timestamp,
                        response[i].subject,
                        response[i].text,
                        response[i].contest_id);
                    if (response[i].type == "announcement") {
                        msgs_public++;
                    } else if (response[i].type == "question"
                               || response[i].type == "message") {
                        msgs_private++;
                    }
                }
                update_unread_counts(msgs_public, msgs_private);
            }
        });
};


/**
 * For the close button of a notification.
 */
CMS.AWSUtils.prototype.close_notification = function(item) {
    var bubble = item.parentNode;
    if (bubble.className.indexOf("notification_type_announcement") != -1) {
        this.update_unread_counts(-1, 0);
    } else if (bubble.className.indexOf("notification_type_question") != -1
               || bubble.className.indexOf("notification_type_message") != -1) {
        this.update_unread_counts(0, -1);
    }
    bubble.parentNode.removeChild(bubble);
};


// Table utilities (get_table_row_comparator, sort_table, init_table_sort, filter_table)
// have been moved to aws_table_utils.js for better code organization.


/**
 * Return a string representation of the number with two digits.
 *
 * n (int): a number with one or two digits.
 * return (string): n as a string with two digits, maybe with a
 *     leading 0.
 */
CMS.AWSUtils.prototype.two_digits = function(n) {
    if (n < 10) {
        return "0" + n;
    } else {
        return "" + n;
    }
};


/**
 * Update the remaining time showed in the "remaining" div.
 */
CMS.AWSUtils.prototype.update_remaining_time = function() {
    var relevant_timestamp = null;
    var text = null;
    var now_timestamp = this.timestamp + (new Date() - this.first_date) / 1000;

    // based on the phase logic from cms/db/contest.py.
    if (now_timestamp < this.contest_start) {
        relevant_timestamp = this.contest_start;
        text = "To start of contest: "
    } else if (now_timestamp <= this.contest_stop) {
        relevant_timestamp = this.contest_stop;
        text = "To end of contest: "
    } else if (this.analysis_enabled && now_timestamp < this.analysis_start) {
        relevant_timestamp = this.analysis_start;
        text = "To start of analysis: "
    } else if (this.analysis_enabled && now_timestamp <= this.analysis_stop) {
        relevant_timestamp = this.analysis_stop;
        text = "To end of analysis: "
    }

    // We are in phase 3, nothing to show.
    if (relevant_timestamp === null) {
        $("#remaining_text").text("");
        $("#remaining_value").text("");
        return;
    }

    var countdown_sec = relevant_timestamp - now_timestamp;

    $("#remaining_text").text(text);
    $("#remaining_value").text(this.format_countdown(countdown_sec));
};


/**
 * Check the status returned by an RPC call and display the error if
 * necessary, otherwise redirect to another page.
 *
 * url (string): the destination page if response is ok.
 * response (dict): the response returned by the RPC.
 */
CMS.AWSUtils.prototype.redirect_if_ok = function(url, response) {
    var msg = this.standard_response(response);
    if (msg != "") {
        if (window.AdminModals && typeof AdminModals.showError === 'function') {
            AdminModals.showError('Unable to invalidate (' + msg + ').');
        } else {
            alert('Unable to invalidate (' + msg + ').');
        }
    } else {
        location.href = url;
    }
};


/**
 * Represent in a nice looking way a couple (job_type, submission_id)
 * coming from the backend.
 *
 * job (array): a tuple (job_type, submission_id, dataset_id)
 * returns (string): nice representation of job
 */
CMS.AWSUtils.prototype.repr_job = function(job) {
    var job_type = "???";
    var object_type = "???";
    if (job == null) {
        return "N/A";
    } else if (job == "disabled") {
        return "Worker disabled";
    } else if (job["type"] == 'compile') {
        job_type = 'Compiling';
        object_type = 'submission';
    } else if (job["type"] == 'evaluate') {
        job_type = 'Evaluating';
        object_type = 'submission';
    } else if (job["type"] == 'compile_test') {
        job_type = 'Compiling';
        object_type = 'user_test';
    } else if (job["type"] == 'evaluate_test') {
        job_type = 'Evaluating';
        object_type = 'user_test';
    }

    if (object_type == 'submission') {
        return job_type
            + ' the <a href="' + this.url("submission", job["object_id"], job["dataset_id"]) + '">result</a>'
            + ' of <a href="' + this.url("submission", job["object_id"]) + '">submission ' + job["object_id"] + '</a>'
            + ' on <a href="' + this.url("dataset", job["dataset_id"]) + '">dataset ' + job["dataset_id"] + '</a>'
            + (job_type == 'Evaluating' && job["multiplicity"]
               ? " [" + job["multiplicity"] + " time(s) in queue]"
               : "")
            + (job["testcase_codename"]
               ? " [testcase: `" + job["testcase_codename"] + "']"
               : "");
    } else {
        return job_type
            + ' the result'
            + ' of user_test ' + job["object_id"]
            + ' on <a href="' + this.url("dataset", job["dataset_id"]) + '">dataset ' + job["dataset_id"] + '</a>';
    }
};


/**
 * Format time as hours, minutes and seconds ago.
 *
 * time (int): a unix time.
 * returns (string): representation of time as "[[H hour(s), ]M
 *     minute(s), ]S second(s)".
 */
CMS.AWSUtils.prototype.repr_time_ago = function(time) {
    if (time == null) {
        return "N/A";
    }
    var diff = parseInt((new Date()).getTime() / 1000 - time);
    var res = "";

    var s = diff % 60;
    diff = diff - s;
    res = s + " second(s)";
    if (diff == 0) {
        return res;
    }
    diff /= 60;

    var m = diff % 60;
    diff -= m;
    res = m + " minute(s), " + res;
    if (diff == 0) {
        return res;
    }
    diff /= 60;

    var h = diff;
    res = h + " hour(s), " + res;
    return res;
};


/**
 * Format time as hours, minutes and seconds ago.
 *
 * time (int): a unix time.
 * returns (string): representation of time as "[[HH:]MM:]SS]".
 */
CMS.AWSUtils.prototype.repr_time_ago_short = function(time) {
    if (time == null) {
        return "N/A";
    }
    var diff = parseInt((new Date()).getTime() / 1000 - time);
    var res = "";

    var s = diff % 60;
    diff = diff - s;
    if (diff > 0) {
        res = this.two_digits(s);
    } else {
        return "" + s;
    }
    diff /= 60;

    var m = diff % 60;
    diff -= m;
    if (diff > 0) {
        res = this.two_digits(m) + ":" + res;
    } else {
        return m + ":" + res;
    }
    diff /= 60;

    var h = diff;
    res = h + ":" + res;
    return res;
};


/**
 * Return timestamp formatted as HH:MM:SS.
 *
 * timestamp (int): unix time.
 * return (string): timestamp formatted as above.
 */
CMS.AWSUtils.prototype.format_time = function(timestamp) {
    var date = new Date(timestamp * 1000);
    var hours = this.two_digits(date.getHours());
    var minutes = this.two_digits(date.getMinutes());
    var seconds = this.two_digits(date.getSeconds());
    return hours + ":" + minutes + ":" + seconds;
};


/**
 * Return the time difference formatted as HHHH:MM:SS.
 *
 * timestamp (int): a time delta in s.
 * return (string): timestamp formatted as above.
 */
CMS.AWSUtils.prototype.format_countdown = function(countdown) {
    var hours = countdown / 60 / 60;
    var hours_rounded = Math.floor(hours);
    var minutes = countdown / 60 - (60 * hours_rounded);
    var minutes_rounded = Math.floor(minutes);
    var seconds = countdown - (60 * 60 * hours_rounded)
        - (60 * minutes_rounded);
    var seconds_rounded = Math.floor(seconds);
    return hours_rounded + ":" + this.two_digits(minutes_rounded) + ":"
        + this.two_digits(seconds_rounded);
};


/**
 * Return timestamp formatted as HH:MM:SS, dd/mm/yyyy.
 *
 * timestamp (int): unix time.
 * return (string): timestamp formatted as above.
 */
CMS.AWSUtils.prototype.format_datetime = function(timestamp) {
    var time = this.format_time(timestamp);
    var date = new Date(timestamp * 1000);
    var days = this.two_digits(date.getDate());
    var months = this.two_digits(date.getMonth() + 1); // months are 0-11
    var years = date.getFullYear();
    return time + ", " + days + "/" + months + "/" + years;
};


/**
 * Return timestamp formatted as HH:MM:SS if the date is the same date
 * as today, as a complete date + time if the date is different.
 *
 * timestamp (int): unix time.
 * return (string): timestamp formatted as above.
 */
CMS.AWSUtils.prototype.format_time_or_date = function(timestamp) {
    var today = (new Date()).toDateString();
    var date = new Date(timestamp * 1000);
    if (today == date.toDateString()) {
        return this.format_time(timestamp);
    } else {
        return this.format_datetime(timestamp);
    }
};


/**
 * If the response is for a standard error (unconnected, ...)  then
 * return an appropriate message, otherwise return "".
 *
 * response (object): an rpc response.
 * return (string): appropriate message or "".
 */
CMS.AWSUtils.prototype.standard_response = function(response) {
    if (response['status'] != 'ok') {
        var msg = "Unexpected reply `" + response['status']
            + "'. This should not happen.";
        if (response['status'] == 'unconnected') {
            msg = 'Service not connected.';
        } else if (response['status'] == 'not authorized') {
            msg = "You are not authorized to call this method.";
        } else if (response['status'] == 'fail') {
            msg = "Call to service failed.";
        }
        return msg;
    }
    return "";
};


CMS.AWSUtils.prototype.show_page = function(item, page, elements_per_page) {
    elements_per_page = elements_per_page || 5;

    var children = $("#paged_content_" + item).children().filter(function() {
        return $(this).css('display') !== 'none' || $(this).data('hidden-by-page') === true;
    });
    children.each(function() { $(this).removeData('hidden-by-page'); });
    var npages = Math.ceil(children.length / elements_per_page);
    var final_page = Math.min(page, npages) - 1;
    if (final_page < 0) final_page = 0;
    children.each(function(i, child) {
        if (i >= elements_per_page * final_page
            && i < elements_per_page * (final_page + 1)) {
            $(child).show();
        } else {
            $(child).hide();
            $(child).data('hidden-by-page', true);
        }
    });

    var self = this;
    var selector = $("#page_selector_" + item);
    selector.empty();
    if (npages <= 1) return;
    selector.append("Pages: ");
    var windowRadius = 2;
    var maxAllVisible = windowRadius * 2 + 3;
    var appendPage = function(i) {
        if (i != page) {
            selector.append($("<a>").text(i + " ")
                            .click(function(j) {
                                return function() {
                                    self.show_page(item, j, elements_per_page);
                                    return false;
                                };
                            }(i)));
        } else {
            selector.append($("<span>").addClass("page-current").text(i + " "));
        }
    };

    if (npages <= maxAllVisible) {
        for (let i = 1; i <= npages; i++) appendPage(i);
        return;
    }

    var start = Math.max(2, page - windowRadius);
    var end = Math.min(npages - 1, page + windowRadius);

    appendPage(1);
    if (start > 2) selector.append(" ... ");
    for (let i = start; i <= end; i++) appendPage(i);
    if (end < npages - 1) selector.append(" ... ");
    appendPage(npages);
};


/**
 * Returns a function binded to an object - useful in case we need to
 * send callback that needs to access to the "this" object.
 *
 * Example:
 * var f = this.utils.bind_func(this, this.cb);
 * function_that_needs_a_cb(function(data) { f(data); });
 *
 * object (object): the object to bind to
 * method (function): the function to bind
 * returns (function): the binded function
 */
CMS.AWSUtils.prototype.bind_func = function(object, method) {
    return function() {
        return method.apply(object, arguments);
    };
};


/**
 * Perform an AJAX GET request.
 *
 * url (string): the url of the resource.
 * args (string|null): the arguments already encoded.
 * callback (function): the function to call with the response.
 */
CMS.AWSUtils.prototype.ajax_request = function(url, args, callback) {
    if (args != null) {
        url = url + "?" + args;
    }
    var jqxhr = $.get(url);
    jqxhr.done(function(data) {
        callback(data, null);
    });
    jqxhr.fail(function() {
        callback(null, jqxhr.status);
    });
};


/**
 * Sends a request and on success redirect to the page
 * specified in the response, if present.
 */
CMS.AWSUtils.ajax_edit_request = function(type, url) {
    var settings = {
        "type": type,
        headers: {"X-XSRFToken": get_cookie("_xsrf")}
    };
    settings["success"] = function(data_redirect_url) {
        if (data_redirect_url) {
            window.location.replace(data_redirect_url);
        }
    };
    $.ajax(url, settings);
};


/**
 * Sends a delete request and on success redirect to the page
 * specified in the response, if present.
 */
CMS.AWSUtils.ajax_delete = function(url) {
    CMS.AWSUtils.ajax_edit_request("DELETE", url);
};


/**
 * Sends a delete request and on success reloads the current page
 * instead of following the server's redirect URL.
 */
CMS.AWSUtils.ajax_delete_reload = function (url) {
    var settings = {
        "type": "DELETE",
        headers: { "X-XSRFToken": get_cookie("_xsrf") }
    };
    settings["success"] = function () {
        window.location.reload();
    };
    settings["error"] = function (xhr) {
        if (window.AdminModals && typeof AdminModals.showError === 'function') {
            AdminModals.showError('Delete failed (' + xhr.status + ').');
        } else {
            alert('Delete failed (' + xhr.status + ').');
        }
    };
    $.ajax(url, settings);
};


/**
 * Sends a post request and on success. See AWSUtils.ajax_request
 * for more details.
 */
CMS.AWSUtils.ajax_post = function(url) {
    CMS.AWSUtils.ajax_edit_request("POST", url);
};


// initPasswordStrength has been moved to aws_form_utils.js


/**
 * Used by templates/macro/question.html.
 * Toggles visibility of the question reply box.
 */
CMS.AWSUtils.prototype.question_reply_toggle = function(event, invoker) {
    var card = invoker.closest('.question-card');
    var obj = card.querySelector(".reply_question");
    if (obj.style.display != "block") {
        obj.style.display = "block";
    } else {
        obj.style.display = "none";
    }
    event.preventDefault();
};

CMS.AWSUtils.prototype.init_questions_page = function() {
    var self = this;
    self.show_page("questions", 1);

    var tabs = document.querySelectorAll('.questions-tab');
    tabs.forEach(function(tab) {
        tab.addEventListener('click', function() {
            tabs.forEach(function(t) { t.classList.remove('active'); });
            this.classList.add('active');
            var filter = this.dataset.filter;
            var cards = document.querySelectorAll('#paged_content_questions .question-card');
            cards.forEach(function(card) {
                $(card).removeData('hidden-by-page');
                if (filter === 'all') {
                    card.style.display = '';
                } else {
                    card.style.display = card.dataset.status === filter ? '' : 'none';
                }
            });
            self.show_page("questions", 1);
        });
    });
}

CMS.AWSUtils.prototype.announcement_edit_toggle = function (event, invoker) {
    const card = invoker.closest('.announcement-card');
    const subjectText = card.querySelector('.announcement_raw_subject').value;
    const bodyText = card.querySelector('.announcement_raw_text').value;
    const form = card.querySelector('.reply_question form');

    form.querySelector('input[name="subject"]').value = subjectText;
    form.querySelector('textarea[name="text"]').value = bodyText;

    const visibleToTagsInput = form.querySelector('input[name="visible_to_tags"]');
    const rawVisibleToTags = card.querySelector('.announcement_raw_visible_to_tags');
    if (visibleToTagsInput && rawVisibleToTags) {
        const rawValue = rawVisibleToTags.value;
        const tagify = visibleToTagsInput._tagify;
        if (tagify) {
            tagify.removeAllTags();
            const tags = rawValue.split(",").map(t => t.trim()).filter(Boolean);
            if (tags.length) tagify.addTags(tags);
        } else {
            visibleToTagsInput.value = rawValue;
        }
    }

    var obj = card.querySelector(".reply_question");
    if (obj.style.display != "block") {
        obj.style.display = "block";
    } else {
        obj.style.display = "none";
    }
    event.preventDefault();
}

/**
 * Used by templates/macro/question.html.
 * Updates visibility of answer box when choosing quick answers.
 */
CMS.AWSUtils.prototype.update_additional_answer = function(event, invoker) {
    var obj = $(invoker).parent().find(".alternative_answer");
    if (invoker.value == "other") {
        obj.css("display", "");
    } else {
        obj.css("display", "none");
    }
}

/**
 * Used by templates/macro/markdown_input.html.
 * Asks the server to render the markdown input and displays it.
 */
CMS.AWSUtils.prototype.render_markdown_preview = function(target) {
    var form_element = $(target).closest("form");
    var md_text = form_element.find(".markdown_input").val();
    $.ajax({
        type: "POST",
        url: this.url("render_markdown"),
        data: {input: md_text},
        dataType: "text",
        headers: {"X-XSRFToken": get_cookie("_xsrf")},
        success: function(response) {
            form_element.find(".markdown_preview").html(response);
        },
    });
}

/**
 * Handlers for diffing submissions.
 */

/**
 * Shows/hides the diff radio buttons when opening/closing the diff section.
 */
CMS.AWSUtils.prototype.update_diffchooser = function() {
    var el = document.getElementById("diffchooser");
    if(el.open) {
        $("#submissions_table").addClass("diff-open");
    } else {
        $("#submissions_table").removeClass("diff-open");
    }
}

/**
 * Updates the submission ID inputs when clicking diff radio buttons.
 */
CMS.AWSUtils.prototype.update_diff_ids = function(ev) {
    var name = ev.target.name;
    var sub_id = ev.target.dataset.submission;
    if(name == "diff-radio-old") {
        $("#diff-old-input").val(sub_id);
    } else {
        $("#diff-new-input").val(sub_id);
    }
}

/**
 * Renders a diff that was received from the server.
 */
CMS.AWSUtils.prototype.show_diff = function(response, error) {
    if(error !== null) {
        this.display_subpage([$('<p>').text('Error: ' + error)]);
        return;
    }
    var elements = [];
    if(response.message !== null) {
        elements.push($('<p>').text(response.message));
    }
    for(let x of response.files) {
        elements.push($('<h2>').text(x.fname));
        if('status' in x) {
            elements.push($('<p>').text(x.status));
            continue;
        }
        var lang_name = CMS.AWSUtils.filename_to_lang(x.fname);
        var codearea = $('<code>').text(x.diff)
            .addClass('language-diff-' + lang_name)
            .addClass('diff-highlight');
        elements.push($('<pre>').append(codearea));
    }
    this.display_subpage(elements);
    Prism.highlightAllUnder(document.getElementById('subpage_content'));
}

/**
 * Called when "Diff" button is clicked, requests the diff from the server.
 */
CMS.AWSUtils.prototype.do_diff = function() {
    var old_id = $("#diff-old-input").val();
    var new_id = $("#diff-new-input").val();
    var show_diff = this.bind_func(this, this.show_diff);
    this.ajax_request(this.url("submission_diff", old_id, new_id), null, show_diff);
};


// Request notification permission on first user interaction.
// This is required by Firefox which only allows permission requests
// from inside a short running user-generated event handler.
if ("Notification" in window && Notification.permission === "default") {
    var cmsRequestNotificationPermissionOnFirstClick = function() {
        if (Notification.permission === "default") {
            Notification.requestPermission();
        }
        document.removeEventListener("click", cmsRequestNotificationPermissionOnFirstClick);
    };
    document.addEventListener("click", cmsRequestNotificationPermissionOnFirstClick);
}

/**
 * Model Solution Subtask Score Utilities
 *
 * These functions handle the auto-calculation of expected score ranges
 * from subtask scores in model solution forms.
 *
 * Two modes are supported:
 * 1. Simple mode (default): Uses form-scoped selectors with field names like
 *    "subtask_{idx}_min", "expected_score_min", etc.
 * 2. Multi-solution mode: Uses data-sol-id attributes and field names like
 *    "sol_{solId}_st_{idx}_min", "sol_{solId}_score_min", etc.
 */

/**
 * Update the expected score range inputs based on subtask score sums.
 * Only updates if the auto-calculate checkbox is checked.
 *
 * @param {HTMLFormElement|string} formOrSolId - The form element (simple mode) or solution ID (multi mode)
 * @param {Object} options - Optional configuration for field naming
 * @param {function} options.subtaskMinName - Function(idx) returning subtask min field name
 * @param {function} options.subtaskMaxName - Function(idx) returning subtask max field name
 * @param {string} options.scoreMinName - Name of overall score min field
 * @param {string} options.scoreMaxName - Name of overall score max field
 * @param {string} options.subtaskMinSelector - CSS selector for subtask min inputs
 * @param {string} options.subtaskMaxSelector - CSS selector for subtask max inputs
 * @param {string} options.checkboxSelector - CSS selector for auto-calc checkbox
 */
CMS.AWSUtils.updateScoreRangeFromSubtasks = function(formOrSolId, options) {
    options = options || {};
    var form, checkbox, minInputs, maxInputs, scoreMinInput, scoreMaxInput;

    if (typeof formOrSolId === 'string') {
        // Multi-solution mode: formOrSolId is a solution ID
        var solId = formOrSolId;
        form = document;
        checkbox = document.querySelector(options.checkboxSelector || '.calc-from-subtasks[data-sol-id="' + solId + '"]');
        minInputs = document.querySelectorAll(options.subtaskMinSelector || '.subtask-min[data-sol-id="' + solId + '"]');
        maxInputs = document.querySelectorAll(options.subtaskMaxSelector || '.subtask-max[data-sol-id="' + solId + '"]');
        scoreMinInput = document.querySelector('input[name="' + (options.scoreMinName || 'sol_' + solId + '_score_min') + '"]');
        scoreMaxInput = document.querySelector('input[name="' + (options.scoreMaxName || 'sol_' + solId + '_score_max') + '"]');
    } else {
        // Simple mode: formOrSolId is a form element
        form = formOrSolId;
        checkbox = form.querySelector('.calc-from-subtasks');
        minInputs = form.querySelectorAll('.subtask-min');
        maxInputs = form.querySelectorAll('.subtask-max');
        scoreMinInput = form.querySelector('input[name="' + (options.scoreMinName || 'expected_score_min') + '"]');
        scoreMaxInput = form.querySelector('input[name="' + (options.scoreMaxName || 'expected_score_max') + '"]');
    }

    if (!checkbox || !checkbox.checked) return;

    var minSum = 0;
    var maxSum = 0;
    minInputs.forEach(function(input) {
        minSum += Number.parseFloat(input.value) || 0;
    });
    maxInputs.forEach(function(input) {
        maxSum += Number.parseFloat(input.value) || 0;
    });

    if (scoreMinInput) scoreMinInput.value = minSum.toFixed(2);
    if (scoreMaxInput) scoreMaxInput.value = maxSum.toFixed(2);
};

/**
 * Initialize model solution subtask score handlers for a page.
 * Sets up event listeners for full/zero score buttons, auto-calculate
 * checkbox, and subtask input changes.
 *
 * @param {Object} options - Optional configuration for field naming
 * @param {boolean} options.multiSolution - If true, use multi-solution mode with data-sol-id
 * @param {function} options.subtaskMinName - Function(idx, solId?) returning subtask min field name
 * @param {function} options.subtaskMaxName - Function(idx, solId?) returning subtask max field name
 * @param {function} options.scoreMinName - Function(solId?) returning overall score min field name
 * @param {function} options.scoreMaxName - Function(solId?) returning overall score max field name
 */
CMS.AWSUtils.initModelSolutionSubtasks = function(options) {
    options = options || {};
    var multiSolution = options.multiSolution || false;

    // Helper to get field names
    var getSubtaskMinName = options.subtaskMinName || function(idx, solId) {
        return multiSolution ? 'sol_' + solId + '_st_' + idx + '_min' : 'subtask_' + idx + '_min';
    };
    var getSubtaskMaxName = options.subtaskMaxName || function(idx, solId) {
        return multiSolution ? 'sol_' + solId + '_st_' + idx + '_max' : 'subtask_' + idx + '_max';
    };
    var getScoreMinName = options.scoreMinName || function(solId) {
        return multiSolution ? 'sol_' + solId + '_score_min' : 'expected_score_min';
    };
    var getScoreMaxName = options.scoreMaxName || function(solId) {
        return multiSolution ? 'sol_' + solId + '_score_max' : 'expected_score_max';
    };

    // Helper to update score range
    var updateScores = function(formOrSolId) {
        CMS.AWSUtils.updateScoreRangeFromSubtasks(formOrSolId, {
            scoreMinName: typeof formOrSolId === 'string' ? getScoreMinName(formOrSolId) : getScoreMinName(),
            scoreMaxName: typeof formOrSolId === 'string' ? getScoreMaxName(formOrSolId) : getScoreMaxName()
        });
    };

    // Full score button handler
    document.querySelectorAll('.btn-full-score').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var maxScore = Number.parseFloat(this.dataset.maxScore);
            var minInput, maxInput, formOrSolId;

            if (multiSolution) {
                var solId = this.dataset.solId;
                var stIdx = this.dataset.stIdx;
                minInput = document.querySelector('input[name="' + getSubtaskMinName(stIdx, solId) + '"]');
                maxInput = document.querySelector('input[name="' + getSubtaskMaxName(stIdx, solId) + '"]');
                formOrSolId = solId;
            } else {
                var idx = this.dataset.idx;
                var form = this.closest('form');
                minInput = form.querySelector('input[name="' + getSubtaskMinName(idx) + '"]');
                maxInput = form.querySelector('input[name="' + getSubtaskMaxName(idx) + '"]');
                formOrSolId = form;
            }

            if (minInput) minInput.value = maxScore;
            if (maxInput) maxInput.value = maxScore;
            updateScores(formOrSolId);
        });
    });

    // Zero score button handler
    document.querySelectorAll('.btn-zero-score').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var minInput, maxInput, formOrSolId;

            if (multiSolution) {
                var solId = this.dataset.solId;
                var stIdx = this.dataset.stIdx;
                minInput = document.querySelector('input[name="' + getSubtaskMinName(stIdx, solId) + '"]');
                maxInput = document.querySelector('input[name="' + getSubtaskMaxName(stIdx, solId) + '"]');
                formOrSolId = solId;
            } else {
                var idx = this.dataset.idx;
                var form = this.closest('form');
                minInput = form.querySelector('input[name="' + getSubtaskMinName(idx) + '"]');
                maxInput = form.querySelector('input[name="' + getSubtaskMaxName(idx) + '"]');
                formOrSolId = form;
            }

            if (minInput) minInput.value = 0;
            if (maxInput) maxInput.value = 0;
            updateScores(formOrSolId);
        });
    });

    // Calculate from subtasks checkbox handler
    document.querySelectorAll('.calc-from-subtasks').forEach(function(checkbox) {
        checkbox.addEventListener('change', function() {
            var minInput, maxInput, formOrSolId;

            if (multiSolution) {
                var solId = this.dataset.solId;
                minInput = document.querySelector('input[name="' + getScoreMinName(solId) + '"]');
                maxInput = document.querySelector('input[name="' + getScoreMaxName(solId) + '"]');
                formOrSolId = solId;
            } else {
                var form = this.closest('form');
                minInput = form.querySelector('input[name="' + getScoreMinName() + '"]');
                maxInput = form.querySelector('input[name="' + getScoreMaxName() + '"]');
                formOrSolId = form;
            }

            if (this.checked) {
                updateScores(formOrSolId);
                if (minInput) minInput.readOnly = true;
                if (maxInput) maxInput.readOnly = true;
            } else {
                if (minInput) minInput.readOnly = false;
                if (maxInput) maxInput.readOnly = false;
            }
        });
    });

    // Update score range when subtask values change
    document.querySelectorAll('.subtask-min, .subtask-max').forEach(function(input) {
        input.addEventListener('input', function() {
            var checkbox, formOrSolId;

            if (multiSolution) {
                var solId = this.dataset.solId;
                checkbox = document.querySelector('.calc-from-subtasks[data-sol-id="' + solId + '"]');
                formOrSolId = solId;
            } else {
                var form = this.closest('form');
                checkbox = form.querySelector('.calc-from-subtasks');
                formOrSolId = form;
            }

            if (checkbox && checkbox.checked) {
                updateScores(formOrSolId);
            }
        });
    });
};


/**
 * ModelSolutionModal - shared modal logic for add/edit model solutions.
 */
var ModelSolutionModal = (function() {
    var _advancedOpen = {};

    function _parse(val, fallback) {
        var v = Number.parseFloat(val);
        return Number.isNaN(v) ? (fallback || 0) : v;
    }

    function _clamp(val, lo, hi) {
        return Math.min(Math.max(val, lo), hi);
    }

    function _getUI(dsId) {
        var p = 'ms-' + dsId;
        return {
            form: document.getElementById('ms-form-' + dsId),
            title: document.getElementById('modal-ms-' + dsId + '-title'),
            submit: document.getElementById(p + '-submit'),
            fileSection: document.getElementById(p + '-file-section'),
            nameInput: document.getElementById(p + '-name'),
            descInput: document.getElementById(p + '-description'),
            globalPct: document.getElementById(p + '-global-pct'),
            cards: document.querySelectorAll('#' + p + '-cards .ms-card-subtask'),
            advSection: document.getElementById(p + '-advanced'),
            advArrow: document.getElementById(p + '-adv-arrow'),
            advLabel: document.getElementById(p + '-adv-label'),
            calcCheckbox: document.getElementById(p + '-calc-total'),
            totalDisplay: document.getElementById(p + '-total'),
            scoreMin: document.getElementById(p + '-score-min'),
            scoreMax: document.getElementById(p + '-score-max'),
            scoreMinSimple: document.getElementById(p + '-score-min-simple'),
            scoreMaxSimple: document.getElementById(p + '-score-max-simple'),
            advTotalPct: document.getElementById(p + '-adv-total-pct'),
            advTotalMin: document.getElementById(p + '-adv-total-min'),
            advTotalMax: document.getElementById(p + '-adv-total-max'),
            stHidden: function (idx) {
                return {
                    min: document.getElementById(p + '-st-' + idx + '-min'),
                    max: document.getElementById(p + '-st-' + idx + '-max')
                };
            },
            advRow: function (idx) {
                var sel = '[data-dataset="' + dsId + '"][data-idx="' + idx + '"]';
                return {
                    pct: document.querySelector('.ms-adv-pct' + sel),
                    minInp: document.querySelector('.ms-adv-min-input' + sel),
                    maxInp: document.querySelector('.ms-adv-max-input' + sel)
                };
            }
        };
    }

    function _setCardState(card, minScore, maxScore, fullScore) {
        card.classList.remove('selected', 'partial');
        if (minScore >= fullScore && fullScore > 0) {
            card.classList.add('selected');
        } else if (maxScore > 0) {
            card.classList.add('partial');
        }
    }

    var _ERR_COLOR = 'var(--tp-danger, #dc2626)';

    function _flagInput(inp, errMsg) {
        if (!inp) return;
        inp.style.borderColor = errMsg ? _ERR_COLOR : '';
        inp.setCustomValidity(errMsg || '');
    }

    function _validateAndClampPercentage(input, allowEmpty) {
        if (allowEmpty && (input.value === '' || input.value === null)) {
            return '';
        }
        var rawPct = _parse(input.value);
        var pct = _clamp(rawPct, 0, 100);
        input.value = pct;
        return pct;
    }

    function _validateRow(pctInp, minInp, maxInp) {
        if (pctInp) _validateAndClampPercentage(pctInp, true);
        if (minInp) _flagInput(minInp, _parse(minInp.value) < 0 ? 'Value must be non-negative' : '');
        if (maxInp) _flagInput(maxInp, _parse(maxInp.value) < 0 ? 'Value must be non-negative' : '');
        if (minInp && maxInp && !minInp.validationMessage && !maxInp.validationMessage) {
            var bad = _parse(minInp.value) > _parse(maxInp.value) ? 'Min must be ≤ Max' : '';
            _flagInput(minInp, bad);
            _flagInput(maxInp, bad);
        }
    }

    function _updateSubtask(ui, dsId, idx, source) {
        var card = document.querySelector('#ms-' + dsId + '-cards .ms-card-subtask[data-idx="' + idx + '"]');
        if (!card) return;
        var maxScore = _parse(card.dataset.max);
        var row = ui.advRow(idx);
        var hidden = ui.stHidden(idx);
        var minVal, maxVal, score;

        if (source === 'card') {
            var isActive = card.classList.contains('selected') || card.classList.contains('partial');
            var gPct = _clamp(_parse(ui.globalPct ? ui.globalPct.value : 100, 100), 0, 100);
            score = isActive ? maxScore * gPct / 100 : 0;
            minVal = maxVal = score;
            if (row.pct) row.pct.value = isActive ? gPct : 0;
            if (row.minInp) row.minInp.value = score.toFixed(2);
            if (row.maxInp) row.maxInp.value = score.toFixed(2);
        } else if (source === 'pct') {
            var rowPct = _clamp(_parse(row.pct ? row.pct.value : 0), 0, 100);
            score = maxScore * rowPct / 100;
            minVal = maxVal = score;
            if (row.minInp) row.minInp.value = score.toFixed(2);
            if (row.maxInp) row.maxInp.value = score.toFixed(2);
        } else {
            minVal = _parse(row.minInp ? row.minInp.value : 0);
            maxVal = _parse(row.maxInp ? row.maxInp.value : 0);
            if (row.pct) {
                // Show percentage when min and max are the same, hide when they differ
                if (Math.abs(minVal - maxVal) < 0.001 && maxScore > 0) {
                    row.pct.value = Math.round((minVal / maxScore) * 100);
                } else {
                    row.pct.value = '';
                }
            }
        }

        if (hidden.min) hidden.min.value = minVal;
        if (hidden.max) hidden.max.value = maxVal;
        _setCardState(card, minVal, maxVal, maxScore);
        _validateRow(row.pct, row.minInp, row.maxInp);
    }

    function _updateTotals(dsId) {
        var ui = _getUI(dsId);
        var calcAuto = ui.calcCheckbox && ui.calcCheckbox.checked;

        [ui.advTotalPct, ui.advTotalMin, ui.advTotalMax].forEach(function (el) {
            if (el) el.readOnly = calcAuto;
        });

        if (calcAuto) {
            var totalMin = 0, totalMax = 0;
            ui.cards.forEach(function (card) {
                var h = ui.stHidden(card.dataset.idx);
                totalMin += _parse(h.min ? h.min.value : 0);
                totalMax += _parse(h.max ? h.max.value : 0);
            });

            if (ui.advTotalMin) ui.advTotalMin.value = totalMin.toFixed(2);
            if (ui.advTotalMax) ui.advTotalMax.value = totalMax.toFixed(2);

            var el = ui.advTotalMax || ui.advTotalMin;
            var totalScore = el ? _parse(el.dataset.totalScore) : 0;
            if (ui.advTotalPct && totalScore > 0) {
                // Show percentage only when min and max are the same, hide when they differ
                if (Math.abs(totalMin - totalMax) < 0.001) {
                    ui.advTotalPct.value = Math.round((totalMax / totalScore) * 100);
                } else {
                    ui.advTotalPct.value = '';
                }
            }

            if (ui.scoreMin) ui.scoreMin.value = totalMin;
            if (ui.scoreMax) ui.scoreMax.value = totalMax;
            if (ui.totalDisplay) ui.totalDisplay.textContent = Math.round(totalMax);
            _flagInput(ui.advTotalMin, '');
            _flagInput(ui.advTotalMax, '');
            _flagInput(ui.advTotalPct, '');
        } else {
            var manMin = _parse(ui.advTotalMin ? ui.advTotalMin.value : 0);
            var manMax = _parse(ui.advTotalMax ? ui.advTotalMax.value : 0);
            if (ui.scoreMin) ui.scoreMin.value = manMin;
            if (ui.scoreMax) ui.scoreMax.value = manMax;
            if (ui.totalDisplay) ui.totalDisplay.textContent = Math.round(manMax);
            _validateRow(ui.advTotalPct, ui.advTotalMin, ui.advTotalMax);
        }
    }

    function _recalcAllFromCards(dsId) {
        var ui = _getUI(dsId);
        ui.cards.forEach(function (card) {
            _updateSubtask(ui, dsId, card.dataset.idx, 'card');
        });
        _updateTotals(dsId);
    }

    function _recalcAllFromPct(dsId) {
        var ui = _getUI(dsId);
        ui.cards.forEach(function (card) {
            _updateSubtask(ui, dsId, card.dataset.idx, 'pct');
        });
        _updateTotals(dsId);
    }

    function _recalcAllFromValues(dsId) {
        var ui = _getUI(dsId);
        ui.cards.forEach(function (card) {
            _updateSubtask(ui, dsId, card.dataset.idx, 'val');
        });
        _updateTotals(dsId);
    }

    function _resetModal(dsId) {
        var ui = _getUI(dsId);
        if (!ui.form) return;
        ui.form.reset();
        var modeInput = ui.form.querySelector('input[name="_ms_mode"]');
        if (modeInput) modeInput.value = 'add';
        if (ui.title) ui.title.textContent = 'Add Model Solution';
        if (ui.submit) ui.submit.textContent = 'Add Model Solution';
        if (ui.fileSection) ui.fileSection.style.display = '';
        if (ui.nameInput) { ui.nameInput.value = ''; ui.nameInput.readOnly = false; }
        if (ui.descInput) ui.descInput.value = '';

        ui.cards.forEach(function (c) { c.classList.remove('partial'); c.classList.add('selected'); });
        if (ui.globalPct) ui.globalPct.value = 100;

        var advPcts = document.querySelectorAll('.ms-adv-pct[data-dataset="' + dsId + '"]');
        advPcts.forEach(function (inp) { inp.value = 100; });

        if (ui.advSection) ui.advSection.style.display = 'none';
        _advancedOpen[dsId] = false;
        if (ui.advArrow) ui.advArrow.style.transform = '';
        if (ui.advLabel) ui.advLabel.textContent = 'Show Advanced Scoring';
        if (ui.calcCheckbox) ui.calcCheckbox.checked = true;

        _recalcAllFromCards(dsId);
    }

    return {
        openAdd: function(dsId, addUrl) {
            _resetModal(dsId);
            var ui = _getUI(dsId);
            if (ui.form && addUrl) ui.form.action = addUrl;
            MicroModal.show('modal-ms-' + dsId);
        },

        openEdit: function(dsId, editUrl, name, description, scoreMin, scoreMax, subtaskScores) {
            _resetModal(dsId);
            var ui = _getUI(dsId);
            if (!ui.form) return;

            ui.form.action = editUrl;
            var modeInput = ui.form.querySelector('input[name="_ms_mode"]');
            if (modeInput) modeInput.value = 'edit';
            if (ui.title) ui.title.textContent = 'Edit Model Solution';
            if (ui.submit) ui.submit.textContent = 'Save Changes';
            if (ui.fileSection) ui.fileSection.style.display = 'none';
            if (ui.nameInput) ui.nameInput.value = name || '';
            if (ui.descInput) ui.descInput.value = description || '';

            if (subtaskScores) {
                var hasPartial = false;
                ui.cards.forEach(function (card) {
                    var idx = card.dataset.idx;
                    var maxScore = _parse(card.dataset.max);
                    var stData = subtaskScores[idx] || subtaskScores[String(idx)];
                    var row = ui.advRow(idx);

                    card.classList.remove('selected', 'partial');
                    if (row.pct) row.pct.value = 0;

                    if (stData) {
                        var stMin = _parse(stData.min);
                        var stMax = _parse(stData.max);
                        if (stMax > 0) {
                            card.classList.add('selected');
                            if (Math.abs(stMin - stMax) >= 0.001) {
                                hasPartial = true;
                                if (row.pct) row.pct.value = '';
                            } else if (maxScore > 0) {
                                var pct = Math.round((stMax / maxScore) * 100);
                                if (pct !== 100 && pct !== 0) hasPartial = true;
                                if (row.pct) row.pct.value = pct;
                            }
                        }
                        if (row.minInp) row.minInp.value = stMin.toFixed(2);
                        if (row.maxInp) row.maxInp.value = stMax.toFixed(2);
                    } else {
                        if (row.minInp) row.minInp.value = "0.00";
                        if (row.maxInp) row.maxInp.value = "0.00";
                    }
                });

                if (hasPartial) {
                    ModelSolutionModal.toggleAdvanced(dsId);
                }
                _recalcAllFromValues(dsId);
            } else {
                ui.cards.forEach(function (c) { c.classList.remove('selected', 'partial'); });
                if (ui.calcCheckbox) ui.calcCheckbox.checked = false;
                var sm = _parse(scoreMin, 0);
                var sx = _parse(scoreMax, 0);
                if (ui.advTotalMin) ui.advTotalMin.value = sm.toFixed(2);
                if (ui.advTotalMax) ui.advTotalMax.value = sx.toFixed(2);
                if (ui.advTotalPct) ui.advTotalPct.value = '';
                _recalcAllFromCards(dsId);
                if (ui.scoreMinSimple) ui.scoreMinSimple.value = sm;
                if (ui.scoreMaxSimple) ui.scoreMaxSimple.value = _parse(scoreMax, 100);
            }

            MicroModal.show('modal-ms-' + dsId);
        },

        toggleCard: function(cardEl) {
            var dsId = cardEl.closest('.ms-expected-results').id.replace('ms-', '').replace('-expected', '');
            if (_advancedOpen[dsId]) return;
            var wasActive = cardEl.classList.contains('selected') || cardEl.classList.contains('partial');
            cardEl.classList.remove('selected', 'partial');
            if (!wasActive) cardEl.classList.add('selected');
            _recalcAllFromCards(dsId);
        },

        globalPctChanged: function (input) {
            _validateAndClampPercentage(input);
            _recalcAllFromCards(input.dataset.dataset);
        },

        toggleAdvanced: function(dsId) {
            var ui = _getUI(dsId);
            if (!ui.advSection) return;

            _advancedOpen[dsId] = !_advancedOpen[dsId];
            if (_advancedOpen[dsId]) {
                ui.advSection.style.display = '';
                if (ui.advArrow) ui.advArrow.style.transform = 'rotate(180deg)';
                if (ui.advLabel) ui.advLabel.textContent = 'Hide Advanced Scoring';
            } else {
                ui.advSection.style.display = 'none';
                if (ui.advArrow) ui.advArrow.style.transform = '';
                if (ui.advLabel) ui.advLabel.textContent = 'Show Advanced Scoring';
                _recalcAllFromCards(dsId);
            }
        },

        pctChanged: function(input) {
            var dsId = input.dataset.dataset;
            var ui = _getUI(dsId);
            _updateSubtask(ui, dsId, input.dataset.idx, 'pct');
            _updateTotals(dsId);
        },

        minMaxChanged: function(input) {
            var dsId = input.dataset.dataset;
            var ui = _getUI(dsId);
            _updateSubtask(ui, dsId, input.dataset.idx, 'val');
            _updateTotals(dsId);
        },

        totalPctChanged: function(input) {
            var dsId = input.dataset.dataset;
            var ui = _getUI(dsId);
            var totalScore = _parse(input.dataset.totalScore);
            var pct = _validateAndClampPercentage(input);

            var score = totalScore * pct / 100;

            if (ui.advTotalMin) ui.advTotalMin.value = score.toFixed(2);
            if (ui.advTotalMax) ui.advTotalMax.value = score.toFixed(2);
            if (ui.scoreMin) ui.scoreMin.value = score;
            if (ui.scoreMax) ui.scoreMax.value = score;
            if (ui.totalDisplay) ui.totalDisplay.textContent = Math.round(score);
        },

        totalMinMaxChanged: function(input) {
            var dsId = input.dataset.dataset;
            var ui = _getUI(dsId);
            if (ui.advTotalPct) ui.advTotalPct.value = '';

            var totalMin = _parse(ui.advTotalMin ? ui.advTotalMin.value : 0);
            var totalMax = _parse(ui.advTotalMax ? ui.advTotalMax.value : 0);

            if (ui.scoreMin) ui.scoreMin.value = totalMin;
            if (ui.scoreMax) ui.scoreMax.value = totalMax;
            if (ui.totalDisplay) ui.totalDisplay.textContent = Math.round(totalMax);
            _validateRow(ui.advTotalPct, ui.advTotalMin, ui.advTotalMax);
        },

        calcFromSubtasksChanged: function(checkbox) {
            _updateTotals(checkbox.dataset.dataset);
        }
    };
})();


// Form utilities (initDateTimeValidation, initReadOnlyTagify, initTagify)
// have been moved to aws_form_utils.js for better code organization.
// Backward compatibility aliases are set up in aws_form_utils.js.
