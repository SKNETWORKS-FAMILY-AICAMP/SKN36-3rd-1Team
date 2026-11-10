from copy import deepcopy
import unittest

from backend.rag.context_builder import build_contexts


def hit(rank, chunk_id, **changes):
    return {
        "rank": rank, "chunk_id": chunk_id, "document_id": "D",
        "text": " 원문 ", "source_url": "https://example.org/notice", **changes,
    }


class ContextBuilderTests(unittest.TestCase):
    def test_policy_balance_rank_metadata_and_input_preservation(self):
        policies = [
            {"policy_id": "B", "policy_name": "정책 B", "best_rank": 3,
             "graph": None, "evidence": [hit(3, "b")]},
            {"policy_id": "A", "policy_name": "정책 A", "best_rank": 1,
             "graph": {"documents": [{"document_id": "D", "title": "공고"}]},
             "data_quality": {"warnings": [{"message": "지역 확인"}]},
             "evidence": [hit(2, "a2"), hit(1, "a1", page_start=2), hit(4, "a2")]},
        ]
        original = deepcopy(policies)
        contexts = build_contexts(policies, 3)
        self.assertEqual([c["chunk_id"] for c in contexts], ["a1", "b", "a2"])
        self.assertEqual([c["source_id"] for c in contexts], [1, 2, 3])
        self.assertEqual(build_contexts(policies, 2), contexts[:2])
        self.assertEqual(contexts[0], {
            "source_id": 1, "policy_id": "A", "policy_name": "정책 A",
            "document_id": "D", "chunk_id": "a1", "title": "공고",
            "url": "https://example.org/notice", "page_number": 2,
            "text": "원문", "quality_warnings": ["지역 확인"],
        })
        self.assertEqual(contexts[1]["title"], "정책 B")
        self.assertIsNone(contexts[1]["page_number"])
        self.assertEqual(policies, original)

    def test_invalid_evidence_and_duplicate_excluded(self):
        missing_url = hit(2, "missing")
        del missing_url["source_url"]
        policy = {"policy_id": "A", "policy_name": "A", "graph": None, "evidence": [
            hit(1, "blank", text=" "), missing_url, hit(3, "bad", source_url="https://"),
            hit(5, "same", text="later"), hit(4, "same", page_start=True),
        ]}
        contexts = build_contexts([policy])
        self.assertEqual([(c["chunk_id"], c["text"], c["page_number"]) for c in contexts],
                         [("same", "원문", None)])
        self.assertEqual(build_contexts([policy], 0), [])

    def test_deprioritized_policies_fill_only_remaining_slots(self):
        def policy(pid, best_rank, *ranks):
            return {"policy_id": pid, "policy_name": pid, "best_rank": best_rank, "graph": None,
                    "evidence": [hit(rank, f"{pid}{rank}") for rank in ranks]}

        policies = [policy("A", 1, 1, 5), policy("B", 2, 2, 6), policy("C", 3, 3)]
        original = deepcopy(policies)
        self.assertEqual([c["chunk_id"] for c in build_contexts(policies, 5)], ["A1", "B2", "C3", "A5", "B6"])
        # A가 마감이면 B·C의 첫 원문과 추가 원문을 먼저 채우고, A는 남은 자리에만 들어간다.
        later = build_contexts(policies, 5, deprioritized_policy_ids={"A"})
        self.assertEqual([c["chunk_id"] for c in later], ["B2", "C3", "B6", "A1", "A5"])
        self.assertEqual([c["source_id"] for c in later], [1, 2, 3, 4, 5])
        self.assertEqual([c["chunk_id"] for c in build_contexts(policies, 3, deprioritized_policy_ids={"A"})],
                         ["B2", "C3", "B6"])
        # 마감 정책끼리는 기존 순서를 유지하고, 모두 마감이면 기존 결과와 같다.
        self.assertEqual([c["chunk_id"] for c in build_contexts(policies, 5, deprioritized_policy_ids={"A", "B"})],
                         ["C3", "A1", "B2", "A5", "B6"])
        self.assertEqual(build_contexts(policies, 5, deprioritized_policy_ids={"A", "B", "C"}),
                         build_contexts(policies, 5))
        # 목록에 없는 ID는 무시하고, 입력은 바꾸지 않는다.
        self.assertEqual(build_contexts(policies, 5, deprioritized_policy_ids={"Z"}), build_contexts(policies, 5))
        self.assertEqual(policies, original)
