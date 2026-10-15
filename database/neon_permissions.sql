-- Create psychology_roulette_runtime using SQL with LOGIN, NOINHERIT, and all
-- elevated role attributes disabled before applying these grants. Set its
-- password through Neon role management, never in a SQL file or shell command.
revoke all on schema public from public;
grant usage on schema public to psychology_roulette_runtime;
revoke all on all tables in schema public from psychology_roulette_runtime;
grant select, insert, update, delete on table public.rooms, public.player_sessions, public.entry_rate_limits to psychology_roulette_runtime;
alter default privileges in schema public revoke all on tables from public;
alter default privileges in schema public revoke all on tables from psychology_roulette_runtime;
