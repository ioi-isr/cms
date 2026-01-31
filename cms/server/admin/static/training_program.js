/* Contest Management System
 * Copyright © 2024 IOI-ISR
 *
 * Training Program JavaScript Utilities
 * Centralized JS for training program pages (histogram modal, etc.)
 */

"use strict";

var CMS = CMS || {};

/**
 * Training Program utilities namespace.
 * Provides histogram modal functionality and other training program specific features.
 */
CMS.TrainingProgram = CMS.TrainingProgram || {};

// Module state (stored on namespace for access by methods)
CMS.TrainingProgram._histogramModal = null;
CMS.TrainingProgram._histogramTagify = null;
CMS.TrainingProgram._currentHistogramData = null;

// Configuration (set via init)
CMS.TrainingProgram._config = {
    allStudentTags: [],
    tagsPerTrainingDay: {},
    historicalStudentTags: {},
    studentData: {},
    trainingDayTasks: {},
    studentAccessibleTasks: {},
    taskMaxScores: {},
    taskMaxScoresByTrainingDay: {}
};


/**
 * Initialize the training program module with data from templates.
 *
 * options (object): Configuration options containing:
 *   - allStudentTags (array): List of all student tags
 *   - tagsPerTrainingDay (object): Tags available per training day
 *   - historicalStudentTags (object): Historical tags per training day per student
 *   - studentData (object): Student information keyed by student ID
 *   - trainingDayTasks (object): Tasks per training day
 *   - studentAccessibleTasks (object): Accessible tasks per student per training day
 *   - taskMaxScores (object): Max scores per task
 *   - taskMaxScoresByTrainingDay (object): Max scores per task per training day
 */
CMS.TrainingProgram.init = function(options) {
    if (options) {
        var config = CMS.TrainingProgram._config;
        Object.keys(options).forEach(function(key) {
            if (config.hasOwnProperty(key)) {
                config[key] = options[key];
            }
        });
    }
};


/**
 * Initialize the histogram modal.
 * Sets up Tagify for filtering and event listeners for closing.
 */
CMS.TrainingProgram.initHistogramModal = function() {
    var modal = document.getElementById('histogramModal');
    if (!modal) return;

    CMS.TrainingProgram._histogramModal = modal;

    var histogramTagsInput = document.getElementById('histogramTagsFilter');
    if (histogramTagsInput && typeof Tagify !== 'undefined') {
        CMS.TrainingProgram._histogramTagify = new Tagify(histogramTagsInput, {
            delimiters: ",",
            whitelist: CMS.TrainingProgram._config.allStudentTags,
            enforceWhitelist: true,
            editTags: false,
            dropdown: { enabled: 0, maxItems: 20, closeOnSelect: true },
            originalInputValueFormat: function(valuesArr) {
                return valuesArr.map(function(item) { return item.value; }).join(', ');
            }
        });
        CMS.TrainingProgram._histogramTagify.on('change', function() {
            var data = CMS.TrainingProgram._currentHistogramData;
            if (data) {
                CMS.TrainingProgram._renderHistogram(data.scores, data.title, data.type);
            }
        });
    }

    modal.addEventListener('click', function(e) {
        if (e.target === modal) {
            CMS.TrainingProgram.closeHistogramModal();
        }
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && modal.style.display === 'flex') {
            CMS.TrainingProgram.closeHistogramModal();
        }
    });
};


/**
 * Open the histogram modal with score data.
 *
 * scores (array): Array of {studentId, score} objects
 * title (string): Title for the histogram
 * type (string): Type of histogram ('task' or 'training_day')
 * trainingDayId (number): ID of the training day
 * maxPossibleScore (number): Maximum possible score
 */
