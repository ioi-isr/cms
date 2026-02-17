#!/usr/bin/env python3

"""Unit tests for training program analysis functions.

Tests cover:
- Time decay weight calculation
- Location weight modification
- Training type weight correction (including 0%, leftover, partial specification)
- Score normalization (none, rank, median, mean)
- Weighted average calculation
- Helper functions (smoothed median, MAD, etc.)
"""

import math
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from cms.server.admin.handlers.analysis import (
    StudentTrainingDayInfo,
    calculate_time_decay_weights,
    apply_location_weights,
    apply_training_type_correction,
    get_raw_scores,
    normalize_scores_none,
    normalize_scores_rank,
    normalize_scores_median,
    normalize_scores_mean,
    normalize_scores,
    calculate_weighted_averages,
    compute_monthly_decay,
    _smoothed_median,
    _mean,
    _std_dev,
    _mad,
    _collect_reference_scores,
    _top_x_scores,
    MAD_MULTIPLIER,
)


def make_td(td_id, start_time=None, training_day_types=None):
    td = MagicMock()
    td.id = td_id
    td.start_time = start_time or datetime(2026, 1, 1)
    td.training_day_types = training_day_types or []
    return td


def make_info(student_id, td_id, score=0.0, location="class",
              recorded=False, status="participated", justified=False):
    return StudentTrainingDayInfo(
        student_id=student_id,
        training_day_id=td_id,
        score=score,
        location=location,
        recorded=recorded,
        status=status,
        justified=justified,
    )


