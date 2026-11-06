-- Add id_number and grade fields to users
ALTER TABLE public.users ADD COLUMN id_number character varying;
ALTER TABLE public.users ADD COLUMN grade integer;

-- Add preferred_theme column to admins for persisting UI theme choice
ALTER TABLE public.admins ADD COLUMN preferred_theme character varying;
ALTER TABLE ONLY public.admins
    ADD CONSTRAINT admins_preferred_theme_check
    CHECK (preferred_theme IN ('original', 'granny-smith', 'pink-lady', 'arkansas-black', 'golden', 'mcintosh'));

ALTER TABLE public.archived_attendances ADD COLUMN declared_bad_day boolean NOT NULL DEFAULT false
