-- Supabase schema for Plovane user accounts.
--
-- Mirrors B05's existing sqlite schema (backend/cloud_server/identity.py)
-- so cloud_server can swap sqlite -> Postgres with a thin driver change.
-- Password hashing stays PBKDF2-HMAC-SHA256 in the backend; the DB only
-- stores the encoded string, kept opaque so Argon2id can be adopted later
-- without a migration.
--
-- Apply with the Supabase SQL editor, or:
--     psql "$SUPABASE_DB_URL" -f supabase/schema.sql
--
-- Row-level security is enabled so anonymous clients cannot read the
-- password hash. The backend connects with the service_role key (RLS
-- bypass); end users only ever reach the API through B05.

create extension if not exists "pgcrypto";

-- --------------------------------------------------------------------
-- plovane_user : one row per registered account
-- --------------------------------------------------------------------
create table if not exists public.plovane_user (
    user_id       text primary key default ('usr_' || replace(gen_random_uuid()::text, '-', '')),
    username      text not null unique,
    email         text not null unique,
    password_hash text not null,
    machine_id    text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create index if not exists plovane_user_email_lower
    on public.plovane_user (lower(email));

create index if not exists plovane_user_username_lower
    on public.plovane_user (lower(username));

-- --------------------------------------------------------------------
-- plovane_session : one row per issued session token
-- --------------------------------------------------------------------
create table if not exists public.plovane_session (
    session_token text primary key,
    user_id       text not null
                  references public.plovane_user(user_id) on delete cascade,
    machine_id    text,
    expires_at    timestamptz not null,
    created_at    timestamptz not null default now()
);

create index if not exists plovane_session_user
    on public.plovane_session (user_id);

create index if not exists plovane_session_expires
    on public.plovane_session (expires_at);

-- --------------------------------------------------------------------
-- updated_at trigger
-- --------------------------------------------------------------------
create or replace function public.plovane_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

drop trigger if exists plovane_user_updated on public.plovane_user;
create trigger plovane_user_updated
    before update on public.plovane_user
    for each row execute function public.plovane_touch_updated_at();

-- --------------------------------------------------------------------
-- Row-level security.
-- The backend uses the service_role key, which bypasses RLS. The anon
-- key must never see password_hash. Policies here lock everything down
-- by default; add read policies only once a user-facing read path is
-- designed (handbook OD-4).
-- --------------------------------------------------------------------
alter table public.plovane_user     enable row level security;
alter table public.plovane_session  enable row level security;

drop policy if exists plovane_user_deny_anon on public.plovane_user;
create policy plovane_user_deny_anon
    on public.plovane_user
    for all
    to anon, authenticated
    using (false)
    with check (false);

drop policy if exists plovane_session_deny_anon on public.plovane_session;
create policy plovane_session_deny_anon
    on public.plovane_session
    for all
    to anon, authenticated
    using (false)
    with check (false);

-- --------------------------------------------------------------------
-- Helper: delete expired sessions. Call from a scheduled job.
-- --------------------------------------------------------------------
create or replace function public.plovane_purge_expired_sessions()
returns integer
language plpgsql
as $$
declare
    removed integer;
begin
    delete from public.plovane_session where expires_at <= now();
    get diagnostics removed = row_count;
    return removed;
end;
$$;
