# -*- coding: utf-8 -*-
"""
v1.4.0-candidate 신규 문서 공식 원문 대조 결과를 staging 데이터에 반영한다.

배경 (2026-11-06 대조):
  - staging/raw의 법령·조례·FAQ·안내문 20건은 원문을 내려받은 파일이 아니라
    staging_poc_pipeline.py에 하드코딩된 요약문이었고, 일부는 공식 원문과 내용이 달랐다.
  - 이 스크립트는 공식 출처에서 확인한 원문만으로 staging 문서·청크를 다시 만든다.
      * 법령·조례: 국가법령정보센터 조문 원문 (data/staging/official/)
      * 서비스 안내: 저장소에 이미 수집된 정부24·온통청년 공식 API 응답 (data/processed/)
  - 공식 원문을 확인하지 못한 문서는 만들지 않고, 제외 사유를 crosscheck_results.json에 남긴다.

실행 순서:
  python scripts/apply_official_crosscheck.py
  python scripts/build_incremental_release_candidate.py
"""
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STAGING_DIR = BASE_DIR / "data" / "staging"
OFFICIAL_DIR = STAGING_DIR / "official"
BACKUP_DIR = STAGING_DIR / "pre_crosscheck"
GOV24_PATH = BASE_DIR / "data" / "processed" / "gov24_public_services.jsonl"
YOUTH_PATH = BASE_DIR / "data" / "processed" / "youthcenter_policies.jsonl"
RETRIEVED_AT = "2026-11-06T00:00:00+09:00"
MAX_CHUNK_CHARS = 900

# 법령·조례: 국가법령정보센터에서 조문 원문을 확인한 문서
OFFICIAL_LAW_DOCS = [
    {"document_id": "DOC_STG_001_LAW", "policy_id": "POL_001", "doc_type": "law",
     "file": "law/공공주택특별법_제49조.md",
     "title": "공공주택 특별법 제49조(공공임대주택의 임대조건 등)", "law_article": "제49조",
     "effective_date": "2026-09-08", "version": "법률 제21895호",
     "source_url": "https://www.law.go.kr/법령/공공주택특별법/제49조"},
    {"document_id": "DOC_STG_NTS_001_LAW", "policy_id": "POL_STG_NTS_001", "doc_type": "law",
     "file": "law/조세특례제한법_제30조.md",
     "title": "조세특례제한법 제30조(중소기업 취업자에 대한 소득세 감면)", "law_article": "제30조",
     "effective_date": "2026-09-18", "version": "법률 제21467호",
     "source_url": "https://www.law.go.kr/법령/조세특례제한법/제30조"},
    {"document_id": "DOC_STG_MOIS_001_LAW", "policy_id": "POL_STG_MOIS_001", "doc_type": "law",
     "file": "law/지방세특례제한법_제22조의2.md",
     "title": "지방세특례제한법 제22조의2(출산 및 양육 지원을 위한 감면)", "law_article": "제22조의2",
     "effective_date": "2026-06-02", "version": "법률 제21738호",
     "source_url": "https://www.law.go.kr/법령/지방세특례제한법/제22조의2"},
    {"document_id": "DOC_STG_EX_001_LAW", "policy_id": "POL_STG_EX_001", "doc_type": "law",
     "file": "law/유료도로법_시행령_제8조.md",
     "title": "유료도로법 시행령 제8조(통행료의 감면대상 차량 및 감면비율) 다자녀가구 부분 발췌", "law_article": "제8조",
     "effective_date": "2026-07-28", "version": "대통령령 제36542호",
     "source_url": "https://www.law.go.kr/법령/유료도로법시행령/제8조"},
    {"document_id": "DOC_STG_SEOUL_001_ORD", "policy_id": "POL_STG_SEOUL_001", "doc_type": "ordinance",
     "file": "ordinance/서울특별시_청년기본조례_제12조.md",
     "title": "서울특별시 청년 기본 조례 제12조(청년의 고용확대 등)", "law_article": "제12조",
     "effective_date": "2025-07-14", "version": "서울특별시조례 제9765호",
     "source_url": "https://www.law.go.kr/자치법규/서울특별시청년기본조례"},
    {"document_id": "DOC_STG_ICN_001_ORD", "policy_id": "POL_STG_ICN_001", "doc_type": "ordinance",
     "file": "ordinance/인천광역시_서해구_청년기본조례_제9조_제12조.md",
     "title": "인천광역시 서해구 청년 기본 조례 제9조(청년정책 사업의 추진)·제12조(청년의 고용확대 및 안정 등)",
     "law_article": "제9조, 제12조",
     "effective_date": "2026-07-01", "version": "인천광역시서해구조례 제2406호",
     "source_url": "https://www.law.go.kr/자치법규/인천광역시서해구청년기본조례"},
]

