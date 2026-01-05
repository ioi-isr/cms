-- Add contest_folders table and folder_id on contests
CREATE TABLE public.contest_folders (
    id integer NOT NULL,
    name public.codename NOT NULL,
    description character varying NOT NULL,
    parent_id integer,
    hidden boolean NOT NULL DEFAULT false
);

CREATE SEQUENCE public.contest_folders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.contest_folders_id_seq OWNED BY public.contest_folders.id;

ALTER TABLE ONLY public.contest_folders
    ALTER COLUMN id SET DEFAULT nextval('public.contest_folders_id_seq'::regclass);

ALTER TABLE ONLY public.contest_folders
    ADD CONSTRAINT contest_folders_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.contest_folders
    ADD CONSTRAINT contest_folders_name_key UNIQUE (name);

ALTER TABLE ONLY public.contest_folders
    ADD CONSTRAINT contest_folders_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.contest_folders(id) ON UPDATE CASCADE ON DELETE RESTRICT;

CREATE INDEX ix_contest_folders_parent_id ON public.contest_folders USING btree (parent_id);

ALTER TABLE ONLY public.contest_folders
    ALTER COLUMN hidden DROP DEFAULT;

ALTER TABLE public.contests ADD COLUMN folder_id integer;
ALTER TABLE ONLY public.contests
    ADD CONSTRAINT contests_folder_id_fkey FOREIGN KEY (folder_id) REFERENCES public.contest_folders(id) ON UPDATE CASCADE ON DELETE SET NULL;
CREATE INDEX ix_contests_folder_id ON public.contests (folder_id);
BEGIN;

-- https://github.com/cms-dev/cms/pull/1378
ALTER TYPE public.feedback_level ADD VALUE 'oi_restricted';

-- https://github.com/cms-dev/cms/pull/1391
ALTER TABLE public.contests ADD COLUMN min_submission_interval_grace_period interval;
ALTER TABLE public.contests ADD CONSTRAINT contests_min_submission_interval_grace_period_check CHECK ((min_submission_interval_grace_period > '00:00:00'::interval));

-- https://github.com/cms-dev/cms/pull/1392
ALTER TABLE public.contests ADD COLUMN allow_unofficial_submission_before_analysis_mode boolean NOT NULL DEFAULT false;
ALTER TABLE public.contests ALTER COLUMN allow_unofficial_submission_before_analysis_mode DROP DEFAULT;

-- https://github.com/cms-dev/cms/pull/1393
ALTER TABLE public.submission_results ADD COLUMN scored_at timestamp without time zone;

-- https://github.com/cms-dev/cms/pull/1416
ALTER TABLE ONLY public.participations DROP CONSTRAINT participations_team_id_fkey;
ALTER TABLE ONLY public.participations ADD CONSTRAINT participations_team_id_fkey FOREIGN KEY (team_id) REFERENCES public.teams(id) ON UPDATE CASCADE ON DELETE SET NULL;

-- https://github.com/cms-dev/cms/pull/1419
ALTER TABLE submissions ADD COLUMN opaque_id BIGINT;
UPDATE submissions SET opaque_id = id WHERE opaque_id IS NULL;
ALTER TABLE submissions ADD CONSTRAINT participation_opaque_unique UNIQUE (participation_id, opaque_id);
ALTER TABLE submissions ALTER COLUMN opaque_id SET NOT NULL;

-- https://github.com/cms-dev/cms/pull/1456
ALTER TABLE submission_results ADD COLUMN compilation_sandbox_paths VARCHAR[];
ALTER TABLE submission_results ADD COLUMN compilation_sandbox_digests VARCHAR[];
UPDATE submission_results SET compilation_sandbox_paths = string_to_array(compilation_sandbox, ':');
ALTER TABLE submission_results DROP COLUMN compilation_sandbox;
ALTER TABLE evaluations ADD COLUMN evaluation_sandbox_paths VARCHAR[];
ALTER TABLE evaluations ADD COLUMN evaluation_sandbox_digests VARCHAR[];
UPDATE evaluations SET evaluation_sandbox_paths = string_to_array(evaluation_sandbox, ':');
ALTER TABLE evaluations DROP COLUMN evaluation_sandbox;
ALTER TABLE user_test_results ADD COLUMN compilation_sandbox_paths VARCHAR[];
ALTER TABLE user_test_results ADD COLUMN compilation_sandbox_digests VARCHAR[];
UPDATE user_test_results SET compilation_sandbox_paths = string_to_array(compilation_sandbox, ':');
ALTER TABLE user_test_results DROP COLUMN compilation_sandbox;
ALTER TABLE user_test_results ADD COLUMN evaluation_sandbox_paths VARCHAR[];
ALTER TABLE user_test_results ADD COLUMN evaluation_sandbox_digests VARCHAR[];
UPDATE user_test_results SET evaluation_sandbox_paths = string_to_array(evaluation_sandbox, ':');
ALTER TABLE user_test_results DROP COLUMN evaluation_sandbox;

