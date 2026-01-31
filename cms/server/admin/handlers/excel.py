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

"""Excel export utilities and handlers for Training Programs.

This module contains Excel formatting utilities and export handlers for
attendance and combined ranking data.

Functions:
- excel_safe: Escape potentially dangerous Excel values
- excel_build_filename: Build filename for Excel exports
- excel_setup_student_tags_headers: Set up Student/Tags column headers
- excel_build_training_day_title: Build title string for training day
- excel_get_zebra_fills: Get header fills for zebra coloring
- excel_write_student_row: Write student name and tags to row
- excel_write_training_day_header: Write training day header with merge

Handlers:
- ExportAttendanceHandler: Export attendance data to Excel
- ExportCombinedRankingHandler: Export combined ranking to Excel
"""

import io
import re
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from cms.db import TrainingProgram

from .base import BaseHandler, require_permission
from .training_analytics import TrainingProgramFilterMixin, build_attendance_data, build_ranking_data


EXCEL_ZEBRA_COLORS = [
    ("4472C4", "D9E2F3"),
    ("70AD47", "E2EFDA"),
    ("ED7D31", "FCE4D6"),
    ("7030A0", "E4DFEC"),
    ("00B0F0", "DAEEF3"),
    ("FFC000", "FFF2CC"),
]

EXCEL_HEADER_FONT = Font(bold=True)
EXCEL_HEADER_FONT_WHITE = Font(bold=True, color="FFFFFF")
EXCEL_THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
EXCEL_DEFAULT_HEADER_FILL = PatternFill(
    start_color="4472C4", end_color="4472C4", fill_type="solid"
)


def excel_safe(value: str) -> str:
    """Escape potentially dangerous Excel values."""
    if value and value[0] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def excel_build_filename(
    program_name: str,
    export_type: str,
    start_date: Any,
    end_date: Any,
    training_day_types: list[str] | None,
    student_tags: list[str] | None,
) -> str:
    """Build a filename for Excel export based on filters."""
    program_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", program_name)
    filename_parts = [program_slug, export_type]

    if start_date:
        filename_parts.append(f"from_{start_date.strftime('%Y%m%d')}")
    if end_date:
        filename_parts.append(f"to_{end_date.strftime('%Y%m%d')}")
    if training_day_types:
        types_slug = re.sub(
            r"[^A-Za-z0-9_-]+", "_", "_".join(training_day_types)
        )
        filename_parts.append(f"types_{types_slug}")
    if student_tags:
        tags_slug = re.sub(r"[^A-Za-z0-9_-]+", "_", "_".join(student_tags))
        filename_parts.append(f"tags_{tags_slug}")

    return "_".join(filename_parts) + ".xlsx"


def excel_setup_student_tags_headers(
    ws: Worksheet,
    default_fill: PatternFill,
) -> None:
    """Set up Student and Tags column headers (merged across rows 1-2)."""
    ws.cell(row=1, column=1, value="Student")
    ws.cell(row=1, column=1).font = EXCEL_HEADER_FONT_WHITE
    ws.cell(row=1, column=1).fill = default_fill
    ws.cell(row=1, column=1).border = EXCEL_THIN_BORDER
    ws.cell(row=1, column=1).alignment = Alignment(
        horizontal="center", vertical="center"
    )
    ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)

    ws.cell(row=1, column=2, value="Tags")
    ws.cell(row=1, column=2).font = EXCEL_HEADER_FONT_WHITE
    ws.cell(row=1, column=2).fill = default_fill
    ws.cell(row=1, column=2).border = EXCEL_THIN_BORDER
    ws.cell(row=1, column=2).alignment = Alignment(
        horizontal="center", vertical="center"
    )
    ws.merge_cells(start_row=1, start_column=2, end_row=2, end_column=2)


def excel_build_training_day_title(td: Any) -> str:
    """Build a title string for a training day including types."""
    title = td.description or td.name or "Session"
    if td.start_time:
        title += f" ({td.start_time.strftime('%b %d')})"
    if td.training_day_types:
        title += f" [{'; '.join(td.training_day_types)}]"
    return title


def excel_get_zebra_fills(color_idx: int) -> tuple[PatternFill, PatternFill]:
    """Get header and subheader fills for zebra coloring."""
    header_color, subheader_color = EXCEL_ZEBRA_COLORS[
        color_idx % len(EXCEL_ZEBRA_COLORS)
    ]
    header_fill = PatternFill(
        start_color=header_color, end_color=header_color, fill_type="solid"
    )
    subheader_fill = PatternFill(
        start_color=subheader_color, end_color=subheader_color, fill_type="solid"
    )
    return header_fill, subheader_fill


