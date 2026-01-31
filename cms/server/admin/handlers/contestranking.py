#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2013 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
# Copyright © 2012-2014 Luca Wehrstedt <luca.wehrstedt@gmail.com>
# Copyright © 2014 Artem Iglikov <artem.iglikov@gmail.com>
# Copyright © 2014 Fabian Gundlach <320pointsguy@gmail.com>
# Copyright © 2015 William Di Luigi <williamdiluigi@gmail.com>
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

"""Ranking-related handlers for AWS for a specific contest.

"""

import csv
import io
import json
import logging
from collections import namedtuple

import tornado.web
from sqlalchemy import and_, or_, func
from sqlalchemy.orm import joinedload

from cms.db import Contest, Participation, ScoreHistory, Student, \
    Submission, SubmissionResult, Task

from cms.grading.scorecache import get_cached_score_entry, ensure_valid_history
from cms.server.util import can_access_task, get_student_for_user_in_program
from cms.server.admin.handlers.utils import get_all_student_tags
from .base import BaseHandler, require_permission

logger = logging.getLogger(__name__)


TaskStatus = namedtuple(
    "TaskStatus", ["score", "partial", "has_submissions", "has_opened", "can_access"]
)


class RankingCommonMixin:
    """Mixin for handlers that need ranking logic (calculation and export)."""

    def _load_contest_data(self, contest_id: str) -> Contest:
        """Load a contest with all necessary data for ranking.

        This method loads the contest with tasks, participations, and related
        entities needed for ranking calculation and display.

        Args:
            contest_id: The ID of the contest to load.

        Returns:
            The fully loaded Contest object.
        """
        # This validates the contest id.
        self.safe_get_item(Contest, contest_id)

        # Load contest with tasks, participations, and statement views.
        # We use the score cache to get score and has_submissions.
        # partial is computed at render time via SQL aggregation for correctness.
        contest: Contest = (
            self.sql_session.query(Contest)
            .filter(Contest.id == contest_id)
            .options(joinedload("tasks"))
            .options(joinedload("tasks.active_dataset"))
            .options(joinedload("participations"))
            .options(joinedload("participations.user"))
            .options(joinedload("participations.team"))
            .options(joinedload("participations.statement_views"))
            .first()
        )
        return contest

    def _calculate_scores(self, contest, can_access_by_pt):
        """Calculate scores for all participations in the contest.

        This method uses the efficient approach from RankingHandler:
        1. SQL aggregation for partial flags.
        2. Score cache for scores and submission existence.
        3. Two-phase commit to handle cache rebuilds safely.

        contest: The contest object (with participations and tasks loaded).
        can_access_by_pt: A dict (participation_id, task_id) -> bool indicating
                          if a participant can access a task.

        Returns:
            show_teams (bool): Whether any participation has a team.
        """
        # SQL aggregation to compute t_partial for all participation/task pairs.
        # t_partial is True when there's an official submission that is not yet scored.
        # has_submissions is retrieved from the cache instead.
        # We join with Participation and filter by contest_id instead of using
        # an IN clause with participation IDs for better query plan efficiency.
        partial_flags_query = (
            self.sql_session.query(
                Submission.participation_id,
                Submission.task_id,
                func.bool_or(
                    and_(
                        Task.active_dataset_id.isnot(None),
                        or_(
                            SubmissionResult.submission_id.is_(None),
                            SubmissionResult.score.is_(None),
                            SubmissionResult.score_details.is_(None),
                            SubmissionResult.public_score.is_(None),
                            SubmissionResult.public_score_details.is_(None),
                            SubmissionResult.ranking_score_details.is_(None),
                        ),
                    )
                ).label("t_partial"),
            )
            .join(Participation, Submission.participation_id == Participation.id)
            .join(Task, Submission.task_id == Task.id)
            .outerjoin(
                SubmissionResult,
                and_(
                    SubmissionResult.submission_id == Submission.id,
                    SubmissionResult.dataset_id == Task.active_dataset_id,
                ),
            )
            .filter(Participation.contest_id == contest.id)
            .filter(Submission.official.is_(True))
            .group_by(Submission.participation_id, Submission.task_id)
        )

        # Build lookup dict: (participation_id, task_id) -> t_partial
        partial_by_pt = {}
        for row in partial_flags_query.all():
            partial_by_pt[(row.participation_id, row.task_id)] = row.t_partial or False

        statement_views_set = set()
        for p in contest.participations:
            for sv in p.statement_views:
                statement_views_set.add((sv.participation_id, sv.task_id))

        # Preprocess participations: get data about teams, scores
        # Use the score cache to get score and has_submissions.
        # partial is computed via SQL aggregation above for correctness.
        #
        # Note: get_cached_score_entry may trigger cache rebuilds which acquire
        # advisory locks. We collect data first, then commit to persist any
        # rebuilds and release the locks, then attach transient attributes.
        # This two-phase approach is needed because commit() expires ORM objects,
        # which would clear any dynamically added attributes like task_statuses.
        show_teams = False
        participation_data = {}  # p.id -> (task_statuses, total_score)
        for p in contest.participations:
            show_teams = show_teams or p.team_id

            task_statuses = []
            total_score = 0.0
            partial = False
            for task in contest.get_tasks():
                # Get the cache entry with score and has_submissions
                cache_entry = get_cached_score_entry(self.sql_session, p, task)
                t_score = round(cache_entry.score, task.score_precision)
                has_submissions = cache_entry.has_submissions
                # Get t_partial from SQL aggregation (not from cache)
                t_partial = partial_by_pt.get((p.id, task.id), False)

                has_opened = (p.id, task.id) in statement_views_set
                can_access = can_access_by_pt.get((p.id, task.id), True)
                task_statuses.append(
                    TaskStatus(
                        score=t_score,
                        partial=t_partial,
                        has_submissions=has_submissions,
                        has_opened=has_opened,
                        can_access=can_access,
                    )
                )
                total_score += t_score
                partial = partial or t_partial
            total_score = round(total_score, contest.score_precision)
            participation_data[p.id] = (task_statuses, (total_score, partial))

        # Commit to persist any cache rebuilds and release advisory locks.
        # This is a no-op if no rebuilds occurred.
        self.sql_session.commit()

        # Now attach transient attributes after commit (so they aren't cleared
        # by SQLAlchemy's expire-on-commit behavior).
        for p in contest.participations:
            p.task_statuses, p.total_score = participation_data[p.id]

        return show_teams

    @staticmethod
    def _status_indicator(status: TaskStatus) -> str:
        star = "*" if status.partial else ""
        if not status.can_access:
            return "N/A"
        if not status.has_submissions:
            return "X" if not status.has_opened else "-"
        if not status.has_opened:
            return "!" + star
        return star

    def _write_csv(
        self,
        contest,
        participations,
        tasks,
        student_tags_by_participation,
        show_teams,
        include_partial=True,
        task_archive_progress_by_participation=None,
    ):
        output = io.StringIO()
        writer = csv.writer(output)

        # Build header row
        row = ["Username", "User"]
        if student_tags_by_participation:
            row.append("Tags")
        if task_archive_progress_by_participation:
            row.append("Task Archive Progress")
        if show_teams:
            row.append("Team")
        for task in tasks:
            row.append(task.name)
            if include_partial:
                row.append("P")

        row.append("Global")
        if include_partial:
            row.append("P")

        writer.writerow(row)

        # Build task index lookup for task_statuses.
        # We assume p.task_statuses follows the order of contest.get_tasks().
        all_tasks = list(contest.get_tasks())
        task_index = {task.id: i for i, task in enumerate(all_tasks)}

        for p in participations:
            row = [p.user.username, "%s %s" % (p.user.first_name, p.user.last_name)]
            if student_tags_by_participation:
                tags = student_tags_by_participation.get(p.id, [])
                row.append(", ".join(tags))
            if task_archive_progress_by_participation:
                progress = task_archive_progress_by_participation.get(p.id, {})
                row.append(
                    "%.1f%% (%.1f/%.1f)"
                    % (
                        progress.get("percentage", 0),
                        progress.get("total_score", 0),
                        progress.get("max_score", 0),
                    )
                )
            if show_teams:
                row.append(p.team.name if p.team else "")

            # Calculate total score for exported tasks only
            total_score = 0.0
            partial = False
            for task in tasks:
                idx = task_index.get(task.id)
                if idx is not None and idx < len(p.task_statuses):
                    status = p.task_statuses[idx]
                    row.append(status.score)
                    if include_partial:
                        row.append(self._status_indicator(status))
                    total_score += status.score
                    partial = partial or status.partial
                else:
                    # Should not happen if data is consistent
                    row.append(0)
                    if include_partial:
                        row.append("")

            total_score = round(total_score, contest.score_precision)
            row.append(total_score)
            if include_partial:
                row.append("*" if partial else "")

            writer.writerow(row)

        return output.getvalue()


