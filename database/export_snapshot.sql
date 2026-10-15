-- Run with psql and redirect stdout. The result is one portable, secret-bearing JSON artifact.
select jsonb_build_object(
  'format', 'psychology-roulette.postgres-snapshot.v1',
  'exported_at', to_char(current_timestamp at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'rooms', coalesce((select jsonb_agg(jsonb_build_object('code', code, 'state_json', state_json,
      'format_version', format_version, 'revision', revision::text,
      'created_at', to_char(created_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
      'updated_at', to_char(updated_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) order by code) from public.rooms), '[]'::jsonb),
  'player_sessions', coalesce((select jsonb_agg(jsonb_build_object('token_hash', encode(token_hash,'hex'), 'room_code', room_code,
      'player_id', player_id, 'created_at', to_char(created_at at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) order by room_code, player_id) from public.player_sessions), '[]'::jsonb),
  'entry_rate_limits', coalesce((select jsonb_agg(jsonb_build_object('bucket_key', bucket_key, 'window_started', window_started::text, 'attempts', attempts) order by bucket_key) from public.entry_rate_limits), '[]'::jsonb)
);
