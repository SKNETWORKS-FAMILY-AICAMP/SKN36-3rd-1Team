"""복지로 2026 인계 자료(data/staging/bokjiro_2026/handoff)에서 고른 정책을 후보 빌드 입력으로 만든다.

- 입력: selection.json(넣을 정책과 분류), handoff/targets.jsonl·review_rows.jsonl·source_text/
- 출력: data/staging/bokjiro_2026/policies.jsonl, documents.jsonl, payload_chunks.jsonl
- 본문은 handoff/source_text의 파일을 그대로 쓰고(새로 만들지 않음), 청크는 기존 복지로 청크와 같은 규칙
  (750자 창, 80자 겹침, [문의처] 블록 보존)으로 자른다.
- 넣을 수 없는 정책이면 멈춘다: 검수 행이 모두 OK가 아님, 기존 정책과 이름 중복 후보, 조직 대상 의심,
  본문 해시 불일치, 필수 값 누락.
나머지 정책도 selection.json에 추가하고 이 스크립트를 다시 돌린 뒤 후보를 재빌드하면 같은 형식으로 들어간다.

실행: python scripts/stage_bokjiro_2026_policies.py
"""
import hashlib
import json
import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
from scripts.build_clean_dataset import iter_document_chunks  # noqa: E402

EVIDENCE_PATH = BASE_DIR / "data/metadata/application_url_evidence_v1.4.0.json"
BATCH_DIR = BASE_DIR / "data/staging/bokjiro_2026"
HANDOFF_DIR = BATCH_DIR / "handoff"
REQUIRED_POLICY_FIELDS = ("policy_id", "official_id", "policy_name", "category", "agency", "regions",
                          "life_stages", "target_groups", "eligibility_text", "benefit_text",
                          "application_method", "source_url", "collected_at")
# 후보 빌드의 TARGETS 관계 이름과 같다(11대 표준 대상집단).
GROUP_NAMES = {"GROUP_NEWLYWED": "신혼부부", "GROUP_PREGNANT_BIRTH": "임산부·출산가구",
               "GROUP_CHILD_YOUTH": "영유아·아동·청소년", "GROUP_YOUTH": "청년", "GROUP_JOBSEEKER": "구직자·취업준비생",
               "GROUP_WORKER_BIZ": "소상공인·재직자", "GROUP_LOW_INCOME": "저소득·취약계층", "GROUP_DISABLED": "장애인",
               "GROUP_SENIOR": "어르신·노년층", "GROUP_GENERAL": "일반국민", "GROUP_FARMER_FISHER": "농어업인"}
