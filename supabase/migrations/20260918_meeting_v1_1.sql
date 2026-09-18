-- 회의 뭐했니? v1.1
-- Supabase business metadata + server-only RPCs.

create table if not exists meeting.businesses (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  research_project_name text,
  project_number text,
  support_organization text,
  total_research_period text,
  round_no integer,
  round_research_period text,
  principal_affiliation text,
  principal_title text,
  principal_name text,
  is_default boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table meeting.businesses enable row level security;
revoke all on meeting.businesses from public, anon, authenticated;
grant all on meeting.businesses to service_role;

insert into meeting.businesses (
  name, research_project_name, project_number, support_organization,
  total_research_period, round_no, round_research_period,
  principal_affiliation, principal_title, principal_name, is_default
) values
('2024년도 대학기술경영촉진사업(TLO혁신형)','2024년도 대학기술경영촉진사업(TLO혁신형)','RS-2024-00459168','과학기술사업화진흥원','2024-07-01 ~ 2026-12-31',3,'2026-01-01 ~ 2026-12-31','기술지주','실장','김성근',true),
('전략기술 딥테크 창업 촉진','전략기술 딥테크 창업 촉진','2024-BS-RD-0001-01','연구개발특구진흥재단','2024-04-01 ~ 2026-12-31',3,'2026-01-01 ~ 2026-12-31','기술지주','실장','김성근',true),
('기술경영촉진 컴퍼니빌더 지원형','기술경영촉진 컴퍼니빌더 지원형','RS-2026-25532485','과학기술사업화진흥원','2026-04-01 ~ 2030-12-31',1,'2026-04-01 ~ 2026-12-31','기술지주','실장','김성근',true),
('원천기술 상용화 플랫폼 구축사업','원천기술 상용화 플랫폼 구축사업',null,'울산테크노파크','2026-05-26 ~ 2026-12-31',1,'2026-05-26 ~ 2026-12-31','기술지주','실장','김성근',true)
on conflict (name) do update set
  research_project_name=excluded.research_project_name,
  project_number=excluded.project_number,
  support_organization=excluded.support_organization,
  total_research_period=excluded.total_research_period,
  round_no=excluded.round_no,
  round_research_period=excluded.round_research_period,
  principal_affiliation=excluded.principal_affiliation,
  principal_title=excluded.principal_title,
  principal_name=excluded.principal_name,
  is_default=excluded.is_default,
  updated_at=now();

alter table meeting.meetings
  add column if not exists business_id uuid references meeting.businesses(id) on delete set null,
  add column if not exists meeting_time text,
  add column if not exists author_name text,
  add column if not exists card_merchant text,
  add column if not exists business_snapshot jsonb;

create sequence if not exists meeting.meetings_id_seq;
select setval('meeting.meetings_id_seq', greatest((select coalesce(max(id),0) from meeting.meetings),1), true);
alter table meeting.meetings alter column id set default nextval('meeting.meetings_id_seq');
alter sequence meeting.meetings_id_seq owned by meeting.meetings.id;
grant usage, select on sequence meeting.meetings_id_seq to service_role;

create or replace function public.meeting_app_load_meetings()
returns jsonb language sql security definer
set search_path=pg_catalog,public,meeting as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'id',m.id,
    'meeting_date',coalesce(m.meeting_date,''),
    'participants_raw',coalesce(nullif(m.participants_final,''),m.participants_raw,''),
    'meeting_purpose_raw',coalesce(nullif(m.meeting_purpose_final,''),m.meeting_purpose_raw,''),
    'meeting_content_raw',coalesce(nullif(m.meeting_content_final,''),m.meeting_content_raw,''),
    'discussion_raw',coalesce(nullif(m.discussion_final,''),m.discussion_raw,''),
    'followup_raw',coalesce(nullif(m.followup_final,''),m.followup_raw,''),
    'project_name',coalesce(b.name,m.project_name,''),
    'research_project_name',coalesce(b.research_project_name,m.research_project_name,''),
    'affiliation',coalesce(m.affiliation,'')
  ) order by m.id),'[]'::jsonb)
  from meeting.meetings m
  left join meeting.businesses b on b.id=m.business_id
  where m.status='ok' and m.is_active=true
    and coalesce(nullif(m.meeting_purpose_final,''),m.meeting_purpose_raw,'')<>'';
$$;

create or replace function public.meeting_app_load_participants()
returns jsonb language sql security definer
set search_path=pg_catalog,public,meeting as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'meeting_id',mp.meeting_id,'participant_id',mp.participant_id,'name',p.name,
    'organization',coalesce(o.name,''),'title',coalesce(mp.title,''),
    'raw_fragment',coalesce(mp.raw_fragment,'')
  ) order by mp.meeting_id,mp.id),'[]'::jsonb)
  from meeting.meeting_participants mp
  join meeting.participants p on p.id=mp.participant_id
  left join meeting.organizations o on o.id=mp.organization_id;
$$;