# 서비스 안내: 정부24 공공서비스 API 응답(서비스 상세)을 원문으로 사용
GOV24_SERVICE_DOCS = {
    "POL_STG_MOLIT_002": "161300000103",
    "POL_STG_MOHW_001": "174000000029",
    "POL_STG_DGU_001": "305000000103",
    "POL_STG_MOIS_001": "131200000008",
    "POL_STG_EX_001": "B50000400042",
    "POL_STG_MOLIT_001": "161300000104",
    "POL_STG_ICN_001": "356100000004",
}
# 온통청년 API 응답을 원문으로 사용 (서울 청년수당 2026)
YOUTH_SERVICE_DOCS = {"POL_STG_SEOUL_001": "20261018005400213994"}

# 공식 원문을 확인하지 못했거나 원문과 달라 제외한 문서
EXCLUDED_DOCS = {
    "DOC_STG_001_NOTICE": "v1.3.2의 DOC_LH_001과 같은 PDF의 중복 요약본. 요약 내용이 원문과 다름(공고일 3/20↔원문 3/31, 자동차가액 3,708만원↔원문 4,542만원, '420호'·'서류접수 4/12 연장'은 원문에 없음)",
    "DOC_STG_001_FAQ": "원본 HWP는 보유하나 추출 텍스트가 원문에서 추출된 것이 아님. HWP 파서(olefile 등) 설치 승인 후 재추출 필요",
    "DOC_STG_MOLIT_002_GDL": "사업안내서 원문 미확보. 출처 URL로 원문 확인 불가 → 정부24 서비스 상세(DOC_STG_MOLIT_002_GOV24)로 대체",
    "DOC_STG_MOLIT_002_FAQ": "질의회시집 원문 미확보. 출처 URL(plus.gov.kr/portal/benefitV2/faq/…) 형식 확인 불가",
    "DOC_STG_MOHW_001_GDL": "표준처리지침 원문 미확보 → 정부24 서비스 상세(DOC_STG_MOHW_001_GOV24)로 대체",
    "DOC_STG_MOHW_001_LAW": "전자정부법 제9조의2 실제 조문은 '전자정부 포털을 통한 생활정보의 제공'으로 정책과 무관. 정부24 근거법령은 '임신·출산 관련 서비스 통합처리에 관한 규정'",
    "DOC_STG_DGU_001_ORD": "'서울특별시 동대문구 출산 및 양육지원에 관한 조례'를 국가법령정보센터에서 찾을 수 없음. 출처 URL(elis.go.kr/ordinance/…)은 404. 정부24 근거법령은 '아동복지법'",
    "DOC_STG_DGU_001_NOT": "신청안내 공고 원문 미확보 → 정부24 서비스 상세(DOC_STG_DGU_001_GOV24)로 대체",
    "DOC_STG_NTS_001_FAQ": "국세청 FAQ 원문 미확보. 출처 URL(hometax.go.kr/nts_faq_tax_30.html) 형식 확인 불가",
    "DOC_STG_MOIS_001_NOT": "신청안내문 원문 미확보 → 정부24 서비스 상세(DOC_STG_MOIS_001_GOV24)로 대체",
    "DOC_STG_EX_001_FAQ": "한국도로공사 Q&A 원문 미확보 → 정부24 서비스 상세(DOC_STG_EX_001_GOV24)로 대체",
    "DOC_STG_MOLIT_001_GDL": "운영지침 원문 미확보 → 정부24 서비스 상세(DOC_STG_MOLIT_001_GOV24)로 대체",
    "DOC_STG_MOLIT_001_FAQ": "K-패스 FAQ 원문 미확보. 요약본의 저소득층 환급률(53.3%)이 정부24 원문(53%)과 다름",
    "DOC_STG_SEOUL_001_NOT": "2026 모집공고 원문 미확보 → 온통청년 정책 상세(DOC_STG_SEOUL_001_YC)로 대체",
    "DOC_STG_SEOUL_001_FAQ": "서울청년수당 FAQ 원문 미확보",
    "DOC_STG_ICN_001_NOT": "참여자 모집공고 원문 미확보 → 정부24 서비스 상세(DOC_STG_ICN_001_GOV24)로 대체",
}

# 정부24·온통청년 API의 소관기관명 (staging 정책 메타데이터의 기관명이 원문과 달라 교정)
OFFICIAL_AGENCY = {
    "POL_STG_MOHW_001": "행정안전부 행정정보공유과",
    "POL_STG_EX_001": "한국도로공사",
    "POL_STG_MOLIT_001": "국토교통부 광역교통경제과",
    "POL_STG_SEOUL_001": "서울특별시 미래청년기획관 청년정책담당관",
    "POL_STG_ICN_001": "인천광역시 서해구 기업일자리정책과",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: list) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def find_raw_page(raw_dir: Path, official_id: str) -> str:
    """API 원본 응답 파일 중 해당 ID가 들어 있는 파일 경로를 찾는다."""
    for page in sorted(raw_dir.glob("*.json")):
        if f'"{official_id}"' in page.read_text(encoding="utf-8"):
            return page.relative_to(BASE_DIR).as_posix()
    raise FileNotFoundError(f"{official_id} 원본 응답을 {raw_dir}에서 찾지 못함")


