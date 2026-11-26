-- Add coach materials to an existing CMS database.
BEGIN;

CREATE TABLE IF NOT EXISTS public.task_coach_materials (
    id serial PRIMARY KEY,
    task_id integer NOT NULL REFERENCES public.tasks(id) ON UPDATE CASCADE ON DELETE CASCADE,
    kind character varying NOT NULL,
    filename character varying(255) NOT NULL,
    digest public.digest NOT NULL,
    UNIQUE (task_id, kind, filename),
    CONSTRAINT task_coach_materials_kind_check CHECK (kind IN ('editorial', 'written_solution'))
);

COMMIT;
