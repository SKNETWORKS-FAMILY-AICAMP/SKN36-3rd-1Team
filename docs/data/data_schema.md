# LifePolicy AI — 데이터 스키마 명세서 (3차 확정)

> **담당:** 데이터 수집/검증 담당 (장반석)  
> **상태:** **3차 연동 및 전수 검증 완료**  
> **근거:** [PRD §6](../product/PRD.md), [CODING_STYLE §8](../development/CODING_STYLE.md), 3차 작업 지시서 §6

---

## 1. 공통 식별자 및 속성 규칙

| 필드 | 타입 | 설명 | 예시 |
|---|---|---|---|
| `policy_id` | String | 프로젝트 고유 정책 ID (`POL_001`~`POL_010`, `POL_BOK_***`, `POL_GOV_***`, `POL_YC_***`) | `POL_001`, `POL_BOK_001` |
| `official_id` | String | 제공기관/공식 API 서비스 식별자 | `LH-2015122300019712`, `WLF00004661`, `161300000099` |
| `document_id` | String | 공식 근거 문서 식별자 | `DOC_LH_001`, `DOC_NHUF_008`, `DOC_BOK_001` |
| `chunk_id` | String | Vector DB 적재용 청크 ID (`{document_id}_P{페이지}_C{순번}` 또는 `{document_id}_C{순번}`) | `DOC_LH_001_P01_C01`, `DOC_BOK_001_C01` |
| `source_url` | String | 공식 원문 접근 URL | `https://nhuf.molit.go.kr/...` |
| `local_raw_path` | String | 프로젝트 루트 기준 원본 파일 상대 경로 (슬래시 표준화) | `data/raw/nhuf/nhuf_youth_monthly_rent_deposit_guide.html` |
| `content_hash` | String | 원문 SHA-256 해시값 (무결성 검증용) | `64자리 16진수 해시` |
| `retrieved_at` / `collected_at` | String | 수집 일시 (ISO 8601 + KST) | `2026-11-04T19:30:00+09:00` |
| `verification_status` | String | 검증 상태 (`verified`, `extracted`, `downloaded`, `failed`, `needs_review`) | `verified`, `extracted` |

---

## 2. 파일별 필드 명세

### 2.1 `data/processed/policies.jsonl` (237건)
- `policy_id`, `official_id`, `policy_name`, `source_type`, `category` (5대 분야)
- `agency`, `organization` (소관/운영기관)
- `regions` (배열), `region` (문자열)
- `target_groups` (배열), `target_group` (문자열)
- `life_stages` (배열 - 6대 표준 생애단계: `영유아`, `아동`, `청소년`, `청년`, `중장년`, `노년`)
- `eligibility_attributes` (구조화 객체: 가족/혼인, 소득, 취업, 특수대상, 주거)
- `requirements` (구조화 사전: 연령, 주택, 소득, 자산, 선정기준 등)
- `eligibility` (자격요건 요약문)
- `benefit` (지원내용 및 혜택금액/한도)
- `application_period`, `application_start`, `application_end`, `application_method`
- `required_documents` (구비서류 리스트)
- `official_apply_url`, `application_url`, `source_url`
- `source_document_ids` (참조 근거 문서 ID 배열)
- `reception_status` (`active`, `closed`, `scheduled`)
- `eligibility_review_status`, `verification_status`
- `retrieved_at`, `collected_at`

### 2.2 `data/processed/documents.jsonl` (246건)
- `document_id`, `policy_id`, `title`, `source_type` (`pdf`, `html`, `api`)
- `source_url`, `local_raw_path` (디스크 실재 100% 검증)
- `published_at`, `effective_at`
- `retrieved_at`, `collected_at`
- `content_hash` (SHA-256)
- `text`, `content` (UI 노이즈 및 에러가 제거된 순수 본문)
- `review_status`, `verification_status`

### 2.3 `data/processed/chunks.jsonl` (557건 - Qdrant Vector DB 적재용)
- `chunk_id`, `policy_id`, `document_id`, `policy_name`, `category`, `region`, `target_group`
- `life_stages` (6대 생애단계 메타데이터)
- `section_title`, `chunk_index`, `page_number`
- `text`, `content` (600~900자 의미 단위 텍스트, 80자 오버랩)
- `source_url`, `verification_status`

### 2.4 `data/processed/relationships.jsonl` & `graph_edges.jsonl` (2,537건 - Neo4j Graph DB 적재용)
- `source_id`, `relation`, `target_id`, `target_name`, `evidence_document_id`, `evidence_location`, `review_status`
- 관계 유형:
  - `ADMINISTERED_BY` (Policy -> Agency)
  - `HAS_CATEGORY` (Policy -> Category)
  - `AVAILABLE_IN` (Policy -> Region)
  - `TARGETS` (Policy -> TargetGroup)
  - `APPLIES_TO_LIFESTAGE` (Policy -> LifeStage: 419건)
  - `REQUIRES_CONDITION` (Policy -> EligibilityCondition)
  - `SUPPORTED_BY` (Policy -> Document)
  - `MODIFIES_CONDITION_OF` (Policy -> Policy)

> **상세 명세:** 세부 엔티티 ER 다이어그램 및 Neo4j/Qdrant 상세 설계 요구사항은 [DB 스키마 설계 요구사항 명세서](db_schema_change_proposal.md)를 참조하십시오.
