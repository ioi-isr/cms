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

"""Admin handler for importing a training day from CSV files.

This allows importing results from external contests or regular contests
that weren't originally conducted inside the training program. The import
creates an archived training day with all the data normally produced by
the archive flow (scores, attendance, archived_tasks_data).

The ranking CSV must have the same format as the CSV export from the
ranking page of a contest or training day. An optional delays CSV
(same format as delays and extra times export) provides attendance data.
"""

import csv
import io
import logging
import re
from datetime import datetime as dt, timedelta

from cms.db import (
    TrainingProgram,
    Student,
    TrainingDay,
    ArchivedAttendance,
    ArchivedStudentRanking,
)
from cms.server.admin.handlers.utils import (
    parse_tags,
)
from cmscommon.datetime import make_datetime

from .base import BaseHandler, require_permission

logger = logging.getLogger(__name__)


_IMPORT_FAILED = "Import failed"

# Columns in the ranking CSV that are not task scores.
# These are identified by header name and skipped when collecting task columns.
_NON_TASK_HEADERS = {"username", "user", "tags", "team", "task archive progress",
                     "global", "p"}


def _to_codename(text: str) -> str:
    """Convert a free-form string to a valid Codename.

    Codename only allows [A-Za-z0-9_-]+.  Spaces become underscores,
    other disallowed characters are stripped.  Falls back to
    'imported' if the result is empty.
    """
    result = text.replace(" ", "_")
    result = re.sub(r"[^A-Za-z0-9_-]", "", result)
    return result or "imported"


def _parse_ranking_csv(csv_content: str) -> tuple[
    list[tuple[str, int]],
    list[str],
    list[list[str]],
]:
    """Parse a ranking CSV and extract task names and per-user data.

    csv_content: the full text of the ranking CSV file.

    return: (tasks, all_headers, rows) where:
        - tasks: ordered list of (task_name, column_index) tuples
        - all_headers: all header names (lowercased)
        - rows: list of raw row values (list of strings per row)
    """
    reader = csv.reader(io.StringIO(csv_content))
    try:
        raw_headers = next(reader)
    except StopIteration:
        raise ValueError("Empty ranking CSV: no headers found") from None
    headers = [h.strip() for h in raw_headers]
    headers_lower = [h.lower() for h in headers]

    # Identify task columns: any column whose header is not a known non-task
    # header. "P" columns (partial indicators) follow task columns and
    # the Global column; we skip them.  We track the column index so that
    # score extraction is positional (no overwrites when two tasks share
    # the same lowercased name).
    tasks: list[tuple[str, int]] = []

    i = 0
    while i < len(headers_lower):
        h = headers_lower[i]
        if h in _NON_TASK_HEADERS:
            i += 1
            continue
        # This is a task column
        tasks.append((headers[i], i))
        # Check if next column is a "P" (partial) column
        if i + 1 < len(headers_lower) and headers_lower[i + 1] == "p":
            i += 2  # skip task + P
        else:
            i += 1

    rows: list[list[str]] = []
    for row_values in reader:
        if not row_values or all(v.strip() == "" for v in row_values):
            continue
        rows.append([v.strip() for v in row_values])

    return tasks, headers_lower, rows


def _parse_delays_csv(csv_content: str) -> dict[str, dict]:
    """Parse a delays and extra times CSV.

    csv_content: the full text of the delays CSV file.

    return: dict mapping username -> {delay_seconds: int, status: str}
    """
    reader = csv.reader(io.StringIO(csv_content))
    try:
        raw_headers = next(reader)
    except StopIteration as e:
        raise ValueError("Empty delays CSV: no headers found") from e
    headers_lower = [h.strip().lower() for h in raw_headers]

    username_idx = None
    delay_idx = None
    status_idx = None

    for i, h in enumerate(headers_lower):
        if h == "username":
            username_idx = i
        elif h == "delay time (seconds)":
            delay_idx = i
        elif h == "status":
            status_idx = i

    if username_idx is None:
        raise ValueError(
            "Delays CSV is missing the 'Username' column"
        )

    result: dict[str, dict] = {}
    for row_values in reader:
        if not row_values or all(v.strip() == "" for v in row_values):
            continue
        username = row_values[username_idx].strip() if username_idx < len(row_values) else ""
        if not username:
            continue

        delay_seconds = 0
        if delay_idx is not None and delay_idx < len(row_values):
            try:
                delay_seconds = int(row_values[delay_idx].strip())
            except (ValueError, TypeError):
                delay_seconds = 0

        status = ""
        if status_idx is not None and status_idx < len(row_values):
            status = row_values[status_idx].strip()

        result[username] = {
            "delay_seconds": delay_seconds,
            "status": status,
        }

    return result


