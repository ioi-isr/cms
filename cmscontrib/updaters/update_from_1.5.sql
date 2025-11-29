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

COMMIT;
