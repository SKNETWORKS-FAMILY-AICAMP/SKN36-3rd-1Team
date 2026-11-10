from copy import deepcopy
import random
import unittest

from backend.rag.context_builder import build_contexts, rank_citable_contexts, select_contexts, valid_source_url


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

    def test_reused_candidates_match_previous_single_pass_selection(self):
        # 후보를 한 번 만들어 재사용해도, 리팩터링 전 한 번에 계산하던 결과와 같아야 한다.
        rng = random.Random(23)
        for _ in range(300):
            policies = []
            for index in range(rng.randint(0, 7)):
                pid = f"P{index}"
                hits = []
                for _ in range(rng.randint(0, 4)):
                    chunk = f"C{rng.randint(0, 9)}"  # 정책 간 청크 중복도 생긴다.
                    changes = rng.choice([{}, {}, {"text": " "}, {"source_url": "https://"}, {"page_start": 3}])
                    hits.append(hit(rng.randint(1, 30), chunk, **changes))
                policies.append({"policy_id": pid, "policy_name": pid, "best_rank": rng.randint(1, 30),
                                 "graph": None, "evidence": hits})
            later = {p["policy_id"] for p in policies if rng.random() < 0.4}
            size = rng.randint(0, 6)
            expected = previous_build_contexts(policies, size, later)
            with self.subTest(policies=policies, later=later, size=size):
                self.assertEqual(build_contexts(policies, size, later), expected)
                ranked = rank_citable_contexts(policies)
                # 같은 후보에서 판정 대상 선정과 최종 선택을 반복해도 결과는 같다.
                self.assertEqual({c["policy_id"] for c in ranked},
                                 {c["policy_id"] for c in previous_build_contexts(policies, 10 ** 6, ())})
                self.assertEqual(select_contexts(ranked, size, later), expected)
                self.assertEqual(select_contexts(ranked, size, later), expected)


def previous_build_contexts(policies, max_contexts, later):
    """리팩터링 전 build_contexts 알고리즘(비교 기준). 검증·중복 제거·정렬·후순위를 한 번에 수행했다."""
    if max_contexts <= 0:
        return []
    candidates = []
    for policy in policies:
        for item in policy.get("evidence", []):
            content, url = item.get("text"), item.get("source_url")
            if not isinstance(content, str) or not content.strip() or not valid_source_url(url):
                continue
            page = item.get("page_start")
            context = {"policy_id": policy["policy_id"], "policy_name": policy["policy_name"],
                       "document_id": item["document_id"], "chunk_id": item["chunk_id"],
                       "title": policy["policy_name"], "url": url,
                       "page_number": page if type(page) is int and page > 0 else None,
                       "text": content.strip(), "quality_warnings": []}
            candidates.append((item["rank"], policy.get("best_rank", item["rank"]), context))
    seen_chunks, seen_policies, first, rest = set(), set(), [], []
    for rank, best_rank, context in sorted(candidates, key=lambda entry: entry[0]):
        if context["chunk_id"] in seen_chunks:
            continue
        seen_chunks.add(context["chunk_id"])
        if context["policy_id"] in seen_policies:
            rest.append(context)
        else:
            first.append((best_rank, rank, context))
            seen_policies.add(context["policy_id"])
    ordered = [entry[2] for entry in sorted(first, key=lambda entry: entry[:2])] + rest
    selected = sorted(ordered, key=lambda context: context["policy_id"] in later)
    return [{"source_id": index, **context} for index, context in enumerate(selected[:max_contexts], 1)]