CMS.TrainingProgram.openHistogramModal = function(scores, title, type, trainingDayId, maxPossibleScore) {
    var modal = CMS.TrainingProgram._histogramModal;
    if (!modal) return;

    CMS.TrainingProgram._currentHistogramData = {
        scores: scores,
        title: title,
        type: type,
        trainingDayId: trainingDayId,
        maxPossibleScore: (maxPossibleScore === undefined || maxPossibleScore === null) ? 100 : maxPossibleScore
    };

    var titleEl = document.getElementById('histogramTitle');
    if (titleEl) {
        titleEl.textContent = title + ' - Score Distribution';
    }

    var tagify = CMS.TrainingProgram._histogramTagify;
    var config = CMS.TrainingProgram._config;
    if (tagify && trainingDayId && config.tagsPerTrainingDay[trainingDayId]) {
        tagify.settings.whitelist = config.tagsPerTrainingDay[trainingDayId];
        tagify.removeAllTags();
    } else if (tagify) {
        tagify.settings.whitelist = config.allStudentTags;
        tagify.removeAllTags();
    }

    modal.style.display = 'flex';
    CMS.TrainingProgram._renderHistogram(scores, title, type);
};


/**
 * Close the histogram modal.
 */
CMS.TrainingProgram.closeHistogramModal = function() {
    var modal = CMS.TrainingProgram._histogramModal;
    if (modal) {
        modal.style.display = 'none';
    }
    CMS.TrainingProgram._currentHistogramData = null;
};


/**
 * Copy histogram data to clipboard.
 */
CMS.TrainingProgram.copyHistogramData = function() {
    var textArea = document.getElementById('histogramTextData');
    if (!textArea) return;

    var textToCopy = textArea.value;
    var btn = document.querySelector('.copy-btn');
    var originalText = btn ? btn.textContent : 'Copy';

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(textToCopy).then(function() {
            if (btn) {
                btn.textContent = 'Copied!';
                setTimeout(function() { btn.textContent = originalText; }, 2000);
            }
        }).catch(function() {
            CMS.TrainingProgram._fallbackCopy(textArea, btn, originalText);
        });
    } else {
        CMS.TrainingProgram._fallbackCopy(textArea, btn, originalText);
    }
};


/**
 * Fallback copy method using execCommand.
 * @private
 */
CMS.TrainingProgram._fallbackCopy = function(textArea, btn, originalText) {
    textArea.select();
    document.execCommand('copy');
    if (btn) {
        btn.textContent = 'Copied!';
        setTimeout(function() { btn.textContent = originalText; }, 2000);
    }
};


/**
 * Get filtered scores based on selected tags.
 * @private
 */
CMS.TrainingProgram._getFilteredScores = function(scores) {
    var tagify = CMS.TrainingProgram._histogramTagify;
    var filterTags = [];

    if (tagify) {
        var tagifyValue = tagify.value;
        if (tagifyValue && tagifyValue.length > 0) {
            filterTags = tagifyValue.map(function(t) { return t.value; });
        }
    }

    if (filterTags.length === 0) {
        return scores;
    }

    var data = CMS.TrainingProgram._currentHistogramData;
    var trainingDayId = data ? data.trainingDayId : null;
    var config = CMS.TrainingProgram._config;

    return scores.filter(function(item) {
        var studentTags = [];

        if (trainingDayId && config.historicalStudentTags[trainingDayId] &&
            config.historicalStudentTags[trainingDayId][item.studentId]) {
            studentTags = config.historicalStudentTags[trainingDayId][item.studentId];
        } else {
            var studentInfo = config.studentData[item.studentId];
            if (studentInfo) {
                studentTags = studentInfo.tags;
            }
        }

        if (!studentTags || studentTags.length === 0) return false;
        return filterTags.every(function(tag) {
            return studentTags.indexOf(tag) !== -1;
        });
    });
};


/**
 * Calculate the maximum score for filtered students.
 * @private
 */
