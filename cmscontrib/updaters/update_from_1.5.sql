

-- Add contest_folders table and folder_id on contests
CREATE TABLE public.contest_folders (
    id integer NOT NULL,
    name public.codename NOT NULL,
    description character varying NOT NULL,
    parent_id integer
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
    ADD CONSTRAINT contest_folders_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.contest_folders(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE INDEX ix_contest_folders_parent_id ON public.contest_folders USING btree (parent_id);

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

COMMIT;