class TestHelpers(unittest.TestCase):

    def test_smoothed_median_empty(self):
        self.assertEqual(_smoothed_median([]), 0.0)

    def test_smoothed_median_single(self):
        self.assertEqual(_smoothed_median([7.0]), 7.0)

    def test_smoothed_median_two(self):
        self.assertAlmostEqual(_smoothed_median([3.0, 5.0]), 4.0)

    def test_smoothed_median_even(self):
        self.assertAlmostEqual(_smoothed_median([1, 2, 3, 4]), 2.5)

    def test_smoothed_median_odd_three(self):
        self.assertAlmostEqual(_smoothed_median([1, 2, 3]), 2.0)

    def test_smoothed_median_odd_five(self):
        result = _smoothed_median([10, 20, 30, 40, 50])
        self.assertAlmostEqual(result, (20 + 30 + 40) / 3.0)

    def test_mean_empty(self):
        self.assertEqual(_mean([]), 0.0)

    def test_mean_values(self):
        self.assertAlmostEqual(_mean([10, 20, 30]), 20.0)

    def test_std_dev_single(self):
        self.assertEqual(_std_dev([5.0], 5.0), 1.0)

    def test_std_dev_values(self):
        vals = [2.0, 4.0, 6.0]
        mean = _mean(vals)
        sd = _std_dev(vals, mean)
        expected = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
        self.assertAlmostEqual(sd, expected)

    def test_mad_single(self):
        self.assertEqual(_mad([5.0], 5.0), 1.0)

    def test_mad_values(self):
        vals = [1, 2, 3, 4, 5]
        median = 3.0
        abs_devs = sorted(abs(v - median) for v in vals)
        expected_mad = abs_devs[len(abs_devs) // 2]
        self.assertAlmostEqual(_mad(vals, median), expected_mad)

    def test_top_x_scores(self):
        self.assertEqual(_top_x_scores([5, 3, 8, 1, 9], 3), [9, 8, 5])

    def test_top_x_fewer_than_x(self):
        self.assertEqual(_top_x_scores([2, 7], 5), [7, 2])


class TestTimeDecayWeights(unittest.TestCase):

    def test_empty_training_days(self):
        self.assertEqual(calculate_time_decay_weights([], 65), {})

    def test_no_decay(self):
        tds = [make_td(1, datetime(2026, 1, 1)), make_td(2, datetime(2026, 2, 1))]
        weights = calculate_time_decay_weights(tds, 100)
        self.assertAlmostEqual(weights[1], 1.0)
        self.assertAlmostEqual(weights[2], 1.0)

    def test_same_date(self):
        d = datetime(2026, 1, 15)
        tds = [make_td(1, d), make_td(2, d)]
        weights = calculate_time_decay_weights(tds, 50)
        self.assertAlmostEqual(weights[1], 1.0)
        self.assertAlmostEqual(weights[2], 1.0)

    def test_linear_decay(self):
        d1 = datetime(2026, 1, 1)
        d2 = datetime(2026, 1, 16)
        d3 = datetime(2026, 1, 31)
        tds = [make_td(1, d1), make_td(2, d2), make_td(3, d3)]
        weights = calculate_time_decay_weights(tds, 50)
        self.assertAlmostEqual(weights[1], 0.5)
        self.assertAlmostEqual(weights[3], 1.0)
        self.assertAlmostEqual(weights[2], 0.75, places=2)

    def test_decay_endpoints(self):
        d_earliest = datetime(2026, 1, 1)
        d_latest = datetime(2026, 4, 1)
        tds = [make_td(1, d_earliest), make_td(2, d_latest)]
        weights = calculate_time_decay_weights(tds, 65)
        self.assertAlmostEqual(weights[1], 0.65)
        self.assertAlmostEqual(weights[2], 1.0)


class TestLocationWeights(unittest.TestCase):

    def test_class_no_change(self):
        base = {1: 0.8}
        info = {10: {1: make_info(10, 1, score=50, location="class")}}
        result = apply_location_weights(base, info, 0.6, 0.9)
        self.assertAlmostEqual(result[10][1], 0.8)

    def test_home_not_recorded(self):
        base = {1: 0.8}
        info = {10: {1: make_info(10, 1, location="home", recorded=False)}}
        result = apply_location_weights(base, info, 0.6, 0.9)
        self.assertAlmostEqual(result[10][1], 0.8 * 0.6)

    def test_home_recorded(self):
        base = {1: 0.8}
        info = {10: {1: make_info(10, 1, location="home", recorded=True)}}
        result = apply_location_weights(base, info, 0.6, 0.9)
        self.assertAlmostEqual(result[10][1], 0.8 * 0.9)

    def test_justified_absence_zeroed(self):
        base = {1: 0.8}
        info = {10: {1: make_info(10, 1, status="missed", justified=True)}}
        result = apply_location_weights(base, info, 0.6, 0.9)
        self.assertAlmostEqual(result[10][1], 0.0)

    def test_unjustified_absence_keeps_weight(self):
        base = {1: 0.8}
        info = {10: {1: make_info(10, 1, status="missed", justified=False)}}
        result = apply_location_weights(base, info, 0.6, 0.9)
        self.assertAlmostEqual(result[10][1], 0.8)

    def test_multiple_students_different_locations(self):
        base = {1: 1.0}
        info = {
            10: {1: make_info(10, 1, location="class")},
            20: {1: make_info(20, 1, location="home", recorded=False)},
            30: {1: make_info(30, 1, location="home", recorded=True)},
        }
        result = apply_location_weights(base, info, 0.5, 0.8)
        self.assertAlmostEqual(result[10][1], 1.0)
        self.assertAlmostEqual(result[20][1], 0.5)
        self.assertAlmostEqual(result[30][1], 0.8)


class TestTrainingTypeCorrection(unittest.TestCase):

    def _make_tds(self):
        return [
            make_td(1, training_day_types=["onsite"]),
            make_td(2, training_day_types=["onsite"]),
            make_td(3, training_day_types=["online"]),
            make_td(4, training_day_types=["competition"]),
        ]

    def test_empty_percentages_no_change(self):
        tds = self._make_tds()
        weights = {10: {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}}
        result = apply_training_type_correction(weights, tds, {}, {})
        self.assertEqual(result[10], {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0})

    def test_all_types_specified_sums_to_one(self):
        tds = self._make_tds()
        weights = {10: {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}}
        pcts = {"onsite": 0.5, "online": 0.25, "competition": 0.25}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        tot = sum(result[10].values())
        onsite_total = result[10][1] + result[10][2]
        online_total = result[10][3]
        comp_total = result[10][4]
        self.assertAlmostEqual(onsite_total / tot, 0.5, places=5)
        self.assertAlmostEqual(online_total / tot, 0.25, places=5)
        self.assertAlmostEqual(comp_total / tot, 0.25, places=5)

    def test_zero_percent_zeroes_type(self):
        tds = self._make_tds()
        weights = {10: {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}}
        pcts = {"onsite": 0.0, "online": 0.5, "competition": 0.5}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        self.assertAlmostEqual(result[10][1], 0.0)
        self.assertAlmostEqual(result[10][2], 0.0)
        self.assertGreater(result[10][3], 0)
        self.assertGreater(result[10][4], 0)

    def test_zero_percent_only_one_type(self):
        tds = self._make_tds()
        weights = {10: {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}}
        pcts = {"onsite": 0.0}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        self.assertAlmostEqual(result[10][1], 0.0)
        self.assertAlmostEqual(result[10][2], 0.0)
        self.assertGreater(result[10][3], 0)
        self.assertGreater(result[10][4], 0)
        online_w = result[10][3]
        comp_w = result[10][4]
        self.assertAlmostEqual(online_w + comp_w, 4.0, places=5)

    def test_partial_specification_leftover(self):
        tds = self._make_tds()
        weights = {10: {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}}
        pcts = {"onsite": 0.3, "online": 0.36}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        tot_before = 4.0
        onsite_total = result[10][1] + result[10][2]
        online_total = result[10][3]
        comp_total = result[10][4]
        self.assertAlmostEqual(onsite_total, tot_before * 0.3, places=5)
        self.assertAlmostEqual(online_total, tot_before * 0.36, places=5)
        self.assertAlmostEqual(comp_total, tot_before * 0.34, places=5)

    def test_single_type_specified_rest_share_leftover(self):
        tds = self._make_tds()
        weights = {10: {1: 1.0, 2: 1.0, 3: 2.0, 4: 1.0}}
        pcts = {"onsite": 0.3}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        tot_before = 5.0
        onsite_total = result[10][1] + result[10][2]
        self.assertAlmostEqual(onsite_total, tot_before * 0.3, places=5)
        online_w = result[10][3]
        comp_w = result[10][4]
        leftover_target = tot_before * 0.7
        self.assertAlmostEqual(online_w + comp_w, leftover_target, places=5)
        self.assertAlmostEqual(online_w / comp_w, 2.0, places=5)

    def test_all_zero_student_weights(self):
        tds = self._make_tds()
        weights = {10: {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}}
        pcts = {"onsite": 0.5, "online": 0.5}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        self.assertEqual(result[10], {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0})

    def test_multi_type_uses_assignment(self):
        tds = [
            make_td(1, training_day_types=["onsite", "online"]),
            make_td(2, training_day_types=["onsite"]),
        ]
        weights = {10: {1: 1.0, 2: 1.0}}
        assignments = {1: "online"}
        pcts = {"onsite": 0.4, "online": 0.6}
        result = apply_training_type_correction(weights, tds, assignments, pcts)
        tot_before = 2.0
        self.assertAlmostEqual(result[10][1], tot_before * 0.6, places=5)
        self.assertAlmostEqual(result[10][2], tot_before * 0.4, places=5)

    def test_multiple_students(self):
        tds = [
            make_td(1, training_day_types=["onsite"]),
            make_td(2, training_day_types=["online"]),
        ]
        weights = {
            10: {1: 1.0, 2: 1.0},
            20: {1: 0.5, 2: 1.5},
        }
        pcts = {"onsite": 0.6, "online": 0.4}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        for sid in [10, 20]:
            tot = sum(weights[sid].values())
            self.assertAlmostEqual(result[sid][1], tot * 0.6, places=5)
            self.assertAlmostEqual(result[sid][2], tot * 0.4, places=5)

    def test_zero_percent_with_leftover(self):
        tds = self._make_tds()
        weights = {10: {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}}
        pcts = {"onsite": 0.0, "online": 0.6}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        tot_before = 4.0
        self.assertAlmostEqual(result[10][1], 0.0)
        self.assertAlmostEqual(result[10][2], 0.0)
        self.assertAlmostEqual(result[10][3], tot_before * 0.6, places=5)
        self.assertAlmostEqual(result[10][4], tot_before * 0.4, places=5)

    def test_untyped_td_gets_other(self):
        tds = [
            make_td(1, training_day_types=["onsite"]),
            make_td(2, training_day_types=[]),
        ]
        weights = {10: {1: 1.0, 2: 1.0}}
        pcts = {"onsite": 0.6, "other": 0.4}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        tot_before = 2.0
        self.assertAlmostEqual(result[10][1], tot_before * 0.6, places=5)
        self.assertAlmostEqual(result[10][2], tot_before * 0.4, places=5)

    def test_untyped_td_zero_percent(self):
        tds = [
            make_td(1, training_day_types=["onsite"]),
            make_td(2, training_day_types=[]),
        ]
        weights = {10: {1: 1.0, 2: 1.0}}
        pcts = {"other": 0.0}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        self.assertAlmostEqual(result[10][2], 0.0)
        self.assertGreater(result[10][1], 0)

    def test_untyped_td_leftover_when_unmentioned(self):
        tds = [
            make_td(1, training_day_types=["onsite"]),
            make_td(2, training_day_types=["online"]),
            make_td(3, training_day_types=[]),
        ]
        weights = {10: {1: 1.0, 2: 1.0, 3: 1.0}}
        pcts = {"onsite": 0.3, "online": 0.3}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        tot_before = 3.0
        self.assertAlmostEqual(result[10][1], tot_before * 0.3, places=5)
        self.assertAlmostEqual(result[10][2], tot_before * 0.3, places=5)
        self.assertAlmostEqual(result[10][3], tot_before * 0.4, places=5)

    def test_mixed_typed_and_untyped(self):
        tds = [
            make_td(1, training_day_types=["onsite"]),
            make_td(2, training_day_types=[]),
            make_td(3, training_day_types=[]),
        ]
        weights = {10: {1: 2.0, 2: 1.0, 3: 1.0}}
        pcts = {"onsite": 0.5, "other": 0.5}
        result = apply_training_type_correction(weights, tds, {}, pcts)
        tot_before = 4.0
        self.assertAlmostEqual(result[10][1], tot_before * 0.5, places=5)
        other_total = result[10][2] + result[10][3]
        self.assertAlmostEqual(other_total, tot_before * 0.5, places=5)
        self.assertAlmostEqual(result[10][2], result[10][3], places=5)


class TestGetRawScores(unittest.TestCase):

    def test_participated_gets_score(self):
        tds = [make_td(1)]
        info = {10: {1: make_info(10, 1, score=85.0)}}
        result = get_raw_scores(info, tds)
        self.assertAlmostEqual(result[10][1], 85.0)

    def test_unjustified_absence_gets_zero(self):
        tds = [make_td(1)]
        info = {10: {1: make_info(10, 1, status="missed", justified=False)}}
        result = get_raw_scores(info, tds)
        self.assertAlmostEqual(result[10][1], 0.0)

    def test_justified_absence_excluded(self):
        tds = [make_td(1)]
        info = {10: {1: make_info(10, 1, status="missed", justified=True)}}
        result = get_raw_scores(info, tds)
        self.assertNotIn(10, result)


class TestCollectReferenceScores(unittest.TestCase):

    def test_excludes_justified(self):
        info = {
            10: {1: make_info(10, 1, score=80)},
            20: {1: make_info(20, 1, status="missed", justified=True)},
            30: {1: make_info(30, 1, score=60)},
        }
        scores = _collect_reference_scores(info, 1)
        self.assertEqual(sorted(scores), [60.0, 80.0])

    def test_includes_unjustified_as_zero(self):
        info = {
            10: {1: make_info(10, 1, score=80)},
            20: {1: make_info(20, 1, status="missed", justified=False)},
        }
        scores = _collect_reference_scores(info, 1)
        self.assertEqual(sorted(scores), [0.0, 80.0])


class TestNormalizationNone(unittest.TestCase):

    def test_returns_raw(self):
        tds = [make_td(1)]
        info = {10: {1: make_info(10, 1, score=90)}}
        result = normalize_scores_none(info, tds)
        self.assertAlmostEqual(result[10][1], 90.0)


class TestNormalizationRank(unittest.TestCase):

    def test_basic_ranking(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, score=80)},
            3: {1: make_info(3, 1, score=60)},
        }
        result = normalize_scores_rank(info, tds)
        self.assertEqual(result[1][1], 1.0)
        self.assertEqual(result[2][1], 2.0)
        self.assertEqual(result[3][1], 3.0)

    def test_ties_get_same_rank(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, score=90)},
            3: {1: make_info(3, 1, score=90)},
            4: {1: make_info(4, 1, score=80)},
        }
        result = normalize_scores_rank(info, tds)
        self.assertEqual(result[1][1], 1.0)
        self.assertEqual(result[2][1], 2.0)
        self.assertEqual(result[3][1], 2.0)
        self.assertEqual(result[4][1], 4.0)

    def test_justified_excluded_from_ranking(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, status="missed", justified=True)},
            3: {1: make_info(3, 1, score=80)},
        }
        result = normalize_scores_rank(info, tds)
        self.assertNotIn(1, result.get(2, {}))
        self.assertEqual(result[1][1], 1.0)
        self.assertEqual(result[3][1], 2.0)