def official_body(markdown: str) -> str:
    """official/*.md에서 출처 메모(> 줄)를 빼고 조문 본문만 남긴다."""
    lines = [line for line in markdown.splitlines() if not line.startswith(">")]
    return "\n".join(lines).strip()


def split_text(text: str, pattern: str) -> list:
    """구분 패턴(조문 항·섹션 머리) 단위로 나눈 뒤 MAX_CHUNK_CHARS 이내로 묶는다."""
    parts = [p.strip() for p in re.split(pattern, text) if p.strip()]
    chunks, current = [], ""
    for part in parts:
        if current and len(current) + len(part) + 2 > MAX_CHUNK_CHARS:
            chunks.append(current)
            current = part
        else:
            current = f"{current}\n\n{part}" if current else part
    if current:
        chunks.append(current)
    return chunks


def chunk_document(doc: dict, text: str) -> list:
    if doc["doc_type"] in ("law", "ordinance"):
        # 조문 머리(제N조)와 항(①②…) 경계에서 나눈다. 법령명·시행 정보 머리말은 첫 청크에 남는다.
        pieces = split_text(text, r"\n(?=제\d+조(?:의\d+)?\()|\n(?=[①-⑳])")
    else:
        # API 원문의 [지원대상]·[지원내용] 같은 섹션 머리에서 나눈다.
        pieces = split_text(text, r"\n(?=\[[^\]]+\]\n)")
    return [
        {"chunk_id": f"CHK_{doc['document_id']}_{i:02d}", "text": piece}
        for i, piece in enumerate(pieces, start=1)
    ]


def build_documents(policies: dict) -> list:
    docs = []
    for spec in OFFICIAL_LAW_DOCS:
        path = OFFICIAL_DIR / spec["file"]
        text = official_body(path.read_text(encoding="utf-8"))
        docs.append({
            "document_id": spec["document_id"], "policy_id": spec["policy_id"],
            "title": spec["title"], "source_type": "law_text" if spec["doc_type"] == "law" else "ordinance_text",
            "source_url": spec["source_url"], "local_raw_path": path.relative_to(BASE_DIR).as_posix(),
            "content_hash": sha256_text(text), "verification_status": "verified_official_text",
            "collected_at": RETRIEVED_AT, "doc_type": spec["doc_type"], "law_article": spec["law_article"],
            "effective_date": spec["effective_date"], "version": spec["version"], "_text": text,
        })

    gov24 = {r["service_id"]: r for r in read_jsonl(GOV24_PATH)}
    for policy_id, service_id in GOV24_SERVICE_DOCS.items():
        record = gov24[service_id]
        short = policy_id.replace("POL_STG_", "")
        docs.append({
            "document_id": f"DOC_STG_{short}_GOV24", "policy_id": policy_id,
            "title": f"정부24 공공서비스 상세 — {record['service_name']}",
            "source_type": "gov24_api", "source_url": record["source_url"],
            "local_raw_path": find_raw_page(BASE_DIR / "data/raw/gov24/serviceDetail", service_id),
            "content_hash": sha256_text(record["full_text"]), "verification_status": "verified_official_api",
            "collected_at": record["retrieved_at"], "doc_type": "service_guide", "law_article": None,
            "effective_date": None, "version": None, "official_id": service_id, "_text": record["full_text"],
        })

    youth = {r["plcyNo"]: r for r in read_jsonl(YOUTH_PATH)}
    for policy_id, plcy_no in YOUTH_SERVICE_DOCS.items():
        record = youth[plcy_no]
        short = policy_id.replace("POL_STG_", "")
        docs.append({
            "document_id": f"DOC_STG_{short}_YC", "policy_id": policy_id,
            "title": f"온통청년 정책 상세 — {record['policy_name']}",
            "source_type": "youthcenter_api", "source_url": record["source_url"],
            "local_raw_path": record["local_raw_path"],
            "content_hash": sha256_text(record["full_text"]), "verification_status": "verified_official_api",
            "collected_at": record["retrieved_at"], "doc_type": "service_guide", "law_article": None,
            "effective_date": None, "version": None, "official_id": plcy_no, "_text": record["full_text"],
        })

    missing = [d["policy_id"] for d in docs if d["policy_id"] not in policies]
    assert not missing, f"staging 정책에 없는 policy_id: {missing}"
    return docs


