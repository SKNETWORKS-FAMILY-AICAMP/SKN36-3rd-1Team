"""지역 데이터 계약 검사기 회귀 테스트 (DB·네트워크 없음).

1) 규칙별 정상/오류 가상 사례 (sample_only)
2) 실제 Issue #21 후보: 감사(docs/development/experiments/region_audit_20261107)에서 확정한 오류 15개를
   모두 검출하는지, 그 밖의 새 오류가 생기지 않는지(재발 방지 게이트)
"""
import unittest
from pathlib import Path

from database import region_contract as rc

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "data/candidates/issue21-v1.4.0-rag"
VOCAB = set(rc.SIDO_NAMES) | {rc.NATIONAL}


def view(**overrides):
    base = dict(policy_id="POL_SAMPLE", region="충청남도", region_detail=None, agency="충청남도",
                eligibility_text="sample_only 청년", source_codes=None, available_in=["충청남도"],
                payload_regions=["충청남도"], metadata_region="충청남도")
    base.update(overrides)
    return rc.RegionPolicyView(**base)


def codes(findings):
    return {f.code for f in findings}


class RuleCaseTests(unittest.TestCase):
    def test_valid_province_and_national_policies_pass(self):
        self.assertEqual(codes(rc.check_policy(view(), VOCAB)), set())
        national = view(region="전국", agency="보건복지부", available_in=["전국"], payload_regions=["전국"],
                        metadata_region="전국", source_codes=["11000", "26000", "27000", "28000", "29000", "30000",
                                                               "31000", "36110", "41000", "42000", "43000", "44000",
                                                               "45000", "46000", "47000", "48000", "50000"])
        self.assertEqual(codes(rc.check_policy(national, VOCAB)), set())

    def test_unregistered_code_must_not_become_nationwide(self):
        bad = view(region="전국", source_codes=["12830"], available_in=["전국"], payload_regions=["전국"],
                   metadata_region="전국")
        found = codes(rc.check_policy(bad, VOCAB, lambda v, r: "TRUE"))
        self.assertIn("UNREGISTERED_CODE_AS_NATIONWIDE", found)
        self.assertIn("UNVERIFIED_REGION_TRUE", found)
        # 미등록 코드여도 지역을 전국으로 바꾸지 않았다면 공식 확인 대상일 뿐 오류가 아니다
        pending = view(region="충청남도", source_codes=["12830", "44130"])
        self.assertEqual({f.severity for f in rc.check_policy(pending, VOCAB) if f.rule == "R1"}, {"needs_official_check"})

    def test_single_municipality_code_requires_detail(self):
        bad = view(source_codes=["44130"])
        self.assertIn("MUNICIPALITY_DETAIL_MISSING", codes(rc.check_policy(bad, VOCAB)))
        ok = view(source_codes=["44130"], region_detail="충청남도 천안시")
        self.assertNotIn("MUNICIPALITY_DETAIL_MISSING", codes(rc.check_policy(ok, VOCAB)))
        sejong = view(region="세종특별자치시", source_codes=["36110"], available_in=["세종특별자치시"],
                      payload_regions=["세종특별자치시"], metadata_region="세종특별자치시")
        self.assertNotIn("MUNICIPALITY_DETAIL_MISSING", codes(rc.check_policy(sejong, VOCAB)))

    def test_text_evidence_vs_agency_only(self):
        text = view(agency="충청남도 천안시", eligibility_text="○ 6개월 이상 천안시에 주민등록을 두고 거주")
        self.assertIn("MUNICIPALITY_DETAIL_MISSING", codes(rc.check_policy(text, VOCAB)))
        local = view(agency="대전광역시 동구", region="대전광역시", available_in=["대전광역시"],
                     payload_regions=["대전광역시"], metadata_region="대전광역시",
                     eligibility_text="부모 등이 대청동에 거주하는 주민")
        self.assertIn("MUNICIPALITY_DETAIL_MISSING", codes(rc.check_policy(local, VOCAB)))
        agency_only = view(agency="경상북도 포항시", region="경상북도", available_in=["경상북도"],
                           payload_regions=["경상북도"], metadata_region="경상북도",
                           eligibility_text="생계급여 수급자이면서 장애인연금 대상자")
        found = rc.check_policy(agency_only, VOCAB)
        self.assertEqual({(f.code, f.severity) for f in found}, {("MUNICIPAL_AGENCY_ONLY", "needs_official_check")})
        # 기관이 구청이어도 원문이 시 단위면 시군구 한정으로 단정하지 않는다
        province = view(agency="서울특별시 종로구", region="서울특별시", available_in=["서울특별시"],
                        payload_regions=["서울특별시"], metadata_region="서울특별시",
                        eligibility_text="○ 서울시에 거주하는 19세이상인 재가장애인")
        self.assertNotIn("MUNICIPALITY_DETAIL_MISSING", codes(rc.check_policy(province, VOCAB)))

    def test_generated_text_is_not_evidence(self):
        generated = view(agency="충청남도 천안시", eligibility_text="천안시에 주민등록", generated_eligibility=True)
        self.assertNotIn("MUNICIPALITY_DETAIL_MISSING", codes(rc.check_policy(generated, VOCAB)))

    def test_multi_region_and_vocabulary(self):
        multi = view(region="광주광역시, 전라남도", available_in=["광주광역시", "전라남도"],
                     payload_regions=["광주광역시, 전라남도"], metadata_region="광주광역시, 전라남도")
        self.assertIn("MULTI_REGION_STRING", codes(rc.check_policy(multi, VOCAB)))
        embedded = view(region="서울특별시 동대문구", available_in=["서울특별시"],
                        payload_regions=["서울특별시 동대문구"], metadata_region="서울특별시 동대문구")
        found = codes(rc.check_policy(embedded, VOCAB))
        self.assertTrue({"REGION_OUTSIDE_VOCABULARY", "LAYER_MISMATCH"} <= found)

    def test_text_region_conflict_and_unregistered_unit(self):
        bad = view(region="전라남도", agency="전남광주통합특별시 동구", available_in=["전라남도"],
                   payload_regions=["전라남도"], metadata_region="전라남도",
                   eligibility_text="○ 광주 동구에 거주하는 초등학교 4학년")
        found = codes(rc.check_policy(bad, VOCAB))
        self.assertTrue({"TEXT_REGION_CONFLICT", "UNREGISTERED_ADMIN_UNIT"} <= found)
        ok = view(region="광주광역시", agency="광주광역시 동구", available_in=["광주광역시"],
                  payload_regions=["광주광역시"], metadata_region="광주광역시",
                  eligibility_text="○ 광주 동구에 거주하는 초등학교 4학년", region_detail="광주광역시 동구")
        self.assertEqual(codes(rc.check_policy(ok, VOCAB)), set())

    def test_layer_mismatch(self):
        bad = view(available_in=["충청남도"], payload_regions=["충청남도"], metadata_region="충청북도")
        self.assertIn("LAYER_MISMATCH", codes(rc.check_policy(bad, VOCAB)))

    def test_unverified_region_true_only_when_judge_says_true(self):
        bad = view(source_codes=["44130"])
        self.assertIn("UNVERIFIED_REGION_TRUE", codes(rc.check_policy(bad, VOCAB, lambda v, r: "TRUE")))
        self.assertNotIn("UNVERIFIED_REGION_TRUE", codes(rc.check_policy(bad, VOCAB, lambda v, r: "UNKNOWN")))


