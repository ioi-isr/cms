begin;

alter table participations add starting_ip inet;

rollback; -- change this to: commit;
