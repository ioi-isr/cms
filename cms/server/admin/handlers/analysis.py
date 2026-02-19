#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Training program analysis functions.

Modular functions for calculating training day weights and normalizing
scores. Designed for reuse in future pairwise analysis mode.

Key concepts:
- Training day weights are per-student per-training-day floats
- Scores are per-student per-training-day floats (total across tasks)
- Justified absences: weight=0, excluded from reference calculations
- Unjustified absences: score=0, included in reference calculations
"""

import math
from dataclasses import dataclass
from typing import Callable, Optional

from cms.db import TrainingDay, ArchivedAttendance, ArchivedStudentRanking


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class StudentTrainingDayInfo:
    """Collected info for one student in one training day."""
    student_id: int
    training_day_id: int
    score: float
    location: str | None
    recorded: bool
    status: str
    justified: bool


def collect_student_td_info(
    ranking_data: dict[int, dict[int, ArchivedStudentRanking]],
    attendance_data: dict[int, dict[int, ArchivedAttendance]],
    training_day_tasks: dict[int, list[dict]],
    training_days: list[TrainingDay],
) -> dict[int, dict[int, StudentTrainingDayInfo]]:
    """Build a unified info dict: student_id -> td_id -> StudentTrainingDayInfo.

    Computes the total score per training day from task_scores, and merges
    attendance metadata (location, recorded, status, justified).
    """
    result: dict[int, dict[int, StudentTrainingDayInfo]] = {}

    for student_id, td_rankings in ranking_data.items():
        result[student_id] = {}
        for td in training_days:
            ranking = td_rankings.get(td.id)
            if ranking is None:
                continue

            total_score = 0.0
            tasks = training_day_tasks.get(td.id, [])
            if ranking.task_scores:
                for task in tasks:
                    total_score += ranking.task_scores.get(str(task["id"]), 0.0)

            att = attendance_data.get(student_id, {}).get(td.id)

            result[student_id][td.id] = StudentTrainingDayInfo(
                student_id=student_id,
                training_day_id=td.id,
                score=total_score,
                location=att.location if att else "class",
                recorded=att.recorded if att else False,
                status=att.status if att else "participated",
                justified=att.justified if att else False,
            )

    return result


# ---------------------------------------------------------------------------
# Helpers: Score Extraction & Statistics
# ---------------------------------------------------------------------------

MAD_MULTIPLIER = 1.4826


def _get_student_score_for_td(info: StudentTrainingDayInfo) -> Optional[float]:
    """Return score to use for analysis, or None to exclude (justified)."""
    if info.status == "missed":
        return None if info.justified else 0.0
    return info.score


def get_raw_scores(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    training_days: list[TrainingDay],
) -> dict[int, dict[int, float]]:
    """Extract raw scores. Justified absences are excluded from the dict."""
    result = {}
    for student_id, td_infos in student_info.items():
        for td in training_days:
            info = td_infos.get(td.id)
            if info:
                val = _get_student_score_for_td(info)
                if val is not None:
                    result.setdefault(student_id, {})[td.id] = val

    return result


def _collect_reference_scores(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    td_id: int,
) -> list[float]:
    """Collect all valid scores for a training day (excludes justified)."""
    scores = []
    for td_infos in student_info.values():
        info = td_infos.get(td_id)
        if info:
            val = _get_student_score_for_td(info)
            if val is not None:
                scores.append(val)
    return scores


def _top_x_scores(scores: list[float], top_x: int) -> list[float]:
    return sorted(scores, reverse=True)[:top_x]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _smoothed_median(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    if n == 1:
        return values[0]
    s = sorted(values)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2.0
    else:
        return (s[mid - 1] + s[mid] + s[mid + 1]) / 3.0


def _std_dev(values: list[float], center: float) -> float:
    if len(values) <= 1:
        return 1.0
    variance = sum((v - center) ** 2 for v in values) / len(values)
    return math.sqrt(variance) if variance > 0 else 1.0


def _mad(values: list[float], center: float) -> float:
    """Compute Median Absolute Deviation."""
    if len(values) <= 1:
        return 1.0
    abs_devs = sorted(abs(v - center) for v in values)
    n = len(abs_devs)
    if n % 2 == 0:
        mad_val = (abs_devs[n // 2 - 1] + abs_devs[n // 2]) / 2.0
    else:
        mad_val = abs_devs[n // 2]
    # 1.4826 makes MAD consistent with SD for normal distributions
    return (mad_val * MAD_MULTIPLIER) if mad_val > 0 else 1.0


# ---------------------------------------------------------------------------
# Stage 1: Weight calculation
# ---------------------------------------------------------------------------

def calculate_time_decay_weights(
    training_days: list[TrainingDay],
    first_training_weight_pct: float,
) -> dict[int, float]:
    """Calculate time-decay weights for each training day.

    Linear decay from 1.0 (most recent) to first_training_weight_pct/100
    (earliest). If all training days have the same date, all weights are 1.0.

    training_days: ordered list of archived training days (by start_time).
    first_training_weight_pct: percentage weight for the first (earliest)
        training day (e.g. 65 means 0.65).

    return: dict mapping training_day_id -> base weight.
    """
    if not training_days:
        return {}

    weights = {td.id: 1.0 for td in training_days}
    if first_training_weight_pct >= 100.0:
        return weights

    # Filter out training days without start_time
    training_days_with_dates = [td for td in training_days if td.start_time is not None]

    # If no training days have dates, return default weights for all
    if not training_days_with_dates:
        return weights

    dates = [td.start_time for td in training_days_with_dates]
    earliest, latest = min(dates), max(dates)
    total_span = (latest - earliest).total_seconds()

    if total_span == 0:
        return weights

    first_weight = first_training_weight_pct / 100.0
    for td in training_days_with_dates:
        elapsed = (td.start_time - earliest).total_seconds()
        fraction = elapsed / total_span
        weights[td.id] = first_weight + fraction * (1.0 - first_weight)

    return weights


def apply_location_weights(
    base_weights: dict[int, float],
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    home_factor: float,
    recorded_home_factor: float,
) -> dict[int, dict[int, float]]:
    """Apply per-student location modification to base weights.

    Also zeroes out weights for justified absences.

    base_weights: td_id -> global time-decay weight.
    student_info: student_id -> td_id -> StudentTrainingDayInfo.
    home_factor: multiplier for home (not recorded) attendance.
    recorded_home_factor: multiplier for home+recorded attendance.

    return: student_id -> td_id -> modified weight.
    """
    result: dict[int, dict[int, float]] = {}

    for student_id, td_infos in student_info.items():
        result[student_id] = {}
        for td_id, info in td_infos.items():
            w = base_weights.get(td_id, 1.0)

            if info.status == "missed" and info.justified:
                w = 0.0
            elif info.location == "home":
                w *= recorded_home_factor if info.recorded else home_factor

            result[student_id][td_id] = w

    return result


def apply_training_type_correction(
    student_weights: dict[int, dict[int, float]],
    training_days: list[TrainingDay],
    type_assignments: dict[int, str],
    type_percentages: dict[str, float],
) -> dict[int, dict[int, float]]:
    """Correct weights so each training type constitutes the desired % of total.

    The total weight sum per student is preserved; only the distribution
    across types changes.

    For multi-type training days, type_assignments maps td_id -> chosen type.

    type_percentages maps type_name -> desired fraction (0-1).  Only types
    explicitly mentioned by the admin are present:

    * If a type is mapped to 0 its weights are zeroed out.
    * Types *not* mentioned share the leftover fraction (1 - sum of mentioned)
      proportionally to their current weight totals.

    For each student (with total weight ``tot``):
      For each mentioned type with target p:
        factor = (tot * p) / x   where x = current weight total for that type
      For unmentioned types (leftover fraction shared proportionally):
        factor = (tot * leftover) / sum_of_unmentioned_weights

    student_weights: student_id -> td_id -> weight (modified in-place concept).
    training_days: list of training days.
    type_assignments: td_id -> assigned type string for correction.
    type_percentages: type_name -> desired fraction (0-1).

    return: student_id -> td_id -> corrected weight.
    """
    if not type_percentages:
        return student_weights

    td_type_map: dict[int, str] = {}
    for td in training_days:
        if td.id in type_assignments:
            td_type_map[td.id] = type_assignments[td.id]
        elif td.training_day_types and len(td.training_day_types) == 1:
            td_type_map[td.id] = td.training_day_types[0]
        elif not td.training_day_types:
            td_type_map[td.id] = "other"

    result: dict[int, dict[int, float]] = {}

    for student_id, td_weights in student_weights.items():
        corrected = dict(td_weights)
        tot = sum(corrected.values())
        if tot == 0:
            result[student_id] = corrected
            continue

        type_totals: dict[str, float] = {}
        for td_id, w in corrected.items():
            t = td_type_map.get(td_id)
            if t is not None:
                type_totals[t] = type_totals.get(t, 0.0) + w

        mentioned_pct_sum = sum(type_percentages.values())
        leftover_pct = max(1.0 - mentioned_pct_sum, 0.0)

        unmentioned_types = set(type_totals.keys()) - set(type_percentages.keys())
        unmentioned_weight_sum = sum(
            type_totals[t] for t in unmentioned_types
        )

        for type_name, p in type_percentages.items():
            x = type_totals.get(type_name, 0.0)
            if p == 0:
                for td_id in corrected:
                    if td_type_map.get(td_id) == type_name:
                        corrected[td_id] = 0.0
            elif x > 0:
                factor = (tot * p) / x
                for td_id in list(corrected):
                    if td_type_map.get(td_id) == type_name:
                        corrected[td_id] *= factor

        if leftover_pct > 0 and unmentioned_weight_sum > 0:
            factor = (tot * leftover_pct) / unmentioned_weight_sum
            for td_id in list(corrected):
                t = td_type_map.get(td_id)
                if t in unmentioned_types:
                    corrected[td_id] = corrected[td_id] * factor

        result[student_id] = corrected

    return result


# ---------------------------------------------------------------------------
# Stage 2: Score normalization
# ---------------------------------------------------------------------------

def _normalize_generic(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    training_days: list[TrainingDay],
    top_x: int,
    normalize_variability: bool,
    center_func: Callable[[list[float]], float],
    spread_func: Callable[[list[float], float], float],
) -> dict[int, dict[int, float]]:
    """Generic normalization logic to reduce duplication."""
    raw = get_raw_scores(student_info, training_days)
    result = {}

    for td in training_days:
        ref_scores = _collect_reference_scores(student_info, td.id)
        if not ref_scores:
            continue

        effective_top_x = max(1, min(top_x, len(ref_scores)))
        top_scores = _top_x_scores(ref_scores, effective_top_x)

        # Calculate stats on the Top X
        reference = center_func(top_scores)
        variability = (
            spread_func(top_scores, reference) if normalize_variability else 1.0
        )

        for student_id, td_map in raw.items():
            if td.id in td_map:
                normalized = (td_map[td.id] - reference) / variability
                if student_id not in result:
                    result[student_id] = {}
                result[student_id][td.id] = normalized
    return result

def normalize_scores(
    method: str,
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    training_days: list[TrainingDay],
    top_x: int = 10,
    normalize_variability: bool = False,
) -> dict[int, dict[int, float]]:
    """Dispatch to appropriate normalization strategy."""

    if method == "rank":
        # Rank-based normalization. Ties get the same rank.
        # E.g. scores [100, 90, 90, 80] -> ranks [1, 2, 2, 4].
        # Rank is distinct enough to keep separate logic
        raw = get_raw_scores(student_info, training_days)
        result = {}
        for td in training_days:
            # Create list of (student_id, score) pairs
            td_scores = [
                (sid, scores[td.id]) for sid, scores in raw.items() if td.id in scores
            ]
            if not td_scores:
                continue

            # Sort descending by score
            td_scores.sort(key=lambda x: x[1], reverse=True)

            # Assign ranks (handling ties)
            rank = 1
            for i, (sid, score) in enumerate(td_scores):
                if i > 0 and score < td_scores[i - 1][1]:
                    rank = i + 1
                if sid not in result:
                    result[sid] = {}
                result[sid][td.id] = float(rank)
        return result

    elif method == "median":
        return _normalize_generic(
            student_info,
            training_days,
            top_x,
            normalize_variability,
            center_func=_smoothed_median,
            spread_func=_mad,
        )

    elif method == "mean":
        return _normalize_generic(
            student_info,
            training_days,
            top_x,
            normalize_variability,
            center_func=_mean,
            spread_func=_std_dev,
        )

    else:  # "none"
        return get_raw_scores(student_info, training_days)

#
# Final weighted average
# ---------------------------------------------------------------------------

def calculate_weighted_averages(
    student_weights: dict[int, dict[int, float]],
    normalized_scores: dict[int, dict[int, float]],
) -> dict[int, float]:
    """Calculate the weighted average normalized score for each student.

    For each student, the weighted average is:
      sum(weight_i * score_i) / sum(weight_i)
    over all training days where both weight > 0 and score exists.

    return: student_id -> weighted average score.
    """
    result: dict[int, float] = {}

    for student_id, td_scores in normalized_scores.items():
        weights = student_weights.get(student_id, {})
        numerator = 0.0
        denominator = 0.0

        for td_id, score in td_scores.items():
            w = weights.get(td_id, 0.0)
            if w > 0:
                numerator += w * score
                denominator += w

        result[student_id] = (numerator / denominator) if denominator > 0 else 0.0
    return result


# ---------------------------------------------------------------------------
# Outlier dropping
# ---------------------------------------------------------------------------

def drop_outliers(
    student_weights: dict[int, dict[int, float]],
    raw_scores: dict[int, dict[int, float]],
    num_outliers: int,
) -> dict[int, dict[int, float]]:
    """Drop N best and N worst training days per student (by score).

    Sets the weight to 0 for the dropped TDs so they do not contribute
    to the weighted average.  Only TDs with weight > 0 and a valid score
    are candidates for dropping.

    If a student has fewer than ``2 * num_outliers + 1`` active TDs,
    no outliers are dropped for that student (there would be nothing left).

    student_weights: student_id -> td_id -> weight.
    raw_scores: student_id -> td_id -> score  (from ``get_raw_scores``).
    num_outliers: number of best *and* worst TDs to drop.

    return: student_id -> td_id -> adjusted weight.
    """
    if num_outliers <= 0:
        return student_weights

    result: dict[int, dict[int, float]] = {}

    for student_id, td_weights in student_weights.items():
        adjusted = dict(td_weights)
        scores = raw_scores.get(student_id, {})

        active = [
            (td_id, scores[td_id])
            for td_id, w in adjusted.items()
            if w > 0 and td_id in scores
        ]

        if len(active) > 2 * num_outliers:
            active.sort(key=lambda x: x[1])
            for td_id, _ in active[:num_outliers]:
                adjusted[td_id] = 0.0
            for td_id, _ in active[-num_outliers:]:
                adjusted[td_id] = 0.0

        result[student_id] = adjusted

    return result


# ---------------------------------------------------------------------------
# Pairwise analysis
# ---------------------------------------------------------------------------

@dataclass
class PairInfo:
    """Metadata for one training-day pair."""
    pair_id: int
    td_a_id: int
    td_b_id: int


def generate_pairwise_data(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    student_weights: dict[int, dict[int, float]],
    training_days: list[TrainingDay],
) -> tuple[
    list[PairInfo],
    dict[int, dict[int, StudentTrainingDayInfo]],
    dict[int, dict[int, float]],
]:
    """Build synthetic StudentTrainingDayInfo and weights for all TD pairs.

    For each ordered pair (td_a, td_b) with a < b, and each student who
    participated in both (no justified absence in either):

    * paired score  = score_a + score_b
    * paired weight = weight_a * weight_b

    Returns
    -------
    pairs : list[PairInfo]
        Ordered list of pairs with synthetic integer IDs starting at 0.
    paired_info : student_id -> pair_id -> StudentTrainingDayInfo
        Synthetic info entries for each student/pair (score = sum,
        status mirrors participation).
    paired_weights : student_id -> pair_id -> float
        Weight = product of the two individual weights.
    """
    pairs: list[PairInfo] = []
    td_list = list(training_days)
    pair_id = 0
    for i in range(len(td_list)):
        for j in range(i + 1, len(td_list)):
            pairs.append(PairInfo(pair_id=pair_id,
                                  td_a_id=td_list[i].id,
                                  td_b_id=td_list[j].id))
            pair_id += 1

    paired_info: dict[int, dict[int, StudentTrainingDayInfo]] = {}
    paired_weights: dict[int, dict[int, float]] = {}

    for student_id, td_infos in student_info.items():
        s_weights = student_weights.get(student_id, {})

        for pair in pairs:
            info_a = td_infos.get(pair.td_a_id)
            info_b = td_infos.get(pair.td_b_id)
            if info_a is None or info_b is None:
                continue

            score_a = _get_student_score_for_td(info_a)
            score_b = _get_student_score_for_td(info_b)

            if score_a is None or score_b is None:
                continue

            combined_score = score_a + score_b
            w_a = s_weights.get(pair.td_a_id, 0.0)
            w_b = s_weights.get(pair.td_b_id, 0.0)

            paired_info.setdefault(student_id, {})[pair.pair_id] = (
                StudentTrainingDayInfo(
                    student_id=student_id,
                    training_day_id=pair.pair_id,
                    score=combined_score,
                    location="class",
                    recorded=False,
                    status="participated",
                    justified=False,
                )
            )
            paired_weights.setdefault(student_id, {})[pair.pair_id] = w_a * w_b

    return pairs, paired_info, paired_weights


def run_pairwise_analysis(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    student_weights: dict[int, dict[int, float]],
    training_days: list[TrainingDay],
    method: str,
    top_x: int = 10,
    normalize_variability: bool = False,
) -> tuple[
    list[PairInfo],
    dict[int, dict[int, float]],
    dict[int, dict[int, float]],
    dict[int, float],
]:
    """Run the full pairwise analysis pipeline.

    Returns
    -------
    pairs : list[PairInfo]
    paired_weights : student_id -> pair_id -> weight
    paired_norm_scores : student_id -> pair_id -> normalized score
    paired_weighted_avgs : student_id -> weighted average
    """
    pairs, paired_info, paired_weights = generate_pairwise_data(
        student_info, student_weights, training_days,
    )

    if not pairs:
        return pairs, {}, {}, {}

    fake_tds = [_make_fake_td(p.pair_id) for p in pairs]

    paired_norm = normalize_scores(
        method, paired_info, fake_tds, top_x, normalize_variability,
    )
    paired_avgs = calculate_weighted_averages(paired_weights, paired_norm)

    return pairs, paired_weights, paired_norm, paired_avgs


class _FakeTD:
    """Minimal stand-in for TrainingDay used by normalization functions."""
    __slots__ = ("id",)

    def __init__(self, td_id: int):
        self.id = td_id


def _make_fake_td(td_id: int) -> "_FakeTD":
    return _FakeTD(td_id)


