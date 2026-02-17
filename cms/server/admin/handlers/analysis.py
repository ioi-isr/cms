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
                    val = ranking.task_scores.get(str(task["id"]))
                    if val is not None:
                        total_score += val

            att = attendance_data.get(student_id, {}).get(td.id)
            status = att.status if att else "participated"
            location = att.location if att else "class"
            recorded = att.recorded if att else False
            justified = att.justified if att else False

            result[student_id][td.id] = StudentTrainingDayInfo(
                student_id=student_id,
                training_day_id=td.id,
                score=total_score,
                location=location,
                recorded=recorded,
                status=status,
                justified=justified,
            )

    return result


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

    if first_training_weight_pct >= 100.0:
        return {td.id: 1.0 for td in training_days}

    dates = []
    for td in training_days:
        dates.append(td.start_time)

    earliest = min(dates)
    latest = max(dates)
    total_span = (latest - earliest).total_seconds()

    if total_span == 0:
        return {td.id: 1.0 for td in training_days}

    first_weight = first_training_weight_pct / 100.0
    weights: dict[int, float] = {}
    for td in training_days:
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
            elif info.location == "home" and info.recorded:
                w *= recorded_home_factor
            elif info.location == "home" and not info.recorded:
                w *= home_factor

            result[student_id][td_id] = w

    return result


def apply_training_type_correction(
    student_weights: dict[int, dict[int, float]],
    training_days: list[TrainingDay],
    type_assignments: dict[int, str],
    type_percentages: dict[str, float],
) -> dict[int, dict[int, float]]:
    """Correct weights so each training type constitutes the desired % of total.

    For multi-type training days, type_assignments maps td_id -> chosen type.

    type_percentages maps type_name -> desired fraction (0-1).  Only types
    explicitly mentioned by the admin are present:

    * If a type is mapped to 0 its weights are zeroed out.
    * Types *not* mentioned share the leftover fraction (1 - sum of mentioned)
      proportionally to their current weight totals.

    For each student:
      tot = sum of all weights before correction
      For each mentioned type with target p:
        factor = (tot * p) / x   where x = current weight total for that type
      For unmentioned types (leftover fraction shared proportionally):
        combined target = tot * leftover
        factor = combined_target / sum_of_unmentioned_weights

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
                        corrected[td_id] = corrected[td_id] * factor

        if leftover_pct > 0 and unmentioned_weight_sum > 0:
            combined_target = tot * leftover_pct
            factor = combined_target / unmentioned_weight_sum
            for td_id in list(corrected):
                t = td_type_map.get(td_id)
                if t in unmentioned_types:
                    corrected[td_id] = corrected[td_id] * factor

        result[student_id] = corrected

    return result


# ---------------------------------------------------------------------------
# Stage 2: Score normalization
# ---------------------------------------------------------------------------

def get_raw_scores(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    training_days: list[TrainingDay],
) -> dict[int, dict[int, float]]:
    """Extract raw total scores per student per training day.

    Unjustified absences get score 0. Justified absences are excluded
    (not present in the returned dict for that student/td).

    return: student_id -> td_id -> score.
    """
    result: dict[int, dict[int, float]] = {}

    for student_id, td_infos in student_info.items():
        result[student_id] = {}
        for td in training_days:
            info = td_infos.get(td.id)
            if info is None:
                continue

            if info.status == "missed" and info.justified:
                continue

            if info.status == "missed" and not info.justified:
                result[student_id][td.id] = 0.0
            else:
                result[student_id][td.id] = info.score

        if not result[student_id]:
            del result[student_id]

    return result


def _collect_reference_scores(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    td_id: int,
) -> list[float]:
    """Collect scores for a training day, excluding justified absences.

    Unjustified absences contribute 0. Justified absences are excluded entirely.
    """
    scores = []
    for student_id, td_infos in student_info.items():
        info = td_infos.get(td_id)
        if info is None:
            continue
        if info.status == "missed" and info.justified:
            continue
        if info.status == "missed" and not info.justified:
            scores.append(0.0)
        else:
            scores.append(info.score)
    return scores


def _top_x_scores(scores: list[float], top_x: int) -> list[float]:
    """Return the top X scores from a list. If fewer than X, return all."""
    sorted_desc = sorted(scores, reverse=True)
    return sorted_desc[:min(top_x, len(sorted_desc))]


def _smoothed_median(values: list[float]) -> float:
    """Compute a smoothed median.

    If even count: average of the two middle values.
    If odd count: average of median, one above, and one below.
    If 1 value: return that value.
    If 2 values: return their average.
    """
    n = len(values)
    if n == 0:
        return 0.0
    if n == 1:
        return values[0]

    s = sorted(values)
    if n == 2:
        return (s[0] + s[1]) / 2.0

    if n % 2 == 0:
        mid = n // 2
        return (s[mid - 1] + s[mid]) / 2.0
    else:
        mid = n // 2
        if n == 3:
            return (s[0] + s[1] + s[2]) / 3.0
        return (s[mid - 1] + s[mid] + s[mid + 1]) / 3.0


def _mean(values: list[float]) -> float:
    """Compute the arithmetic mean."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std_dev(values: list[float], mean_val: float) -> float:
    """Compute population standard deviation."""
    if len(values) <= 1:
        return 1.0
    variance = sum((v - mean_val) ** 2 for v in values) / len(values)
    return math.sqrt(variance) if variance > 0 else 1.0


