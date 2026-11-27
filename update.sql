-- Coach-only task materials (editorial slides, written solutions).
CREATE TABLE public.editorials (
    id integer NOT NULL,
    task_id integer NOT NULL,
    filename character varying NOT NULL,
    digest public.digest NOT NULL
);

CREATE SEQUENCE public.editorials_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE public.editorials_id_seq OWNED BY public.editorials.id;

ALTER TABLE ONLY public.editorials
    ALTER COLUMN id SET DEFAULT nextval('public.editorials_id_seq'::regclass);

ALTER TABLE ONLY public.editorials
    ADD CONSTRAINT editorials_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.editorials
    ADD CONSTRAINT editorials_task_id_filename_key UNIQUE (task_id, filename);

ALTER TABLE ONLY public.editorials
    ADD CONSTRAINT editorials_task_id_fkey FOREIGN KEY (task_id) REFERENCES public.tasks(id) ON UPDATE CASCADE ON DELETE CASCADE;

CREATE INDEX ix_editorials_task_id ON public.editorials USING btree (task_id);
