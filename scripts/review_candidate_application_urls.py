"""동결 인계본 대신 후보 정책의 신청 주소만 교정한다.

저장된 원본에서 추출한 증거 목록을 사용하므로 API 키나 재수집이 필요 없다.
원본 전문과 원문 해시는 바꾸지 않고, 변경 전후 값은 별도 목록에 남긴다.
"""

import json
import hashlib
from pathlib import Path

from collectors.preprocessing.application_urls import review_application_url


BASE_DIR = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = BASE_DIR / "data/metadata/application_url_evidence_v1.4.0.json"


def review_candidate_policies(
    policies: list[dict], baseline_policies: list[dict],
) -> tuple[list[dict], list[dict]]:
    """정책의 신청 주소 하나만 수정하고 전체 판정 및 기존 정책 변경분을 반환한다."""
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))["policies"]
    baseline = {policy["policy_id"]: policy for policy in baseline_policies}
    reviews = []
    changes = []
    checked_hashes = {}
    for policy in policies:
        policy_id = policy["policy_id"]
        item = evidence[policy_id]
        # 원본이 있는 PC에서는 증거를 뽑은 뒤 원본이 달라지지 않았는지도 확인한다.
        evidence_path = (BASE_DIR / item["evidence_path"]).resolve()
        if not evidence_path.is_relative_to(BASE_DIR):
            raise ValueError("증거 경로가 저장소 밖을 가리킵니다")
        if evidence_path.exists():
            if evidence_path not in checked_hashes:
                checked_hashes[evidence_path] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            if checked_hashes[evidence_path] != item["evidence_sha256"]:
                raise ValueError(f"신청 주소 증거의 원본 해시 불일치: {policy_id}")
        # 다른 정책이나 다른 출처에 예전 증거를 잘못 적용하지 않도록 중단한다.
        if item["official_id"] != policy["official_id"] or item["source_url"] != policy["source_url"]:
            raise ValueError(f"신청 주소 증거와 정책이 다릅니다: {policy_id}")
        new_url, reason = review_application_url(item["application_url"], policy["source_url"])
        if new_url is None and item.get("reference_url") and not item["application_url"]:
            reason = "reference_url_only"
        old_url = policy.get("official_apply_url")
        policy["official_apply_url"] = new_url
        reviews.append({
            "policy_id": policy_id,
            "original_url": old_url,
            "official_apply_url": new_url,
            "reason": reason,
            "evidence_path": item["evidence_path"],
            "evidence_sha256": item["evidence_sha256"],
            "evidence_field": item["evidence_field"],
            "live_application_page_verified": False,
        })
        if policy_id in baseline and baseline[policy_id].get("official_apply_url") != new_url:
            changes.append({
                "policy_id": policy_id,
                "field": "official_apply_url",
                "before": baseline[policy_id].get("official_apply_url"),
                "after": new_url,
                "reason": reason,
            })
    return reviews, changes
