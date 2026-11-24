"""GLS 교육과정 화면의 '표'만 저장하는 수집 도구.

동작 방식 (안전하게 만들기 위한 규칙)
- 브라우저 창을 띄우기만 한다. 로그인과 메뉴 이동은 사람이 직접 한다.
  → 비밀번호를 코드에도, 파일에도, .env 에도 저장하지 않는다.
- 사람이 터미널에서 Enter 를 누른 순간 화면에 보이는 <table> 과 표 형태(grid)의 글자만 읽는다.
  클릭, 입력, 다른 메뉴 이동은 하지 않는다.
- 쿠키·로그인 상태를 저장하지 않는 새 브라우저(시크릿 창과 같음)를 쓰고, 끝나면 닫는다.
- 결과는 raw/ 폴더에 JSON 으로 저장된다. raw/ 는 .gitignore 에 들어 있어 커밋되지 않는다.

사용법
    python gls_capture.py                 # 기본 주소로 브라우저 열기
    python gls_capture.py --url <주소>    # 다른 시작 주소
    python gls_capture.py --save-html     # 표 구조가 이상할 때만: 화면 HTML도 raw/ 에 저장
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

# 성균관대 정보광장(GLS). 로그인 후 수업영역 > 교육과정 > 학사과정-교육과정 으로 이동한다.
DEFAULT_URL = "https://kingoinfo.skku.edu"

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "raw"

# 화면 안의 표를 [[칸, 칸, ...], ...] 형태로 꺼내는 스크립트.
# 1) 정보광장(넥사크로) 그리드: id 가 "...grd.body.gridrow_3.cell_3_2" 같은 div
# 2) 일반 <table>   3) div 로 그린 표(role=grid)
# 한 칸에 여러 줄(예: 한글 과목명 + 영문 과목명)이 있으면 " / " 로 이어 붙인다.
EXTRACT_TABLES_JS = r"""
() => {
  const clean = s => (s || '').split(/\n+/).map(x => x.replace(/\s+/g, ' ').trim())
                              .filter(Boolean).join(' / ');
  const out = [];

  // 1) 넥사크로 그리드
  const re = /^(.*)\.(head|body|summ|summary)\.gridrow_(-?\d+)\.cell_(-?\d+)_(\d+)(?:[.:].*)?$/;
  const grids = {};
  document.querySelectorAll('[id*="gridrow_"][id*="cell_"]').forEach(el => {
    const m = el.id.match(re);
    if (!m) return;
    const [, gid, part, r, , c] = m;
    const g = grids[gid] = grids[gid] || {head: {}, body: {}};
    const bucket = part === 'head' ? g.head : (part === 'body' ? g.body : null);
    if (!bucket) return;
    const row = bucket[r] = bucket[r] || {};
    if (row[c] === undefined || row[c] === '') row[c] = clean(el.innerText);  // 바깥 div 를 우선
  });
  Object.entries(grids).forEach(([gid, g], i) => {
    const toRows = part => Object.keys(part).map(Number).sort((a, b) => a - b).map(r => {
      const cells = part[r];
      const n = Math.max(...Object.keys(cells).map(Number)) + 1;
      return Array.from({length: n}, (_, c) => cells[c] || '');
    });
    const rows = [...toRows(g.head), ...toRows(g.body)];
    if (rows.some(r => r.some(Boolean))) out.push({kind: 'nexacro', index: i, grid: gid.split('.').pop(), rows});
  });

  document.querySelectorAll('table').forEach((t, i) => {
    const rows = [...t.rows].map(r => [...r.cells].map(c => clean(c.innerText)));
    if (rows.some(r => r.some(Boolean))) out.push({kind: 'table', index: i, rows});
  });
  document.querySelectorAll('[role="grid"], [role="table"]').forEach((g, i) => {
    const rows = [...g.querySelectorAll('[role="row"]')].map(r =>
      [...r.querySelectorAll('[role="columnheader"], [role="gridcell"], [role="cell"]')]
        .map(c => clean(c.innerText)));
    if (rows.some(r => r.some(Boolean))) out.push({kind: 'grid', index: i, rows});
  });
  return out;
}
"""


def capture(page, save_html: bool, n: int) -> Path:
    """현재 탭의 모든 프레임(iframe 포함)에서 표를 읽어 raw/capture_XX.json 으로 저장."""
    frames = []
    for f_i, frame in enumerate(page.frames):
        try:
            tables = frame.evaluate(EXTRACT_TABLES_JS)
        except Exception as exc:  # 다른 사이트의 iframe 등은 읽을 수 없을 수 있다
            tables, err = [], str(exc)[:200]
        else:
            err = None
        url = frame.url.split("?")[0]  # 주소 뒤 파라미터(세션 값 등)는 저장하지 않는다
        frames.append({"frame": f_i, "url": url, "tables": tables, "error": err})
        if save_html and tables:
            (RAW_DIR / f"capture_{n:02d}_frame{f_i}.html").write_text(frame.content(), encoding="utf-8")

    out = RAW_DIR / f"capture_{n:02d}.json"
    out.write_text(json.dumps({
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "frames": frames,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    n_tables = sum(len(f["tables"]) for f in frames)
    n_rows = sum(len(t["rows"]) for f in frames for t in f["tables"])
    print(f"  저장: {out.name}  (표 {n_tables}개, 줄 {n_rows}개)")
    if n_rows == 0:
        print("  ! 표를 못 찾았어요. 교육과정 화면에서 '조회'를 누른 뒤인지 확인하고,"
              " 그래도 안 되면 --save-html 로 다시 실행해 주세요.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="GLS 교육과정 표 수집 (수동 로그인)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--save-html", action="store_true", help="디버깅용: 표가 있는 화면의 HTML도 저장")
    args = ap.parse_args()

    RAW_DIR.mkdir(exist_ok=True)
    start = len(list(RAW_DIR.glob("capture_*.json")))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()  # 저장된 쿠키 없음, 끝나면 사라짐
        page = context.new_page()
        page.goto(args.url)

        print("\n1) 열린 브라우저에서 직접 로그인하세요.")
        print("2) 정보광장 > 수업영역 > 교육과정 > 학사과정-교육과정 으로 이동하세요.")
        print("3) 교육과정년도·학과를 고르고 '조회'를 누른 뒤, 여기서 Enter 를 누르세요.")
        print("4) 표를 아래로 스크롤해서 새 과목이 보이면 Enter 를 또 누르세요. 끝까지 반복 (중복은 자동으로 걸러요).")
        print("5) 끝내려면 q 를 입력하고 Enter.\n")

        n = start
        while True:
            cmd = input("[Enter] 저장 / [q] 종료 > ").strip().lower()
            if cmd == "q":
                break
            n += 1
            # 사람이 새 탭에서 열었을 수도 있으니 가장 최근 탭을 읽는다
            capture(context.pages[-1], args.save_html, n)

        context.close()
        browser.close()

    print("\n끝. raw/ 폴더의 JSON 을 normalize.py 로 정리하세요. 브라우저 로그인 상태는 저장되지 않았어요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