class TestNormalizationMedian(unittest.TestCase):

    def test_basic_median_no_variability(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, score=80)},
            3: {1: make_info(3, 1, score=60)},
        }
        result = normalize_scores_median(info, tds, top_x=10)
        ref_scores = [60, 80, 100]
        reference = _smoothed_median(ref_scores)
        self.assertAlmostEqual(result[1][1], 100 - reference, places=5)
        self.assertAlmostEqual(result[2][1], 80 - reference, places=5)
        self.assertAlmostEqual(result[3][1], 60 - reference, places=5)

    def test_median_with_variability(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, score=80)},
            3: {1: make_info(3, 1, score=60)},
        }
        result = normalize_scores_median(info, tds, top_x=10, normalize_variability=True)
        ref_scores = [60, 80, 100]
        reference = _smoothed_median(ref_scores)
        mad_val = _mad(ref_scores, _smoothed_median(ref_scores))
        variability = mad_val * MAD_MULTIPLIER
        self.assertAlmostEqual(result[1][1], (100 - reference) / variability, places=5)

    def test_top_x_limits_reference(self):
        tds = [make_td(1)]
        info = {}
        for i in range(1, 21):
            info[i] = {1: make_info(i, 1, score=float(i * 5))}
        result = normalize_scores_median(info, tds, top_x=5)
        top5 = sorted([i * 5.0 for i in range(1, 21)], reverse=True)[:5]
        reference = _smoothed_median(top5)
        self.assertAlmostEqual(result[20][1], 100 - reference, places=5)


