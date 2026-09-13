create table if not exists public.rooms (
    code text primary key,
    state_json jsonb not null,
    format_version integer not null,
    revision bigint not null default 0,
    created_at timestamptz not null default current_timestamp,
    updated_at timestamptz not null default current_timestamp
);

create table if not exists public.player_sessions (
    token_hash bytea primary key,
    room_code text not null references public.rooms(code) on delete cascade,
    player_id text not null,
    created_at timestamptz not null default current_timestamp,
    unique (room_code, player_id)
);

create index if not exists rooms_updated_at_idx
    on public.rooms (updated_at);

create index if not exists player_sessions_room_code_idx
    on public.player_sessions (room_code);

create table if not exists public.entry_rate_limits (
    bucket_key text primary key,
    window_started bigint not null,
    attempts integer not null
);

alter table public.rooms enable row level security;
alter table public.player_sessions enable row level security;
alter table public.entry_rate_limits enable row level security;

revoke all on table public.rooms from anon, authenticated;
revoke all on table public.player_sessions from anon, authenticated;
revoke all on table public.entry_rate_limits from anon, authenticated;

comment on table public.rooms is
    'Server-owned Psychology Roulette snapshots; never expose through the browser client.';
comment on table public.player_sessions is
    'Hashed bearer credentials owned exclusively by the FastAPI service.';
