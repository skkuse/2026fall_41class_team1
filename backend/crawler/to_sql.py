"""courses 형식 CSV 를 Supabase SQL Editor 에 붙여 넣을 수 있는 INSERT 문으로 바꾼다.

같은 (roadmap_version, name, category) 가 이미 있으면 새 값으로 덮어쓴다(upsert).
빈 칸은 NULL 로 넣는다.

사용법
    python to_sql.py ../db/seed_courses_2026.csv  > ../db/seed_courses_2026.sql
    python to_sql.py out/courses_merged.csv       > out/courses_upsert.sql
"""
from __future__ import annotations

import csv
import sys

COLUMNS = ["roadmap_version", "name", "category", "course_code", "name_en", "credits", "gls_years",
           "roadmap_year", "roadmap_term", "field_main", "modifier", "input_method", "source",
           "collected_at", "note"]
NUMERIC = {"credits", "roadmap_year", "roadmap_term"}


def literal(col: str, value: str) -> str:
    value = (value or "").strip()
    if value == "":
        return "null"
    if col in NUMERIC:
        float(value)  # 숫자가 아니면 여기서 에러가 나서 잘못된 데이터를 막는다
        return value
    return "'" + value.replace("'", "''") + "'"


def csv_to_sql(path: str) -> str:
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    values = ",\n".join(
        "  (" + ", ".join(literal(c, r.get(c, "")) for c in COLUMNS) + ")" for r in rows
    )
    # 학수번호·학점·수집일은 새 값이 비어 있으면 기존 값을 지우지 않는다
    # (시드를 다시 넣어도 GLS 에서 채운 값이 사라지지 않게)
    keep_old = {"course_code", "name_en", "credits", "gls_years", "collected_at"}
    # 입력 방식·출처도, 새 행에 학수번호가 없고 기존 행에 있으면(=시드를 다시 넣는 경우) 기존 값을 유지
    keep_if_reseed = {"input_method", "source"}

    def update(c: str) -> str:
        if c in keep_old:
            return f"{c} = coalesce(excluded.{c}, courses.{c})"
        if c in keep_if_reseed:
            return (f"{c} = case when excluded.course_code is null and courses.course_code is not null "
                    f"then courses.{c} else excluded.{c} end")
        return f"{c} = excluded.{c}"

    updates = ", ".join(update(c) for c in COLUMNS if c not in ("roadmap_version", "name", "category"))
    return (f"-- generated from {path} ({len(rows)} rows)\n"
            f"insert into public.courses ({', '.join(COLUMNS)}) values\n{values}\n"
            f"on conflict (roadmap_version, name, category) do update set {updates};\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    sys.stdout.write(csv_to_sql(sys.argv[1]))