CMS.TrainingProgram._calculateFilteredMaxScore = function(filteredScores, trainingDayId, type) {
    var data = CMS.TrainingProgram._currentHistogramData;
    var config = CMS.TrainingProgram._config;

    if (type === 'task') {
        return data ? data.maxPossibleScore : 100;
    }

    if (type === 'training_day' && trainingDayId && config.trainingDayTasks[trainingDayId]) {
        var accessibleTasksSet = new Set();
        filteredScores.forEach(function(item) {
            var studentTasks = config.studentAccessibleTasks[trainingDayId] &&
                               config.studentAccessibleTasks[trainingDayId][item.studentId];
            if (studentTasks) {
                studentTasks.forEach(function(taskId) {
                    accessibleTasksSet.add(taskId);
                });
            }
        });

        var maxScore = 0;
        accessibleTasksSet.forEach(function(taskId) {
            var taskMaxScore = 0;
            if (trainingDayId && config.taskMaxScoresByTrainingDay[trainingDayId]) {
                taskMaxScore = config.taskMaxScoresByTrainingDay[trainingDayId][taskId] || 0;
            } else {
                taskMaxScore = config.taskMaxScores[taskId] || 0;
            }
            maxScore += taskMaxScore;
        });

        return maxScore > 0 ? maxScore : (data ? data.maxPossibleScore : 100);
    }

    return data ? data.maxPossibleScore : 100;
};


/**
 * Render the histogram with the given scores.
 * @private
 */