def excel_write_student_row(
    ws: Worksheet,
    row: int,
    student: Any,
) -> None:
    """Write student name and tags to columns 1 and 2."""
    if student.participation:
        user = student.participation.user
        student_name = f"{user.first_name} {user.last_name} ({user.username})"
    else:
        student_name = "(Unknown)"

    ws.cell(row=row, column=1, value=excel_safe(student_name))
    ws.cell(row=row, column=1).border = EXCEL_THIN_BORDER

    tags_str = ""
    if student.student_tags:
        tags_str = "; ".join(student.student_tags)
    ws.cell(row=row, column=2, value=excel_safe(tags_str))
    ws.cell(row=row, column=2).border = EXCEL_THIN_BORDER


def excel_write_training_day_header(
    ws: Worksheet,
    col: int,
    td: Any,
    td_idx: int,
    num_columns: int,
) -> None:
    """Write a training day header row with zebra coloring and merge cells.

    ws: the worksheet to write to.
    col: the starting column for this training day header.
    td: the training day object.
    td_idx: the index of the training day (for zebra coloring).
    num_columns: the number of columns to merge for this training day.
    """
    title = excel_build_training_day_title(td)
    safe_title = excel_safe(title)
    header_fill, _ = excel_get_zebra_fills(td_idx)

    ws.cell(row=1, column=col, value=safe_title)
    ws.cell(row=1, column=col).font = EXCEL_HEADER_FONT_WHITE
    ws.cell(row=1, column=col).fill = header_fill
    ws.cell(row=1, column=col).border = EXCEL_THIN_BORDER
    ws.cell(row=1, column=col).alignment = Alignment(
        horizontal="center", vertical="center"
    )
    ws.merge_cells(
        start_row=1, start_column=col,
        end_row=1, end_column=col + num_columns - 1
    )


