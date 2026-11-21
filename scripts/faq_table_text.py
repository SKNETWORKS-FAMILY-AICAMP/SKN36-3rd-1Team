"""FAQ 추출본에서 붙어 버린 표 텍스트를 행·열이 보이는 줄로 바꾼다.

화면 텍스트로 저장한 FAQ는 표의 셀이 한 줄로 이어져 있어 행·열 관계가 사라진다.
공식 상세 화면의 표 HTML에서 옮긴 셀 값(JSON)을 받아, 이어진 셀 텍스트가 추출본에
정확히 한 번 있을 때만 그 자리를 표 줄로 바꾼다. 값은 추가·수정하지 않는다.

  python scripts/faq_table_text.py            # 추출본을 바꾼다 (이미 바뀌었으면 그대로 둔다)
  python scripts/faq_table_text.py --check    # 바뀐 상태인지 확인만 한다
"""

import argparse
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
TEXT_PATH = BASE_DIR / "data/staging/official/faq/korea-pass_faq_20261118.txt"
TABLES_PATH = BASE_DIR / "data/staging/official/faq/korea-pass_faq_item1_tables_20261121.json"
EMPTY_CELL = "(빈 칸)"


def load_tables(path: Path = TABLES_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["tables"]


def flat_text(table: dict) -> str:
    """화면 텍스트에 남은 형태: 머리글 셀을 HTML 순서대로, 이어서 본문 셀을 그대로 붙인 문자열."""
    columns = table["columns"]
    if table["header_rows"] == 1:
        head = "".join(columns)
    else:
        top, bottom = [], []
        for column in columns:
            upper, _, lower = column.partition(" > ")
            if not lower or upper not in top:
                top.append(upper)
            if lower:
                bottom.append(lower)
        head = "".join(top) + "".join(bottom)
    return head + "".join("".join(row) for row in table["rows"])


def render_table(number: int, table: dict) -> str:
    """머리글 줄 하나와, 열 이름을 셀마다 붙인 행 줄. 청크가 표 중간에서 나뉘어도 행 줄만으로 읽힌다."""
    columns = table["columns"]
    prefix = f"{table['title']} — " if table["title"] else f"{columns[0]} "
    lines = [f"[표 {number}] 열: " + " | ".join(columns)]
    for row in table["rows"]:
        if len(row) != len(columns):
            raise ValueError(f"표 {number}의 행 길이가 열 수와 다릅니다: {row[0]}")
        cells = " | ".join(f"{name} {value or EMPTY_CELL}" for name, value in zip(columns[1:], row[1:]))
        lines.append(f"{prefix}{row[0]}: {cells}")
    return "\n".join(lines)


def apply_tables(text: str, tables: list[dict]) -> str:
    for number, table in enumerate(tables, start=1):
        flat, rendered = flat_text(table), render_table(number, table)
        if text.count(rendered) == 1 and flat not in text:
            continue
        if text.count(flat) != 1:
            raise ValueError(f"표 {number}의 셀 텍스트를 추출본에서 한 번만 찾을 수 있어야 합니다")
        text = text.replace(flat, f"\n{rendered}\n")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = TEXT_PATH.read_text(encoding="utf-8")
    updated = apply_tables(text, load_tables())
    if args.check:
        print("OK" if updated == text else "NEEDS_UPDATE")
        return 0 if updated == text else 1
    TEXT_PATH.write_text(updated, encoding="utf-8", newline="\n")
    print("updated" if updated != text else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
