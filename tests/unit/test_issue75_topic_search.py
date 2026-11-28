"""#75: 여러 분야를 나열한 질문의 분야별 검색 단위 테스트.

'교육비·구직·교통 지원 정책이 있나요?'를 한 번에 검색하면 교육 결과가 상위를 차지했다(#122 최종 회차 R3D 재현).
정책·청크는 가상 값이다(sample_only).
"""

import unittest

from backend.rag.context_builder import rank_citable_contexts, select_contexts
from backend.rag.pipeline import _topic_searches, search_policies


def topic_queries(question):
    return [query for _, query in _topic_searches(question)]


# 가상 원문에 분야 낱말을 넣는다. 분야별 결과는 낱말이 실제로 든 정책만 남기기 때문이다.
WORD = {"EDU": "교육비", "JOB": "구직", "BUS": "교통", "TEL": "통신", "RENT": "월세"}


def policy(pid, rank):
    word = next((value for prefix, value in WORD.items() if pid.startswith(prefix)), "")
    return {"policy_id": pid, "policy_name": f"가상 {pid}", "best_rank": rank, "graph": {"documents": []},
            "evidence": [{"rank": rank, "document_id": f"DOC_{pid}", "chunk_id": f"CHK_{pid}",
                          "text": f"{pid} {word} 원문", "source_url": f"https://example.org/{pid}"}]}