class ExportAttendanceHandler(TrainingProgramFilterMixin, BaseHandler):
    """Export attendance data to Excel format."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        """Export filtered attendance data to Excel."""
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            _,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        if not archived_training_days:
            self.redirect(self.url(
                "training_program", training_program_id, "attendance"
            ))
            return

        attendance_data, _, sorted_students = build_attendance_data(
            archived_training_days, student_tags, current_tag_student_ids
        )

        wb = Workbook()
        ws = wb.active
        ws.title = "Attendance"

        subcolumns = ["Status", "Location", "Recorded", "Delay Reasons", "Comments"]
        num_subcolumns = len(subcolumns)

        excel_setup_student_tags_headers(ws, EXCEL_DEFAULT_HEADER_FILL)

        col = 3
        for td_idx, td in enumerate(archived_training_days):
            excel_write_training_day_header(ws, col, td, td_idx, num_subcolumns)
            _, subheader_fill = excel_get_zebra_fills(td_idx)

            for i, subcol_name in enumerate(subcolumns):
                cell = ws.cell(row=2, column=col + i, value=subcol_name)
                cell.font = EXCEL_HEADER_FONT
                cell.fill = subheader_fill
                cell.border = EXCEL_THIN_BORDER
                cell.alignment = Alignment(horizontal="center")

            col += num_subcolumns

        row = 3
        for student in sorted_students:
            excel_write_student_row(ws, row, student)

            col = 3
            for td in archived_training_days:
                att = attendance_data.get(student.id, {}).get(td.id)

                if att:
                    if att.status == "missed":
                        if att.justified:
                            status = "Justified Absent"
                        else:
                            status = "Missed"
                    elif att.delay_time:
                        delay_minutes = att.delay_time.total_seconds() / 60
                        if delay_minutes < 60:
                            status = f"Delayed ({delay_minutes:.0f}m)"
                        else:
                            status = f"Delayed ({delay_minutes / 60:.1f}h)"
                    else:
                        status = "On Time"

                    location = ""
                    if att.status != "missed" and att.location:
                        location_map = {
                            "class": "Class",
                            "home": "Home",
                            "both": "Both",
                        }
                        location = location_map.get(att.location, att.location)

                    recorded = ""
                    if att.status != "missed":
                        recorded = "Yes" if att.recorded else "No"

                    delay_reasons = att.delay_reasons or ""
                    comment = att.comment or ""
                else:
                    status = ""
                    location = ""
                    recorded = ""
                    delay_reasons = ""
                    comment = ""

                values = [status, location, recorded, delay_reasons, comment]
                for i, value in enumerate(values):
                    cell = ws.cell(row=row, column=col + i, value=excel_safe(value))
                    cell.border = EXCEL_THIN_BORDER

                col += num_subcolumns

            row += 1

        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 20
        for col_idx in range(3, 3 + len(archived_training_days) * num_subcolumns):
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 15

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = excel_build_filename(
            training_program.name, "attendance",
            start_date, end_date, training_day_types, student_tags
        )

        self.set_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.set_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"'
        )
        self.write(output.getvalue())
        self.finish()


class ExportCombinedRankingHandler(TrainingProgramFilterMixin, BaseHandler):
    """Export combined ranking data to Excel format."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, training_program_id: str):
        """Export filtered combined ranking data to Excel."""
        training_program = self.safe_get_item(TrainingProgram, training_program_id)

        (
            start_date,
            end_date,
            training_day_types,
            student_tags,
            student_tags_mode,
            archived_training_days,
            current_tag_student_ids,
        ) = self._get_filtered_context(training_program)

        if not archived_training_days:
            self.redirect(self.url(
                "training_program", training_program_id, "combined_ranking"
            ))
            return

        (
            ranking_data,
            all_students,
            training_day_tasks,
            filtered_training_days,
            _,
        ) = build_ranking_data(
            self.sql_session,
            archived_training_days,
            student_tags,
            student_tags_mode,
            current_tag_student_ids,
            self._tags_match,
        )

        if not filtered_training_days:
            self.redirect(self.url(
                "training_program", training_program_id, "combined_ranking"
            ))
            return

        sorted_students = sorted(
            all_students.values(),
            key=lambda s: s.participation.user.username if s.participation else ""
        )

        wb = Workbook()
        ws = wb.active
        ws.title = "Combined Ranking"

        excel_setup_student_tags_headers(ws, EXCEL_DEFAULT_HEADER_FILL)

        col = 3
        for td_idx, td in enumerate(filtered_training_days):
            tasks = training_day_tasks.get(td.id, [])
            num_task_cols = len(tasks) + 1

            excel_write_training_day_header(ws, col, td, td_idx, num_task_cols)
            _, subheader_fill = excel_get_zebra_fills(td_idx)

            for i, task in enumerate(tasks):
                cell = ws.cell(row=2, column=col + i, value=excel_safe(task["name"]))
                cell.font = EXCEL_HEADER_FONT
                cell.fill = subheader_fill
                cell.border = EXCEL_THIN_BORDER
                cell.alignment = Alignment(horizontal="center")

            total_cell = ws.cell(row=2, column=col + len(tasks), value="Total")
            total_cell.font = EXCEL_HEADER_FONT
            total_cell.fill = subheader_fill
            total_cell.border = EXCEL_THIN_BORDER
            total_cell.alignment = Alignment(horizontal="center")

            col += num_task_cols

        global_header_fill = PatternFill(
            start_color="808080", end_color="808080", fill_type="solid"
        )
        ws.cell(row=1, column=col, value="Global")
        ws.cell(row=1, column=col).font = EXCEL_HEADER_FONT_WHITE
        ws.cell(row=1, column=col).fill = global_header_fill
        ws.cell(row=1, column=col).border = EXCEL_THIN_BORDER
        ws.cell(row=1, column=col).alignment = Alignment(
            horizontal="center", vertical="center"
        )
        ws.merge_cells(start_row=1, start_column=col, end_row=2, end_column=col)

        row = 3
        for student in sorted_students:
            excel_write_student_row(ws, row, student)

            col = 3
            global_total = 0.0

            for td in filtered_training_days:
                tasks = training_day_tasks.get(td.id, [])
                ranking = ranking_data.get(student.id, {}).get(td.id)

                td_total = 0.0
                for task in tasks:
                    score_val = None
                    if ranking and ranking.task_scores:
                        score_val = ranking.task_scores.get(str(task["id"]))

                    cell = ws.cell(row=row, column=col)
                    if score_val is not None:
                        cell.value = score_val
                        td_total += score_val
                    else:
                        cell.value = ""
                    cell.border = EXCEL_THIN_BORDER
                    col += 1

                total_cell = ws.cell(row=row, column=col)
                if ranking and ranking.task_scores:
                    total_cell.value = td_total
                    global_total += td_total
                else:
                    total_cell.value = ""
                total_cell.border = EXCEL_THIN_BORDER
                col += 1

            global_cell = ws.cell(row=row, column=col)
            global_cell.value = global_total if global_total > 0 else ""
            global_cell.border = EXCEL_THIN_BORDER
            global_cell.font = Font(bold=True)

            row += 1

        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 20

        total_cols = 3
        for td in filtered_training_days:
            total_cols += len(training_day_tasks.get(td.id, [])) + 1
        total_cols += 1

        for col_idx in range(3, total_cols):
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 10

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        filename = excel_build_filename(
            training_program.name, "ranking",
            start_date, end_date, training_day_types, student_tags
        )

        self.set_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        self.set_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"'
        )
        self.write(output.getvalue())
        self.finish()