class RankingHandler(RankingCommonMixin, BaseHandler):
    """Shows the ranking for a contest."""

    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id, format="online"):
        self.contest = self._load_contest_data(contest_id)

        # Build lookup for task accessibility based on visibility tags.
        training_day = self.contest.training_day
        can_access_by_pt = {}  # (participation_id, task_id) -> bool
        for p in self.contest.participations:
            for task in self.contest.get_tasks():
                can_access_by_pt[(p.id, task.id)] = can_access_task(
                    self.sql_session, task, p, training_day
                )

        show_teams = self._calculate_scores(self.contest, can_access_by_pt)

        self.r_params = self.render_params()
        self.r_params["show_teams"] = show_teams
        self.r_params["task_archive_progress_by_participation"] = (
            None  # Only for training programs
        )

        # Check if this is a training day with main groups
        training_day = self.contest.training_day
        main_groups_data = []
        student_tags_by_participation = {}  # participation_id -> list of tags

        # For training days, always build student tags lookup (batch query)
        if training_day:
            training_program = training_day.training_program
            # Batch query: fetch all students for this training program's participations
            participation_user_ids = {p.user_id for p in self.contest.participations}
            students = (
                self.sql_session.query(Student, Participation.user_id)
                .join(Participation, Student.participation_id == Participation.id)
                .filter(Student.training_program_id == training_program.id)
                .filter(Participation.user_id.in_(participation_user_ids))
                .all()
            )
            student_by_user_id = {uid: student for student, uid in students}

            for p in self.contest.participations:
                student = student_by_user_id.get(p.user_id)
                if student:
                    student_tags_by_participation[p.id] = student.student_tags or []
                else:
                    student_tags_by_participation[p.id] = []

        if training_day and training_day.groups:
            # Get main group tag names
            main_group_tags = {g.tag_name for g in training_day.groups}

            # Organize participations by main group
            # A participation belongs to a main group if it has that tag
            participations_by_group = {mg: [] for mg in sorted(main_group_tags)}
            tasks_by_group = {mg: [] for mg in sorted(main_group_tags)}

            for p in self.contest.participations:
                if p.hidden:
                    continue
                p_tags = set(student_tags_by_participation.get(p.id, []))
                p_main_groups = p_tags & main_group_tags
                for mg in p_main_groups:
                    participations_by_group[mg].append(p)

            # Build task index lookup for computing group-specific scores
            all_tasks = list(self.contest.get_tasks())
            task_index = {task.id: i for i, task in enumerate(all_tasks)}

            # For each group, determine which tasks are accessible to at least one member
            for mg in sorted(main_group_tags):
                group_participations = participations_by_group[mg]
                if not group_participations:
                    continue

                # Find tasks accessible to at least one member of this group
                accessible_tasks = []
                for task in self.contest.get_tasks():
                    for p in group_participations:
                        if can_access_by_pt.get((p.id, task.id), True):
                            accessible_tasks.append(task)
                            break

                tasks_by_group[mg] = accessible_tasks

                # Sort participations by group-specific total score (sum of accessible tasks only)
                # Capture accessible_tasks in closure to avoid late binding issues
                def get_group_score(p, tasks=accessible_tasks):
                    return sum(p.task_statuses[task_index[t.id]].score for t in tasks)

                sorted_participations = sorted(
                    group_participations, key=get_group_score, reverse=True
                )

                main_groups_data.append(
                    {
                        "name": mg,
                        "participations": sorted_participations,
                        "tasks": accessible_tasks,
                    }
                )

            # Get all student tags for display
            self.r_params["all_student_tags"] = get_all_student_tags(
                self.sql_session, training_program
            )

        self.r_params["main_groups_data"] = main_groups_data
        self.r_params["student_tags_by_participation"] = student_tags_by_participation
        self.r_params["training_day"] = training_day

        date_str = self.contest.start.strftime("%Y%m%d")
        contest_name = self.contest.name.replace(" ", "_")

        # Handle main_group filter for exports
        main_group_filter = self.get_argument("main_group", None)

        # If main_group filter is specified for export, find the group data
        export_group_data = None
        if main_group_filter and main_groups_data:
            for gd in main_groups_data:
                if gd["name"] == main_group_filter:
                    export_group_data = gd
                    break

        if format == "txt":
            if export_group_data:
                group_slug = main_group_filter.replace(" ", "_").lower()
                filename = f"{date_str}_{contest_name}_ranking_{group_slug}.txt"
            else:
                filename = f"{date_str}_{contest_name}_ranking.txt"
            self.set_header("Content-Type", "text/plain")
            self.set_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.render("ranking.txt", **self.r_params)
        elif format == "csv":
            if export_group_data:
                group_slug = main_group_filter.replace(" ", "_").lower()
                filename = f"{date_str}_{contest_name}_ranking_{group_slug}.csv"
            else:
                filename = f"{date_str}_{contest_name}_ranking.csv"
            self.set_header("Content-Type", "text/csv")
            self.set_header("Content-Disposition", f'attachment; filename="{filename}"')

            contest: Contest = self.r_params["contest"]

            # Determine which participations and tasks to export
            if export_group_data:
                export_participations = export_group_data["participations"]
                export_tasks = export_group_data["tasks"]
            else:
                export_participations = sorted(
                    [p for p in contest.participations if not p.hidden],
                    key=lambda p: p.total_score,
                    reverse=True,
                )
                export_tasks = list(contest.get_tasks())

            csv_content = self._write_csv(
                contest,
                export_participations,
                export_tasks,
                student_tags_by_participation,
                show_teams,
                include_partial=True,
                task_archive_progress_by_participation=self.r_params.get(
                    "task_archive_progress_by_participation"
                ),
            )
            self.finish(csv_content)
        else:
            self.render("ranking.html", **self.r_params)