class TestNormalizationMean(unittest.TestCase):

    def test_basic_mean_no_variability(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, score=80)},
            3: {1: make_info(3, 1, score=60)},
        }
        result = normalize_scores_mean(info, tds, top_x=10)
        reference = _mean([60, 80, 100])
        self.assertAlmostEqual(result[1][1], 100 - reference, places=5)
        self.assertAlmostEqual(result[2][1], 80 - reference, places=5)

    def test_mean_with_variability(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, score=80)},
            3: {1: make_info(3, 1, score=60)},
        }
        result = normalize_scores_mean(info, tds, top_x=10, normalize_variability=True)
        ref_scores = [60, 80, 100]
        reference = _mean(ref_scores)
        sd = _std_dev(ref_scores, _mean(ref_scores))
        self.assertAlmostEqual(result[1][1], (100 - reference) / sd, places=5)

    def test_justified_excluded_from_reference(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, score=80)},
            3: {1: make_info(3, 1, status="missed", justified=True)},
        }
        result = normalize_scores_mean(info, tds, top_x=10)
        reference = _mean([80, 100])
        self.assertAlmostEqual(result[1][1], 100 - reference, places=5)
        self.assertNotIn(1, result.get(3, {}))


class TestNormalizeScoresDispatch(unittest.TestCase):

    def test_dispatch_none(self):
        tds = [make_td(1)]
        info = {1: {1: make_info(1, 1, score=50)}}
        result = normalize_scores("none", info, tds)
        self.assertAlmostEqual(result[1][1], 50.0)

    def test_dispatch_rank(self):
        tds = [make_td(1)]
        info = {
            1: {1: make_info(1, 1, score=100)},
            2: {1: make_info(2, 1, score=80)},
        }
        result = normalize_scores("rank", info, tds)
        self.assertEqual(result[1][1], 1.0)
        self.assertEqual(result[2][1], 2.0)

    def test_dispatch_median(self):
        tds = [make_td(1)]
        info = {1: {1: make_info(1, 1, score=50)}}
        result = normalize_scores("median", info, tds, top_x=10)
        self.assertIsNotNone(result)

    def test_dispatch_mean(self):
        tds = [make_td(1)]
        info = {1: {1: make_info(1, 1, score=50)}}
        result = normalize_scores("mean", info, tds, top_x=10)
        self.assertIsNotNone(result)


