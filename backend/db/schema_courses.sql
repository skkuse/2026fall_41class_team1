-- KingoZeta 교과목 DB (Supabase / PostgreSQL)
-- Supabase 대시보드 > SQL Editor 에 붙여 넣고 Run 하면 테이블이 만들어진다.
-- 여러 번 실행해도 안전하도록 IF NOT EXISTS 를 썼다.

-- 1) 교과목
create table if not exists public.courses (
  id               bigint generated always as identity primary key,
  roadmap_version  text     not null default '2026',          -- 로드맵 버전 (학번별로 다를 수 있음)
  name             text     not null,                         -- 과목명 (로드맵 표기)
  category         text     not null check (category in
                     ('기초필수','전공코어','전공심화','실험실습','학석공통','프로젝트','창업','교양')),
  course_code      text,                                      -- GLS 학수번호 (수집 전에는 비어 있음)
  name_en          text,                                      -- 영문 과목명 (GLS)
  credits          numeric(3,1),                              -- 배정학점 (GLS)
  gls_years        text,                                      -- GLS 이수학년 표기 그대로 (예: '3-4')
  roadmap_year     smallint check (roadmap_year between 1 and 4),
  roadmap_term     smallint check (roadmap_term in (1, 2)),   -- 비어 있으면 두 학기 모두에 걸친 과목
  field_main       text check (field_main in ('A','E','W','C')),  -- 주 분야: A AI·데이터 / E 시스템 / W 서비스 / C 보안
  field_sub        text check (field_sub  in ('A','E','W','C')),  -- 보조 분야 (선택)
  modifier         text check (modifier in ('common','theory')),  -- 분야가 아닌 과목: 공통·진로 / 이론
  offered_status   text not null default 'unconfirmed'
                     check (offered_status in ('confirmed','unconfirmed','not_offered')),  -- 개설 확인 / 미확인 / 미개설
  other_dept       boolean not null default false,            -- 타학과 과목 여부
  substitute_course_id bigint references public.courses(id),  -- 안 열릴 때 대체과목
  input_method     text not null default 'manual' check (input_method in ('crawl','manual')),
  source           text,                                      -- 근거 자료 (예: '2026 로드맵 이미지', 'GLS 교육과정')
  collected_at     timestamptz,                               -- 수집 일시
  note             text,
  unique (roadmap_version, name, category),                   -- 같은 과목명이 구분만 다르게 두 번 있을 수 있음 (예: 정보보호개론)
  unique (roadmap_version, course_code, category)
);

-- 예전 버전으로 이미 테이블을 만든 경우에도 새 칸이 생기도록
alter table public.courses add column if not exists name_en   text;
alter table public.courses add column if not exists gls_years text;

-- 2) 선수과목 (과목 A 를 들으려면 과목 B 가 먼저 필요)
create table if not exists public.course_prerequisites (
  course_id         bigint not null references public.courses(id) on delete cascade,
  prereq_course_id  bigint not null references public.courses(id) on delete cascade,
  source            text not null,                            -- 근거 자료 (조교 피드백: 출처가 있는 사실만)
  primary key (course_id, prereq_course_id),
  check (course_id <> prereq_course_id)
);

-- 3) 추천 점수용 과목 태그 (분야·직무·세부 분야). 값 목록은 팀에서 확정 후 채운다.
create table if not exists public.course_tags (
  course_id  bigint not null references public.courses(id) on delete cascade,
  tag        text   not null,                                 -- 예: 'A', 'K', 'NLP'
  weight     numeric(3,2) not null default 1.0 check (weight between 0 and 1),
  source     text,
  primary key (course_id, tag)
);

-- 4) 읽기 권한: 누구나 조회 가능, 수정은 대시보드(관리자)에서만
alter table public.courses              enable row level security;
alter table public.course_prerequisites enable row level security;
alter table public.course_tags          enable row level security;

drop policy if exists "courses are public" on public.courses;
create policy "courses are public" on public.courses for select using (true);
drop policy if exists "prereqs are public" on public.course_prerequisites;
create policy "prereqs are public" on public.course_prerequisites for select using (true);
drop policy if exists "tags are public" on public.course_tags;
create policy "tags are public" on public.course_tags for select using (true);
