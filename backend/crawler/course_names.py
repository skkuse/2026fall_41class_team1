"""GLS 수집(gls/)과 학과 홈페이지 수집(homepage/)이 같이 쓰는 과목명 비교 규칙."""
from __future__ import annotations

import re

# 로드맵과 GLS 가 같은 과목을 다르게 적는 경우 (2026-11-24 실제 수집 결과에서 확인)
SPELLING = [
    ("네트웍", "네트워크"),   # GLS '컴퓨터네트웍개론' = 로드맵 '컴퓨터네트워크개론'
    ("sw", "소프트웨어"),     # 로드맵 '오픈소스SW실습' = GLS '오픈소스소프트웨어실습'
]


def name_key(name: str) -> str:
    """과목명 비교용 키: 공백·가운데점·괄호 내용 제거, Ⅰ/Ⅱ → 1/2, 표기 차이 통일."""
    s = (name or "").replace("Ⅰ", "1").replace("Ⅱ", "2").replace("Ⅲ", "3")
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s)
    s = re.sub(r"[\s·・\.\-_/]", "", s).lower()
    for a, b in SPELLING:
        s = s.replace(a, b)
    return s
