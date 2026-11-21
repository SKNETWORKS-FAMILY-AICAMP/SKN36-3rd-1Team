"""#104 검수(이슈 #103) 회귀: 공식 FAQ의 충돌·옛 기한 행 보류와 모두의카드 표 행·열 보존.

DB·LLM 없이 후보 릴리스 파일과 실제 B2/B3 청킹 결과로 확인한다.
"""

import json
import unittest
from pathlib import Path

from database import chunking
from database.release_adapter import load_release_documents
from scripts import data_corrections as dc
from scripts import faq_table_text

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "data/releases/v1.4.0-candidate"
SEOUL_DOC = "DOC_STG_SEOUL_001_FAQ_2026"
KPASS_DOC = "DOC_STG_MOLIT_001_FAQ_2026"
INTRO_DOC = "DOC_STG_SEOUL_001_INTRO_2026"  # 보류 항목의 기준: 공식 소개 탭
SEOUL_STAGING = ROOT / "data/staging/official/faq/youth_seoul_allowance_faq_20261118.txt"
# 답변 근거로 무경고 전달되면 안 되는 문구 (검수에서 B2 C002·C016으로 재현)
SHORT_TERM_EXCLUSION = "③ 주 30시간 이하 또는 3개월 이하의 단기근로자(취업자)"
OLD_RETENTION_DEADLINE = "2025.12.31.까지 보관 필요"


def release_chunks() -> list[dict]:
    with (PACKAGE / "qdrant/payload_chunks.jsonl").open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


class FaqReviewHoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        documents = load_release_documents(PACKAGE, ROOT)
        cls.documents = {d["document_id"]: d for d in documents if d["document_id"] in (SEOUL_DOC, KPASS_DOC, INTRO_DOC)}
        selected = list(cls.documents.values())
        cls.strategy_chunks = {strategy: chunking.chunk_documents(selected, strategy=strategy)[0]
                               for strategy in ("B2", "B3")}
        cls.release = release_chunks()

    def texts(self, document_id: str) -> dict[str, list[str]]:
        found = {name: [c["text"] for c in chunks if c["document_id"] == document_id]
                 for name, chunks in self.strategy_chunks.items()}
        found["release"] = [c["text"] for c in self.release if c["document_id"] == document_id]
        return found

    def test_held_seoul_lines_never_reach_search_chunks(self):
        for source, texts in self.texts(SEOUL_DOC).items():
            self.assertTrue(texts, source)
            for phrase in (SHORT_TERM_EXCLUSION, OLD_RETENTION_DEADLINE):
                self.assertFalse([t for t in texts if phrase in t], f"{source}: {phrase}")
        # 같은 FAQ의 다른 항목(단기근로자 신청 가능 안내)은 남는다
        self.assertTrue(any("단기근로를 하고 있습니다. 신청 가능한가요?" in t for t in self.texts(SEOUL_DOC)["B2"]))

    def test_intro_tab_is_the_searchable_basis(self):
        # 팀 결정(2026-11-21): 단기근로자 자격과 증빙 보관 기한은 소개 탭 기준
        for source, texts in self.texts(INTRO_DOC).items():
            joined = " ".join(texts)
            self.assertIn("단, 주30시간 이하 또는 3개월 이하 단기근로자 신청 가능", joined, source)
            self.assertIn("증빙자료를 2026.12.31.까지 보관 바람", joined, source)
        for item in dc.load_corrections()["held_document_lines"]:
            self.assertEqual(item["superseded_by_document_id"], INTRO_DOC)

    def test_original_faq_text_is_preserved(self):
        original = SEOUL_STAGING.read_text(encoding="utf-8")
        self.assertIn(SHORT_TERM_EXCLUSION, original)
        self.assertIn(OLD_RETENTION_DEADLINE, original)
        self.assertNotIn(SHORT_TERM_EXCLUSION, self.documents[SEOUL_DOC]["text"])

    def test_held_items_are_recorded_as_needs_review(self):
        held = dc.load_corrections()["held_document_lines"]
        self.assertEqual({item["document_id"] for item in held}, {SEOUL_DOC})
        self.assertEqual(len(held), 2)
        for item in held:
            self.assertEqual(item["status"], "needs_review")
            self.assertTrue(item["reason"] and item["evidence"])
        delta = json.loads((PACKAGE / "release_delta_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(delta["data_corrections"]["held_line_dropped_chunks"], ["DOC_STG_SEOUL_001_FAQ_2026_C11"])
        self.assertNotIn("DOC_STG_SEOUL_001_FAQ_2026_C11", {c["chunk_id"] for c in self.release})

    def test_kpass_tables_keep_rows_and_columns(self):
        tables = faq_table_text.load_tables()
        text = self.documents[KPASS_DOC]["text"]
        rows = []
        for number, table in enumerate(tables, start=1):
            rendered = faq_table_text.render_table(number, table)
            self.assertIn(rendered, text)
            self.assertNotIn(faq_table_text.flat_text(table), text)
            rows += rendered.split("\n")[1:]
        self.assertEqual(len(rows), 28)
        self.assertIn("시·도 강원: 특별지원지역 양구군, 화천군 | 우대지원지역 고성군, 삼척시, 양양군, 영월군,정선군, 철원군, "
                      "태백시, 평창군,홍천군, 횡성군 | 일반지방권 강릉시, 동해시, 속초시,원주시, 인제군, 춘천시", rows)
        # 청크가 표 중간에서 나뉘어도, 행 하나는 어느 청크에든 통째로 들어 있어야 한다
        for source, texts in self.texts(KPASS_DOC).items():
            for row in rows:
                self.assertTrue(any(row in t for t in texts), f"{source}: {row[:40]}")


if __name__ == "__main__":
    unittest.main()
