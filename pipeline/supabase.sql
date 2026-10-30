-- Sportfinder – Datenbank für "Mitspielen" (Supabase, kostenloser Plan reicht).
-- Einmal im Supabase-SQL-Editor ausführen. Danach in site/index.html bei
--   const SB={url:"",key:""};
-- die Projekt-URL und den öffentlichen "anon"-Schlüssel eintragen.

create extension if not exists pgcrypto;

create table if not exists checkins (
  id uuid primary key default gen_random_uuid(),
  place_id text not null, place_name text, lat double precision not null, lon double precision not null,
  sport text, day date not null, from_h int not null check (from_h between 0 and 24),
  to_h int not null check (to_h between 0 and 24), level text,
  nick text not null check (char_length(nick) <= 20), device text not null,
  created_at timestamptz not null default now()
);
create table if not exists rounds (
  id uuid primary key default gen_random_uuid(),
  place_id text not null, place_name text, lat double precision not null, lon double precision not null,
  sport text, weekday int not null check (weekday between 0 and 6), time text not null, level text,
  spots int check (spots between 2 and 40),
  link text check (link is null or link ~ '^https://chat\.whatsapp\.com/[A-Za-z0-9]+/?$'),
  note text check (char_length(note) <= 80),
  nick text not null check (char_length(nick) <= 20), device text not null,
  created_at timestamptz not null default now()
);
create table if not exists joins (
  round_id uuid not null references rounds(id) on delete cascade,
  device text not null, nick text, created_at timestamptz not null default now(),
  primary key (round_id, device)
);
create table if not exists tips (
  id uuid primary key default gen_random_uuid(),
  place_id text not null, place_name text, lat double precision not null, lon double precision not null,
  sport text, text text not null check (char_length(text) <= 140),
  nick text not null check (char_length(nick) <= 20), device text not null,
  created_at timestamptz not null default now()
);
create index if not exists checkins_geo on checkins (day, lat, lon);
create index if not exists rounds_geo on rounds (lat, lon);
create index if not exists tips_geo on tips (lat, lon);

-- Zugriff ohne Login: lesen und eintragen für alle, austragen nur bei "joins".
alter table checkins enable row level security;
alter table rounds   enable row level security;
alter table joins    enable row level security;
alter table tips     enable row level security;
create policy "lesen"     on checkins for select using (true);
create policy "eintragen" on checkins for insert with check (true);
create policy "lesen"     on rounds   for select using (true);
create policy "eintragen" on rounds   for insert with check (true);
create policy "lesen"     on joins    for select using (true);
create policy "eintragen" on joins    for insert with check (true);
create policy "austragen" on joins    for delete using (true);
create policy "lesen"     on tips     for select using (true);
create policy "eintragen" on tips     for insert with check (true);

-- Alte "heute da"-Einträge aufräumen (optional, z. B. per Supabase-Cron täglich):
-- delete from checkins where day < current_date - 1;
