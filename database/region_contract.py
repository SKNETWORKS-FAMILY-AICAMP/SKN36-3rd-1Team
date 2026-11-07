"""
지역 데이터 계약 검사기 (읽기 전용, DB·네트워크 없음).

목적: 정책의 지역 의미가 생성 → Neo4j → Qdrant → 자격 판정까지 같은지, 그리고 생성 단계의
지역 해석 오류가 다시 생기지 않는지 확인한다. 데이터를 고치지 않고 위반 목록만 만든다.

규칙 (모두 원칙에서 출발. 특정 정책 ID를 하드코딩하지 않는다)
  R1 UNREGISTERED_CODE_AS_NATIONWIDE  미등록 행정구역 코드를 전국으로 바꿈 (원칙: 모르는 코드 ≠ 전국)
  R1 UNREGISTERED_CODE_RESOLVED       미등록 코드만으로 특정 시도를 확정함 (공식 코드표 없이 지역 확정 금지)
  R1 UNREGISTERED_CODE                미등록 코드가 있으나 전국 목록의 일부이거나 지역이 unknown → 공식 확인 필요
  R2 MUNICIPALITY_DETAIL_MISSING      원천 코드(단일 시군구) 또는 원문(시군구 거주·관내·동 단위)에
                                       시군구 한정 근거가 있는데 시군구 정보가 없음
  R2 MUNICIPAL_AGENCY_ONLY            기관만 시군구이고 원문 근거 없음 → 공식 확인 필요
                                       (원칙: 기관 소재지를 수혜 대상 거주지로 자동 간주하지 않음)
  R3 REGION_OUTSIDE_VOCABULARY         region이 시도·전국 어휘 밖 (시군구 포함 문자열 등)
  R3 MULTI_REGION_STRING               복수 지역을 한 문자열로 저장 (검색 필터 누락 원인)
  R4 TEXT_REGION_CONFLICT              원문에 다른 시도의 거주 요건이 명시됨
  R4 UNREGISTERED_ADMIN_UNIT           기관명이 어휘에 없는 시도(개편 명칭)로 시작 → 접두 매핑 위험
  R5 LAYER_MISMATCH                    Policy.region / AVAILABLE_IN / Qdrant payload / policy_metadata 불일치
  R6 UNVERIFIED_REGION_TRUE            위 근거로 시군구 한정이거나 지역이 불확실한데 평가기가 시도 일치로 TRUE
                                       (정적 위험 탐지: 참조 평가기의 동작을 확인한다. Issue #5 검색은 자격을 판정하지
                                        않으므로 실제 서비스 판정 오류를 뜻하지 않는다)

최종 게이트: contract_violations(report) == [] 이면 계약 위반 0건.

severity: "error"(구조화 원천 코드·원문·데이터 형식으로 확인) / "needs_official_check"(공식 원문 확인 필요)
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

NATIONAL = "전국"
# 법정동코드 시도 2자리 → 이름. 저장소 근거: collectors/api/youthcenter_collector.py ZIP_CODE_TO_REGION
# (강원 42/51, 전북 45/52 병기). 표에 없는 코드는 "미등록"으로만 보고하고 이름을 추정하지 않는다.
SIDO_CODES: dict[str, str] = {
    "11": "서울특별시", "26": "부산광역시", "27": "대구광역시", "28": "인천광역시", "29": "광주광역시",
    "30": "대전광역시", "31": "울산광역시", "36": "세종특별자치시", "41": "경기도", "42": "강원특별자치도",
    "51": "강원특별자치도", "43": "충청북도", "44": "충청남도", "45": "전북특별자치도", "52": "전북특별자치도",
    "46": "전라남도", "47": "경상북도", "48": "경상남도", "50": "제주특별자치도",
}
SIDO_NAMES = frozenset(SIDO_CODES.values())
SINGLE_TIER_SIDO = frozenset({"세종특별자치시"})       # 시군구가 없는 단층제
SIDO_ALIAS = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
              "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
              "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
              "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
              "제주": "제주특별자치도", **{name: name for name in SIDO_NAMES}}
MULTI_SEPARATORS = re.compile(r"[,，/·|]| 및 ")
_ALIAS_RE = "|".join(sorted(map(re.escape, SIDO_ALIAS), key=len, reverse=True))
# "광주 동구에 거주", "부산광역시 중구에 주민등록" — 시도 + 시군구 + 거주 요건
TEXT_SIDO_RESIDENCE = re.compile(rf"({_ALIAS_RE})\s*([가-힣]{{1,8}}(?:시|군|구))\s*(?:관내)?에?\s*"
                                 r"(?:\d+\s*(?:개월|년)\s*이상\s*)?(?:계속하여\s*)?(?:주민등록|거주)")
RESIDENCE_CUE = re.compile(r"주민등록|거주|관내|주민")
DONG_RESIDENCE = re.compile(r"[가-힣]{1,6}동에\s*(?:계속\s*)?(?:주민등록|거주)")


@dataclass
class Finding:
    rule: str
    code: str
    severity: str
    policy_id: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegionPolicyView:
    """검사에 필요한 정책 한 건의 지역 관련 값 (계층별)."""
    policy_id: str
    region: str
    region_detail: str | None
    agency: str
    eligibility_text: str
    source_codes: list[str] | None            # 온통청년 zipCd 등 공식 행정구역 코드 (없으면 None)
    available_in: list[str]
    payload_regions: list[str]
    metadata_region: str | None
    generated_eligibility: bool = False       # 코드값으로 만든 문장(온통청년 eligibility)이면 원문 근거로 쓰지 않음


def _municipal_token(agency: str) -> str | None:
    tokens = [t for t in re.findall(r"([가-힣]+(?:시|군|구))(?=\s|$|/)", agency or "")
              if not t.endswith(("특별시", "광역시", "특별자치시"))]
    return tokens[0] if tokens else None


def _agency_sido_prefix(agency: str) -> str | None:
    first = (agency or "").split(" ")[0]
    return first if first.endswith(("특별시", "광역시", "특별자치시", "특별자치도", "도")) else None


def municipal_text_evidence(view: RegionPolicyView) -> str | None:
    """원문에서 시군구(또는 그 아래) 한정 근거 문장. 기관명만으로는 근거로 보지 않는다."""
    if view.generated_eligibility:
        return None
    text = view.eligibility_text or ""
    muni = _municipal_token(view.agency)
    candidates = []
    if muni:
        base = muni[:-1]
        candidates += [rf"{re.escape(muni)}\s*(?:관내)?에?\s*[^.\n]{{0,20}}(?:주민등록|거주)",
                       rf"{re.escape(base)}\s*지역", rf"[가-힣]+\s+{re.escape(muni)}\s*,"]
        if "관내" in text:
            candidates.append(r"관내")
    for pattern in candidates:
        match = re.search(pattern, text)
        if match:
            return text[max(0, match.start() - 20):match.end() + 20].strip()
    match = DONG_RESIDENCE.search(text)
    return text[max(0, match.start() - 20):match.end() + 10].strip() if match else None


def check_policy(view: RegionPolicyView, vocabulary: set[str],
                 region_judge: Callable[[RegionPolicyView, str], str] | None = None) -> list[Finding]:
    out: list[Finding] = []
    pid = view.policy_id
    add = lambda rule, code, severity, **detail: out.append(Finding(rule, code, severity, pid, detail))  # noqa: E731

    # R3 형식
    if MULTI_SEPARATORS.search(view.region or ""):
        add("R3", "MULTI_REGION_STRING", "error", region=view.region)
    elif view.region not in vocabulary:
        add("R3", "REGION_OUTSIDE_VOCABULARY", "error", region=view.region)

    # R1 공식 코드
    sidos: set[str] = set()
    if view.source_codes is not None:
        unknown = sorted({c[:2] for c in view.source_codes if c[:2] not in SIDO_CODES})
        sidos = {SIDO_CODES[c[:2]] for c in view.source_codes if c[:2] in SIDO_CODES}
        if unknown:
            covers_all = sidos >= (SIDO_NAMES - {"강원특별자치도", "전북특별자치도"}) or len(sidos) >= 15
            if view.region == NATIONAL and not covers_all:
                add("R1", "UNREGISTERED_CODE_AS_NATIONWIDE", "error", unknown_prefixes=unknown,
                    codes=view.source_codes[:5])
            elif not covers_all and view.region not in ("unknown", NATIONAL) and not sidos:
                add("R1", "UNREGISTERED_CODE_RESOLVED", "error", unknown_prefixes=unknown, region=view.region)
            else:
                add("R1", "UNREGISTERED_CODE", "needs_official_check", unknown_prefixes=unknown)

    # R2 시군구 정보
    municipal_basis = None
    if view.source_codes is not None:
        codes = view.source_codes
        single = (len(codes) == 1 and not codes[0].endswith("000")
                  and SIDO_CODES.get(codes[0][:2]) not in SINGLE_TIER_SIDO)
        if single:
            municipal_basis = f"source_code {codes[0]}"
    evidence = municipal_text_evidence(view)
    if evidence and not municipal_basis:
        municipal_basis = f"text: {evidence[:60]}"
    if municipal_basis and not view.region_detail:
        add("R2", "MUNICIPALITY_DETAIL_MISSING", "error", basis=municipal_basis)
    elif not municipal_basis and not view.region_detail and _municipal_token(view.agency) and view.source_codes is None:
        add("R2", "MUNICIPAL_AGENCY_ONLY", "needs_official_check", agency=view.agency)

    # R4 원문 시도 충돌 / 개편 명칭
    if not view.generated_eligibility:
        for match in TEXT_SIDO_RESIDENCE.finditer(view.eligibility_text or ""):
            stated = SIDO_ALIAS[match.group(1)]
            if stated not in (view.region or "") and view.region != NATIONAL:
                add("R4", "TEXT_REGION_CONFLICT", "error", stated=stated, region=view.region,
                    text=match.group(0)[:40])
                break
    prefix = _agency_sido_prefix(view.agency)
    if prefix and prefix not in vocabulary:
        add("R4", "UNREGISTERED_ADMIN_UNIT", "needs_official_check", agency_prefix=prefix, region=view.region)

    # R5 계층 일치
    expected = sorted(r for r in re.split(MULTI_SEPARATORS, view.region or "") if r.strip())
    expected = [r.strip() for r in expected]
    layers = {"available_in": sorted(view.available_in), "payload": sorted(view.payload_regions),
              "metadata": [view.metadata_region] if view.metadata_region is not None else None}
    if (sorted(view.available_in) != expected or (view.payload_regions and sorted(set(view.payload_regions)) != [view.region])
            or (view.metadata_region is not None and view.metadata_region != view.region)):
        add("R5", "LAYER_MISMATCH", "error", region=view.region, **layers)

    # R6 불확실한 지역의 TRUE 판정
    unverified = any(f.code in ("MUNICIPALITY_DETAIL_MISSING", "REGION_OUTSIDE_VOCABULARY", "MULTI_REGION_STRING",
                                "UNREGISTERED_CODE_AS_NATIONWIDE", "UNREGISTERED_CODE_RESOLVED",
                                "TEXT_REGION_CONFLICT") for f in out)
    if unverified and region_judge is not None:
        probe = sorted(sidos)[0] if sidos else next((r for r in view.available_in if r != NATIONAL), None)
        if probe is None and NATIONAL in view.available_in:
            probe = "서울특별시"        # 전국으로 잘못 연결된 정책은 임의 시도에서도 TRUE가 되는지 확인
        if probe and region_judge(view, probe) == "TRUE":
            add("R6", "UNVERIFIED_REGION_TRUE", "error", probe_region=probe)
    return out


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_candidate_views(candidate_dir: Path | str) -> tuple[list[RegionPolicyView], set[str]]:
    """Issue #21 후보 폴더(processed/·neo4j/·qdrant/)에서 계층별 지역 값을 모은다."""
    base = Path(candidate_dir)
    processed = {p["policy_id"]: p for p in _rows(base / "processed/policies.jsonl")}
    nodes = {p["policy_id"]: p for p in _rows(base / "neo4j/nodes_policy.jsonl")}
    region_names = {r["region_id"]: r["name"] for r in _rows(base / "neo4j/nodes_region.jsonl")}
    available = defaultdict(list)
    for edge in _rows(base / "neo4j/relationships.jsonl"):
        if edge["relation"] == "AVAILABLE_IN":
            available[edge["source_id"]].append(region_names[edge["target_id"]])
    payload = defaultdict(set)
    for chunk in _rows(base / "qdrant/payload_chunks.jsonl"):
        payload[chunk["policy_id"]].add(chunk["region"])
    metadata_path = base / "qdrant/policy_metadata.jsonl"
    metadata = {m["policy_id"]: m["region"] for m in _rows(metadata_path)} if metadata_path.is_file() else {}
    views = []
    for pid, node in nodes.items():
        p = processed.get(pid, {})
        req = p.get("requirements") or {}
        generated = "age_limit_yn" in req          # 온통청년 eligibility는 코드값으로 만든 문장
        text = req.get("additional_qualification") or "" if generated else p.get("eligibility") or ""
        views.append(RegionPolicyView(
            policy_id=pid, region=node.get("region"), region_detail=node.get("region_detail"),
            agency=p.get("agency") or node.get("agency") or "", eligibility_text=text,
            source_codes=req.get("region_codes"), available_in=available[pid],
            payload_regions=sorted(payload[pid]), metadata_region=metadata.get(pid),
            generated_eligibility=False))
    return views, set(region_names.values())