class ScoreHistoryHandler(BaseHandler):
    """Returns the score history for a contest as JSON.

    This endpoint provides score history data in RWS format:
    [[user_id, task_id, time, score], ...]

    This matches the format expected by RWS's HistoryStore.js for
    computing score and rank histories.

    By default, excludes hidden participations to match ranking page behavior.
    Use ?include_hidden=1 to include hidden participations.

    For training days with main groups, use ?main_group_user_ids=id1,id2,...
    to filter history to only include users from a specific main group.

    Before returning history data, this handler checks for any cache entries
    with history_valid=False and rebuilds their history to ensure correctness.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id):
        # Validate contest exists
        self.safe_get_item(Contest, contest_id)

        include_hidden = self.get_argument("include_hidden", "0") == "1"
        main_group_user_ids_param = self.get_argument("main_group_user_ids", None)

        main_group_user_ids = None
        if main_group_user_ids_param:
            try:
                main_group_user_ids = set(
                    int(uid) for uid in main_group_user_ids_param.split(",") if uid
                )
            except ValueError:
                raise tornado.web.HTTPError(400, "Invalid main_group_user_ids parameter")

        # Ensure all score history for the contest is valid before querying
        if ensure_valid_history(self.sql_session, int(contest_id)):
            self.sql_session.commit()

        query = (
            self.sql_session.query(ScoreHistory)
            .join(Participation)
            .filter(Participation.contest_id == contest_id)
            .options(joinedload(ScoreHistory.participation).joinedload(Participation.user))
        )

        if not include_hidden:
            query = query.filter(Participation.hidden.is_(False))

        if main_group_user_ids is not None:
            query = query.filter(Participation.user_id.in_(main_group_user_ids))

        history = query.order_by(ScoreHistory.timestamp).all()

        result = [
            [
                str(h.participation.user_id),
                str(h.task_id),
                int(h.timestamp.timestamp()),
                h.score,
            ]
            for h in history
        ]

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(result))


class ParticipationDetailHandler(BaseHandler):
    """Shows detailed score/rank progress for a participation.

    This handler provides a user detail view similar to RWS's UserDetail,
    showing score and rank progress over time for a specific participation.
    It includes global and per-task score/rank charts, a navigator table,
    and a submission table for each task.

    For training days with main groups, the ranking is computed relative to
    the user's main group only, not all participants.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id, user_id):
        self.contest = (
            self.sql_session.query(Contest)
            .filter(Contest.id == contest_id)
            .options(joinedload("tasks"))
            .options(joinedload("tasks.active_dataset"))
            .options(joinedload("participations"))
            .options(joinedload("participations.user"))
            .first()
        )
        if self.contest is None:
            raise tornado.web.HTTPError(404, "Contest not found")

        participation = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == contest_id)
            .filter(Participation.user_id == user_id)
            .first()
        )
        if participation is None:
            raise tornado.web.HTTPError(404, "Participation not found")

        visible_participations = [
            p for p in self.contest.participations if not p.hidden
        ]

        training_day = self.contest.training_day
        main_group_user_ids = None
        if training_day and training_day.groups:
            training_program = training_day.training_program
            main_group_tags = {g.tag_name for g in training_day.groups}

            user_student = get_student_for_user_in_program(
                self.sql_session, training_program, user_id
            )
            if user_student:
                user_tags = set(user_student.student_tags or [])
                user_main_groups = user_tags & main_group_tags
                if user_main_groups:
                    # Use deterministic selection (sorted first) instead of arbitrary
                    user_main_group = sorted(user_main_groups)[0]

                    # Batch query: fetch all Student rows for visible participations
                    visible_user_ids = {p.user_id for p in visible_participations}
                    students = (
                        self.sql_session.query(Student, Participation.user_id)
                        .join(Participation, Student.participation_id == Participation.id)
                        .filter(Student.training_program_id == training_program.id)
                        .filter(Participation.user_id.in_(visible_user_ids))
                        .all()
                    )

                    # Build main_group_user_ids from batch results
                    main_group_user_ids = set()
                    for student, uid in students:
                        p_tags = set(student.student_tags or [])
                        if user_main_group in p_tags:
                            main_group_user_ids.add(uid)

        if main_group_user_ids is not None:
            visible_participations = [
                p for p in visible_participations if p.user_id in main_group_user_ids
            ]

        user_count = len(visible_participations)

        users_data = {}
        for p in visible_participations:
            users_data[str(p.user_id)] = {
                "f_name": p.user.first_name or "",
                "l_name": p.user.last_name or "",
            }

        tasks_data = {}
        total_max_score = 0.0
        for task in self.contest.get_tasks():
            max_score = 100.0
            extra_headers = []
            if task.active_dataset:
                try:
                    score_type = task.active_dataset.score_type_object
                    max_score = score_type.max_score
                    extra_headers = score_type.ranking_headers
                except (KeyError, TypeError, AttributeError) as e:
                    logger.warning(
                        "Failed to get score type for task %s: %s", task.id, e
                    )
            tasks_data[str(task.id)] = {
                "key": str(task.id),
                "name": task.title,
                "short_name": task.name,
                "contest": str(self.contest.id),
                "max_score": max_score,
                "score_precision": task.score_precision,
                "extra_headers": extra_headers,
            }
            total_max_score += max_score

        contest_data = {
            "key": str(self.contest.id),
            "name": self.contest.name,
            "begin": int(self.contest.start.timestamp()),
            "end": int(self.contest.stop.timestamp()),
            "max_score": total_max_score,
            "score_precision": self.contest.score_precision,
        }

        self.r_params = self.render_params()
        self.r_params["participation"] = participation
        self.r_params["user_id"] = str(user_id)
        self.r_params["user_count"] = user_count
        self.r_params["users_data"] = users_data
        self.r_params["tasks_data"] = tasks_data
        self.r_params["contest_data"] = contest_data
        history_url = self.url("contest", contest_id, "ranking", "history")
        if main_group_user_ids is not None:
            history_url += "?main_group_user_ids=" + ",".join(
                str(uid) for uid in main_group_user_ids
            )
        self.r_params["history_url"] = history_url
        self.r_params["submissions_url"] = self.url(
            "contest", contest_id, "user", user_id, "submissions"
        )
        self.render("participation_detail.html", **self.r_params)


