# 교과목 DB + GLS 수집 도구

담당: 김규성 (백엔드). KingoZeta 교과 추천에 쓰는 교과목 DB 를 Supabase 에 만들고, GLS 교육과정에서 학수번호·학점 같은 사실 정보를 채운다.

```
backend/
├─ db/
│  ├─ schema_courses.sql       # 테이블 만들기 (courses, course_prerequisites, course_tags)
│  ├─ seed_courses_2026.csv    # 2026 로드맵 76과목 (과목명·구분·학년·학기·분야 태그)
│  └─ seed_courses_2026.sql    # 위 CSV 를 넣는 INSERT 문
└─ crawler/
   ├─ gls_capture.py           # GLS 화면의 표만 저장 (로그인은 사람이 직접)
   ├─ normalize.py             # 저장한 표 정리 + 시드와 합치기
   ├─ to_sql.py                # CSV → INSERT 문 변환
   └─ requirements.txt
```

## 데이터 출처

| 항목 | 출처 | 상태 |
|---|---|---|
| 과목명, 구분(기초필수·전공코어·전공심화·실험실습·학석공통 등), 로드맵 학년·학기 | 2026 소프트웨어학과 로드맵 이미지 (노션 1120 회의록) | 시드에 들어 있음 |
| 분야 태그 A/E/W/C, 공통·이론 | 노션 "교과목 커버리지"·"SW-MBTI" 페이지의 팀 분류 | 시드에 들어 있음 (팀 임시 분류) |
| 학수번호, 학점 | GLS 교육과정 | **수집 필요** |
| 선수과목, 개설 여부 | GLS 에 있으면 수집, 없으면 공식 자료 보고 직접 입력 (출처 기록) | **확인 필요** |
| 추천 점수용 태그, 교수님 우선순위 | 팀 결정 대기 | `course_tags` 테이블만 만들어 둠 |

## 1. Supabase 에 테이블 만들기 (처음 한 번)

1. supabase.com → 팀 프로젝트 열기 (없으면 New project, 무료 요금제)
2. 왼쪽 **SQL Editor** → New query
3. `db/schema_courses.sql` 내용을 붙여 넣고 **Run**
4. 새 query 에 `db/seed_courses_2026.sql` 내용을 붙여 넣고 **Run** → `courses` 에 76행
5. 왼쪽 **Table Editor** → `courses` 에서 확인

> 무료 프로젝트는 일주일 동안 사용이 거의 없으면 자동으로 일시정지된다. 통합·발표 전에 대시보드에서 켜져 있는지 확인할 것.

## 2. GLS 에서 학수번호·학점 수집

```bash
cd backend/crawler
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
python gls_capture.py            # 정보광장(kingoinfo.skku.edu)이 열린다
```

1. 뜬 브라우저에서 **직접** 로그인한다.
2. **수업영역 > 교육과정 > 학사과정-교육과정** 으로 이동한다.
3. 교육과정년도 **2026 년도 교육과정**, 주관학부 **소프트웨어융합대학**, 학부·전공 **소프트웨어학과** → **조회**.
4. 표가 보이면 터미널에서 **Enter** → `raw/capture_01.json` 저장.
5. 표를 아래로 스크롤해서 새 과목이 보이면 **Enter** 를 또 누른다. 맨 아래(Total 60 정도)까지 반복한다. 겹친 과목은 자동으로 하나만 남는다.
6. 다 끝나면 `q` + Enter → 브라우저가 닫힌다.

이 화면의 칸: 번호 · 학수번호 · 이수구분 · 교과목명(한글/영문) · 배정학점 · 영역구분 · 이수학년 · 과목구분.
**선수과목과 개설 여부는 이 화면에 없다.** (다른 메뉴에서 찾거나 공식 자료로 직접 입력)

그다음:

```bash
python normalize.py
```

- `out/report.txt` 를 열어 **[GLS에 없음] / [헷갈림] / [로드맵에 없음]** 목록을 확인한다.
- 괜찮으면 `out/courses_upsert.sql` 을 Supabase SQL Editor 에서 Run → 학수번호·학점이 채워진다.
  (시드 → 수집 결과 순서로 넣는다. 시드를 나중에 다시 넣어도 학수번호·학점은 지워지지 않는다.)
- 표를 못 찾았다고 나오면: `raw/capture_*.json` 에서 머리글 줄을 확인하고 `normalize.py` 의 `HEADER_ALIASES` 에 그 이름을 추가한다.

## 개인정보·보안 규칙

- 비밀번호는 코드·파일·`.env` 어디에도 저장하지 않는다. 로그인은 사람이 브라우저에서 직접 한다.
- 크롤러는 Enter 를 누른 순간 **화면에 보이는 표 글자만** 읽는다. 클릭·입력·메뉴 이동을 하지 않는다.
  → 교육과정 화면에서만 Enter 를 누를 것. 성적·시간표 화면에서 누르면 그 표가 저장된다.
- 쿠키를 저장하지 않는 새 브라우저를 쓰고, 끝나면 닫는다.
- `raw/`, `out/` 은 `.gitignore` 에 들어 있어 커밋되지 않는다. 화면 상단의 이름·학번 등이 섞일 수 있으므로 공유하지 않는다.
- `--save-html` 은 디버깅용이다. 저장된 HTML 에는 화면 전체(이름 등)가 들어가므로 확인 후 지운다.
- 요청은 사람이 Enter 를 누를 때만 일어나므로 학교 서버에 부담이 없다.


