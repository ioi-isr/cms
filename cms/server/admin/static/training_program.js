/**
 * Training Program JavaScript Utilities
 * Centralized JS for training program pages
 */

(function(CMS) {
    'use strict';

    CMS.TrainingProgram = CMS.TrainingProgram || {};

    // Histogram modal state
    var histogramModal = null;
    var histogramTagify = null;
    var currentHistogramData = null;

    // Configuration passed from templates
    var config = {
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
     * Initialize the training program module with data from templates
     * @param {Object} options - Configuration options
     */
    CMS.TrainingProgram.init = function(options) {
        if (options) {
            Object.keys(options).forEach(function(key) {
                if (config.hasOwnProperty(key)) {
                    config[key] = options[key];
                }
            });
        }
    };

    /**
     * Initialize histogram modal
     */
    CMS.TrainingProgram.initHistogramModal = function() {
        histogramModal = document.getElementById('histogramModal');
        if (!histogramModal) return;

        var histogramTagsInput = document.getElementById('histogramTagsFilter');
        if (histogramTagsInput && typeof Tagify !== 'undefined') {
            histogramTagify = new Tagify(histogramTagsInput, {
                delimiters: ",",
                whitelist: config.allStudentTags,
                enforceWhitelist: true,
                editTags: false,
                dropdown: { enabled: 0, maxItems: 20, closeOnSelect: true },
                originalInputValueFormat: function(valuesArr) {
                    return valuesArr.map(function(item) { return item.value; }).join(', ');
                }
            });
            histogramTagify.on('change', function() {
                if (currentHistogramData) {
                    renderHistogram(currentHistogramData.scores, currentHistogramData.title, currentHistogramData.type);
                }
            });
        }

        histogramModal.addEventListener('click', function(e) {
            if (e.target === histogramModal) {
                CMS.TrainingProgram.closeHistogramModal();
            }
        });

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' && histogramModal.style.display === 'flex') {
                CMS.TrainingProgram.closeHistogramModal();
            }
        });
    };

    /**
     * Open histogram modal with score data
     */
    CMS.TrainingProgram.openHistogramModal = function(scores, title, type, trainingDayId, maxPossibleScore) {
        if (!histogramModal) return;
        currentHistogramData = {
            scores: scores,
            title: title,
            type: type,
            trainingDayId: trainingDayId,
            maxPossibleScore: (maxPossibleScore === undefined || maxPossibleScore === null) ? 100 : maxPossibleScore
        };
        document.getElementById('histogramTitle').textContent = title + ' - Score Distribution';

        if (histogramTagify && trainingDayId && config.tagsPerTrainingDay[trainingDayId]) {
            histogramTagify.settings.whitelist = config.tagsPerTrainingDay[trainingDayId];
            histogramTagify.removeAllTags();
        } else if (histogramTagify) {
            histogramTagify.settings.whitelist = config.allStudentTags;
            histogramTagify.removeAllTags();
        }

        histogramModal.style.display = 'flex';
        renderHistogram(scores, title, type);
    };

    /**
     * Close histogram modal
     */
    CMS.TrainingProgram.closeHistogramModal = function() {
        if (histogramModal) {
            histogramModal.style.display = 'none';
        }
        currentHistogramData = null;
    };

    /**
     * Copy histogram data to clipboard
     */
    CMS.TrainingProgram.copyHistogramData = function() {
        var textArea = document.getElementById('histogramTextData');
        var textToCopy = textArea.value;
        var btn = document.querySelector('.copy-btn');
        var originalText = btn.textContent;

        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(textToCopy).then(function() {
                btn.textContent = 'Copied!';
                setTimeout(function() { btn.textContent = originalText; }, 2000);
            }).catch(function() {
                fallbackCopy(textArea, btn, originalText);
            });
        } else {
            fallbackCopy(textArea, btn, originalText);
        }
    };

    function fallbackCopy(textArea, btn, originalText) {
        textArea.select();
        document.execCommand('copy');
        btn.textContent = 'Copied!';
        setTimeout(function() { btn.textContent = originalText; }, 2000);
    }

    function getFilteredScores(scores) {
        var filterTags = [];
        if (histogramTagify) {
            var tagifyValue = histogramTagify.value;
            if (tagifyValue && tagifyValue.length > 0) {
                filterTags = tagifyValue.map(function(t) { return t.value; });
            }
        }

        if (filterTags.length === 0) {
            return scores;
        }

        var trainingDayId = currentHistogramData ? currentHistogramData.trainingDayId : null;

        return scores.filter(function(item) {
            var studentTags = [];

            if (trainingDayId && config.historicalStudentTags[trainingDayId] && config.historicalStudentTags[trainingDayId][item.studentId]) {
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
    }

    function calculateFilteredMaxScore(filteredScores, trainingDayId, type) {
        if (type === 'task') {
            return currentHistogramData ? currentHistogramData.maxPossibleScore : 100;
        }

        if (type === 'training_day' && trainingDayId && config.trainingDayTasks[trainingDayId]) {
            var accessibleTasksSet = new Set();
            filteredScores.forEach(function(item) {
                var studentTasks = config.studentAccessibleTasks[trainingDayId] && config.studentAccessibleTasks[trainingDayId][item.studentId];
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

            return maxScore > 0 ? maxScore : (currentHistogramData ? currentHistogramData.maxPossibleScore : 100);
        }

        return currentHistogramData ? currentHistogramData.maxPossibleScore : 100;
    }

    function renderHistogram(scores, title, type) {
        var filteredScores = getFilteredScores(scores);
        var scoreValues = filteredScores.map(function(s) { return s.score; });

        scoreValues.sort(function(a, b) { return b - a; });

        var trainingDayId = currentHistogramData ? currentHistogramData.trainingDayId : null;
        var maxPossibleScore = calculateFilteredMaxScore(filteredScores, trainingDayId, type);

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

            for (var i = 1; i <= 9; i++) {
                var upperBound = Math.round(i * bucketSize);
                var lowerBound = Math.round((i - 1) * bucketSize);
                var key = upperBound.toString();
                buckets[key] = 0;
                bucketLabels[key] = '(' + lowerBound + ',' + upperBound + ']';
                bucketOrder.push(key);
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
                    var bucketKey = Math.round(bucketIndex * bucketSize).toString();
                    buckets[bucketKey]++;
                }
            });
        }

        var histogramBars = document.getElementById('histogramBars');
        var maxCount = Math.max.apply(null, Object.values(buckets)) || 1;
        var totalStudents = scoreValues.length;

        var barsHtml = '';
        bucketOrder.forEach(function(bucketKey, index) {
            var count = buckets[bucketKey] || 0;
            var percentage = totalStudents > 0 ? ((count / totalStudents) * 100).toFixed(1) : 0;
            var barHeight = maxCount > 0 ? (count / maxCount) * 100 : 0;
            var hue = (index / (bucketOrder.length - 1)) * 120;

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

        document.getElementById('histogramStats').innerHTML =
            '<strong>Total students:</strong> ' + totalStudents +
            ' | <strong>Max possible:</strong> ' + Math.round(maxPossibleScore) +
            (scoreValues.length > 0 ? ' | <strong>Average:</strong> ' + (scoreValues.reduce(function(a, b) { return a + b; }, 0) / scoreValues.length).toFixed(1) +
            ' | <strong>Median:</strong> ' + median.toFixed(1) +
            ' | <strong>Max:</strong> ' + Math.max.apply(null, scoreValues).toFixed(1) +
            ' | <strong>Min:</strong> ' + Math.min.apply(null, scoreValues).toFixed(1) : '');

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

        document.getElementById('histogramTextData').value = textData;
    }

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

})(window.CMS = window.CMS || {});