def evaluator_like_judge(v, region):
    """현재 참조 평가기(region_match)와 같은 규칙: 시도/전국 일치이면 region_detail이 없을 때 TRUE."""
    if "전국" in v.available_in or region in v.available_in:
        return "UNKNOWN" if v.region_detail else "TRUE"
    return "FALSE"


def snapshot(**fields):
    base = dict(region_detail=None, eligibility_text="", source_codes=None)
    base.update(fields)
    base.setdefault("payload_regions", [base["region"]])
    base.setdefault("metadata_region", base["region"])
    return rc.RegionPolicyView(**base)


# 감사(region_audit_20261107)에서 확정한 15개 정책의 결함 당시 값 (데이터가 고쳐져도 재발 검출을 보장하는 고정 사례)
DEFECT_SNAPSHOTS = {
    "POL_YC_002": (snapshot(policy_id="POL_YC_002", region="전국", agency="전남광주통합특별시 영광군",
                            source_codes=["12830"], available_in=["전국"]), "UNREGISTERED_CODE_AS_NATIONWIDE"),
    "POL_STG_DGU_001": (snapshot(policy_id="POL_STG_DGU_001", region="서울특별시 동대문구",
                                 agency="서울특별시 동대문구 가족정책과", eligibility_text="○ 셋째자녀 이상 입학아동의 부 또는 모",
                                 available_in=["서울특별시"]), "REGION_OUTSIDE_VOCABULARY"),
    "POL_STG_ICN_001": (snapshot(policy_id="POL_STG_ICN_001", region="인천광역시 서구",
                                 agency="인천광역시 서해구 기업일자리정책과",
                                 eligibility_text="중소기업에 6개월 이상 재직 중인 인천광역시 서해구, 검단구 청년",
                                 available_in=["인천광역시"]), "REGION_OUTSIDE_VOCABULARY"),
    "POL_GOV_061": (snapshot(policy_id="POL_GOV_061", region="광주광역시, 전라남도", agency="전남광주통합특별시",
                             available_in=["광주광역시", "전라남도"]), "MULTI_REGION_STRING"),
    **{pid: (snapshot(policy_id=pid, region=region, agency=agency, source_codes=[code], available_in=[region]),
             "MUNICIPALITY_DETAIL_MISSING")
       for pid, region, code, agency in (
           ("POL_YC_032", "부산광역시", "26380", "부산광역시 사하구"), ("POL_YC_034", "부산광역시", "26380", "부산광역시 사하구"),
           ("POL_YC_036", "부산광역시", "26380", "부산광역시 사하구"),
           ("POL_YC_052", "전북특별자치도", "52720", "전북특별자치도 진안군 농산촌미래국"),
           ("POL_YC_055", "전북특별자치도", "52180", "전북특별자치도 정읍시"),
           ("POL_YC_063", "경상북도", "47290", "경상북도 경산시 교육문화국"),
           ("POL_YC_064", "경상북도", "47290", "경상북도 경산시 교육문화국"))},
    "POL_GOV_029": (snapshot(policy_id="POL_GOV_029", region="전라남도", agency="전남광주통합특별시 동구",
                             eligibility_text="○ 광주 동구에 거주하는 초등학교 4학년 ~ 중학교 3학년",
                             available_in=["전라남도"]), "TEXT_REGION_CONFLICT"),
    "POL_GOV_028": (snapshot(policy_id="POL_GOV_028", region="전라남도", agency="전남광주통합특별시 동구",
                             eligibility_text="○ 관내 첫째아 이상 출산가정(동구 출생신고 필수, 2026년 이후 출생아)",
                             available_in=["전라남도"]), "MUNICIPALITY_DETAIL_MISSING"),
    "POL_GOV_032": (snapshot(policy_id="POL_GOV_032", region="대전광역시", agency="대전광역시 동구",
                             eligibility_text="부모 등이 대청동에 거주하는 주민으로서 대청동 수몰지역 이주민의 직계비속",
                             available_in=["대전광역시"]), "MUNICIPALITY_DETAIL_MISSING"),
    "POL_GOV_038": (snapshot(policy_id="POL_GOV_038", region="경기도", agency="경기도 수원시",
                             eligibility_text="○ 수원지역 범죄 피해를 입은 대상 및 가족", available_in=["경기도"]),
                    "MUNICIPALITY_DETAIL_MISSING"),
}


