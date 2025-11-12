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

-- https://github.com/ioi-isr/cms/pull/32
CREATE TABLE public.model_solutions (
    "timestamp" timestamp without time zone NOT NULL,
    dataset_id integer NOT NULL,
    description character varying NOT NULL,
    expected_score_max double precision NOT NULL,
    expected_score_min double precision NOT NULL,
    id integer NOT NULL,
    language character varying,
    score_in_range boolean
);

CREATE SEQUENCE public.model_solutions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.model_solutions_id_seq OWNED BY public.model_solutions.id;

ALTER TABLE ONLY public.model_solutions ALTER COLUMN id SET DEFAULT nextval('public.model_solutions_id_seq'::regclass);

ALTER TABLE ONLY public.model_solutions ADD CONSTRAINT model_solutions_pkey PRIMARY KEY (id);

CREATE INDEX ix_model_solutions_dataset_id ON public.model_solutions USING btree (dataset_id);

ALTER TABLE ONLY public.model_solutions ADD CONSTRAINT model_solutions_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE TABLE public.model_solution_files (
    digest public.digest NOT NULL,
    filename public.filename_schema NOT NULL,
    id integer NOT NULL,
    model_solution_id integer NOT NULL
);

CREATE SEQUENCE public.model_solution_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.model_solution_files_id_seq OWNED BY public.model_solution_files.id;

ALTER TABLE ONLY public.model_solution_files ALTER COLUMN id SET DEFAULT nextval('public.model_solution_files_id_seq'::regclass);

ALTER TABLE ONLY public.model_solution_files ADD CONSTRAINT model_solution_files_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.model_solution_files ADD CONSTRAINT model_solution_files_model_solution_id_filename_key UNIQUE (model_solution_id, filename);

CREATE INDEX ix_model_solution_files_model_solution_id ON public.model_solution_files USING btree (model_solution_id);

ALTER TABLE ONLY public.model_solution_files ADD CONSTRAINT model_solution_files_model_solution_id_fkey FOREIGN KEY (model_solution_id) REFERENCES public.model_solutions(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE TABLE public.model_solution_results (
    compilation_memory bigint,
    compilation_outcome character varying,
    compilation_sandbox_digests character varying[],
    compilation_sandbox_paths character varying[],
    compilation_shard integer,
    compilation_stderr character varying,
    compilation_stdout character varying,
    compilation_text character varying[] NOT NULL,
    compilation_time double precision,
    compilation_tries integer NOT NULL,
    compilation_wall_clock_time double precision,
    dataset_id integer NOT NULL,
    evaluation_outcome character varying,
    evaluation_tries integer NOT NULL,
    model_solution_id integer NOT NULL,
    public_score double precision,
    public_score_details jsonb,
    ranking_score_details character varying[],
    score double precision,
    score_details jsonb,
    scored_at timestamp without time zone
);

ALTER TABLE ONLY public.model_solution_results ADD CONSTRAINT model_solution_results_pkey PRIMARY KEY (model_solution_id, dataset_id);

ALTER TABLE ONLY public.model_solution_results ADD CONSTRAINT model_solution_results_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.model_solution_results ADD CONSTRAINT model_solution_results_model_solution_id_fkey FOREIGN KEY (model_solution_id) REFERENCES public.model_solutions(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE TABLE public.model_solution_executables (
    dataset_id integer NOT NULL,
    digest public.digest NOT NULL,
    filename public.filename NOT NULL,
    id integer NOT NULL,
    model_solution_id integer NOT NULL
);

CREATE SEQUENCE public.model_solution_executables_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.model_solution_executables_id_seq OWNED BY public.model_solution_executables.id;

ALTER TABLE ONLY public.model_solution_executables ALTER COLUMN id SET DEFAULT nextval('public.model_solution_executables_id_seq'::regclass);

ALTER TABLE ONLY public.model_solution_executables ADD CONSTRAINT model_solution_executables_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.model_solution_executables ADD CONSTRAINT model_solution_executables_model_solution_id_dataset_id_fil_key UNIQUE (model_solution_id, dataset_id, filename);

CREATE INDEX ix_model_solution_executables_dataset_id ON public.model_solution_executables USING btree (dataset_id);

CREATE INDEX ix_model_solution_executables_model_solution_id ON public.model_solution_executables USING btree (model_solution_id);

ALTER TABLE ONLY public.model_solution_executables ADD CONSTRAINT model_solution_executables_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.model_solution_executables ADD CONSTRAINT model_solution_executables_model_solution_id_dataset_id_fkey FOREIGN KEY (model_solution_id, dataset_id) REFERENCES public.model_solution_results(model_solution_id, dataset_id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.model_solution_executables ADD CONSTRAINT model_solution_executables_model_solution_id_fkey FOREIGN KEY (model_solution_id) REFERENCES public.model_solutions(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE TABLE public.model_solution_evaluations (
    dataset_id integer NOT NULL,
    evaluation_sandbox_digests character varying[],
    evaluation_sandbox_paths character varying[],
    evaluation_shard integer,
    execution_memory bigint,
    execution_time double precision,
    execution_wall_clock_time double precision,
    id integer NOT NULL,
    model_solution_id integer NOT NULL,
    outcome character varying,
    testcase_id integer NOT NULL,
    text character varying[] NOT NULL
);

CREATE SEQUENCE public.model_solution_evaluations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.model_solution_evaluations_id_seq OWNED BY public.model_solution_evaluations.id;

ALTER TABLE ONLY public.model_solution_evaluations ALTER COLUMN id SET DEFAULT nextval('public.model_solution_evaluations_id_seq'::regclass);

ALTER TABLE ONLY public.model_solution_evaluations ADD CONSTRAINT model_solution_evaluations_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.model_solution_evaluations ADD CONSTRAINT model_solution_evaluations_model_solution_id_dataset_id_tes_key UNIQUE (model_solution_id, dataset_id, testcase_id);

CREATE INDEX ix_model_solution_evaluations_dataset_id ON public.model_solution_evaluations USING btree (dataset_id);

CREATE INDEX ix_model_solution_evaluations_model_solution_id ON public.model_solution_evaluations USING btree (model_solution_id);

CREATE INDEX ix_model_solution_evaluations_testcase_id ON public.model_solution_evaluations USING btree (testcase_id);

ALTER TABLE ONLY public.model_solution_evaluations ADD CONSTRAINT model_solution_evaluations_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES public.datasets(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.model_solution_evaluations ADD CONSTRAINT model_solution_evaluations_model_solution_id_dataset_id_fkey FOREIGN KEY (model_solution_id, dataset_id) REFERENCES public.model_solution_results(model_solution_id, dataset_id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.model_solution_evaluations ADD CONSTRAINT model_solution_evaluations_model_solution_id_fkey FOREIGN KEY (model_solution_id) REFERENCES public.model_solutions(id) ON UPDATE CASCADE ON DELETE CASCADE;

ALTER TABLE ONLY public.model_solution_evaluations ADD CONSTRAINT model_solution_evaluations_testcase_id_fkey FOREIGN KEY (testcase_id) REFERENCES public.testcases(id) ON UPDATE CASCADE ON DELETE CASCADE;

COMMIT;
