"""소프트웨어학과 공식 홈페이지(cse.skku.edu) '학사과정 - 교육과정' 표와 교과목 개요를 수집하는 도구.

대상 (둘 다 로그인이 필요 없는 공개 페이지)
    https://cse.skku.edu/cse/dept_curriculum.do   학사과정 - 교육과정
    https://cse.skku.edu/cse/grad_curriculum.do   대학원과정 - 교육과정 (학석공통 과목이 여기 있다)

동작 방식
- 쿠키·로그인 상태를 저장하지 않는 새 브라우저로 연다. 로그인은 하지 않는다.
- 표가 한 쪽에 30과목씩 나뉘어 있어서 pager.offset=0, 30, 60 … 순서로 쪽을 넘기며 읽고,
  새 과목이 더 나오지 않으면 멈춘다. 쪽을 넘길 때마다 1초 쉰다.
- 표의 칸(학수번호, 교과목명, 학점, 자기학습시간, 영역, 학위, 이수학년, 비고, 언어, 개설여부)과
  과목명 아래에 펼쳐지는 '교과목 개요' 문장을 읽는다.
- 개요가 페이지 안에 이미 들어 있으면 그대로 읽고, 없을 때만 과목명을 하나씩 눌러 펼친다.
  누를 때마다 잠깐 쉬어 학교 서버에 부담을 주지 않는다.
- 결과는 out/ 폴더에 저장된다. out/ 은 .gitignore 에 들어 있어 커밋되지 않는다.
- 저장 전에 이메일·전화번호·학번(10자리 숫자) 모양이 있는지 검사하고, 있으면 저장하지 않고 멈춘다.

사용법 (backend/crawler/homepage 폴더에서, venv 를 켠 상태로)
    python homepage_curriculum.py              # 화면 없이 실행
    python homepage_curriculum.py --show       # 브라우저 창을 보면서 실행 (구조가 이상할 때 확인용)

결과
    out/homepage_courses.csv / .json   공홈 표의 모든 과목 + 개요 (JSON 은 AI팀 프롬프트 입력용, 내용은 같음)
    out/homepage_merged.csv / .json    로드맵 76과목(seed)에 공홈 학수번호·개설여부·개요를 이름으로 맞춘 결과
    out/homepage_report.txt    맞춘 개수, 못 맞춘 로드맵 과목, 학수번호가 GLS 결과와 다른 과목
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright


URL = "https://cse.skku.edu/cse/dept_curriculum.do"        # 학사과정 - 교육과정
GRAD_URL = "https://cse.skku.edu/cse/grad_curriculum.do"   # 대학원과정 - 교육과정 (학석공통 과목)
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))   # backend/crawler/course_names.py
from course_names import name_key  # noqa: E402  GLS 수집과 같은 과목명 비교 규칙(네트웍=네트워크, SW=소프트웨어 등)
OUT_DIR = HERE / "out"
SEED = HERE.parents[1] / "db" / "seed_courses_2026.csv"

# 공홈 표의 머리글 → 저장할 칸 이름
HEADER_MAP = {
    "학수번호": "course_code",
    "교과목명": "name",
    "학점": "credits",
    "자기학습시간": "self_study_hours",
    "영역": "area",
    "학위": "degree",
    "이수학년": "years",
    "비고": "note",
    "언어": "language",
    "개설여부": "offered",
}
COURSE_COLUMNS = ["program"] + list(HEADER_MAP.values()) + ["description", "source_url", "collected_at"]
PAGE_SIZE = 30          # 공홈은 한 쪽에 30과목씩 보여 준다 (주소의 pager.offset=0, 30, 60 …)
MAX_PAGES = 20
CODE_RE = re.compile(r"^[A-Z]{2,4}\d{4}[A-Z]?$")

# 페이지의 모든 <table> 을 [[{text, link}...], ...] 로 꺼낸다.
# textContent 를 써서, 접혀 있어 화면에 안 보이는(display:none) 개요 줄도 읽는다.
EXTRACT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  return [...document.querySelectorAll('table')].map(t =>
    [...t.querySelectorAll('tr')].map(tr =>
      [...tr.children].filter(c => c.tagName === 'TD' || c.tagName === 'TH').map(c => ({
        text: clean(c.textContent),
        colspan: c.colSpan || 1,
        has_link: !!c.querySelector('a, button, [onclick]')
      }))
    )
  );
}
"""

PRIVACY_PATTERNS = {
    "이메일": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "전화번호": re.compile(r"\b0\d{1,2}-\d{3,4}-\d{4}\b"),
    "학번(10자리)": re.compile(r"\b20\d{8}\b"),
}