class TestWeightedAverages(unittest.TestCase):

    def test_basic_weighted_average(self):
        weights = {10: {1: 1.0, 2: 0.5}}
        scores = {10: {1: 80.0, 2: 60.0}}
        result = calculate_weighted_averages(weights, scores)
        expected = (1.0 * 80 + 0.5 * 60) / (1.0 + 0.5)
        self.assertAlmostEqual(result[10], expected)

    def test_zero_weight_excluded(self):
        weights = {10: {1: 1.0, 2: 0.0}}
        scores = {10: {1: 80.0, 2: 60.0}}
        result = calculate_weighted_averages(weights, scores)
        self.assertAlmostEqual(result[10], 80.0)

    def test_no_weights_gives_zero(self):
        weights = {10: {1: 0.0}}
        scores = {10: {1: 80.0}}
        result = calculate_weighted_averages(weights, scores)
        self.assertAlmostEqual(result[10], 0.0)

    def test_missing_weight_treated_as_zero(self):
        weights = {10: {}}
        scores = {10: {1: 80.0}}
        result = calculate_weighted_averages(weights, scores)
        self.assertAlmostEqual(result[10], 0.0)


class TestComputeMonthlyDecay(unittest.TestCase):

    def test_no_span(self):
        self.assertEqual(compute_monthly_decay(65, 0), 0.0)

    def test_three_months(self):
        decay = compute_monthly_decay(65, 90)
        self.assertAlmostEqual(decay, 35.0 / 3.0, places=2)


