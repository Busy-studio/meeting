-- v1.2 additive migration: the managed internal-staff roster.
-- Apply this once in the Supabase SQL Editor before enabling the new UI.
-- Existing meetings/revisions and the Excel/PDF template are not modified.

create table if not exists meeting.internal_staff (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(btrim(name)) between 1 and 100),
  title text not null default '' check (char_length(title) <= 100),
  is_active boolean not null default true,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table meeting.internal_staff enable row level security;
revoke all on meeting.internal_staff from public, anon, authenticated;
grant select, insert, update on meeting.internal_staff to service_role;

-- Initial roster is the 23 ordered rows of manager_order(1).xlsx.
-- Guard the seed against accidental re-application after users have edited it.
insert into meeting.internal_staff (name, title, sort_order)
select seed.name, seed.title, seed.sort_order
from (values
  (1,'김성근','실장'), (2,'박성호','팀장'), (3,'차민정','과장'),
  (4,'최미현','과장'), (5,'신성현','대리'), (6,'장윤경','팀장'),
  (7,'한진호','과장'), (8,'전연희','사원'), (9,'이소혜','사원'),
  (10,'홍석빈','팀장'), (11,'황기수','사원'), (12,'배민화','사원'),
  (13,'윤재철','팀장'), (14,'김정환','차장'), (15,'최정식','과장'),
  (16,'이강민','과장'), (17,'구민예','과장'), (18,'배지현','대리'),
  (19,'김남용','팀장'), (20,'김나현','과장'), (21,'박수현','과장'),
  (22,'송용호','과장'), (23,'최은지','사원')
) as seed(sort_order,name,title)
where not exists (select 1 from meeting.internal_staff)
order by seed.sort_order;

create or replace function public.meeting_app_list_internal_staff()
returns jsonb language sql security definer
set search_path=pg_catalog,public,meeting as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', s.id,
    'name', s.name,
    'title', s.title,
    'is_active', s.is_active,
    'sort_order', s.sort_order
  ) order by s.sort_order, s.created_at, s.id), '[]'::jsonb)
  from meeting.internal_staff s;
$$;

create or replace function public.meeting_app_save_internal_staff(payload jsonb)
returns jsonb language plpgsql security definer
set search_path=pg_catalog,public,meeting as $$
declare
  item jsonb;
  v_id uuid;
  v_name text;
  v_title text;
  v_active boolean;
  v_sort_order integer;
begin
  if jsonb_typeof(payload) is distinct from 'array' or jsonb_array_length(payload) > 200 then
    raise exception '내부 인원 저장 형식이 올바르지 않습니다.' using errcode='22023';
  end if;

  select coalesce(max(sort_order),0) into v_sort_order from meeting.internal_staff;
  for item in select value from jsonb_array_elements(payload) loop
    v_name := btrim(coalesce(item->>'name',''));
    v_title := btrim(coalesce(item->>'title',''));
    if char_length(v_name) not between 1 and 100 or char_length(v_title) > 100 then
      raise exception '직원 이름(1~100자)과 직급(100자 이하)을 확인하세요.' using errcode='22023';
    end if;
    v_active := coalesce((item->>'is_active')::boolean,true);
    v_id := nullif(item->>'id','')::uuid;

    if v_id is null then
      v_sort_order := v_sort_order + 1;
      insert into meeting.internal_staff (name,title,is_active,sort_order)
      values (v_name,v_title,v_active,v_sort_order);
    else
      update meeting.internal_staff
      set name=v_name, title=v_title, is_active=v_active, updated_at=now()
      where id=v_id;
      if not found then
        raise exception '존재하지 않는 내부 인원 ID입니다.' using errcode='22023';
      end if;
    end if;
  end loop;
  return public.meeting_app_list_internal_staff();
end;
$$;

revoke all on function public.meeting_app_list_internal_staff() from public, anon, authenticated;
revoke all on function public.meeting_app_save_internal_staff(jsonb) from public, anon, authenticated;
grant execute on function public.meeting_app_list_internal_staff() to service_role;
grant execute on function public.meeting_app_save_internal_staff(jsonb) to service_role;