def _get_task_score(row: list[str], col_idx: int) -> float:
    """Extract a task score from a CSV row by column index.

    row: list of cell values for this CSV row.
    col_idx: the column index of the task.

    return: the score as a float, or 0.0 if missing/invalid.
    """
    if col_idx >= len(row):
        return 0.0
    val = row[col_idx]
    if not val:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _get_row_value(row: list[str], headers_lower: list[str],
                   column_name: str) -> str:
    """Get a value from a CSV row by lowercased column name.

    row: list of cell values for this CSV row.
    headers_lower: list of lowercased header names.
    column_name: the lowercased column name to look up.

    return: the cell value, or '' if not found.
    """
    try:
        idx = headers_lower.index(column_name)
    except ValueError:
        return ""
    if idx >= len(row):
        return ""
    return row[idx]


class ImportTrainingDayFromCsvHandler(BaseHandler):
    """Import a training day from ranking CSV (and optional delays CSV).

    Creates an archived training day directly, with ArchivedStudentRanking
    and ArchivedAttendance records for each matched student.
    """

    @require_permission(BaseHandler.PERMISSION_ALL)
    def post(self, training_program_id: str):
        fallback_page = self.url(
            "training_program", training_program_id, "combined_ranking"
        )

        training_program = self.safe_get_item(
            TrainingProgram, training_program_id
        )

        # --- Parse form fields ---
        training_day_date_str = self.get_argument("training_day_date", "")
        if not training_day_date_str:
            self.service.add_notification(
                make_datetime(), _IMPORT_FAILED, "Training day date is required."
            )
            self.redirect(fallback_page)
            return

        try:
            training_day_date = dt.strptime(
                training_day_date_str, "%Y-%m-%d"
            )
        except ValueError:
            self.service.add_notification(
                make_datetime(),
                _IMPORT_FAILED,
                "Invalid date format. Please use YYYY-MM-DD.",
            )
            self.redirect(fallback_page)
            return

        td_name = self.get_argument("training_day_name", "").strip()
        if not td_name:
            td_name = "Imported Training Day"

        td_description = self.get_argument(
            "training_day_description", ""
        ).strip()
        if not td_description:
            td_description = td_name

        duration_minutes_str = self.get_argument(
            "training_day_duration", "300"
        )
        try:
            duration_minutes = int(duration_minutes_str)
            if duration_minutes <= 0:
                raise ValueError("Duration must be positive")
        except (ValueError, TypeError):
            duration_minutes = 300

        td_types_str = self.get_argument("training_day_types", "")
        td_types = parse_tags(td_types_str) if td_types_str else []

        # --- Parse CSV files ---
        ranking_files = self.request.files.get("ranking_csv")
        if not ranking_files:
            self.service.add_notification(
                make_datetime(), _IMPORT_FAILED, "Ranking CSV file is required."
            )
            self.redirect(fallback_page)
            return

        try:
            ranking_csv_content = ranking_files[0]["body"].decode("utf-8-sig")
        except (UnicodeDecodeError, IndexError):
            self.service.add_notification(
                make_datetime(),
                _IMPORT_FAILED,
                "Could not read ranking CSV file. "
                "Please ensure it is a valid UTF-8 CSV.",
            )
            self.redirect(fallback_page)
            return

        delays_data: dict[str, dict] = {}
        delays_files = self.request.files.get("delays_csv")
        if delays_files:
            try:
                delays_csv_content = delays_files[0]["body"].decode(
                    "utf-8-sig"
                )
                delays_data = _parse_delays_csv(delays_csv_content)
            except (UnicodeDecodeError, IndexError):
                self.service.add_notification(
                    make_datetime(),
                    _IMPORT_FAILED,
                    "Could not read delays CSV file. "
                    "Please ensure it is a valid UTF-8 CSV.",
                )
                self.redirect(fallback_page)
                return
            except ValueError as e:
                self.service.add_notification(make_datetime(), _IMPORT_FAILED, str(e))
                self.redirect(fallback_page)
                return

        # --- Parse ranking CSV ---
        try:
            tasks, headers_lower, rows = _parse_ranking_csv(
                ranking_csv_content
            )
        except (csv.Error, ValueError) as e:
            self.service.add_notification(
                make_datetime(),
                _IMPORT_FAILED,
                f"Could not parse ranking CSV: {e}",
            )
            self.redirect(fallback_page)
            return

        if not tasks:
            self.service.add_notification(
                make_datetime(),
                _IMPORT_FAILED,
                "No task columns found in the ranking CSV.",
            )
            self.redirect(fallback_page)
            return

        if not rows:
            self.service.add_notification(
                make_datetime(),
                _IMPORT_FAILED,
                "No data rows found in the ranking CSV.",
            )
            self.redirect(fallback_page)
            return

        has_tags_column = "tags" in headers_lower

        # --- Match students ---
        # Build username -> student lookup
        username_to_student: dict[str, Student] = {}
        for student in training_program.students:
            if student.participation and not student.participation.hidden:
                username_to_student[
                    student.participation.user.username
                ] = student

        try:
            self._do_import(
                training_program=training_program,
                training_day_date=training_day_date,
                td_name=td_name,
                td_description=td_description,
                duration_minutes=duration_minutes,
                td_types=td_types,
                tasks=tasks,
                has_tags_column=has_tags_column,
                headers_lower=headers_lower,
                rows=rows,
                delays_data=delays_data,
                username_to_student=username_to_student,
            )
        except Exception as e:
            self.sql_session.rollback()
            logger.exception("CSV import failed")
            self.service.add_notification(make_datetime(), _IMPORT_FAILED, repr(e))
            self.redirect(fallback_page)
            return

        if self.try_commit():
            self.service.add_notification(
                make_datetime(),
                "Training day imported",
                f"Training day '{td_name}' has been imported from CSV "
                f"successfully.",
            )

        self.redirect(fallback_page)

    # Large numeric base for synthetic task IDs to avoid collisions
    # with real Task PKs.  The combined-ranking handler does
    # ``int(task_id_str)`` so keys must be numeric strings.
    _SYNTHETIC_ID_BASE = 10_000_000

    @staticmethod
    def _build_archived_tasks_data(
        tasks: list[tuple[str, int]],
        rows: list[list[str]],
    ) -> dict[str, dict]:
        """Build the archived_tasks_data dict for a training day.

        tasks: ordered list of (task_name, column_index) tuples.
        rows: parsed CSV data rows.

        return: dict mapping synthetic task-id strings to task metadata.
        """
        base = ImportTrainingDayFromCsvHandler._SYNTHETIC_ID_BASE
        task_max_scores: dict[str, float] = {}
        for task_idx, (_task_name, col_idx) in enumerate(tasks):
            task_key = str(base + task_idx)
            max_score = 0.0
            for row in rows:
                score = _get_task_score(row, col_idx)
                if score > max_score:
                    max_score = score
            # Default to 100 if no scores found
            if max_score <= 0:
                max_score = 100.0
            task_max_scores[task_key] = max_score

        archived_tasks_data: dict[str, dict] = {}
        for task_idx, (task_name, _) in enumerate(tasks):
            task_key = str(base + task_idx)
            archived_tasks_data[task_key] = {
                "name": task_name,
                "short_name": task_name,
                "max_score": task_max_scores[task_key],
                "score_precision": 2,
                "extra_headers": [],
                "training_day_num": task_idx,
            }

        return archived_tasks_data

    @staticmethod
    def _parse_student_tags(
        row: list[str],
        headers_lower: list[str],
        student: Student,
        has_tags_column: bool,
    ) -> list[str]:
        """Determine student tags for a CSV row.

        Uses the CSV "tags" column if present and non-empty, otherwise
        falls back to the student's current tags.
        """
        if has_tags_column:
            tags_str = _get_row_value(row, headers_lower, "tags")
            if tags_str:
                return [t.strip() for t in tags_str.split(",") if t.strip()]
        return list(student.student_tags) if student.student_tags else []

    def _create_archived_ranking(
        self,
        student: Student,
        training_day: TrainingDay,
        student_tags: list[str],
        task_scores: dict[str, float],
    ) -> None:
        """Create and persist an ArchivedStudentRanking record."""
        archived_ranking = ArchivedStudentRanking(
            student_tags=student_tags,
            task_scores=task_scores if task_scores else None,
            submissions=None,
            history=None,
        )
        archived_ranking.training_day_id = training_day.id
        archived_ranking.student_id = student.id
        self.sql_session.add(archived_ranking)

    def _create_archived_attendance(
        self,
        student: Student,
        training_day: TrainingDay,
        delay_info: dict | None,
    ) -> None:
        """Create and persist an ArchivedAttendance record."""
        if delay_info:
            delay_seconds = delay_info.get("delay_seconds", 0)
            status_str = delay_info.get("status", "").lower()
            if status_str == "missed":
                status = "missed"
                delay_time = None
            else:
                status = "participated"
                delay_time = (
                    timedelta(seconds=delay_seconds) if delay_seconds > 0 else None
                )
        else:
            delay_time = None
            status = "participated"

        archived_attendance = ArchivedAttendance(
            status=status,
            location=None,
            delay_time=delay_time,
            delay_reasons=None,
        )
        archived_attendance.training_day_id = training_day.id
        archived_attendance.student_id = student.id
        self.sql_session.add(archived_attendance)

    def _process_csv_rows(
        self,
        rows: list[list[str]],
        headers_lower: list[str],
        tasks: list[tuple[str, int]],
        has_tags_column: bool,
        delays_data: dict[str, dict],
        username_to_student: dict[str, Student],
        training_day: TrainingDay,
    ) -> tuple[int, list[str]]:
        """Process CSV rows, creating ranking and attendance records.

        return: (matched_count, skipped_usernames)
        """
        base = self._SYNTHETIC_ID_BASE
        matched_count = 0
        skipped_usernames: list[str] = []
        seen_student_ids: set[int] = set()

        for row in rows:
            username = _get_row_value(row, headers_lower, "username")
            if not username:
                username = _get_row_value(row, headers_lower, "user")
            if not username:
                continue

            student = username_to_student.get(username)
            if student is None:
                skipped_usernames.append(username)
                continue

            if student.id in seen_student_ids:
                logger.warning("Duplicate username '%s' in CSV, skipping", username)
                continue
            seen_student_ids.add(student.id)

            matched_count += 1

            # Build task_scores
            task_scores: dict[str, float] = {}
            for task_idx, (_task_name, col_idx) in enumerate(tasks):
                task_key = str(base + task_idx)
                task_scores[task_key] = _get_task_score(row, col_idx)

            student_tags = self._parse_student_tags(
                row, headers_lower, student, has_tags_column
            )
            self._create_archived_ranking(
                student, training_day, student_tags, task_scores
            )
            self._create_archived_attendance(
                student, training_day, delays_data.get(username)
            )

        return matched_count, skipped_usernames

    def _do_import(
        self,
        training_program: TrainingProgram,
        training_day_date: dt,
        td_name: str,
        td_description: str,
        duration_minutes: int,
        td_types: list[str],
        tasks: list[tuple[str, int]],
        has_tags_column: bool,
        headers_lower: list[str],
        rows: list[list[str]],
        delays_data: dict[str, dict],
        username_to_student: dict[str, Student],
    ) -> None:
        """Perform the actual import, creating all DB records.

        This is separated from the POST handler so we can wrap it in
        a try/except for clean rollback.
        """
        # 1. Create the archived training day
        position = len(training_program.training_days)
        training_day = TrainingDay(
            training_program=training_program,
            position=position,
            name=_to_codename(td_name),
            description=td_description,
            start_time=training_day_date,
            duration=timedelta(minutes=duration_minutes),
            training_day_types=td_types,
        )
        self.sql_session.add(training_day)
        self.sql_session.flush()

        # 2. Build archived_tasks_data
        training_day.archived_tasks_data = self._build_archived_tasks_data(tasks, rows)

        # 3. Process each row from the CSV
        matched_count, skipped_usernames = self._process_csv_rows(
            rows=rows,
            headers_lower=headers_lower,
            tasks=tasks,
            has_tags_column=has_tags_column,
            delays_data=delays_data,
            username_to_student=username_to_student,
            training_day=training_day,
        )

        # 4. Validate that at least one student matched (check before
        #    adding notifications so that a rollback doesn't leave
        #    misleading "Some students not found" messages).
        if matched_count == 0:
            raise ValueError(
                "No students from the CSV matched any student in the "
                "training program. Please check that the usernames in "
                "the CSV match the usernames in the training program."
            )

        # 5. Log warnings for skipped users (only after confirming
        #    the import will proceed).
        if skipped_usernames:
            logger.warning(
                "CSV import for training program %s: skipped %d usernames "
                "not found in program: %s",
                training_program.name,
                len(skipped_usernames),
                ", ".join(skipped_usernames[:10]),
            )
            if len(skipped_usernames) <= 5:
                skip_msg = ", ".join(skipped_usernames)
            else:
                skip_msg = (
                    ", ".join(skipped_usernames[:5])
                    + f" and {len(skipped_usernames) - 5} more"
                )
            self.service.add_notification(
                make_datetime(),
                "Some students not found",
                f"Skipped {len(skipped_usernames)} username(s) not found "
                f"in the training program: {skip_msg}",
            )