CMS.TrainingProgram._renderHistogram = function(scores, title, type) {
    var filteredScores = CMS.TrainingProgram._getFilteredScores(scores);
    var scoreValues = filteredScores.map(function(s) { return s.score; });

    scoreValues.sort(function(a, b) { return b - a; });

    var data = CMS.TrainingProgram._currentHistogramData;
    var trainingDayId = data ? data.trainingDayId : null;
    var maxPossibleScore = CMS.TrainingProgram._calculateFilteredMaxScore(filteredScores, trainingDayId, type);

    var buckets = {};
    var bucketLabels = {};
    var bucketOrder = [];

    if (maxPossibleScore === 0) {
        maxPossibleScore = 1;
    }

    if (maxPossibleScore <= 15) {
        var maxInt = Math.ceil(maxPossibleScore);

        for (var i = 0; i <= maxInt; i++) {
            var key = i.toString();
            buckets[key] = 0;
            bucketLabels[key] = key;
            bucketOrder.push(key);
        }

        scoreValues.forEach(function(score) {
            var rounded = Math.round(score);
            if (rounded > maxInt) rounded = maxInt;
            if (rounded < 0) rounded = 0;
            buckets[rounded.toString()]++;
        });
    } else {
        var bucketSize = maxPossibleScore / 10;
        var lastBucketThreshold = maxPossibleScore * 0.9;

        buckets['0'] = 0;
        bucketLabels['0'] = '0';
        bucketOrder.push('0');

        for (var j = 1; j <= 9; j++) {
            var upperBound = Math.round(j * bucketSize);
            var lowerBound = Math.round((j - 1) * bucketSize);
            var bucketKey = upperBound.toString();
            buckets[bucketKey] = 0;
            bucketLabels[bucketKey] = '(' + lowerBound + ',' + upperBound + ']';
            bucketOrder.push(bucketKey);
        }

        var lastKey = Math.round(maxPossibleScore).toString();
        buckets[lastKey] = 0;
        bucketLabels[lastKey] = '>' + Math.round(lastBucketThreshold);
        bucketOrder.push(lastKey);

        scoreValues.forEach(function(score) {
            if (score === 0) {
                buckets['0']++;
            } else if (score > lastBucketThreshold) {
                buckets[lastKey]++;
            } else {
                var bucketIndex = Math.ceil(score / bucketSize);
                if (bucketIndex < 1) bucketIndex = 1;
                if (bucketIndex > 9) bucketIndex = 9;
                var bKey = Math.round(bucketIndex * bucketSize).toString();
                buckets[bKey]++;
            }
        });
    }

    var histogramBars = document.getElementById('histogramBars');
    if (!histogramBars) return;

    var maxCount = Math.max.apply(null, Object.values(buckets)) || 1;
    var totalStudents = scoreValues.length;

    var barsHtml = '';
    bucketOrder.forEach(function(bucketKey, index) {
        var count = buckets[bucketKey] || 0;
        var percentage = totalStudents > 0 ? ((count / totalStudents) * 100).toFixed(1) : 0;
        var barHeight = maxCount > 0 ? (count / maxCount) * 100 : 0;
        var hue = bucketOrder.length > 1 ? (index / (bucketOrder.length - 1)) * 120 : 60;

        barsHtml += '<div class="histogram-bar-container">' +
            '<div class="histogram-bar-wrapper">' +
            '<div class="histogram-bar" style="height: ' + barHeight + '%; background-color: hsl(' + hue + ', 75%, 60%);" title="' + count + ' students (' + percentage + '%)"></div>' +
            '</div>' +
            '<div class="histogram-label">' + bucketLabels[bucketKey] + '</div>' +
            '<div class="histogram-count">' + count + '</div>' +
            '</div>';
    });
    histogramBars.innerHTML = barsHtml;

    var median = 0;
    if (scoreValues.length > 0) {
        var sorted = scoreValues.slice().sort(function(a, b) { return a - b; });
        var mid = Math.floor(sorted.length / 2);
        if (sorted.length % 2 === 0) {
            median = (sorted[mid - 1] + sorted[mid]) / 2;
        } else {
            median = sorted[mid];
        }
    }

    var statsEl = document.getElementById('histogramStats');
    if (statsEl) {
        statsEl.innerHTML =
            '<strong>Total students:</strong> ' + totalStudents +
            ' | <strong>Max possible:</strong> ' + Math.round(maxPossibleScore) +
            (scoreValues.length > 0 ? ' | <strong>Average:</strong> ' + (scoreValues.reduce(function(a, b) { return a + b; }, 0) / scoreValues.length).toFixed(1) +
            ' | <strong>Median:</strong> ' + median.toFixed(1) +
            ' | <strong>Max:</strong> ' + Math.max.apply(null, scoreValues).toFixed(1) +
            ' | <strong>Min:</strong> ' + Math.min.apply(null, scoreValues).toFixed(1) : '');
    }

    var textData = title + ' - Score Distribution\n';
    textData += '================================\n\n';
    textData += 'Statistics:\n';
    textData += 'Total: ' + totalStudents + '\n';
    textData += 'Max possible score: ' + Math.round(maxPossibleScore) + '\n';
    if (scoreValues.length > 0) {
        textData += 'Average: ' + (scoreValues.reduce(function(a, b) { return a + b; }, 0) / scoreValues.length).toFixed(1) + '\n';
        textData += 'Median: ' + median.toFixed(1) + '\n';
        textData += 'Max: ' + Math.max.apply(null, scoreValues).toFixed(1) + '\n';
        textData += 'Min: ' + Math.min.apply(null, scoreValues).toFixed(1) + '\n';
    }
    textData += '\nScores (high to low):\n';

    var scoreGroups = {};
    scoreValues.forEach(function(score) {
        var roundedScore = score.toFixed(1);
        scoreGroups[roundedScore] = (scoreGroups[roundedScore] || 0) + 1;
    });

    var sortedScoreKeys = Object.keys(scoreGroups).sort(function(a, b) { return parseFloat(b) - parseFloat(a); });
    sortedScoreKeys.forEach(function(score) {
        var count = scoreGroups[score];
        var pct = totalStudents > 0 ? ((count / totalStudents) * 100).toFixed(1) : 0;
        textData += score + ': ' + count + ' student' + (count !== 1 ? 's' : '') + ' (' + pct + '%)\n';
    });

    textData += '\nHistogram buckets:\n';
    var reverseBucketOrder = bucketOrder.slice().reverse();
    reverseBucketOrder.forEach(function(bucketKey) {
        var count = buckets[bucketKey] || 0;
        var pct = totalStudents > 0 ? ((count / totalStudents) * 100).toFixed(1) : 0;
        textData += bucketLabels[bucketKey] + ': ' + count + ' (' + pct + '%)\n';
    });

    var textDataEl = document.getElementById('histogramTextData');
    if (textDataEl) {
        textDataEl.value = textData;
    }
};


// Expose functions globally for backwards compatibility with onclick handlers
window.openHistogramModal = function(scores, title, type, trainingDayId, maxPossibleScore) {
    CMS.TrainingProgram.openHistogramModal(scores, title, type, trainingDayId, maxPossibleScore);
};
window.closeHistogramModal = CMS.TrainingProgram.closeHistogramModal;
window.copyHistogramData = CMS.TrainingProgram.copyHistogramData;


// Auto-initialize histogram modal on DOM ready
$(document).ready(function() {
    CMS.TrainingProgram.initHistogramModal();
});
