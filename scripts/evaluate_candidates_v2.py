# -*- coding: utf-8 -*-
"""
SSOK — 후보 정책 전수 평가 및 증분 선별 v2 스크립트
경로: scripts/evaluate_candidates_v2.py

원칙:
1. Evaluate-First Architecture: 사전 하드 캡(65건) 및 ASCII 편향 정렬 제거, 전수 채점(100점 만점)
2. 다차원 상태 필드 엄격 분리:
   - verification_status: verified | unverified | needs_review (데이터 정제 무결성)
   - approval_status: pending | approved | rejected (관리자 승인 상태)
   - reception_status: apply_always | apply_period | apply_closed | reception_unknown (신청 접수 상태)
   - lifecycle_status: active | suspended | repealed (정책 시행 상태)
3. 충돌 없는 신규 ID 발급: 기존 237개 policy_id 및 official_id 대조 후 미충돌 건에만 ID 발급
"""

import sys
import re
import json
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]

def load_existing_policy_ids(base_dir: Path) -> tuple[set, set]:
    existing_policy_ids = set()
    existing_official_ids = set()
    
    handoff_nodes = base_dir / "data" / "handoff" / "neo4j" / "nodes_policy.jsonl"
    if handoff_nodes.exists():
        with open(handoff_nodes, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                data = json.loads(line)
                existing_policy_ids.add(data["policy_id"])
                if data.get("official_id"):
                    existing_official_ids.add(data["official_id"])
    return existing_policy_ids, existing_official_ids

def score_candidate(item: dict) -> dict:
    """
    100점 만점 다차원 루브릭 (STEP A 선별 설계 v2 반영)
    1. 혜택 구체성 (30점)
    2. 대상자 적합성 (25점)
    3. 신청 절차 명확성 (20점)
    4. 정보 완결성 (15점)
    5. 최신성 및 시행 상태 (10점)
    """
    scores = {}
    
    # 1. 혜택 구체성 (30점)
    benefit_text = item.get("benefit") or item.get("sportCn") or ""
    b_score = 0
    if re.search(r'\d+(?:만원|원|%|톤|호)', benefit_text):
        b_score += 20
    if any(k in benefit_text for k in ["감면", "지원", "지급", "할인", "환급", "면제"]):
        b_score += 10
    scores["benefit_concreteness"] = min(30, b_score)
    
    # 2. 대상자 적합성 (25점)
    target_text = item.get("target_audience") or item.get("ageInfo") or item.get("prcpCn") or ""
    t_score = 0
    if any(k in target_text for k in ["청년", "신혼", "다자녀", "소득", "중소기업", "임산부", "아동"]):
        t_score += 15
    if re.search(r'\d+세', target_text) or "중위소득" in target_text:
        t_score += 10
    scores["target_relevance"] = min(25, t_score)
    
    # 3. 신청 절차 명확성 (20점)
    method_text = item.get("application_method") or item.get("aplyMthdCn") or ""
    url_text = item.get("service_url") or item.get("rfcSiteUrldvsnNm") or ""
    p_score = 0
    if method_text and len(method_text) > 5:
        p_score += 10
    if url_text and "http" in url_text:
        p_score += 10
    scores["execution_clarity"] = min(20, p_score)
    
    # 4. 정보 완결성 (15점)
    c_score = 0
    if item.get("policy_name") or item.get("service_name") or item.get("polyBizSjnm"):
        c_score += 5
    if item.get("agency") or item.get("agency_name") or item.get("mngtMbyNm"):
        c_score += 5
    if item.get("purpose") or item.get("polyExplnCn"):
        c_score += 5
    scores["information_completeness"] = min(15, c_score)
    
    # 5. 최신성 및 시행 상태 (10점)
    r_score = 0
    status_text = f"{method_text} {benefit_text}"
    if "신규지원 중단" in status_text or "폐지" in status_text:
        r_score = 2
    else:
        r_score = 10
    scores["recency_status"] = r_score
    
    total = sum(scores.values())
    return {
        "scores": scores,
        "total_score": total,
        "grade": "Tier-1 (최우수)" if total >= 80 else ("Tier-2 (우수)" if total >= 65 else "Tier-3 (보류/탈락)")
    }

def classify_statuses(item: dict) -> dict:
    """상태 필드 엄격 분리"""
    text_corpus = " ".join(str(v) for v in item.values())
    
    # 1. reception_status
    if "상시" in text_corpus or "연중" in text_corpus:
        reception = "apply_always"
    elif "신규지원 중단" in text_corpus or "모집마감" in text_corpus:
        reception = "apply_closed"
    elif re.search(r'\d+월|\d+일|공고', text_corpus):
        reception = "apply_period"
    else:
        reception = "reception_unknown"
        
    # 2. lifecycle_status
    if "폐지" in text_corpus:
        lifecycle = "repealed"
    elif "신규지원 중단" in text_corpus:
        lifecycle = "suspended"
    else:
        lifecycle = "active"
        
    # 3. verification_status
    verification = "verified" if item.get("service_name") or item.get("polyBizSjnm") else "unverified"
    
    # 4. approval_status (관리자 승인 대기 기본값)
    approval = "pending"
    
    return {
        "verification_status": verification,
        "approval_status": approval,
        "reception_status": reception,
        "lifecycle_status": lifecycle
    }

def main():
    parser = argparse.ArgumentParser(description="Evaluate-First Policy Candidates V2")
    parser.add_argument("--limit", type=int, default=30, help="Number of items to evaluate")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing outputs")
    parser.add_argument("--output", type=str, default="data/staging/evaluated_candidates.jsonl")
    args = parser.parse_args()
    
    existing_pids, existing_oids = load_existing_policy_ids(BASE_DIR)
    print(f"[초기 로드] 기존 237개 정책 ID {len(existing_pids)}개, official_id {len(existing_oids)}개 로드 완료")
    
    # Load candidate items from gov24 processed
    candidates = []
    gov_file = BASE_DIR / "data" / "processed" / "gov24_public_services.jsonl"
    if gov_file.exists():
        with open(gov_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                candidates.append(json.loads(line))
                if len(candidates) >= args.limit:
                    break
                    
    print(f"[평가 대상] {len(candidates)}개 후보 레코드 전수 다차원 스코어링 시작...")
    
    results = []
    for c in candidates:
        oid = c.get("official_id") or c.get("service_id")
        pname = c.get("policy_name") or c.get("service_name")
        
        # Check duplicate
        is_dup = oid in existing_oids
        
        # Score
        evaluation = score_candidate(c)
        statuses = classify_statuses(c)
        
        # ID generation policy: Only assign temporary STG ID if non-colliding
        stg_pid = None
        if not is_dup:
            stg_pid = f"POL_STG_EV2_{len(results)+1:03d}"
            assert stg_pid not in existing_pids, f"Collision detected: {stg_pid}"
            
        res = {
            "staged_policy_id": stg_pid,
            "official_id": oid,
            "policy_name": pname,
            "agency": c.get("agency") or c.get("agency_name"),
            "is_duplicate_with_baseline": is_dup,
            "evaluation": evaluation,
            "statuses": statuses
        }
        results.append(res)
        
    print(f"[평가 완료] {len(results)}건 채점 완료")
    tier1 = sum(1 for r in results if r["evaluation"]["grade"].startswith("Tier-1"))
    tier2 = sum(1 for r in results if r["evaluation"]["grade"].startswith("Tier-2"))
    tier3 = sum(1 for r in results if r["evaluation"]["grade"].startswith("Tier-3"))
    dups = sum(1 for r in results if r["is_duplicate_with_baseline"])
    print(f"  - Tier-1 (최우수): {tier1}건")
    print(f"  - Tier-2 (우수): {tier2}건")
    print(f"  - Tier-3 (보류): {tier3}건")
    print(f"  - 기존 237건 중복: {dups}건 (ID 미발급 격리)")
    
    if not args.dry_run:
        out_path = BASE_DIR / args.output
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[저장 완료] 평가 결과 파일: {out_path}")

if __name__ == "__main__":
    main()

