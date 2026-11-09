ALTER TABLE public.students ADD COLUMN IF NOT EXISTS is_coach boolean NOT NULL DEFAULT false;

-- Add custom task labels independently of visibility tags.
ALTER TABLE public.tasks ADD COLUMN tags character varying[] NOT NULL DEFAULT '{}';
ALTER TABLE public.tasks ALTER COLUMN tags DROP DEFAULT;