class TopicQueriesTests(unittest.TestCase):
    def test_listed_topics_become_separate_queries(self):
        cases = {
            "교육비·구직·교통 지원 정책이 있나요?": ["교육비 지원 정책이 있나요?", "구직 지원 정책이 있나요?", "교통 지원 정책이 있나요?"],
            "교육비, 구직, 교통 지원 있어?": ["교육비 지원 있어?", "구직 지원 있어?", "교통 지원 있어?"],
            "주거 및 생활 지원 정책": ["주거 지원 정책", "생활 지원 정책"],
            "출산·육아·주거·교육·의료 지원": ["출산 지원", "육아 지원", "주거 지원", "교육 지원", "의료 지원"],
            # 한글 입력기 가운뎃점(ㆍ)과 붙임표가 든 이름(#75 리뷰).
            "교육비ㆍ교통비 지원": ["교육비 지원", "교통비 지원"],
            "K-패스·기후동행카드 지원 비교": ["K-패스 지원 비교", "기후동행카드 지원 비교"],
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(topic_queries(question), expected)

    def test_single_topic_or_unrelated_phrasing_is_not_split(self):
        for question in ("청년 월세 지원 알려줘", "교통비 지원 받을 수 있나요?", "검사 결과 지원 정책", "학과 장학 지원",
                         "관련 및 기타 지원", "K-패스와 모두의카드 차이가 뭐야?", "청년과 신혼부부 주거 지원",
                         "출산·육아·주거·교육·의료·교통 지원",
                         # 조사 '와·과·이나·랑'은 낱말 끝과 구분할 수 없어 나누지 않는다(#75 리뷰: R3D 결과 악화 재현).
                         "월세와 전세 지원 알려줘", "취업이나 창업 지원 사업 뭐 있어?", "산부인과 진료 지원 있어?",
                         "소아청소년과 진료비 지원", "아이와 함께 지원 받을 수 있는 정책",
                         "취업 지원 및 창업 지원 사업", "청년 월세 지원과 교통비 지원 둘 다 받을 수 있어?",
                         # 저장소 평가 질문: '부모급여'의 '급여'는 끝말이 아니다(명칭을 묻는 질문은 나누지 않는다).
                         "출생신고 시 한 번에 첫만남이용권, 부모급여, 전기요금 감면을 일괄 신청하는 서비스 명칭과 절차는?"):
            with self.subTest(question=question):
                self.assertEqual(topic_queries(question), [])


class SearchPoliciesTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        results = {
            "교육비 지원 정책이 있나요?": [policy("EDU1", 1), policy("EDU2", 2)],
            "구직 지원 정책이 있나요?": [policy("JOB1", 1), policy("EDU1", 2)],
            "교통 지원 정책이 있나요?": [policy("BUS1", 1)],
            "교육비·구직·교통 지원 정책이 있나요?": [policy("EDU1", 1), policy("EDU3", 2), policy("JOB1", 3)],
            "청년 월세 지원 알려줘": [policy("RENT", 1)],
        }

        def search(query, filters=None, k=10):
            self.calls.append((query, filters, k))
            return {"policies": results[query]}
        self.search = search

    def test_missing_topics_are_filled_after_first_result(self):
        found = search_policies(self.search, "교육비·구직·교통 지원 정책이 있나요?", {"region": "서울특별시"})
        # 교육비·구직은 원래 결과에 이미 있다(분야 검색 상위가 보임). 빠진 교통만 원래 1위 바로 뒤에 넣는다.
        self.assertEqual([p["policy_id"] for p in found], ["EDU1", "BUS1", "EDU3", "JOB1"])
        self.assertEqual([p["best_rank"] for p in found], [1, 2, 3, 4])
        # 지역 필터는 분야별 검색에도 그대로 쓴다. 원래 질문 1회 + 분야 3회.
        self.assertTrue(all(call[1] == {"region": "서울특별시"} for call in self.calls))
        self.assertEqual(len(self.calls), 4)
        contexts = select_contexts(rank_citable_contexts(found))
        self.assertEqual([c["policy_id"] for c in contexts], ["EDU1", "BUS1", "EDU3", "JOB1"])

    def test_pick_prefers_policy_named_with_topic(self):
        # R3D 재현: '교통 지원' 검색 1위가 '이동통신요금감면', 정책명에 '교통'이 든 정책은 3위였다.
        def search(query, filters=None, k=10):
            if query.startswith("교통"):
                return {"policies": [{**policy("TEL", 1), "policy_name": "가상 이동통신요금감면"},
                                     {**policy("BUS", 2), "policy_name": "가상 대중교통비 환급"}]}
            return {"policies": [{**policy("EDU", 1), "policy_name": "가상 교육비 지원"}]}
        found = search_policies(search, "교육비·교통 지원 정책이 있나요?", None)
        self.assertEqual([p["policy_id"] for p in found], ["EDU", "BUS"])

    def test_topic_covered_by_name_word_is_not_filled(self):
        # 원래 결과 정책명에 분야 이름으로 시작하는 낱말('의료급여')이 있으면 이미 다룬 분야로 본다.
        # '국토교통부'처럼 낱말 중간에 든 '교통'은 다룬 것으로 보지 않는다(R3D '월세·교통' 재현).
        calls = []

        def search(query, filters=None, k=10):
            calls.append(query)
            if "·" in query:
                return {"policies": [{**policy("MED", 1), "policy_name": "가상 의료급여 진료비 지원"},
                                     {**policy("RENT", 2), "policy_name": "국토교통부 가상 청년월세"}]}
            if query.startswith("의료"):
                return {"policies": [{**policy("MED2", 1), "policy_name": "가상 의료급여(요양비)"}]}
            return {"policies": [{**policy("BUS", 1), "policy_name": "가상 대중교통비 환급"}]}
        found = search_policies(search, "의료·교통 지원 정책", None)
        self.assertEqual([p["policy_id"] for p in found], ["MED", "BUS", "RENT"])
        # 정책명으로 이미 다룬 분야(의료)는 분야 검색을 하지 않는다.
        self.assertEqual(calls, ["의료·교통 지원 정책", "교통 지원 정책"])

    def test_common_overlap_without_topic_name_does_not_cover_topic(self):
        # #75 3차 리뷰: '긴급돌봄'처럼 어느 분야 검색에나 나오는 정책이 겹쳐도, 정책명에 '교통'이 없으면 다룬 분야가 아니다.
        def search(query, filters=None, k=10):
            if "·" in query:
                return {"policies": [{**policy("MED", 1), "policy_name": "가상 의료비 지원"},
                                     {**policy("CARE", 2), "policy_name": "가상 긴급돌봄"}]}
            if query.startswith("교통"):
                return {"policies": [{**policy("CARE", 1), "policy_name": "가상 긴급돌봄"},
                                     {**policy("BUS", 2), "policy_name": "가상 대중교통비 환급"}]}
            return {"policies": [{**policy("MED", 1), "policy_name": "가상 의료비 지원"}]}
        found = search_policies(search, "의료·교통 지원 사업", None)
        self.assertEqual([p["policy_id"] for p in found], ["MED", "BUS", "CARE"])

    def test_agency_name_is_not_a_topic_match_for_pick(self):
        # #75 3차 리뷰: 보충 정책을 고를 때 '국토교통부' 같은 기관 이름 속 '교통'은 일치로 보지 않는다.
        def search(query, filters=None, k=10):
            if query.startswith("교통"):
                return {"policies": [{**policy("MOLIT", 1), "policy_name": "국토교통부 가상 청년 전세"},
                                     {**policy("BUS", 2), "policy_name": "가상 대중교통비 환급"}]}
            return {"policies": [{**policy("RENT", 1), "policy_name": "가상 청년월세"}]}
        found = search_policies(search, "월세·교통 지원 정책", None)
        self.assertEqual([p["policy_id"] for p in found], ["RENT", "BUS"])

    def test_all_topics_covered_or_skipped_returns_whole_result_unchanged(self):
        whole = [policy("A", 1), policy("B", 2)]

        def search(query, filters=None, k=10):
            if "·" in query:  # 원래 질문
                return {"policies": whole}
            return {"policies": [{**policy("X", 1), "policy_name": "가상 무관 정책"}]}  # 분야 낱말 없음
        self.assertIs(search_policies(search, "가상갑·가상을 지원 정책", None), whole)

    def test_topic_missing_from_data_adds_no_unrelated_policy(self):
        # R3D 재현: 데이터에 없는 '기후동행카드' 검색은 무관한 정책만 돌려줬다. 그 분야는 채우지 않는다.
        def search(query, filters=None, k=10):
            if query.startswith("기후동행카드"):
                return {"policies": [{**policy("ENERGY", 1), "policy_name": "가상 에너지바우처"}]}
            return {"policies": [{**policy("KPASS", 1), "policy_name": "가상 K-패스"}]}
        found = search_policies(search, "K-패스·기후동행카드 지원 비교", None)
        self.assertEqual([p["policy_id"] for p in found], ["KPASS"])

    def test_search_results_are_not_mutated(self):
        original = policy("EDU1", 1)
        bus = policy("BUS1", 1)

        def search(query, filters=None, k=10):
            return {"policies": [bus] if query.startswith("교통") else [original]}
        found = search_policies(search, "교육비·교통 지원 정책", None)
        self.assertEqual([p["evidence"][0]["rank"] for p in found], [1001, 2001])  # 순위를 다시 매겼다.
        self.assertEqual((original["best_rank"], original["evidence"][0]["rank"]), (1, 1))
        self.assertEqual((bus["best_rank"], bus["evidence"][0]["rank"]), (1, 1))

    def test_single_topic_question_searches_once(self):
        found = search_policies(self.search, "청년 월세 지원 알려줘", None)
        self.assertEqual([p["policy_id"] for p in found], ["RENT"])
        self.assertEqual(len(self.calls), 1)


class PipelineSearchTests(unittest.TestCase):
    """run_ask_pipeline이 분야별 검색을 쓰는지, 선택 정책 질문은 나누지 않는지 확인한다."""

    def run_pipeline(self, question, selected=None):
        from backend.api.schemas import AskRequest
        from backend.rag.pipeline import run_ask_pipeline
        calls = []

        def search(query, filters=None, k=10):
            calls.append(query)
            pid = {"교육비": "EDU1", "구직": "JOB1", "교통": "BUS1"}.get(query.split()[0], "EDU1")
            return {"policies": [policy(pid, 1)]}

        def check(profile, found):
            return {"policy_id": found["policy_id"], "reception_status": "unknown",
                    "conditions": [{"field": "eligibility", "label": "자격 요건", "status": "needs_check",
                                    "reason": "가상 조건은 확인이 필요합니다."}]}

        def generate(question, contexts, checks, history, **kwargs):
            return {"status": "answered", "answer": "가상 답변입니다 [1].", "used_source_ids": [1],
                    "follow_up_questions": []}

        request = {"question": question, "profile": {"age": 29, "region": "", "income": None}, "history": []}
        if selected:
            request["selected_policy_id"] = selected
        response = run_ask_pipeline(AskRequest(**request), search_fn=search, check_fn=check, generate_fn=generate)
        return calls, response

    def test_multi_topic_question_uses_topic_searches(self):
        calls, response = self.run_pipeline("교육비·구직·교통 지원 정책이 있나요?")
        self.assertEqual(calls, ["교육비·구직·교통 지원 정책이 있나요?", "교육비 지원 정책이 있나요?",
                                 "구직 지원 정책이 있나요?", "교통 지원 정책이 있나요?"])
        self.assertEqual([c.policy_id for c in response.policy_checks], ["EDU1", "JOB1", "BUS1"])

    def test_selected_policy_question_is_not_split(self):
        calls, _ = self.run_pipeline("교육비·구직·교통 지원 정책이 있나요?", selected="POL_EDU1")
        self.assertEqual(calls, ["교육비·구직·교통 지원 정책이 있나요?"])


if __name__ == "__main__":
    unittest.main()
