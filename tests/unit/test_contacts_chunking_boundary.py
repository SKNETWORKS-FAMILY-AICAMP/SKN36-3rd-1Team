"""scripts/build_clean_dataset.py의 iter_document_chunks 경계 보존 회귀 테스트 (#77).

750자 고정창 청킹이 [문의처] 블록(기관·전화번호 목록) 중간을 자르면 PolicyCheck.contacts가
반쪽짜리 번호를 돌려준다. DOC_GOV_029(문의처 2건)·DOC_GOV_048(문의처 32건)에서 실제로
재현됐던 잘림을, 실제 원본 JSON(data/raw/gov24/items/*.json)으로 다시 검증한다.
"""

import json
import unittest
from pathlib import Path

from collectors.preprocessing.normalize import format_gov24_item_text
from scripts.build_clean_dataset import iter_document_chunks

RAW_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "gov24" / "items"


def _naive_chunks(text: str, size: int, overlap: int) -> list[str]:
    """회귀 전 로직(순수 750자 고정창, 경계 보존 없음)과 같은 결과를 내는지 비교하는 기준선."""
    pieces = []
    start = 0
    while start < len(text):
        pieces.append(text[start:start + size].strip())
        if start + size >= len(text):
            break
        start += size - overlap
    return pieces


class ContactBoundaryPreservedTests(unittest.TestCase):
    def test_no_contact_block_matches_naive_fixed_window(self):
        # [문의처]가 없는 문서는 기존 단순 750/80 분할과 완전히 같아야 한다(회귀 방지).
        text = "본문 설명. " * 200
        self.assertEqual(list(iter_document_chunks(text, 750, 80)), _naive_chunks(text, 750, 80))

    def test_contact_block_fitting_in_one_window_is_unaffected(self):
        text = "앞부분 설명\n\n[문의처]\n보건복지상담센터 (129\n\n[출처 URL] https://example.org"
        self.assertEqual(list(iter_document_chunks(text, 750, 80)), [text.strip()])

    def test_contact_block_longer_than_window_is_kept_whole(self):
        # 문의처 블록 자체가 750자보다 길면(예: 32개 기관), 잘라도 전화번호가 안 끊기는 자리가
        # 없으므로 블록을 통째로 한 청크에 담는다.
        long_contacts = "||".join(f"기관{i}/02-000-{i:04d}" for i in range(40))
        text = "앞부분 설명\n\n[문의처]\n" + long_contacts + "\n\n[출처 URL] https://example.org"
        pieces = list(iter_document_chunks(text, 750, 80))
        self.assertTrue(any(long_contacts in piece for piece in pieces))
        for piece in pieces:
            self.assertNotRegex(piece, r"02-000-\d{1,3}$")  # 번호가 중간에서 끊기지 않는다

    def test_doc_gov_029_contacts_no_longer_truncated(self):
        with open(RAW_DIR / "359000000104.json", encoding="utf-8") as f:
            item = json.load(f)
        pieces = [p for p in iter_document_chunks(format_gov24_item_text(item), 750, 80) if len(p) >= 50]
        self.assertEqual(len(pieces), 2)
        self.assertIn(
            "[문의처] 동구(청소년수련관)청소년방과후아카데미/062-233-7224||동구(청소년문화의집)청소년방과후 아카데미/062-251-1318",
            pieces[1],
        )

    def test_doc_gov_048_contacts_no_longer_truncated(self):
        with open(RAW_DIR / "449000000108.json", encoding="utf-8") as f:
            item = json.load(f)
        pieces = [p for p in iter_document_chunks(format_gov24_item_text(item), 750, 80) if len(p) >= 50]
        self.assertEqual(len(pieces), 3)
        contact_piece = pieces[1]
        self.assertIn("여성가족과/041-521-5374", contact_piece)
        # 원본에 실제로 있는 32건 중 마지막 기관(부성2동)까지 안 끊기고 들어있어야 한다.
        self.assertIn("부성2동(주민복지팀)/041-521-6703", contact_piece)


if __name__ == "__main__":
    unittest.main()