class ParticipationSubmissionsHandler(BaseHandler):
    """Returns submissions for a participation as JSON in RWS format.

    This endpoint provides submission data in the format expected by
    RWS's UserDetail.js for displaying the submission table.

    """
    @require_permission(BaseHandler.AUTHENTICATED)
    def get(self, contest_id, user_id):
        self.contest = self.safe_get_item(Contest, contest_id)

        participation = (
            self.sql_session.query(Participation)
            .filter(Participation.contest_id == contest_id)
            .filter(Participation.user_id == user_id)
            .first()
        )
        if participation is None:
            raise tornado.web.HTTPError(404, "Participation not found")

        submissions = (
            self.sql_session.query(Submission)
            .filter(Submission.participation_id == participation.id)
            .filter(Submission.official.is_(True))
            .options(joinedload(Submission.token))
            .options(joinedload(Submission.results))
            .order_by(Submission.timestamp)
            .all()
        )

        dataset_by_task_id = {
            task.id: task.active_dataset for task in self.contest.get_tasks()
        }

        result = []
        for s in submissions:
            score = 0.0
            extra = []
            dataset = dataset_by_task_id.get(s.task_id)
            if dataset is not None:
                sr = s.get_result(dataset)
                if sr is not None and sr.score is not None:
                    score = sr.score
                    if sr.ranking_score_details is not None:
                        extra = sr.ranking_score_details

            result.append({
                "task": str(s.task_id),
                "time": int(s.timestamp.timestamp()),
                "score": score,
                "token": s.token is not None,
                "extra": extra,
            })

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(result))