class TestIntegration(unittest.TestCase):

    def test_full_pipeline_with_type_correction_and_normalization(self):
        d1 = datetime(2026, 1, 1)
        d2 = datetime(2026, 2, 1)
        d3 = datetime(2026, 3, 1)
        tds = [
            make_td(1, d1, ["onsite"]),
            make_td(2, d2, ["online"]),
            make_td(3, d3, ["onsite"]),
        ]

        info = {
            10: {
                1: make_info(10, 1, score=80, location="class"),
                2: make_info(10, 2, score=70, location="home", recorded=False),
                3: make_info(10, 3, score=90, location="class"),
            },
            20: {
                1: make_info(20, 1, score=60, location="class"),
                2: make_info(20, 2, score=85, location="class"),
                3: make_info(20, 3, score=75, location="class"),
            },
        }

        base_weights = calculate_time_decay_weights(tds, 50)
        self.assertAlmostEqual(base_weights[1], 0.5)
        self.assertAlmostEqual(base_weights[3], 1.0)

        student_weights = apply_location_weights(
            base_weights, info, 0.6, 0.9
        )
        self.assertAlmostEqual(student_weights[10][2], base_weights[2] * 0.6)
        self.assertAlmostEqual(student_weights[20][2], base_weights[2])

        pcts = {"onsite": 0.6, "online": 0.4}
        student_weights = apply_training_type_correction(
            student_weights, tds, {}, pcts
        )

        norm_scores = normalize_scores("mean", info, tds, top_x=10)
        weighted_avgs = calculate_weighted_averages(student_weights, norm_scores)
        self.assertIn(10, weighted_avgs)
        self.assertIn(20, weighted_avgs)

    def test_pipeline_with_zero_percent_type(self):
        tds = [
            make_td(1, datetime(2026, 1, 1), ["onsite"]),
            make_td(2, datetime(2026, 2, 1), ["online"]),
        ]
        info = {
            10: {
                1: make_info(10, 1, score=80),
                2: make_info(10, 2, score=70),
            },
        }
        base_weights = calculate_time_decay_weights(tds, 100)
        student_weights = apply_location_weights(base_weights, info, 1.0, 1.0)
        pcts = {"onsite": 0.0}
        student_weights = apply_training_type_correction(
            student_weights, tds, {}, pcts
        )
        self.assertAlmostEqual(student_weights[10][1], 0.0)
        self.assertGreater(student_weights[10][2], 0)

        norm_scores = normalize_scores("none", info, tds)
        weighted_avgs = calculate_weighted_averages(student_weights, norm_scores)
        self.assertAlmostEqual(weighted_avgs[10], 70.0)


if __name__ == "__main__":
    unittest.main()