def build_chunks(docs: list, policies: dict) -> list:
    payloads = []
    for doc in docs:
        policy = policies[doc["policy_id"]]
        for chunk in chunk_document(doc, doc["_text"]):
            article = doc["law_article"]
            payloads.append({
                "point_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk["chunk_id"])),
                "chunk_id": chunk["chunk_id"],
                "policy_id": policy["policy_id"],
                "policy_name": policy["policy_name"],
                "document_id": doc["document_id"],
                "document_title": doc["title"],
                "section_title": doc["title"],
                "category": policy["category"],
                "region": policy["regions"][0] if policy["regions"] else "전국",
                "life_stages": policy["life_stages"],
                "target_groups": policy["target_groups"],
                "income_level": None,
                "benefit_type": policy["category"],
                "verification_status": doc["verification_status"],
                "eligibility_attributes": {"source": doc["doc_type"], "law_article": article},
                "text": chunk["text"],
                "doc_type": doc["doc_type"],
                "law_article": article,
                "effective_date": doc["effective_date"],
                "version": doc["version"],
                "source_url": doc["source_url"],
            })
    return payloads


def update_policies(policies: list) -> list:
    """정부24·온통청년 원문으로 소관기관·지원내용·대상 문구를 교정하고, 조건 미검증 상태를 표시한다."""
    gov24 = {r["service_id"]: r for r in read_jsonl(GOV24_PATH)}
    youth = {r["plcyNo"]: r for r in read_jsonl(YOUTH_PATH)}
    for p in policies:
        if p["policy_id"] == "POL_001":
            continue  # 기존 v1.3.2 정책은 건드리지 않음
        if p["policy_id"] in OFFICIAL_AGENCY:
            p["agency"] = OFFICIAL_AGENCY[p["policy_id"]]
        record = None
        if p["policy_id"] in GOV24_SERVICE_DOCS:
            r = gov24[GOV24_SERVICE_DOCS[p["policy_id"]]]
            record = {"eligibility_text": r["target_audience"], "benefit_text": r["benefit"],
                      "application_period": r["application_period"], "application_method": r["application_method"],
                      "official_apply_url": r["official_apply_url"] or r["source_url"]}
        elif p["policy_id"] in YOUTH_SERVICE_DOCS:
            r = youth[YOUTH_SERVICE_DOCS[p["policy_id"]]]
            record = {"eligibility_text": r["eligibility"], "benefit_text": r["benefit"],
                      "application_period": r["application_period"], "application_method": r["application_method"],
                      "official_apply_url": r["official_apply_url"]}
        if record:
            p.update(record)
        # 자격 조건 그룹이 UNKNOWN이고 조건 노드가 없으므로 'verified'로 두지 않는다.
        p["verification_status"] = "extracted"
    return policies


def main() -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    for name in ("documents.jsonl", "payload_chunks.jsonl", "policies.jsonl"):
        backup = BACKUP_DIR / name
        if not backup.exists():  # 최초 실행 시점(원래 staging)만 보존
            shutil.copy2(STAGING_DIR / name, backup)

    old_docs = read_jsonl(BACKUP_DIR / "documents.jsonl")
    policies = update_policies(read_jsonl(BACKUP_DIR / "policies.jsonl"))
    policy_map = {p["policy_id"]: p for p in policies}

    docs = build_documents(policy_map)
    chunks = build_chunks(docs, policy_map)

    kept_ids = {d["document_id"] for d in docs}
    results = []
    for d in old_docs:
        did = d["document_id"]
        if did in kept_ids:
            results.append({"document_id": did, "result": "replaced_with_official_text",
                            "old_status": d["verification_status"]})
        else:
            assert did in EXCLUDED_DOCS, f"제외 사유가 없는 문서: {did}"
            results.append({"document_id": did, "result": "excluded",
                            "old_status": d["verification_status"], "reason": EXCLUDED_DOCS[did]})
    for d in docs:
        if d["document_id"] not in {o["document_id"] for o in old_docs}:
            results.append({"document_id": d["document_id"], "result": "added_from_official_api",
                            "source_url": d["source_url"]})

    write_jsonl(STAGING_DIR / "documents.jsonl", [{k: v for k, v in d.items() if k != "_text"} for d in docs])
    write_jsonl(STAGING_DIR / "payload_chunks.jsonl", chunks)
    write_jsonl(STAGING_DIR / "policies.jsonl", policies)
    with open(STAGING_DIR / "crosscheck_results.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"checked_at": RETRIEVED_AT, "documents": results}, f, ensure_ascii=False, indent=2)

    print(f"문서 {len(docs)}건 (법령·조례 {len(OFFICIAL_LAW_DOCS)}, API 서비스 안내 {len(docs) - len(OFFICIAL_LAW_DOCS)}), "
          f"청크 {len(chunks)}건, 제외 {sum(r['result'] == 'excluded' for r in results)}건")


if __name__ == "__main__":
    main()
