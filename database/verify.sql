select 'rooms' as table_name, count(*) from public.rooms union all
select 'player_sessions', count(*) from public.player_sessions union all
select 'entry_rate_limits', count(*) from public.entry_rate_limits;
select table_name, column_name, data_type
from information_schema.columns
where table_schema = 'public' and table_name in ('rooms','player_sessions','entry_rate_limits')
order by table_name, ordinal_position;
