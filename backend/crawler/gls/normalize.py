"""raw/ 에 저장된 GLS 표를 정리해서, 로드맵 시드와 합친 CSV 를 만든다.

입력
- raw/capture_*.json            : gls_capture.py 가 저장한 표
- ../db/seed_courses_2026.csv   : 로드맵 이미지에서 옮긴 76과목 (과목명, 구분, 학년·학기, 분야)

출력 (out/ 폴더)
- gls_courses.csv     : GLS 표에서 찾은 과목 행 (정리만 한 원본)
- courses_merged.csv  : 시드 + GLS 학수번호·학점
- courses_upsert.sql  : 위 CSV 를 Supabase SQL Editor 에 붙여 넣을 INSERT 문 (기존 행은 덮어씀)
- prereqs_raw.csv     : GLS 에 선수과목 칸이 있으면 그 글자 그대로 (사람이 확인 후 입력)
- report.txt          : 못 찾은 과목, 헷갈리는 과목 목록

사용법
    python normalize.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))   # backend/crawler/course_names.py
from course_names import name_key  # noqa: E402  (과목명 비교 규칙은 홈페이지 수집과 공유)
RAW_DIR = HERE / "raw"
OUT_DIR = HERE / "out"
SEED = HERE.parents[1] / "db" / "seed_courses_2026.csv"

# GLS 표의 머리글(헤더)이 정확히 무엇인지 아직 모르므로, 비슷한 이름을 여러 개 받아 준다.
# 실제 화면을 보고 빠진 이름이 있으면 여기에 추가하면 된다.
HEADER_ALIASES = {
    # 정보광장 '학사과정-교육과정' 화면 (2026-11-24 확인):
    # 번호 | 학수번호 | 이수구분 | 교과목명 | 배정학점 | 영역구분 | 이수학년 | 과목구분
    "code":     ["학수번호", "교과목번호", "과목번호", "과목코드", "교과목코드", "학수코드"],
    "name":     ["교과목명", "과목명", "교과목", "강좌명", "교과명"],
    "credits":  ["배정학점", "학점", "학점수", "시수(학점)"],
    "category": ["영역구분", "교과구분", "영역"],                   # 전공심화 / 전공코어 / 실험실습 ...
    "track":    ["이수구분"],                                      # 전공 / 교양 (참고만)
    "level":    ["과목구분"],                                      # 학부전공과목(고학년용) 등 (참고만)
    "year":     ["이수학년", "학년", "권장학년", "대상학년"],         # 예: 3-4
    "term":     ["학기", "권장학기", "개설학기", "이수학기"],
    "prereq":   ["선수과목", "선수교과목", "선수"],
    "dept":     ["개설학과", "주관학과", "개설전공", "주관전공", "학과"],
}

SEED_COLUMNS = ["roadmap_version", "name", "category", "course_code", "name_en", "credits", "gls_years",
                "roadmap_year", "roadmap_term", "field_main", "modifier", "input_method", "source",
                "collected_at", "note"]


def norm_header(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def match_header(cell: str) -> str | None:
    """'이수구분' 같은 머리글 글자를 'category' 같은 필드 이름으로 바꾼다. 모르면 None."""
    c = norm_header(cell)
    for field, aliases in HEADER_ALIASES.items():
        if c in aliases:
            return field
    # '교과목명(영문)' 처럼 뒤에 붙은 말이 있으면 앞부분으로 한 번 더 비교
    for field, aliases in HEADER_ALIASES.items():
        if any(c.startswith(a) for a in aliases):
            return field
    return None


def parse_credits(s: str) -> str:
    m = re.match(r"\s*(\d+(?:\.\d+)?)", s or "")
    return m.group(1) if m else ""


def find_course_tables(capture: dict):
    """한 번 저장한 화면 안에서 '과목명 + (학수번호 또는 학점)' 머리글이 있는 표를 찾는다."""
    for frame in capture.get("frames", []):
        for table in frame.get("tables", []):
            rows = table.get("rows", [])
            for h_i, row in enumerate(rows[:5]):  # 머리글은 보통 앞쪽 몇 줄 안에 있다
                fields = [match_header(c) for c in row]
                if "name" in fields and ("code" in fields or "credits" in fields):
                    yield fields, rows[h_i + 1:]
                    break


def load_gls_rows():
    rows, captured_at = [], None
    for path in sorted(RAW_DIR.glob("capture_*.json")):
        cap = json.loads(path.read_text(encoding="utf-8"))
        captured_at = captured_at or cap.get("captured_at")
        for fields, body in find_course_tables(cap):
            for r in body:
                rec = {f: (r[i] if i < len(r) else "") for i, f in enumerate(fields) if f}
                if not rec.get("name") or match_header(rec.get("name", "")):  # 빈 줄이나 반복 머리글 건너뜀
                    continue
                rec["credits"] = parse_credits(rec.get("credits", ""))
                # "AI캡스톤디자인 / AI Capstone Design" → 한글명, 영문명
                parts = [p.strip() for p in rec["name"].split(" / ")]
                rec["name"], rec["name_en"] = parts[0], " ".join(parts[1:])
                # 대학원-교육과정 화면은 영역구분이 비어 있다. 과목구분 '학사/석사' = 학석공통,
                # '석사/박사' = 대학원 전용 (2026-11-24 ESW4020 정보보호개론 등으로 확인)
                if not rec.get("category") and re.sub(r"\s", "", rec.get("level", "")) == "학사/석사":
                    rec["category"] = "학석공통"
                rec["_file"] = path.name
                rows.append(rec)
    # 같은 학수번호(또는 이름)가 여러 화면에 나오면 하나만 남긴다
    seen, unique = set(), []
    for r in rows:
        k = r.get("code") or name_key(r["name"])
        if k not in seen:
            seen.add(k)
            unique.append(r)
    return unique, captured_at


def main() -> int:
    if not list(RAW_DIR.glob("capture_*.json")):
        print("raw/ 에 capture_*.json 이 없어요. 먼저 gls_capture.py 로 수집하세요.")
        return 1
    OUT_DIR.mkdir(exist_ok=True)

    gls, captured_at = load_gls_rows()
    if not gls:
        print("과목 표를 못 찾았어요. 표 머리글 이름이 HEADER_ALIASES 에 없는 것 같아요.")
        print("raw/capture_*.json 을 열어 머리글 줄을 확인하고 HEADER_ALIASES 에 추가하세요.")
        return 1

    gls_fields = ["code", "name", "name_en", "credits", "category", "track", "level", "year",
                  "term", "prereq", "dept", "_file"]
    with open(OUT_DIR / "gls_courses.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=gls_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(gls)

    seed = list(csv.DictReader(open(SEED, encoding="utf-8")))
    gls_by_key: dict[str, list[dict]] = {}
    for r in gls:
        gls_by_key.setdefault(name_key(r["name"]), []).append(r)

    seed_name_count: dict[str, int] = {}
    for s in seed:
        seed_name_count[name_key(s["name"])] = seed_name_count.get(name_key(s["name"]), 0) + 1

    report, merged, used = [], [], set()
    for s in seed:
        key = name_key(s["name"])
        cands = [c for c in gls_by_key.get(key, []) if id(c) not in used]  # 한 GLS 행은 한 번만 쓴다
        if seed_name_count[key] > 1:
            # 로드맵에 같은 이름이 여러 번(예: 정보보호개론 전공심화/학석공통) → 구분이 같은 것만 인정
            cands = [c for c in cands if c.get("category") == s["category"]]
        elif len(cands) > 1:
            narrowed = [c for c in cands if s["category"] in c.get("category", "")]
            cands = narrowed or cands
        row = {c: s.get(c, "") for c in SEED_COLUMNS}
        if len(cands) == 1:
            g = cands[0]
            used.add(id(g))
            row.update(course_code=g.get("code", ""), credits=g.get("credits", ""),
                       name_en=g.get("name_en", ""), gls_years=g.get("year", ""),
                       input_method="crawl", collected_at=captured_at or "",
                       source=(s["source"] + " + GLS 교육과정").strip(" +"))
            if g.get("category") and g["category"] != s["category"]:
                report.append(f"[구분 다름] {s['name']}: 로드맵 {s['category']} / GLS {g['category']}")
        elif len(cands) > 1:
            report.append(f"[헷갈림] {s['name']} ({s['category']}): GLS 에 후보 {len(cands)}개 "
                          + ", ".join(c.get('code', '?') for c in cands) + " → 직접 골라 넣기")
        else:
            report.append(f"[GLS에 없음] {s['name']} ({s['category']})")
        merged.append(row)

    for g in gls:
        if id(g) not in used and not any(name_key(g["name"]) == name_key(s["name"]) for s in seed):
            report.append(f"[로드맵에 없음] {g.get('code','')} {g['name']} ({g.get('category','')}) → 타학과/폐지 과목인지 확인")

    with open(OUT_DIR / "courses_merged.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SEED_COLUMNS)
        w.writeheader()
        w.writerows(merged)

    from to_sql import csv_to_sql  # backend/crawler/to_sql.py (공용)
    (OUT_DIR / "courses_upsert.sql").write_text(csv_to_sql(str(OUT_DIR / "courses_merged.csv")),
                                                encoding="utf-8")

    prereqs = [g for g in gls if g.get("prereq")]
    with open(OUT_DIR / "prereqs_raw.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "prereq"], extrasaction="ignore")
        w.writeheader()
        w.writerows(prereqs)

    filled = sum(1 for m in merged if m["course_code"])
    summary = [f"GLS 에서 찾은 과목 {len(gls)}개",
               f"로드맵 {len(merged)}과목 중 학수번호 채움 {filled}개, 못 채움 {len(merged) - filled}개",
               f"선수과목 칸이 있는 과목 {len(prereqs)}개", ""]
    (OUT_DIR / "report.txt").write_text("\n".join(summary + report) + "\n", encoding="utf-8")
    print("\n".join(summary))
    print(f"자세한 목록: {OUT_DIR / 'report.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