def _mad(values: list[float], median_val: float) -> float:
    """Compute Median Absolute Deviation."""
    if len(values) <= 1:
        return 1.0
    abs_devs = sorted(abs(v - median_val) for v in values)
    n = len(abs_devs)
    if n % 2 == 0:
        mad_val = (abs_devs[n // 2 - 1] + abs_devs[n // 2]) / 2.0
    else:
        mad_val = abs_devs[n // 2]
    return mad_val if mad_val > 0 else 1.0


MAD_MULTIPLIER = 1.4826


def normalize_scores_none(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    training_days: list[TrainingDay],
) -> dict[int, dict[int, float]]:
    """No normalization - return raw scores."""
    return get_raw_scores(student_info, training_days)


def normalize_scores_rank(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    training_days: list[TrainingDay],
) -> dict[int, dict[int, float]]:
    """Rank-based normalization. Ties get the same rank.

    E.g. scores [100, 90, 90, 80] -> ranks [1, 2, 2, 4].
    Justified absences are excluded from ranking.
    """
    raw = get_raw_scores(student_info, training_days)
    result: dict[int, dict[int, float]] = {}

    for td in training_days:
        td_scores: list[tuple[int, float]] = []
        for student_id, td_map in raw.items():
            if td.id in td_map:
                td_scores.append((student_id, td_map[td.id]))

        if not td_scores:
            continue

        sorted_scores = sorted(td_scores, key=lambda x: x[1], reverse=True)

        ranks: dict[int, int] = {}
        current_rank = 1
        i = 0
        while i < len(sorted_scores):
            j = i
            while j < len(sorted_scores) and sorted_scores[j][1] == sorted_scores[i][1]:
                j += 1
            for k in range(i, j):
                ranks[sorted_scores[k][0]] = current_rank
            current_rank = j + 1
            i = j

        for student_id, rank in ranks.items():
            if student_id not in result:
                result[student_id] = {}
            result[student_id][td.id] = float(rank)

    return result


def normalize_scores_median(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    training_days: list[TrainingDay],
    top_x: int = 10,
    normalize_variability: bool = False,
) -> dict[int, dict[int, float]]:
    """Median-based normalization.

    Reference = smoothed median of top X scores.
    If normalize_variability: score = (raw - reference) / (MAD * 1.4826)
    Else: score = raw - reference
    """
    raw = get_raw_scores(student_info, training_days)
    result: dict[int, dict[int, float]] = {}

    for td in training_days:
        ref_scores = _collect_reference_scores(student_info, td.id)
        if not ref_scores:
            continue

        top_scores = _top_x_scores(ref_scores, top_x)
        reference = _smoothed_median(top_scores)

        variability = 1.0
        if normalize_variability:
            variability = _mad(ref_scores, _smoothed_median(ref_scores)) * MAD_MULTIPLIER

        for student_id, td_map in raw.items():
            if td.id not in td_map:
                continue
            normalized = (td_map[td.id] - reference) / variability
            if student_id not in result:
                result[student_id] = {}
            result[student_id][td.id] = normalized

    return result


def normalize_scores_mean(
    student_info: dict[int, dict[int, StudentTrainingDayInfo]],
    training_days: list[TrainingDay],
    top_x: int = 10,
    normalize_variability: bool = False,
) -> dict[int, dict[int, float]]:
    """Mean-based normalization.

    Reference = mean of top X scores.
    If normalize_variability: score = (raw - reference) / SD
    Else: score = raw - reference
    """
    raw = get_raw_scores(student_info, training_days)
    result: dict[int, dict[int, float]] = {}

    for td in training_days:
        ref_scores = _collect_reference_scores(student_info, td.id)
        if not ref_scores:
            continue

        top_scores = _top_x_scores(ref_scores, top_x)
        reference = _mean(top_scores)

        variability = 1.0
        if normalize_variability:
            variability = _std_dev(ref_scores, _mean(ref_scores))

        for student_id, td_map in raw.items():
            if td.id not in td_map:
                continue
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
    """Dispatch to the appropriate normalization method.

    method: one of "none", "rank", "median", "mean".
    """
    if method == "rank":
        return normalize_scores_rank(student_info, training_days)
    elif method == "median":
        return normalize_scores_median(
            student_info, training_days, top_x, normalize_variability
        )
    elif method == "mean":
        return normalize_scores_mean(
            student_info, training_days, top_x, normalize_variability
        )
    else:
        return normalize_scores_none(student_info, training_days)


# ---------------------------------------------------------------------------
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

        if denominator > 0:
            result[student_id] = numerator / denominator
        else:
            result[student_id] = 0.0

    return result


def compute_monthly_decay(first_weight_pct: float, span_days: float) -> float:
    """Compute the monthly decay percentage for display purposes.

    first_weight_pct: the % weight of the first training (e.g. 65).
    span_days: number of days between first and last training.

    return: monthly decay as a percentage (e.g. 11.67).
    """
    if span_days <= 0:
        return 0.0
    months = span_days / 30.0
    if months <= 0:
        return 0.0
    total_decay = 100.0 - first_weight_pct
    return total_decay / months