create or replace function public.meeting_app_list_businesses()
returns jsonb language sql security definer
set search_path=pg_catalog,public,meeting as $$
  select coalesce(jsonb_agg(to_jsonb(b) order by b.is_default desc,b.created_at,b.name),'[]'::jsonb)
  from meeting.businesses b where b.is_active=true;
$$;

create or replace function public.meeting_app_upsert_business(payload jsonb)
returns jsonb language plpgsql security definer
set search_path=pg_catalog,public,meeting as $$
declare
  v_id uuid;
  v_name text := nullif(btrim(payload->>'name'),'');
begin
  if v_name is null then raise exception '사업명은 필수입니다.'; end if;
  begin v_id := nullif(payload->>'id','')::uuid;
  exception when invalid_text_representation then v_id := null; end;

  if v_id is not null and exists(select 1 from meeting.businesses where id=v_id) then
    update meeting.businesses set
      name=v_name,
      research_project_name=nullif(btrim(payload->>'research_project_name'),''),
      project_number=nullif(btrim(payload->>'project_number'),''),
      support_organization=nullif(btrim(payload->>'support_organization'),''),
      total_research_period=nullif(btrim(payload->>'total_research_period'),''),
      round_no=case when nullif(payload->>'round_no','') is null then null else (payload->>'round_no')::integer end,
      round_research_period=nullif(btrim(payload->>'round_research_period'),''),
      principal_affiliation=nullif(btrim(payload->>'principal_affiliation'),''),
      principal_title=nullif(btrim(payload->>'principal_title'),''),
      principal_name=nullif(btrim(payload->>'principal_name'),''),
      updated_at=now()
    where id=v_id;
  else
    insert into meeting.businesses(name,research_project_name,project_number,support_organization,total_research_period,round_no,round_research_period,principal_affiliation,principal_title,principal_name,is_default)
    values(v_name,nullif(btrim(payload->>'research_project_name'),''),nullif(btrim(payload->>'project_number'),''),nullif(btrim(payload->>'support_organization'),''),nullif(btrim(payload->>'total_research_period'),''),case when nullif(payload->>'round_no','') is null then null else (payload->>'round_no')::integer end,nullif(btrim(payload->>'round_research_period'),''),nullif(btrim(payload->>'principal_affiliation'),''),nullif(btrim(payload->>'principal_title'),''),nullif(btrim(payload->>'principal_name'),''),false)
    on conflict(name) do update set research_project_name=excluded.research_project_name,project_number=excluded.project_number,support_organization=excluded.support_organization,total_research_period=excluded.total_research_period,round_no=excluded.round_no,round_research_period=excluded.round_research_period,principal_affiliation=excluded.principal_affiliation,principal_title=excluded.principal_title,principal_name=excluded.principal_name,updated_at=now()
    returning id into v_id;
    if v_id is null then select id into v_id from meeting.businesses where name=v_name; end if;
  end if;
  return (select to_jsonb(b) from meeting.businesses b where b.id=v_id);
end;
$$;

create or replace function public.meeting_app_save_final(payload jsonb)
returns jsonb language plpgsql security definer
set search_path=pg_catalog,public,meeting as $$
declare
  v_business jsonb; v_business_id uuid; v_meeting_id bigint; v_existing jsonb; v_revision_no integer;
  item jsonb; v_org_id bigint; v_person_id bigint; v_org_name text; v_org_norm text;
  v_person_name text; v_person_norm text; v_identity_key text; v_title text; v_raw_fragment text;