-- https://github.com/cms-dev/cms/pull/1486
ALTER TABLE public.tasks ADD COLUMN allowed_languages varchar[];

-- https://github.com/ioi-isr/cms/pull/22
CREATE TABLE public.delay_requests (
    id integer NOT NULL,
    request_timestamp timestamp without time zone NOT NULL,
    requested_start_time timestamp without time zone NOT NULL,
    reason character varying NOT NULL,
    status character varying NOT NULL,
    processed_timestamp timestamp without time zone,
    participation_id integer NOT NULL,
    admin_id integer
);

CREATE SEQUENCE public.delay_requests_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.delay_requests_id_seq OWNED BY public.delay_requests.id;

ALTER TABLE ONLY public.delay_requests ALTER COLUMN id SET DEFAULT nextval('public.delay_requests_id_seq'::regclass);

ALTER TABLE ONLY public.delay_requests ADD CONSTRAINT delay_requests_pkey PRIMARY KEY (id);

CREATE INDEX ix_delay_requests_participation_id ON public.delay_requests USING btree (participation_id);

CREATE INDEX ix_delay_requests_admin_id ON public.delay_requests USING btree (admin_id);

ALTER TABLE ONLY public.delay_requests ADD CONSTRAINT delay_requests_participation_id_fkey FOREIGN KEY (participation_id) REFERENCES public.participations(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.delay_requests ADD CONSTRAINT delay_requests_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES public.admins(id) ON UPDATE CASCADE ON DELETE SET NULL;

-- https://github.com/ioi-isr/cms/pull/31
ALTER TABLE ONLY public.contests DROP CONSTRAINT contests_check1;
ALTER TABLE ONLY public.contests ADD CONSTRAINT contests_check1 CHECK (((per_user_time IS NULL) AND (stop <= analysis_start)) OR ((per_user_time IS NOT NULL) AND ((start + per_user_time) <= analysis_start)));

-- https://github.com/ioi-isr/cms/pull/35
ALTER TABLE public.participations ADD COLUMN starting_ip_addresses character varying;

-- https://github.com/ioi-isr/cms/pull/49
ALTER TABLE ONLY public.contests ADD CONSTRAINT contests_check4 CHECK (stop <= analysis_stop);

-- https://github.com/ioi-isr/cms/pull/37
CREATE TABLE public.statement_views (
    id integer NOT NULL,
    participation_id integer NOT NULL,
    task_id integer NOT NULL,
    "timestamp" timestamp without time zone NOT NULL
);

CREATE SEQUENCE public.statement_views_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.statement_views_id_seq OWNED BY public.statement_views.id;

ALTER TABLE ONLY public.statement_views ALTER COLUMN id SET DEFAULT nextval('public.statement_views_id_seq'::regclass);

ALTER TABLE ONLY public.statement_views ADD CONSTRAINT statement_views_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.statement_views ADD CONSTRAINT participation_task_unique UNIQUE (participation_id, task_id);

CREATE INDEX ix_statement_views_participation_id ON public.statement_views USING btree (participation_id);

CREATE INDEX ix_statement_views_task_id ON public.statement_views USING btree (task_id);

ALTER TABLE ONLY public.statement_views ADD CONSTRAINT statement_views_participation_id_fkey FOREIGN KEY (participation_id) REFERENCES public.participations(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.statement_views ADD CONSTRAINT statement_views_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON UPDATE CASCADE ON DELETE CASCADE;

-- https://github.com/ioi-isr/cms/pull/73
CREATE TABLE public.participation_task_scores (
    id integer NOT NULL,
    participation_id integer NOT NULL,
    task_id integer NOT NULL,
    score double precision NOT NULL,
    subtask_max_scores jsonb,
    max_tokened_score double precision NOT NULL,
    last_submission_score double precision,
    last_submission_timestamp timestamp without time zone,
    history_valid boolean NOT NULL,
    has_submissions boolean NOT NULL,
    last_update timestamp without time zone NOT NULL,
    created_at timestamp without time zone,
    invalidated_at timestamp without time zone
);

CREATE SEQUENCE public.participation_task_scores_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.participation_task_scores_id_seq OWNED BY public.participation_task_scores.id;

ALTER TABLE ONLY public.participation_task_scores ALTER COLUMN id SET DEFAULT nextval('public.participation_task_scores_id_seq'::regclass);

ALTER TABLE ONLY public.participation_task_scores ADD CONSTRAINT participation_task_scores_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.participation_task_scores ADD CONSTRAINT participation_task_scores_participation_id_task_id_key UNIQUE (participation_id, task_id);

CREATE INDEX ix_participation_task_scores_task_id ON public.participation_task_scores USING btree (task_id);

ALTER TABLE ONLY public.participation_task_scores ADD CONSTRAINT participation_task_scores_participation_id_fkey FOREIGN KEY (participation_id) REFERENCES public.participations(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.participation_task_scores ADD CONSTRAINT participation_task_scores_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON UPDATE CASCADE ON DELETE CASCADE;

-- Score history for user detail view
CREATE TABLE public.score_history (
    id integer NOT NULL,
    participation_id integer NOT NULL,
    task_id integer NOT NULL,
    "timestamp" timestamp without time zone NOT NULL,
    score double precision NOT NULL,
    submission_id integer NOT NULL
);

CREATE SEQUENCE public.score_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.score_history_id_seq OWNED BY public.score_history.id;

ALTER TABLE ONLY public.score_history ALTER COLUMN id SET DEFAULT nextval('public.score_history_id_seq'::regclass);

ALTER TABLE ONLY public.score_history ADD CONSTRAINT score_history_pkey PRIMARY KEY (id);

CREATE INDEX ix_score_history_task_id ON public.score_history USING btree (task_id);

CREATE INDEX ix_score_history_timestamp ON public.score_history USING btree ("timestamp");

CREATE INDEX ix_score_history_submission_id ON public.score_history USING btree (submission_id);

CREATE INDEX ix_score_history_participation_task ON public.score_history USING btree (participation_id, task_id);

ALTER TABLE ONLY public.score_history ADD CONSTRAINT score_history_participation_id_fkey FOREIGN KEY (participation_id) REFERENCES public.participations(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.score_history ADD CONSTRAINT score_history_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.score_history ADD CONSTRAINT score_history_submission_id_fkey FOREIGN KEY (submission_id) REFERENCES public.submissions(id) ON UPDATE CASCADE ON DELETE CASCADE;

-- https://github.com/ioi-isr/cms/pull/82 - Add rejection reason to delay requests
ALTER TABLE public.delay_requests ADD COLUMN rejection_reason character varying;

-- https://github.com/ioi-isr/cms/pull/79 - Add evaluation failure details
-- These fields store details about why the last evaluation attempt failed,
-- which helps admins diagnose issues with checkers or managers.
ALTER TABLE public.submission_results ADD COLUMN last_evaluation_failure_text VARCHAR[];
ALTER TABLE public.submission_results ADD COLUMN last_evaluation_failure_shard INTEGER;
ALTER TABLE public.submission_results ADD COLUMN last_evaluation_failure_sandbox_paths VARCHAR[];
ALTER TABLE public.submission_results ADD COLUMN last_evaluation_failure_sandbox_digests VARCHAR[];
-- JSONB field storing detailed failure information (exit_status, signal, time, memory, stdout, stderr)
ALTER TABLE public.submission_results ADD COLUMN last_evaluation_failure_details JSONB;

-- https://github.com/ioi-isr/cms/pull/79 - Add 'fail' value to evaluation_outcome enum
-- This allows marking submissions as permanently failed when evaluation fails
-- (e.g., checker/manager crash) after max retries, showing "Evaluation system error"
-- to contestants instead of leaving them stuck in "Evaluating..." state.
ALTER TYPE public.evaluation_outcome ADD VALUE 'fail';

-- Training programs table for organizing year-long training with multiple sessions
CREATE TABLE public.training_programs (
    id integer NOT NULL,
    name public.codename NOT NULL,
    description character varying NOT NULL,
    managing_contest_id integer NOT NULL
);

CREATE SEQUENCE public.training_programs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.training_programs_id_seq OWNED BY public.training_programs.id;

ALTER TABLE ONLY public.training_programs
    ALTER COLUMN id SET DEFAULT nextval('public.training_programs_id_seq'::regclass);

ALTER TABLE ONLY public.training_programs
    ADD CONSTRAINT training_programs_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.training_programs
    ADD CONSTRAINT training_programs_name_key UNIQUE (name);

ALTER TABLE ONLY public.training_programs
    ADD CONSTRAINT training_programs_managing_contest_id_fkey FOREIGN KEY (managing_contest_id) REFERENCES public.contests(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE UNIQUE INDEX ix_training_programs_managing_contest_id ON public.training_programs USING btree (managing_contest_id);

-- Students table for training program participation with tags
CREATE TABLE public.students (
    id integer NOT NULL,
    training_program_id integer NOT NULL,
    participation_id integer NOT NULL,
    student_tags character varying[] NOT NULL DEFAULT '{}'
);

CREATE SEQUENCE public.students_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.students_id_seq OWNED BY public.students.id;

ALTER TABLE ONLY public.students
    ALTER COLUMN id SET DEFAULT nextval('public.students_id_seq'::regclass);

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_training_program_id_fkey FOREIGN KEY (training_program_id) REFERENCES public.training_programs(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.students
    ADD CONSTRAINT students_participation_id_fkey FOREIGN KEY (participation_id) REFERENCES public.participations(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE INDEX ix_students_training_program_id ON public.students USING btree (training_program_id);

CREATE UNIQUE INDEX ix_students_participation_id ON public.students USING btree (participation_id);

ALTER TABLE ONLY public.students
    ALTER COLUMN student_tags DROP DEFAULT;

-- Training days table for organizing training days within a training program
CREATE TABLE public.training_days (
    id integer NOT NULL,
    training_program_id integer NOT NULL,
    contest_id integer NOT NULL,
    "position" integer
);

CREATE SEQUENCE public.training_days_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.training_days_id_seq OWNED BY public.training_days.id;

ALTER TABLE ONLY public.training_days
    ALTER COLUMN id SET DEFAULT nextval('public.training_days_id_seq'::regclass);

ALTER TABLE ONLY public.training_days
    ADD CONSTRAINT training_days_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.training_days
    ADD CONSTRAINT training_days_training_program_id_fkey FOREIGN KEY (training_program_id) REFERENCES public.training_programs(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.training_days
    ADD CONSTRAINT training_days_contest_id_fkey FOREIGN KEY (contest_id) REFERENCES public.contests(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE INDEX ix_training_days_training_program_id ON public.training_days USING btree (training_program_id);

CREATE UNIQUE INDEX ix_training_days_contest_id ON public.training_days USING btree (contest_id);

ALTER TABLE ONLY public.training_days
    ADD CONSTRAINT training_days_training_program_id_position_key UNIQUE (training_program_id, "position");

-- Add training_day_id and training_day_num to tasks table for training day-specific tasks
-- Tasks keep their contest_id (managing contest) and can also be assigned to a training day
ALTER TABLE public.tasks ADD COLUMN training_day_id integer;
ALTER TABLE public.tasks ADD COLUMN training_day_num integer;

ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_training_day_id_fkey FOREIGN KEY (training_day_id) REFERENCES public.training_days(id) ON UPDATE CASCADE ON DELETE SET NULL;

CREATE INDEX ix_tasks_training_day_id ON public.tasks USING btree (training_day_id);

-- Ensure a task's position is unique within a training day
ALTER TABLE ONLY public.tasks
    ADD CONSTRAINT tasks_training_day_id_training_day_num_key UNIQUE (training_day_id, training_day_num);

-- https://github.com/ioi-isr/cms/pull/83 - Add allow_delay_requests to contests
ALTER TABLE public.contests ADD COLUMN allow_delay_requests boolean NOT NULL DEFAULT true;
ALTER TABLE public.contests ALTER COLUMN allow_delay_requests DROP DEFAULT;

-- Set allow_delay_requests=false for existing training program managing contests
UPDATE public.contests SET allow_delay_requests = false WHERE id IN (
    SELECT managing_contest_id FROM public.training_programs
);

-- Add visible_to_tags column to tasks for controlling task visibility based on student tags
-- If empty, the task is visible to all students. If set, only students with at least one matching tag can see the task.
ALTER TABLE public.tasks ADD COLUMN visible_to_tags character varying[] NOT NULL DEFAULT '{}';
ALTER TABLE public.tasks ALTER COLUMN visible_to_tags DROP DEFAULT;

-- Training day groups table for per-group configuration of training days
-- Each group (identified by a student tag) can have its own start/end times and task display order
CREATE TABLE public.training_day_groups (
    id integer NOT NULL,
    training_day_id integer NOT NULL,
    tag_name character varying NOT NULL,
    start_time timestamp without time zone,
    end_time timestamp without time zone,
    alphabetical_task_order boolean NOT NULL DEFAULT false
);

CREATE SEQUENCE public.training_day_groups_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.training_day_groups_id_seq OWNED BY public.training_day_groups.id;

ALTER TABLE ONLY public.training_day_groups
    ALTER COLUMN id SET DEFAULT nextval('public.training_day_groups_id_seq'::regclass);

ALTER TABLE ONLY public.training_day_groups
    ADD CONSTRAINT training_day_groups_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.training_day_groups
    ADD CONSTRAINT training_day_groups_training_day_id_fkey FOREIGN KEY (training_day_id) REFERENCES public.training_days(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE INDEX ix_training_day_groups_training_day_id ON public.training_day_groups USING btree (training_day_id);

ALTER TABLE ONLY public.training_day_groups
    ADD CONSTRAINT training_day_groups_training_day_id_tag_name_key UNIQUE (training_day_id, tag_name);

ALTER TABLE ONLY public.training_day_groups
    ALTER COLUMN alphabetical_task_order DROP DEFAULT;

-- Add GIN index on student_tags for efficient querying
CREATE INDEX ix_students_student_tags_gin ON public.students USING gin (student_tags);

COMMIT;