def evaluator_region_judge(candidate_dir: Path | str) -> Callable[[RegionPolicyView, str], str]:
    """참조 평가기(scripts/eligibility_3vl.region_match)를 그대로 호출하는 판정 함수."""
    from scripts.eligibility_3vl import Candidate, region_match
    candidate = Candidate(candidate_dir)
    return lambda view, region: region_match(candidate, view.policy_id, region)


def check_candidate(candidate_dir: Path | str, with_evaluator: bool = True) -> dict[str, Any]:
    views, vocabulary = load_candidate_views(candidate_dir)
    judge = evaluator_region_judge(candidate_dir) if with_evaluator else None
    findings = [f for view in views for f in check_policy(view, vocabulary, judge)]
    by_policy: dict[str, list[str]] = defaultdict(list)
    for f in findings:
        by_policy[f.policy_id].append(f.code)
    return {"policies_checked": len(views), "vocabulary": sorted(vocabulary),
            # 공식 행정구역 코드가 없어 원문·기관명으로만 검사한 정책 (원본 대조 불가 범위)
            "no_official_code_policies": [v.policy_id for v in views if v.source_codes is None],
            "error_policies": sorted({f.policy_id for f in findings if f.severity == "error"}),
            "official_check_policies": sorted({f.policy_id for f in findings if f.severity == "needs_official_check"}
                                              - {f.policy_id for f in findings if f.severity == "error"}),
            "by_policy": dict(by_policy), "findings": [f.__dict__ for f in findings]}


def contract_violations(report: dict[str, Any], allowed: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """허용 목록 밖의 오류 정책. 최종 게이트는 allowed를 비워 [] 를 요구한다."""
    return sorted(set(report["error_policies"]) - set(allowed))


if __name__ == "__main__":
    import sys
    report = check_candidate(sys.argv[1] if len(sys.argv) > 1 else "data/candidates/issue21-v1.4.0-rag")
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in report.items()
                      if k not in ("by_policy", "vocabulary")}, ensure_ascii=False, indent=1))
    for pid in report["error_policies"]:
        print("ERROR", pid, report["by_policy"][pid])