GROUP_CODES = set(GROUP_NAMES)
LIFE_STAGES = {"영유아", "아동", "청소년", "청년", "중장년", "노년"}
CATEGORIES = {"출산·육아·가족", "청년 취업·교육·자산형성", "주거·금융", "교통·생활비·의료·돌봄", "지역별 생활지원·복지"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def section(text: str, name: str) -> str:
    """본문에서 '[이름]' 칸의 내용을 그대로 꺼낸다(없으면 빈 문자열)."""
    start = text.find(f"[{name}]\n")
    if start < 0:
        return ""
    start += len(name) + 3
    end = text.find("\n\n[", start)
    return text[start:end if end >= 0 else len(text)].strip()


def main() -> None:
    selection = json.loads((BATCH_DIR / "selection.json").read_text(encoding="utf-8"))
    targets = {row["target_id"]: row for row in read_jsonl(HANDOFF_DIR / "targets.jsonl")}
    rows = read_jsonl(HANDOFF_DIR / "review_rows.jsonl")
    policies, documents, chunks = [], [], []
    seen = set()
    for item in selection["policies"]:
        target = targets[item["target_id"]]
        policy_id = item["policy_id"]
        if policy_id in seen or not policy_id.startswith("POL_BOK_"):
            raise ValueError(f"정책 ID 중복 또는 형식 오류: {policy_id}")
        seen.add(policy_id)
        target_rows = [r for r in rows if r["target_id"] == item["target_id"]]
        if target["review_status"] != "OK" or not target_rows or any(r["review_status"] != "OK" for r in target_rows):
            raise ValueError(f"검수 행이 모두 OK가 아님: {item['target_id']}")
        if target["name_overlap_policy_ids"]:
            raise ValueError(f"기존 정책과 이름 중복 후보: {item['target_id']} {target['name_overlap_policy_ids']}")
        # 조직 대상 의심은 자동 표시(원문 단어 기준)라, 원문으로 개인 신청을 확인한 사유가 있을 때만 넣는다.
        if target["org_suspect"] and not item.get("org_suspect_reviewed"):
            raise ValueError(f"조직 대상 의심(확인 사유 필요): {item['target_id']}")
        if (set(item["target_groups"]) - GROUP_CODES or set(item["life_stages"]) - LIFE_STAGES
                or item["category"] not in CATEGORIES):
            raise ValueError(f"분류 값이 표준이 아님: {policy_id}")
        text_path = BASE_DIR / target["source_text_path"]
        text = text_path.read_text(encoding="utf-8").strip()
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if content_hash != target["source_text_sha256"]:
            raise ValueError(f"본문 해시 불일치: {item['target_id']}")
        document_id = policy_id.replace("POL_", "DOC_")
        eligibility = " ".join(part for part in (section(text, "지원대상 상세"), section(text, "선정기준")) if part)
        benefit = section(text, "지원내용(급여서비스)") or section(text, "사업개요")
        policy = {
            "policy_id": policy_id,
            "official_id": target["servId"],
            "policy_name": target["policy_name"],
            "category": item["category"],
            "agency": target["agency"],
            "regions": ["전국"],
            "life_stages": item["life_stages"],
            "target_groups": item["target_groups"],
            "eligibility_text": eligibility,
            "benefit_text": benefit,
            "application_method": item["application_method"],
            "official_apply_url": None,
            "source_url": target["source_url"],
            "source_type": "api",
            "verification_status": "extracted",
            "approval_status": "approved",
            "reception_status": "reception_unknown",
            "lifecycle_status": "active",
            "collected_at": target["raw_saved_at"],
            "review_target_id": item["target_id"],
            "review_record_ids": [r["record_id"] for r in target_rows],
            "selection_rationale": f"복지로 F01~F12 검수 v8 전 행 OK({len(target_rows)}행), 기존 정책과 이름 중복 없음",
        }
        missing = [key for key in REQUIRED_POLICY_FIELDS if policy.get(key) in (None, "", [])]
        if missing:
            raise ValueError(f"필수 값 누락: {policy_id} {missing}")
        policies.append(policy)
        title = f"[중앙부처복지서비스] {target['policy_name']} ({target['servId']})"
        documents.append({
            "document_id": document_id,
            "policy_id": policy_id,
            "title": title,
            "source_type": "api",
            "source_url": target["source_url"],
            "local_raw_path": target["local_raw_path"],
            "extracted_text_path": target["source_text_path"],
            "content_hash": content_hash,
            "raw_source_sha256": target["raw_source_sha256"],
            "verification_status": "verified_official_api",
            "collected_at": target["raw_saved_at"],
            "doc_type": "service_guide",
            "version": "복지로 상세 XML(기준연도 2026)",
        })
        number = 0
        last_section = text.rsplit("\n\n", 1)[-1]
        for piece in iter_document_chunks(text, 750, 80):
            if len(piece) < 50:
                continue
            # 창 끝에서 잘린 '[출처 URL] …' 줄 조각만 남은 청크는 만들지 않는다. 주소는 청크의 source_url에 있다.
            if last_section.startswith("[출처 URL]") and piece in last_section:
                continue
            number += 1
            chunk_id = f"{document_id}_C{number:02d}"
            chunks.append({
                "point_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id)),
                "chunk_id": chunk_id,
                "policy_id": policy_id,
                "policy_name": target["policy_name"],
                "document_id": document_id,
                "document_title": title,
                "section_title": f"{title} (섹션 {number})",
                "category": item["category"],
                "region": "전국",
                "life_stages": item["life_stages"],
                "target_groups": [GROUP_NAMES[code] for code in item["target_groups"]],
                "income_level": None,
                "benefit_type": item["category"],
                "verification_status": "verified_official_api",
                "eligibility_attributes": {"source": "service_guide"},
                "text": piece,
                "doc_type": "service_guide",
                "source_url": target["source_url"],
            })
    write_jsonl(BATCH_DIR / "policies.jsonl", policies)
    write_jsonl(BATCH_DIR / "documents.jsonl", documents)
    write_jsonl(BATCH_DIR / "payload_chunks.jsonl", chunks)
    record_application_url_evidence(policies, targets)
    print(f"정책 {len(policies)}건, 문서 {len(documents)}건, 청크 {len(chunks)}건")


def record_application_url_evidence(policies: list[dict], targets: dict) -> None:
    """후보 빌드의 신청 주소 판정(review_candidate_policies)이 읽는 증거 목록에 신규 정책을 적는다.

    복지로 상세 XML에는 신청 URL이 없다(applmetList에 링크 없음). 그래서 신청 주소는 null이고,
    증거 파일은 XML에서 만든 본문이다. 본문이 바뀌면 빌드가 해시 불일치로 멈춘다.
    """
    by_target = {policy["review_target_id"]: policy for policy in policies}
    evidence = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
    for target_id, policy in by_target.items():
        target = targets[target_id]
        text_path = BASE_DIR / target["source_text_path"]
        evidence["policies"][policy["policy_id"]] = {
            "official_id": policy["official_id"],
            "source_url": policy["source_url"],
            "application_url": None,
            "reference_url": None,
            "evidence_field": "applmetList.servSeDetailLink(복지로 상세 XML, 링크 없음)",
            "evidence_path": target["source_text_path"],
            "evidence_sha256": hashlib.sha256(text_path.read_bytes()).hexdigest(),
            "collected_at": target["raw_saved_at"],
        }
    EVIDENCE_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
