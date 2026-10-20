-- Выполнить один раз: Supabase → SQL Editor → вставить → Run.
create table if not exists profiles(
  id uuid primary key references auth.users on delete cascade,
  name text not null check (char_length(name) between 2 and 40),
  group_id text not null check (char_length(group_id) <= 40),
  stream_id text not null check (char_length(stream_id) <= 40),
  course int not null default 1 check (course between 1 and 6),
  role text not null default 'student' check (role in ('student','moderator','admin')),
  created_at timestamptz not null default now());

-- профиль создаётся на сервере из данных регистрации
create or replace function handle_new_user() returns trigger language plpgsql security definer set search_path=public as $$
begin
  insert into profiles(id,name,group_id,stream_id,course)
  values(new.id, left(coalesce(new.raw_user_meta_data->>'name','Студент'),40),
    left(coalesce(new.raw_user_meta_data->>'group_id',''),40), left(coalesce(new.raw_user_meta_data->>'stream_id',''),40),
    coalesce(nullif(new.raw_user_meta_data->>'course','')::int,1));
  return new;
end $$;
drop trigger if exists on_signup on auth.users;
create trigger on_signup after insert on auth.users for each row execute function handle_new_user();

-- роль нельзя поменять через сайт, только из SQL Editor
create or replace function lock_role() returns trigger language plpgsql as $$
begin
  if new.role <> old.role and auth.uid() is not null then new.role := old.role; end if;
  return new;
end $$;
drop trigger if exists lock_role_t on profiles;
create trigger lock_role_t before update on profiles for each row execute function lock_role();

-- kind: chat (сообщения), idea (пожелания к сайту), tip (советы о преподавателе)
create table if not exists posts(
  id bigint generated always as identity primary key,
  kind text not null check (kind in ('chat','idea','tip')),
  room text not null check (char_length(room) <= 80),   -- univ | stream:<id> | group:<id> | ideas | teachers
  target text check (char_length(target) <= 80),         -- имя преподавателя для tip
  parent_id bigint references posts(id) on delete set null,
  user_id uuid not null default auth.uid() references profiles(id) on delete cascade,
  body text not null check (char_length(body) between 1 and 1000),
  created_at timestamptz not null default now());
create index if not exists posts_feed on posts(kind, room, created_at desc);

create table if not exists likes(
  post_id bigint references posts(id) on delete cascade,
  user_id uuid default auth.uid() references profiles(id) on delete cascade,
  primary key(post_id, user_id));
create table if not exists reports(
  id bigint generated always as identity primary key,
  post_id bigint references posts(id) on delete cascade,
  user_id uuid default auth.uid() references profiles(id) on delete cascade,
  created_at timestamptz default now(), unique(post_id, user_id));

alter table profiles enable row level security; alter table posts enable row level security;
alter table likes enable row level security;    alter table reports enable row level security;

create or replace function is_mod() returns boolean language sql stable security definer set search_path=public as
$$ select exists(select 1 from profiles where id=auth.uid() and role in ('moderator','admin')) $$;
-- вход в комнату: университет — всем; поток и группа — только своим (по профилю)
create or replace function can_room(r text) returns boolean language sql stable security definer set search_path=public as
$$ select r in ('univ','ideas','teachers') or exists(select 1 from profiles p where p.id=auth.uid()
   and (r='stream:'||p.stream_id or r='group:'||p.group_id)) $$;

create policy prof_read on profiles for select to authenticated using (true);
create policy prof_upd  on profiles for update to authenticated using (id=auth.uid()) with check (id=auth.uid());
create policy prof_del  on profiles for delete to authenticated using (id=auth.uid());
create policy post_read on posts for select to authenticated using (can_room(room));
create policy post_ins  on posts for insert to authenticated with check (user_id=auth.uid() and can_room(room)
  and (kind='chat' or room in ('ideas','teachers')) and (kind<>'tip' or target is not null));
create policy post_del  on posts for delete to authenticated using (user_id=auth.uid() or is_mod());
create policy like_read on likes for select to authenticated using (true);
create policy like_ins  on likes for insert to authenticated with check (user_id=auth.uid());
create policy like_del  on likes for delete to authenticated using (user_id=auth.uid());
create policy rep_ins   on reports for insert to authenticated with check (user_id=auth.uid());
create policy rep_read  on reports for select to authenticated using (is_mod());

-- антиспам: не больше 15 публикаций в минуту с аккаунта
create or replace function rate_limit() returns trigger language plpgsql as $$
begin
  if (select count(*) from posts where user_id=new.user_id and created_at > now()-interval '1 minute') >= 15
  then raise exception 'Слишком часто, подожди минуту'; end if;
  return new;
end $$;
drop trigger if exists rate_t on posts;
create trigger rate_t before insert on posts for each row execute function rate_limit();

alter publication supabase_realtime add table posts;

-- Первый админ (безопасно, только из SQL Editor, после своей регистрации):
-- update profiles set role='admin' where id=(select id from auth.users where email='ТВОЯ@ПОЧТА');
