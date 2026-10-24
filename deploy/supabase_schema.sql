-- Socialscraper: Tabellen für die gehostete Datenbank (Supabase / PostgreSQL mit PostgREST)
-- Einmal im Supabase-Dashboard unter "SQL Editor" ausführen. Mehrfaches Ausführen schadet nicht.
--
-- Wichtig zu Schlüsseln:
--   * Der Schlüssel, mit dem Socialscraper SENDET (service_role oder ein eigener Schreib-Schlüssel), ist ein GEHEIMNIS.
--     Er gehört nur nach data/integration.json auf dem Rechner/Server von Socialscraper – niemals in PULSE-Code,
--     niemals in eine Webseite.
--   * PULSE im Browser bekommt nur den LESE-Schlüssel (anon), und die Regeln unten erlauben ihm nur Lesen.
--   * Konten (accounts) und Cookies werden bewusst NICHT synchronisiert.
--
-- Zeiten sind UTC (timestamptz); created_at der Videos/Kommentare ist der Text der Plattform (kann ungenau sein).
-- Gruppen werden über ihren NAMEN verbunden (die lokalen Zahlen-IDs sind je Rechner verschieden).

create table if not exists public.videos (
    uid            text primary key,              -- plattform:video_id
    platform       text not null,                 -- tiktok | instagram | youtube | reddit | x
    video_id       text not null,
    url            text,
    author         text,
    author_id      text,
    caption        text,
    created_at     text,
    duration_s     double precision,
    view_count     bigint,
    like_count     bigint,
    comment_count  bigint,
    share_count    bigint,
    save_count     bigint,
    hashtags       jsonb,
    mentions       jsonb,
    music          text,
    language       text,
    thumbnail      text,
    raw            jsonb,
    first_seen_at  timestamptz,
    fetched_at     timestamptz not null default now()
);
create index if not exists idx_videos_fetched   on public.videos (fetched_at, uid);
create index if not exists idx_videos_platform  on public.videos (platform, author);

create table if not exists public.comments (
    uid             text primary key,             -- plattform:comment_id
    platform        text not null,                -- tiktok | instagram | youtube | reddit | x (bei X sind Antworten die Kommentare)
    video_id        text not null,
    comment_id      text not null,
    text            text,
    author          text,
    author_id       text,
    created_at      text,
    like_count      bigint,
    reply_count     bigint,
    parent_id       text,
    is_author_reply boolean default false,
    language        text,
    raw             jsonb,
    first_seen_at   timestamptz,
    fetched_at      timestamptz not null default now()
);
create index if not exists idx_comments_fetched on public.comments (fetched_at, uid);
create index if not exists idx_comments_video   on public.comments (platform, video_id);
create index if not exists idx_comments_seen    on public.comments (first_seen_at);

create table if not exists public.video_snapshots (
    video_uid      text not null,
    captured_at    timestamptz not null,
    view_count     bigint,
    like_count     bigint,
    comment_count  bigint,
    share_count    bigint,
    primary key (video_uid, captured_at)
);
create index if not exists idx_snapshots_captured on public.video_snapshots (captured_at, video_uid);

create table if not exists public.groups (
    name        text primary key,
    color       text,
    note        text,
    created_at  timestamptz
);

create table if not exists public.group_videos (
    group_name  text not null,
    video_uid   text not null,
    added_at    timestamptz not null default now(),
    primary key (group_name, video_uid)
);
create index if not exists idx_group_videos_added on public.group_videos (added_at, group_name, video_uid);
create index if not exists idx_group_videos_video on public.group_videos (video_uid);

-- KI-Einschätzung der Stimmung je Kommentar (eine Schätzung eines Sprachmodells, nicht gemessen).
create table if not exists public.comment_sentiment (
    comment_uid     text primary key,             -- wie comments.uid (plattform:comment_id)
    label           text not null check (label in ('positiv', 'neutral', 'negativ')),
    score           double precision check (score is null or (score >= -1 and score <= 1)),
    model           text,
    prompt_version  integer,
    analyzed_at     timestamptz not null default now(),
    text_hash       text                          -- Hash des eingestuften Textes (ändert sich der Text, gilt die Einstufung nicht mehr)
);
create index if not exists idx_comment_sentiment_analyzed on public.comment_sentiment (analyzed_at, comment_uid);

-- Personen (Ersteller von Videos, Kommentarschreiber) in Gruppen. author_key = Autoren-Kennung, sonst Name
-- kleingeschrieben ohne @. Gruppen sind über ihren NAMEN verbunden.
create table if not exists public.group_people (
    group_name    text not null,
    role          text not null check (role in ('creator', 'commenter')),
    platform      text not null,
    author_key    text not null,
    display_name  text,
    added_at      timestamptz not null default now(),
    primary key (group_name, role, platform, author_key)
);
create index if not exists idx_group_people_added on public.group_people (added_at);
create index if not exists idx_group_people_person on public.group_people (platform, author_key);

-- ---------------------------------------------------------------------------------------------
-- Zugriffsregeln (Row Level Security)
-- Der service_role-Schlüssel umgeht diese Regeln und darf alles (deshalb: Geheimnis!).
-- Der anon-Schlüssel (für PULSE im Browser) darf nur lesen. Wer die Daten nicht öffentlich lesbar
-- haben will, löscht die "anon"-Leseregeln unten und gibt PULSE stattdessen angemeldete Nutzer
-- (Rolle "authenticated") bzw. lässt PULSE nur über ein eigenes Backend lesen.
-- ---------------------------------------------------------------------------------------------
alter table public.videos          enable row level security;
alter table public.comments        enable row level security;
alter table public.video_snapshots enable row level security;
alter table public.groups          enable row level security;
alter table public.group_videos    enable row level security;
alter table public.comment_sentiment enable row level security;
alter table public.group_people    enable row level security;

do $$
declare t text;
begin
    foreach t in array array['videos','comments','video_snapshots','groups','group_videos','comment_sentiment','group_people'] loop
        if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = t and policyname = 'lesen_fuer_pulse') then
            execute format('create policy lesen_fuer_pulse on public.%I for select to anon, authenticated using (true)', t);
        end if;
    end loop;
end $$;

-- Schreibrechte für anon/authenticated gibt es absichtlich keine: ohne Regel ist Schreiben verboten.