def page_url(base: str, offset: int) -> str:
    """공홈 쪽 넘김 주소. 예) ...dept_curriculum.do?pager.offset=30&lang=All"""
    if "cse.skku.edu" not in base:            # 테스트용 주소는 그대로
        return base if offset == 0 else f"{base}?pager.offset={offset}"
    return f"{base.split('?')[0].split('#')[0]}?pager.offset={offset}&lang=All"


def norm_header(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def find_course_table(tables):
    """머리글에 '학수번호'와 '교과목명'이 있는 표를 찾는다."""
    for rows in tables:
        for i, row in enumerate(rows[:3]):
            heads = [norm_header(c["text"]) for c in row]
            if "학수번호" in heads and "교과목명" in heads:
                return heads, rows[i + 1:]
    return None, None


def parse_rows(heads, rows):
    """과목 줄과 그 아래 개요 줄(칸 하나짜리)을 묶는다."""
    idx = {HEADER_MAP[h]: i for i, h in enumerate(heads) if h in HEADER_MAP}
    courses = []
    for row in rows:
        texts = [c["text"] for c in row]
        if len(row) >= len(idx) and CODE_RE.match(texts[idx["course_code"]] if "course_code" in idx else ""):
            courses.append({k: texts[i] for k, i in idx.items()} | {"description": ""})
        elif courses and len(row) == 1 and row[0]["text"]:
            # 과목 줄 바로 아래의 칸 하나짜리 줄 = 교과목 개요
            if not courses[-1]["description"]:
                courses[-1]["description"] = row[0]["text"]
    return courses


def expand_missing(page, courses, pause: float):
    """페이지에 개요가 들어 있지 않은 과목만 과목명을 눌러 펼친 뒤 다시 읽는다."""
    missing = [c for c in courses if not c["description"]]
    if not missing:
        return courses
    print(f"페이지에 개요가 들어 있지 않은 과목 {len(missing)}개 → 과목명을 하나씩 눌러 펼칩니다.")
    for c in missing:
        link = page.locator("table a", has_text=c["name"]).first
        if link.count() == 0:
            continue
        try:
            link.click(timeout=3000)
            page.wait_for_timeout(int(pause * 1000))
        except Exception as e:  # 눌리지 않는 과목은 건너뛴다
            print(f"  - {c['course_code']} {c['name']}: 펼치기 실패 ({e.__class__.__name__})")
    heads, rows = find_course_table(page.evaluate(EXTRACT_JS))
    again = {(x["course_code"], x["name"]): x["description"] for x in parse_rows(heads, rows)}
    for c in courses:
        c["description"] = c["description"] or again.get((c["course_code"], c["name"]), "")
    return courses


def privacy_check(rows) -> list[str]:
    hits = []
    for r in rows:
        for col, v in r.items():
            for label, pat in PRIVACY_PATTERNS.items():
                if v and pat.search(str(v)):
                    hits.append(f"{r.get('course_code', '')} {r.get('name', '')} [{col}] {label} 모양 발견")
    return hits


def write_csv(path: Path, rows, columns):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_json(path: Path, rows, columns):
    """AI팀·프롬프트 입력용 JSON. CSV 와 같은 내용을 [{칸: 값}, ...] 로 저장한다."""
    data = [{c: r.get(c, "") for c in columns} for r in rows]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_with_seed(courses):
    """로드맵 76과목에 공홈 값을 과목명으로 맞춘다. 학수번호는 seed 에 이미 있으면 덮지 않는다."""
    by_key = {}
    for c in courses:
        by_key.setdefault(name_key(c["name"]), []).append(c)
    with SEED.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        seed_cols = reader.fieldnames or []
        seed = list(reader)
    extra = ["course_code", "homepage_course_code", "homepage_offered", "description", "description_source"]
    merged, unmatched, code_diff = [], [], []
    for s in seed:
        row = dict(s)
        hits = by_key.get(name_key(s["name"]), [])
        if hits:
            # 같은 이름이 학사·대학원에 다른 학수번호로 있으면(예: 정보보호개론 SWE3025 / ESW4020)
            # 로드맵 구분이 학석공통이면 대학원 쪽, 아니면 학사 쪽을 고른다.
            want = "대학원" if s.get("category") == "학석공통" else "학사"
            same = [x for x in hits if x.get("program") == want]
            pool = same or hits
            h = next((x for x in pool if x["description"]), pool[0])
            row["homepage_course_code"] = " / ".join(dict.fromkeys(x["course_code"] for x in hits))
            row["homepage_offered"] = h["offered"]
            row["description"] = h["description"]
            row["description_source"] = h["source_url"] if h["description"] else ""
            if row.get("course_code") and row["course_code"] not in row["homepage_course_code"]:
                code_diff.append(f"{s['name']}: seed/GLS {row['course_code']} vs 공홈 {row['homepage_course_code']}")
            if not row.get("course_code") and (len(hits) == 1 or same):
                row["course_code"] = h["course_code"]
        else:
            unmatched.append(f"{s['name']} ({s.get('category', '')})")
        merged.append(row)
    return merged, seed_cols + [c for c in extra if c not in seed_cols], unmatched, code_diff


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="브라우저 창을 띄워서 실행")
    ap.add_argument("--pause", type=float, default=0.4, help="과목명을 누를 때마다 쉬는 시간(초)")
    ap.add_argument("--url", action="append", help=argparse.SUPPRESS)  # 테스트용 (여러 번 줄 수 있음)
    args = ap.parse_args()

    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    targets = [("학사", URL), ("대학원", GRAD_URL)]
    if args.url:
        targets = [(f"test{i}", u) for i, u in enumerate(args.url)]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.show)
        page = browser.new_context().new_page()   # 저장된 쿠키 없이 새로 연다
        courses, seen = [], set()
        for program, base in targets:
            print(f"[{program}] {page_url(base, 0)}")
            for n in range(MAX_PAGES):
                page.goto(page_url(base, n * PAGE_SIZE), wait_until="networkidle", timeout=30000)
                heads, rows = find_course_table(page.evaluate(EXTRACT_JS))
                if heads is None:
                    if n == 0:
                        print(f"  {program} 교육과정 표를 찾지 못했습니다. --show 로 페이지가 뜨는지 확인해 주세요.")
                    break
                found = expand_missing(page, parse_rows(heads, rows), args.pause)
                new = [c for c in found if (c["course_code"], c["name"]) not in seen]
                print(f"  {n + 1}쪽: {len(found)}과목 (새 과목 {len(new)})")
                if not new:          # 마지막 쪽을 넘어서면 같은 목록이 오거나 빈 표가 온다
                    break
                for c in new:
                    seen.add((c["course_code"], c["name"]))
                    c["program"] = program
                    c["source_url"] = page_url(base, n * PAGE_SIZE)
                courses += new
                if len(found) < PAGE_SIZE:   # 한 쪽이 다 차지 않았으면 마지막 쪽
                    break
                page.wait_for_timeout(1000)  # 쪽을 넘길 때 1초 쉼
        if not courses:
            browser.close()
            return 1
        browser.close()

    for c in courses:
        c["collected_at"] = collected_at

    hits = privacy_check(courses)
    if hits:
        print("개인정보로 보이는 값이 있어 저장하지 않았습니다:")
        print("\n".join("  - " + h for h in hits))
        return 2

    OUT_DIR.mkdir(exist_ok=True)
    write_csv(OUT_DIR / "homepage_courses.csv", courses, COURSE_COLUMNS)
    write_json(OUT_DIR / "homepage_courses.json", courses, COURSE_COLUMNS)
    merged, cols, unmatched, code_diff = merge_with_seed(courses)
    write_csv(OUT_DIR / "homepage_merged.csv", merged, cols)
    write_json(OUT_DIR / "homepage_merged.json", merged, cols)

    with_desc = sum(1 for c in courses if c["description"])
    matched = len(merged) - len(unmatched)
    lines = [
        f"수집 시각: {collected_at}",
        f"공홈 과목 수: {len(courses)} (학사 {sum(1 for c in courses if c['program'] == '학사')}, 대학원 {sum(1 for c in courses if c['program'] == '대학원')}; 개요 있음 {with_desc}, 개설 Yes {sum(1 for c in courses if c['offered'].lower() == 'yes')})",
        f"로드맵 {len(merged)}과목 중 공홈과 이름이 맞은 과목: {matched}",
        f"학수번호가 있는 로드맵 과목(seed·GLS 값 포함): {sum(1 for m in merged if m.get('course_code'))}",
        "",
        "[공홈에서 못 찾은 로드맵 과목]",
        *(unmatched or ["(없음)"]),
        "",
        "[seed/GLS 학수번호와 공홈 학수번호가 다른 과목]",
        *(code_diff or ["(없음)"]),
    ]
    (OUT_DIR / "homepage_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4]))
    print(f"저장: {OUT_DIR}/ homepage_courses.csv·json, homepage_merged.csv·json, homepage_report.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