begin
  v_business := public.meeting_app_upsert_business(payload->'business');
  v_business_id := (v_business->>'id')::uuid;
  begin v_meeting_id := nullif(payload->>'meeting_id','')::bigint;
  exception when invalid_text_representation then v_meeting_id := null; end;

  if v_meeting_id is not null and exists(select 1 from meeting.meetings where id=v_meeting_id) then
    select to_jsonb(m) into v_existing from meeting.meetings m where m.id=v_meeting_id;
    update meeting.meetings set
      business_id=v_business_id,business_snapshot=v_business,
      meeting_date=nullif(payload->>'meeting_date',''),meeting_time=nullif(payload->>'meeting_time',''),
      meeting_place=nullif(payload->>'meeting_place',''),author_name=nullif(payload->>'author_name',''),
      card_merchant=nullif(payload->>'card_merchant',''),amount_raw=nullif(payload->>'amount_raw',''),
      participants_raw=nullif(payload->>'participants',''),participants_final=nullif(payload->>'participants',''),
      meeting_purpose_raw=nullif(payload->>'purpose',''),meeting_purpose_final=nullif(payload->>'purpose',''),
      meeting_content_raw=nullif(payload->>'content_block',''),meeting_content_final=nullif(payload->>'content_block',''),
      discussion_raw=nullif(payload->>'meeting_content',''),discussion_final=nullif(payload->>'meeting_content',''),
      followup_raw=nullif(payload->>'future_plan',''),followup_final=nullif(payload->>'future_plan',''),
      project_name=v_business->>'name',research_project_name=v_business->>'research_project_name',
      project_number=v_business->>'project_number',research_period=v_business->>'total_research_period',
      principal_investigator=v_business->>'principal_name',affiliation=v_business->>'principal_affiliation',
      status='ok',source_type='web_final',edited_by=nullif(payload->>'author_name',''),edited_at=now(),updated_at=now()
    where id=v_meeting_id;
    select coalesce(max(revision_no),0)+1 into v_revision_no from meeting.meeting_revisions where meeting_id=v_meeting_id;
  else
    insert into meeting.meetings(business_id,business_snapshot,meeting_date,meeting_time,meeting_place,author_name,card_merchant,amount_raw,participants_raw,participants_final,meeting_purpose_raw,meeting_purpose_final,meeting_content_raw,meeting_content_final,discussion_raw,discussion_final,followup_raw,followup_final,project_name,research_project_name,project_number,research_period,principal_investigator,affiliation,status,source_type,created_by,edited_by,edited_at)
    values(v_business_id,v_business,nullif(payload->>'meeting_date',''),nullif(payload->>'meeting_time',''),nullif(payload->>'meeting_place',''),nullif(payload->>'author_name',''),nullif(payload->>'card_merchant',''),nullif(payload->>'amount_raw',''),nullif(payload->>'participants',''),nullif(payload->>'participants',''),nullif(payload->>'purpose',''),nullif(payload->>'purpose',''),nullif(payload->>'content_block',''),nullif(payload->>'content_block',''),nullif(payload->>'meeting_content',''),nullif(payload->>'meeting_content',''),nullif(payload->>'future_plan',''),nullif(payload->>'future_plan',''),v_business->>'name',v_business->>'research_project_name',v_business->>'project_number',v_business->>'total_research_period',v_business->>'principal_name',v_business->>'principal_affiliation','ok','web_final',nullif(payload->>'author_name',''),nullif(payload->>'author_name',''),now()) returning id into v_meeting_id;
    v_revision_no:=1; v_existing:=payload->'generated_original';
  end if;

  insert into meeting.meeting_revisions(meeting_id,revision_no,before_data,after_data,edited_by,edit_source)
  values(v_meeting_id,v_revision_no,v_existing,payload,nullif(payload->>'author_name',''),'web');

  delete from meeting.meeting_participants where meeting_id=v_meeting_id;
  for item in select value from jsonb_array_elements(coalesce(payload->'participant_items','[]'::jsonb)) loop
    v_person_name:=nullif(btrim(item->>'name'),''); if v_person_name is null then continue; end if;
    v_person_norm:=coalesce(nullif(btrim(item->>'normalized_name'),''),regexp_replace(v_person_name,'\\s+','','g'));
    v_org_name:=nullif(btrim(item->>'organization'),''); v_org_norm:=nullif(btrim(item->>'normalized_organization'),'');
    v_title:=nullif(btrim(item->>'title'),''); v_raw_fragment:=coalesce(nullif(btrim(item->>'raw_fragment'),''),v_person_name);
    v_org_id:=null;
    if v_org_name is not null then
      if v_org_norm is null then v_org_norm:=regexp_replace(v_org_name,'[[:space:]㈜()（）주식회사]+','','g'); end if;
      insert into meeting.organizations(name,normalized_name) values(v_org_name,v_org_norm)
      on conflict(normalized_name) do update set updated_at=now() returning id into v_org_id;
    end if;
    if v_org_norm is not null and v_org_norm<>'' then v_identity_key:=v_person_norm||'|'||v_org_norm;
    else v_identity_key:=v_person_norm||'|meeting:'||v_meeting_id::text; end if;
    insert into meeting.participants(name,normalized_name,identity_key) values(v_person_name,v_person_norm,v_identity_key)
    on conflict(identity_key) do update set name=excluded.name,last_seen_at=now(),updated_at=now() returning id into v_person_id;
    insert into meeting.meeting_participants(meeting_id,participant_id,organization_id,organization_raw,title,raw_fragment,parse_confidence,source_type)
    values(v_meeting_id,v_person_id,v_org_id,v_org_name,v_title,v_raw_fragment,'user_final','web_final')
    on conflict(meeting_id,participant_id,raw_fragment) do update set organization_id=excluded.organization_id,organization_raw=excluded.organization_raw,title=excluded.title,parse_confidence=excluded.parse_confidence,source_type=excluded.source_type;
  end loop;

  return jsonb_build_object('meeting_id',v_meeting_id,'revision_no',v_revision_no,'business',v_business);
end;
$$;

revoke all on function public.meeting_app_load_meetings() from public,anon,authenticated;
revoke all on function public.meeting_app_load_participants() from public,anon,authenticated;
revoke all on function public.meeting_app_list_businesses() from public,anon,authenticated;
revoke all on function public.meeting_app_upsert_business(jsonb) from public,anon,authenticated;
revoke all on function public.meeting_app_save_final(jsonb) from public,anon,authenticated;

grant execute on function public.meeting_app_load_meetings() to service_role;
grant execute on function public.meeting_app_load_participants() to service_role;
grant execute on function public.meeting_app_list_businesses() to service_role;
grant execute on function public.meeting_app_upsert_business(jsonb) to service_role;
grant execute on function public.meeting_app_save_final(jsonb) to service_role;