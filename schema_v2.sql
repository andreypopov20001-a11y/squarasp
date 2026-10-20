-- Выполнить ПОСЛЕ schema.sql (Supabase → SQL Editor → Run). Можно запускать повторно.
alter table profiles add column if not exists banned boolean not null default false;

create or replace function is_admin() returns boolean language sql stable security definer set search_path=public as
$$ select exists(select 1 from profiles where id=auth.uid() and role='admin') $$;
create or replace function is_banned() returns boolean language sql stable security definer set search_path=public as
$$ select coalesce((select banned from profiles where id=auth.uid()),false) $$;

-- защита колонок: роль меняет только админ, бан — модератор/админ (но не админа); чужое имя и группу не трогает никто
create or replace function lock_role() returns trigger language plpgsql as $$
begin
  if auth.uid() is not null then
    if not is_admin() then new.role := old.role; end if;
    if not is_mod() or old.role='admin' then new.banned := old.banned; end if;
    if auth.uid() <> old.id then
      new.name:=old.name; new.group_id:=old.group_id; new.stream_id:=old.stream_id; new.course:=old.course;
    end if;
  end if;
  return new;
end $$;

drop policy if exists prof_mod on profiles;
create policy prof_mod on profiles for update to authenticated using (is_mod()) with check (is_mod());

-- модераторы видят все комнаты
create or replace function can_room(r text) returns boolean language sql stable security definer set search_path=public as
$$ select r in ('univ','ideas','teachers','materials') or is_mod() or exists(select 1 from profiles p where p.id=auth.uid()
   and (r='stream:'||p.stream_id or r='group:'||p.group_id)) $$;

-- новые виды публикаций: объявления (только модераторы) и материалы
alter table posts drop constraint if exists posts_kind_check;
alter table posts add constraint posts_kind_check check (kind in ('chat','idea','tip','announce','material'));
drop policy if exists post_ins on posts;
create policy post_ins on posts for insert to authenticated with check (
  user_id=auth.uid() and not is_banned() and can_room(room) and (
    (kind='chat' and (room='univ' or room like 'stream:%' or room like 'group:%'))
    or (kind='idea' and room='ideas')
    or (kind='tip' and room='teachers' and target is not null)
    or (kind='material' and room='materials')
    or (kind='announce' and is_mod() and (room='univ' or room like 'stream:%' or room like 'group:%'))));

drop policy if exists rep_del on reports;
create policy rep_del on reports for delete to authenticated using (is_mod());

-- календарь: room is null = личное, иначе событие комнаты (создают модераторы)
create table if not exists events(
  id bigint generated always as identity primary key,
  room text check (char_length(room) <= 80),
  user_id uuid not null default auth.uid() references profiles(id) on delete cascade,
  title text not null check (char_length(title) between 1 and 200),
  due timestamptz not null,
  done boolean not null default false,
  created_at timestamptz not null default now());
create index if not exists events_due on events(due);
alter table events enable row level security;
drop policy if exists ev_read on events; drop policy if exists ev_ins on events; drop policy if exists ev_upd on events; drop policy if exists ev_del on events;
create policy ev_read on events for select to authenticated using ((room is null and user_id=auth.uid()) or (room is not null and can_room(room)));
create policy ev_ins on events for insert to authenticated with check (user_id=auth.uid() and not is_banned() and (room is null or (is_mod() and can_room(room))));
create policy ev_upd on events for update to authenticated using (user_id=auth.uid() and room is null) with check (user_id=auth.uid() and room is null);
create policy ev_del on events for delete to authenticated using (user_id=auth.uid() or (is_mod() and room is not null));

-- опросы; чужие голоса не видны, только итоги через функцию
create table if not exists polls(
  id bigint generated always as identity primary key,
  room text not null check (room='univ' or room like 'stream:%' or room like 'group:%'),
  question text not null check (char_length(question) between 3 and 200),
  options text[] not null check (array_length(options,1) between 2 and 6),
  user_id uuid not null default auth.uid() references profiles(id) on delete cascade,
  created_at timestamptz not null default now());
create table if not exists poll_votes(
  poll_id bigint references polls(id) on delete cascade,
  user_id uuid default auth.uid() references profiles(id) on delete cascade,
  choice int not null check (choice between 0 and 5),
  primary key(poll_id, user_id));
alter table polls enable row level security; alter table poll_votes enable row level security;
drop policy if exists pl_read on polls; drop policy if exists pl_ins on polls; drop policy if exists pl_del on polls;
drop policy if exists pv_read on poll_votes; drop policy if exists pv_ins on poll_votes; drop policy if exists pv_upd on poll_votes;
create policy pl_read on polls for select to authenticated using (can_room(room));
create policy pl_ins on polls for insert to authenticated with check (user_id=auth.uid() and not is_banned() and can_room(room) and (room<>'univ' or is_mod()));
create policy pl_del on polls for delete to authenticated using (user_id=auth.uid() or is_mod());
create policy pv_read on poll_votes for select to authenticated using (user_id=auth.uid());
create policy pv_ins on poll_votes for insert to authenticated with check (user_id=auth.uid() and not is_banned() and exists(select 1 from polls p where p.id=poll_id and can_room(p.room)));
create policy pv_upd on poll_votes for update to authenticated using (user_id=auth.uid()) with check (user_id=auth.uid() and not is_banned() and exists(select 1 from polls p where p.id=poll_id and can_room(p.room)));
create or replace function poll_results(ids bigint[]) returns table(poll_id bigint, choice int, n bigint) language sql stable security definer set search_path=public as
$$ select v.poll_id, v.choice, count(*) from poll_votes v join polls p on p.id=v.poll_id where v.poll_id=any(ids) and can_room(p.room) group by 1,2 $$;
revoke all on function poll_results(bigint[]) from public, anon; grant execute on function poll_results(bigint[]) to authenticated;

-- удаление своего аккаунта со всеми данными
create or replace function delete_my_account() returns void language sql security definer set search_path=public,auth as
$$ delete from auth.users where id=auth.uid() $$;
revoke all on function delete_my_account() from public, anon; grant execute on function delete_my_account() to authenticated;