class KnownDefectSnapshotTests(unittest.TestCase):
    def test_each_known_defect_pattern_is_detected(self):
        self.assertEqual(len(DEFECT_SNAPSHOTS), 15)
        for pid, (v, code) in DEFECT_SNAPSHOTS.items():
            with self.subTest(pid=pid):
                found = codes(rc.check_policy(v, VOCAB, evaluator_like_judge))
                self.assertIn(code, found)
                self.assertIn("UNVERIFIED_REGION_TRUE", found)

    def test_unregistered_code_cannot_be_resolved_without_official_table(self):
        guessed = view(region="전라남도", source_codes=["12830"], available_in=["전라남도"],
                       payload_regions=["전라남도"], metadata_region="전라남도")
        self.assertIn("UNREGISTERED_CODE_RESOLVED", codes(rc.check_policy(guessed, VOCAB)))
        pending = view(region="unknown", source_codes=["12830"], available_in=[], payload_regions=["unknown"],
                       metadata_region="unknown")
        self.assertNotIn("UNREGISTERED_CODE_RESOLVED", codes(rc.check_policy(pending, VOCAB)))

    def test_fixed_shapes_pass(self):
        # 같은 정책을 계약대로 고친 모양은 오류가 없어야 한다 (정상 정책 오거부 방지)
        fixed = [view(region="부산광역시", agency="부산광역시 사하구", source_codes=["26380"],
                      region_detail="부산광역시 사하구", available_in=["부산광역시"], payload_regions=["부산광역시"],
                      metadata_region="부산광역시"),
                 view(region="광주광역시", agency="광주광역시 동구", eligibility_text="○ 광주 동구에 거주하는 초등학생",
                      region_detail="광주광역시 동구", available_in=["광주광역시"], payload_regions=["광주광역시"],
                      metadata_region="광주광역시")]
        for v in fixed:
            with self.subTest(region=v.region):
                self.assertEqual([f.code for f in rc.check_policy(v, VOCAB, evaluator_like_judge)
                                  if f.severity == "error"], [])


@unittest.skipUnless((CANDIDATE / "processed/policies.jsonl").is_file(), "Issue #21 후보 없음")
class CandidateRegressionTests(unittest.TestCase):
    # 현재 데이터의 미해결 확정 오류 허용 목록. #21 수정 후 줄이고, 최종에는 비워 위반 0건을 강제한다.
    ALLOWED_ERRORS = frozenset(DEFECT_SNAPSHOTS)

    @classmethod
    def setUpClass(cls):
        cls.report = rc.check_candidate(CANDIDATE)

    def test_no_contract_violation_outside_allowed_list(self):
        self.assertEqual(rc.contract_violations(self.report, self.ALLOWED_ERRORS), [])

    def test_remaining_allowed_errors_keep_expected_rule(self):
        for pid in set(self.report["error_policies"]) & self.ALLOWED_ERRORS:
            with self.subTest(pid=pid):
                self.assertIn(DEFECT_SNAPSHOTS[pid][1], self.report["by_policy"][pid])

    def test_official_check_list_is_separate_from_errors(self):
        self.assertFalse(set(self.report["official_check_policies"]) & set(self.report["error_policies"]))
        self.assertIn("POL_GOV_052", self.report["official_check_policies"])
        self.assertIn("POL_GOV_016", self.report["official_check_policies"])
        self.assertGreater(len(self.report["no_official_code_policies"]), 0)


if __name__ == "__main__":
    unittest.main()
